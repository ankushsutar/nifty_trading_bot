import time
import datetime

from bot.config.settings import Config
from bot.core.trade_repo import trade_repo
from bot.utils.logger import logger
from bot.config.instruments import get_instrument
from bot.strategies.base_strategy import BaseStrategy


class FuturesStrategy(BaseStrategy):
    """
    Directional futures strategy for MCX commodity contracts (FUTCOM).

    Trades the front-month futures contract directly — no CE/PE strike selection.

    Entry:  Trend + ADX gate from DecisionEngine regime (BULLISH → BUY, BEARISH → SELL).
    SL:     ATR × 1.5 (commodity-appropriate volatility-adjusted stop).
    Target: 2:1 R/R on the ATR-based risk.
    Exit:   MARKET order to close the futures position (CARRYFORWARD producttype).

    This strategy is NOT compatible with NSE index instruments (OPTIDX).
    It is only safe to instantiate for instruments with asset_type == "COMMODITY".
    """

    def __init__(self, api, token_loader, dry_run=False):
        super().__init__(api, token_loader, "FUTURES_MOMENTUM", dry_run)
        self.sync_state()

    # ------------------------------------------------------------------ #
    #  Entry                                                               #
    # ------------------------------------------------------------------ #

    def execute(self, expiry):
        instr = get_instrument(Config.ACTIVE_SYMBOL)
        logger.info(f"🛢️ --- FUTURES STRATEGY: {instr.name} ({expiry}) ---")

        while self.running:
            # Safety guards
            if not self.gatekeeper.is_market_open():
                logger.warning("Futures: 🛑 Market Closed. Exiting.")
                break
            if self.gatekeeper.is_blackout_period():
                logger.info("Futures: ⏸️ Blackout/Cutoff period. Waiting 60s.")
                time.sleep(60)
                continue
            if not self.gatekeeper.check_max_daily_loss(0.0):
                logger.critical("Futures: 🛑 Daily loss limit hit. Halting.")
                break

            # Post-SL cooldown (5 min)
            _SL_COOLDOWN = 300
            _since_sl = time.time() - self._last_sl_hit_time
            if self._last_sl_hit_time > 0 and _since_sl < _SL_COOLDOWN:
                logger.info(f"Futures: ⏳ Post-SL cooldown — {int(_SL_COOLDOWN - _since_sl)}s remaining.")
                time.sleep(30)
                continue

            # Crash recovery
            mode = "PAPER" if self.dry_run else "LIVE"
            active_trade = trade_repo.get_active_trade(mode=mode, strategy="FUTURES_MOMENTUM")
            if active_trade:
                if active_trade.get('entry_price', 0.0) == 0.0:
                    logger.warning(f"Futures: Ghost trade (entry=0). Removing: {active_trade['symbol']}")
                    trade_repo.collection.delete_one({"id": active_trade['id']})
                    continue
                logger.info(f">>> [Resumption] Resuming futures trade: {active_trade['symbol']}")
                self._monitor(
                    symbol=active_trade['symbol'],
                    token=active_trade['token'],
                    qty=active_trade['qty'],
                    entry_price=active_trade['entry_price'],
                    sl_price=active_trade['sl_price'],
                    direction=active_trade.get('leg', 'BUY'),
                    trade_id=active_trade['id'],
                    instr=instr,
                )
                break

            # Regime check
            from backend.market_service import market_service
            market_data = market_service.get_market_data()
            analysis = market_data.get('analysis', {})
            trend = analysis.get('trend', 'NEUTRAL')
            adx = float(analysis.get('adx', 0))
            regime = analysis.get('regime', 'UNKNOWN')

            capital = self.gatekeeper.get_current_capital()
            tier = Config.get_tier(capital)

            if regime == 'VOLATILE':
                logger.warning(f"Futures: Volatile regime. Staying in cash.")
                time.sleep(60)
                continue

            if adx < tier.min_adx_to_trade:
                logger.info(f"Futures: ADX {adx:.1f} < {tier.min_adx_to_trade} gate. Waiting...")
                time.sleep(60)
                continue

            if trend not in ('BULLISH', 'BEARISH'):
                logger.info(f"Futures: No directional trend ({trend}). Waiting...")
                time.sleep(60)
                continue

            direction = "BUY" if trend == "BULLISH" else "SELL"

            # Token lookup
            token, symbol = self.token_loader.get_futures_token(
                instr.name, expiry,
                instrument_type=instr.instrument_type,
                exchange=instr.exchange,
            )
            if not token:
                logger.error(f"Futures: Token not found for {instr.name} {expiry}. Retrying in 60s.")
                time.sleep(60)
                continue

            # Fetch LTP
            ltp = self.data_fetcher.get_ltp(token, exchange=instr.exchange, symbol=symbol)
            if not ltp or ltp <= 0:
                logger.warning("Futures: Could not fetch LTP. Retrying in 30s.")
                time.sleep(30)
                continue

            # ATR-based SL
            df = self.data_fetcher.fetch_latest_candles(
                instr.analysis_token, interval="FIVE_MINUTE", exchange=instr.exchange
            )
            if df is None or len(df) < 14:
                logger.warning("Futures: Insufficient candle data for ATR. Retrying in 60s.")
                time.sleep(60)
                continue

            from bot.utils.indicators import atr as compute_atr
            atr_val = float(compute_atr(
                df['high'].values, df['low'].values, df['close'].values
            )[-1])
            if atr_val <= 0:
                logger.warning("Futures: ATR is zero. Skipping.")
                time.sleep(60)
                continue

            sl_points = round(atr_val * 1.5, 1)
            if direction == "BUY":
                sl_price = round(ltp - sl_points, 1)
                target_price = round(ltp + sl_points * 2.0, 1)
            else:
                sl_price = round(ltp + sl_points, 1)
                target_price = round(ltp - sl_points * 2.0, 1)

            # Position sizing: risk-amount ÷ (sl_points × lot_size)
            risk_amount = capital * tier.risk_per_trade_pct
            risk_per_lot = sl_points * instr.lot_size
            lots = max(1, int(risk_amount / risk_per_lot))
            if tier.max_lots > 0:
                lots = min(lots, tier.max_lots)
            qty = lots * instr.lot_size

            # Margin guard: approximate MCX margin as ~10% of notional
            approx_margin = ltp * qty * 0.10
            if not self.gatekeeper.check_trade_margin(approx_margin):
                logger.warning(f"Futures: Margin check failed for {lots} lot(s). Halting.")
                break

            logger.info(
                f">>> [Futures Entry] {symbol} ({direction}) | LTP=₹{ltp} | "
                f"SL=₹{sl_price} ({sl_points}pts) | Target=₹{target_price} | Qty={qty} ({lots} lot)"
            )

            # Place order
            oid = self._place_order(symbol, token, qty, ltp, direction, instr.exchange)
            if not oid:
                logger.error("Futures: Order placement failed. Retrying in 60s.")
                time.sleep(60)
                continue

            # Wait for fill
            fill_result = self.wait_for_fill(oid)
            if fill_result['status'] != 'FILLED':
                logger.warning(f"Futures: Entry not filled ({fill_result['status']}). Cancelling.")
                if fill_result['status'] == 'TIMEOUT':
                    self.order_manager.cancel_order(oid, variety="NORMAL")
                time.sleep(60)
                continue

            fill_price = fill_result.get('price') or ltp
            # Recalculate SL/target from actual fill
            if direction == "BUY":
                sl_price = round(fill_price - sl_points, 1)
                target_price = round(fill_price + sl_points * 2.0, 1)
            else:
                sl_price = round(fill_price + sl_points, 1)
                target_price = round(fill_price - sl_points * 2.0, 1)

            # Persist
            trade_id = trade_repo.save_trade(
                symbol=symbol,
                token=token,
                leg=direction,
                qty=qty,
                entry_price=fill_price,
                sl_price=sl_price,
                side=direction,
                mode=mode,
                strategy="FUTURES_MOMENTUM",
            )

            logger.info(
                f">>> [Futures] Position Open | Fill=₹{fill_price} | "
                f"SL=₹{sl_price} | Target=₹{target_price}"
            )

            self._monitor(symbol, token, qty, fill_price, sl_price, target_price, direction, trade_id, instr)
            break

    # ------------------------------------------------------------------ #
    #  Monitor loop                                                        #
    # ------------------------------------------------------------------ #

    def _monitor(self, symbol, token, qty, entry_price, sl_price,
                 target_price=None, direction="BUY", trade_id=None, instr=None):
        if instr is None:
            instr = get_instrument(Config.ACTIVE_SYMBOL)
        if target_price is None:
            # Reconstruct target from stored SL distance (2:1 R/R)
            risk = abs(entry_price - sl_price)
            if direction == "BUY":
                target_price = round(entry_price + risk * 2.0, 1)
            else:
                target_price = round(entry_price - risk * 2.0, 1)

        risk_pts = abs(entry_price - sl_price)
        _last_log = 0

        while self.running:
            if not self.gatekeeper.is_market_open():
                logger.warning("Futures Monitor: Market closed. Exiting position.")
                self._close(symbol, token, qty, direction, trade_id, instr, reason="MARKET_CLOSE")
                break

            if not self.gatekeeper.check_max_daily_loss(active_unrealized_pnl=0.0):
                logger.critical("Futures Monitor: Daily loss limit. Exiting position.")
                self._close(symbol, token, qty, direction, trade_id, instr, reason="DAILY_LOSS")
                break

            ltp = self.data_fetcher.get_ltp(token, exchange=instr.exchange, symbol=symbol)
            if not ltp or ltp <= 0:
                time.sleep(1)
                continue

            if direction == "BUY":
                pnl = (ltp - entry_price) * qty
                sl_hit = ltp <= sl_price
                target_hit = ltp >= target_price
            else:
                pnl = (entry_price - ltp) * qty
                sl_hit = ltp >= sl_price
                target_hit = ltp <= target_price

            pnl_pct = pnl / (entry_price * qty) * 100

            # Heartbeat every 30s
            now = time.time()
            if now - _last_log >= 30:
                logger.info(
                    f"Futures: 📊 MONITOR | LTP=₹{ltp} | Entry=₹{entry_price} | "
                    f"SL=₹{sl_price} | Target=₹{target_price} | Qty={qty} | "
                    f"P&L=₹{pnl:+.0f} ({pnl_pct:+.1f}%)"
                )
                _last_log = now

            if sl_hit:
                logger.warning(f"Futures: 🛑 SL HIT! LTP={ltp} | SL={sl_price}")
                self._close(symbol, token, qty, direction, trade_id, instr, reason="SL")
                self._last_sl_hit_time = time.time()
                break

            if target_hit:
                logger.info(f"Futures: 🎯 TARGET HIT! LTP={ltp} | Target={target_price}")
                self._close(symbol, token, qty, direction, trade_id, instr, reason="TARGET")
                break

            # Trail: move SL by 1 ATR once we are 1R in profit
            if direction == "BUY" and ltp > entry_price + risk_pts:
                new_sl = round(ltp - risk_pts, 1)
                if new_sl > sl_price:
                    logger.info(f"Futures: 📈 Trailing SL {sl_price} → {new_sl}")
                    sl_price = new_sl
                    if trade_id:
                        trade_repo.update_sl(trade_id, sl_price)
            elif direction == "SELL" and ltp < entry_price - risk_pts:
                new_sl = round(ltp + risk_pts, 1)
                if new_sl < sl_price:
                    logger.info(f"Futures: 📉 Trailing SL {sl_price} → {new_sl}")
                    sl_price = new_sl
                    if trade_id:
                        trade_repo.update_sl(trade_id, sl_price)

            time.sleep(1)

    # ------------------------------------------------------------------ #
    #  Order helpers                                                       #
    # ------------------------------------------------------------------ #

    def _place_order(self, symbol, token, qty, ltp, transaction_type, exchange):
        """Limit order with 0.5% slippage buffer, CARRYFORWARD producttype (MCX NRML)."""
        buf = 1.005 if transaction_type == "BUY" else 0.995
        limit_price = round(ltp * buf, 1)

        if self.dry_run:
            logger.info(
                f"🧪 [DRY RUN] Futures: {symbol} {transaction_type} {qty} @ {limit_price}"
            )
            return f"DRY_{int(time.time())}"

        from bot.utils.rate_limiter import rate_limiter
        from bot.core.kill_switch import is_kill_switch_active
        if is_kill_switch_active():
            logger.critical("🛑 KILL SWITCH ACTIVE. Futures order rejected.")
            return None

        rate_limiter.wait()
        try:
            params = {
                "variety": "NORMAL",
                "tradingsymbol": symbol,
                "symboltoken": token,
                "transactiontype": transaction_type,
                "exchange": exchange,
                "ordertype": "LIMIT",
                "producttype": "CARRYFORWARD",   # MCX futures: NRML (no MIS/INTRADAY)
                "duration": "DAY",
                "quantity": qty,
                "price": limit_price,
                "disclosedquantity": 0,
            }
            resp = self.api.placeOrder(params)
            if resp and resp.get('status'):
                oid = resp['data']['orderid']
                logger.info(f"✅ Futures Order: {oid}")
                from bot.core.order_feed import order_feed
                order_feed.register_order(oid)
                return oid
            logger.error(f"❌ Futures Order Rejected: {resp.get('message') if resp else 'No response'}")
            return None
        except Exception as e:
            logger.error(f"Futures order error: {e}")
            return None

    def _close(self, symbol, token, qty, direction, trade_id, instr, reason="EXIT"):
        """Market order to flatten the futures position."""
        close_side = "SELL" if direction == "BUY" else "BUY"
        logger.info(f">>> [Futures Exit] {symbol} {close_side} {qty} | Reason: {reason}")

        if self.dry_run:
            logger.info(f"🧪 [DRY RUN] Futures Close: {symbol} {close_side} {qty}")
            if trade_id:
                trade_repo.close_trade(trade_id=trade_id, exit_price=0.0, exit_reason=reason)
            self.active_position = None
            return

        from bot.utils.rate_limiter import rate_limiter
        rate_limiter.wait()
        try:
            params = {
                "variety": "NORMAL",
                "tradingsymbol": symbol,
                "symboltoken": token,
                "transactiontype": close_side,
                "exchange": instr.exchange,
                "ordertype": "MARKET",
                "producttype": "CARRYFORWARD",
                "duration": "DAY",
                "quantity": qty,
                "price": 0,
                "disclosedquantity": 0,
            }
            resp = self.api.placeOrder(params)
            if resp and resp.get('status'):
                oid = resp['data']['orderid']
                logger.info(f"✅ Futures Exit Order: {oid}")
                fill = self.wait_for_fill(oid)
                exit_price = fill.get('price', 0.0)
                if trade_id:
                    trade_repo.close_trade(
                        trade_id=trade_id,
                        exit_price=exit_price,
                        exit_reason=reason,
                    )
            else:
                logger.error(f"❌ Futures Exit Failed: {resp.get('message') if resp else 'No response'}")
        except Exception as e:
            logger.error(f"Futures close error: {e}")
        finally:
            self.active_position = None
