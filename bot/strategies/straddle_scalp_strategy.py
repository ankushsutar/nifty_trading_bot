import time
import datetime
from bot.config.settings import Config
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.trade_repo import trade_repo
from bot.core.data_fetcher import DataFetcher
from bot.core.order_manager import OrderManager
from bot.core.regime_classifier import RegimeClassifier
from bot.core.market_feed import market_feed
from bot.utils.logger import logger
from bot.utils.notifier import notifier
from backend.market_service import market_service


class StraddleScalpStrategy:
    """
    Long Straddle (Option Buying) Strategy.
    Designed to trade range breakouts or volatility expansions on range-bound / sideways days (ADX < 25.0).
    
    By buying options instead of selling, this strategy:
      1. Resolves margin block issues (uses premium budget only, ~₹5k-₹10k per lot).
      2. Halves brokerage expenses (places 2 orders instead of 4 wings + adjustments).
      3. Minimizes whipsaw stop-loss hits through basket-level monitoring.
      
    Execution Protocol:
      - Long Call (LC): ATM Strike CE
      - Long Put (LP): ATM Strike PE
      
    Exit Monitoring:
      - Monitor combined premium basket.
      - Take Profit: Combined LTP >= 1.25 * Net Debit (25% expansion).
      - Stop Loss: Combined LTP <= 0.85 * Net Debit (15% decay).
    """

    STRATEGY_NAME      = "STRADDLE_SCALP"
    PROFIT_TARGET_PCT  = 0.25   # Close at 25% premium expansion of net debit
    STOP_LOSS_PCT      = 0.15   # Close at 15% premium decay of net debit
    MAX_ADX_TO_ENTER   = 25.0   # Sideways filter
    MAX_ENTRY_TIME     = datetime.time(11, 0)
    MAX_ENTRY_EXPIRY   = datetime.time(12, 30)

    def __init__(self, api, token_loader, dry_run=False):
        self.api            = api
        self.token_loader   = token_loader
        self.dry_run        = dry_run
        self.gatekeeper     = SafetyGatekeeper(api, dry_run=dry_run)
        self.order_manager  = OrderManager(api, dry_run=dry_run)
        self.data_fetcher   = DataFetcher(api)
        self.classifier     = RegimeClassifier()
        self.running        = True
        self.risk_multiplier = 1.0
        self._last_log_ts   = 0.0
        self._last_trend_check = 0.0
        self.last_failed_entry_time = 0.0

        # Position tracking variables
        self.lc_position = None  # Long Call Protection: symbol, token, qty, entry_price, id
        self.lp_position = None  # Long Put Protection

    @property
    def active_position(self):
        """Shim used by main.py shutdown handler."""
        return self.lc_position or self.lp_position

    # ─────────────────────────────────────────────────────────────────────
    # Lifecycle & Recovery
    # ─────────────────────────────────────────────────────────────────────

    def stop(self):
        self.running = False
        if self.active_position:
            logger.warning("Straddle Scalp: 🛑 Stop requested — closing Long Straddle legs.")
            self._close_all("USER_STOPPED")

    def sync_state(self):
        """Recover open Long Straddle legs from DB after a restart."""
        mode = "PAPER" if self.dry_run else "LIVE"
        try:
            query = {"status": {"$in": ["OPEN", "PLACED"]}, "mode": mode, "strategy": self.STRATEGY_NAME}
            open_trades = list(trade_repo.collection.find(query))
            if not open_trades:
                return

            # 📅 EXPIRY GUARD: if ANY leg belongs to a past-expiry contract, close ALL
            # stale DB records and skip recovery entirely — never monitor a dead basket.
            expired_symbols = [t['symbol'] for t in open_trades if trade_repo._is_symbol_expired(t['symbol'])]
            if expired_symbols:
                logger.warning(
                    f"⚠️ [Straddle Scalp] Past-expiry legs detected on recovery: {expired_symbols}. "
                    f"Closing all {len(open_trades)} stale DB records."
                )
                for t in open_trades:
                    trade_repo.close_trade(
                        trade_id=t['id'],
                        exit_price=0.0,
                        pnl=0.0,
                        exit_reason="EXPIRED_CONTRACT_ON_RECOVERY"
                    )
                return  # No basket to recover

            for t in open_trades:
                pos = {
                    'id':          t['id'],
                    'symbol':      t['symbol'],
                    'token':       t['token'],
                    'qty':         t['qty'],
                    'entry_price': t['entry_price'],
                    'sl_oid':      t.get('sl_order_id')
                }
                leg_type = t.get('leg')
                if leg_type == 'LC' and self.lc_position is None:
                    self.lc_position = pos
                    logger.info(f"♻️ [Straddle Scalp] Recovered Long Call: {t['symbol']} @ ₹{t['entry_price']}")
                elif leg_type == 'LP' and self.lp_position is None:
                    self.lp_position = pos
                    logger.info(f"♻️ [Straddle Scalp] Recovered Long Put: {t['symbol']} @ ₹{t['entry_price']}")
        except Exception as e:
            logger.error(f"Straddle Scalp sync_state error: {e}")

    # ─────────────────────────────────────────────────────────────────────
    # Main Loop
    # ─────────────────────────────────────────────────────────────────────

    def execute(self, expiry, action="BUY"):
        logger.info(f"🎯 --- STRADDLE SCALP STRATEGY ACTIVE (Long Straddle, Expiry: {expiry}) ---")
        self.sync_state()

        today_str  = datetime.datetime.now().strftime("%d%b%Y").upper()
        is_expiry  = (expiry == today_str)
        entry_cutoff = self.MAX_ENTRY_EXPIRY if is_expiry else self.MAX_ENTRY_TIME
        logger.info(
            f"Straddle Scalp: Entry window → {entry_cutoff} "
            f"({'Expiry day' if is_expiry else 'Normal day'})"
        )

        while self.running:
            # 0. Entry Cooldown Circuit Breaker (5 mins)
            if time.time() - getattr(self, 'last_failed_entry_time', 0.0) < 300:
                time.sleep(10)
                continue

            # 1. Hard safety checks
            if not self.gatekeeper.is_market_open():
                logger.warning("Straddle Scalp: 🛑 Market Closed. Exiting.")
                break
            if not self.gatekeeper.check_max_daily_loss(0.0):
                if self.gatekeeper.last_breaker_triggered == "PROFIT_PROTECTION":
                    logger.critical("Straddle Scalp: 🛑 Profit Protection locked. Halting.")
                else:
                    logger.critical("Straddle Scalp: 🛑 Max Daily Loss hit. Halting.")
                break

            # 2. Check if positions are established
            has_positions = (self.lc_position and self.lp_position)
            if has_positions:
                result = self._monitor_straddle()
                if result in ("PROFIT", "LOSS", "TIME"):
                    break
                time.sleep(10)
                continue

            # 3. Handle incomplete basket (crash recovery / execution gap)
            if self.lc_position or self.lp_position:
                logger.warning("Straddle Scalp: ⚠️ Incomplete Long Straddle basket detected. Closing all legs for safety.")
                self._close_all("INCOMPLETE_BASKET")
                break

            # 4. Entry time window gate
            now = datetime.datetime.now().time()
            if now >= entry_cutoff:
                logger.info(f"Straddle Scalp: ⏰ Past entry window ({entry_cutoff}). No new entries allowed today.")
                break

            # 5. ADX + Regime Gates
            market_data = market_service.get_market_data()
            analysis    = market_data.get('analysis', {})
            regime      = analysis.get('regime', 'UNKNOWN')
            adx         = analysis.get('adx', 99.0)

            if not analysis or regime == "UNKNOWN":
                logger.warning("Straddle Scalp: Centralized intelligence not ready. Retrying in 30s.")
                time.sleep(30)
                continue

            if adx >= self.MAX_ADX_TO_ENTER:
                logger.info(
                    f"Straddle Scalp: ⏸️ ADX={adx:.1f} ≥ {self.MAX_ADX_TO_ENTER} "
                    "— trending market, not a range-bound day. Waiting."
                )
                time.sleep(60)
                continue

            if regime not in ("SIDEWAYS", "CHOP", "RANGING"):
                logger.info(f"Straddle Scalp: ⏸️ Regime={regime} — need SIDEWAYS/CHOP. Waiting.")
                time.sleep(60)
                continue

            logger.info(
                f"Straddle Scalp: ✅ Entry conditions met — ADX={adx:.1f} | Regime={regime}. "
                "Entering Long Straddle."
            )
            success = self._enter_straddle(expiry)
            if success is False:
                logger.warning("Straddle Scalp: 🛑 Entry failed. Activating 5-minute cooldown circuit breaker.")
                self.last_failed_entry_time = time.time()
            time.sleep(30)

    # ─────────────────────────────────────────────────────────────────────
    # Entry Execution
    # ─────────────────────────────────────────────────────────────────────

    def _enter_straddle(self, expiry):
        """
        Execute 2-leg Long Straddle:
        1. Place BUY orders for ATM Call (LC) & ATM Put (LP).
        2. Wait for BUY orders to fill.
        """
        from bot.config.settings import Config
        from bot.config.instruments import get_instrument
        active_sym = Config.ACTIVE_SYMBOL
        instr = get_instrument(active_sym)
        strike_diff = instr.strike_step
        spot_token = instr.analysis_token

        nifty_ltp = self.data_fetcher.get_ltp(spot_token, exchange=instr.exchange)
        if not nifty_ltp:
            logger.error(f"Straddle Scalp: Cannot fetch {active_sym} LTP. Aborting.")
            return False

        atm_strike = round(nifty_ltp / strike_diff) * strike_diff

        logger.info(
            f"Straddle Scalp: {active_sym}={nifty_ltp:.1f} | ATM={atm_strike}\n"
            f"    Call Side: Long {atm_strike} CE\n"
            f"    Put Side:  Long {atm_strike} PE"
        )

        lc_token, lc_symbol = self.token_loader.get_token(active_sym, expiry, atm_strike, "CE", instrument_type=instr.instrument_type, exchange=instr.option_exchange)
        lp_token, lp_symbol = self.token_loader.get_token(active_sym, expiry, atm_strike, "PE", instrument_type=instr.instrument_type, exchange=instr.option_exchange)

        if not all([lc_token, lp_token]):
            logger.error("Straddle Scalp: Strike token resolution failed. Aborting.")
            return False

        # Cooldown check
        for sym in [lc_symbol, lp_symbol]:
            if not self.gatekeeper.check_instrument_cooldown(sym):
                logger.warning(f"Straddle Scalp: {sym} is in cooldown. Aborting.")
                return False

        # Get LTPs to initialize smart limit order prices
        lc_ltp = self.data_fetcher.get_ltp(lc_token, exchange="NFO") or 0.0
        lp_ltp = self.data_fetcher.get_ltp(lp_token, exchange="NFO") or 0.0

        if any(ltp <= 0 for ltp in [lc_ltp, lp_ltp]):
            logger.error("Straddle Scalp: Cannot fetch LTPs for all legs. Aborting.")
            return False

        # Size Position (using ₹10,000 margin/premium per lot for option buying)
        margin_per_lot = 10000.0
        qty_lots = int(
            self.gatekeeper.get_compounded_lots(
                margin_per_lot=margin_per_lot,
                multiplier=self.risk_multiplier
            )
        )
        
        # Cap lots by tier max_lots
        starting_capital = self.gatekeeper.get_starting_capital()
        tier = Config.get_tier(starting_capital)
        if tier.max_lots > 0:
            qty_lots = min(qty_lots, tier.max_lots)
            
        qty_lots = max(1, qty_lots)
        qty_units = qty_lots * Config.NIFTY_LOT_SIZE

        # Margin/Premium check on live/simulation balance
        required_margin = (lc_ltp + lp_ltp) * qty_units
        if not self.dry_run and not self.gatekeeper.check_trade_margin(required_margin):
            logger.warning(f"Straddle Scalp: ❌ Insufficient funds. Required: ₹{required_margin:,.2f}")
            return False

        # Worst-case loss projection daily limit check (entire premium lost)
        est_worst_case = required_margin
        if not self.gatekeeper.check_max_daily_loss(0.0, worst_case_new_loss=est_worst_case):
            logger.critical(f"Straddle Scalp: 🛑 Skipped entry because worst-case loss of ₹{est_worst_case:.2f} would breach daily limit.")
            return False

        logger.info(f"Straddle Scalp: Placing Long Straddle Basket. Size: {qty_lots} lot(s) ({qty_units} units)")
        mode = "PAPER" if self.dry_run else "LIVE"

        # Place BUY orders for both legs in parallel
        lc_oid = self.order_manager.place_smart_limit(
            symbol=lc_symbol, token=lc_token, qty=qty_units, initial_price=lc_ltp, 
            transaction_type="BUY", strategy_name=self.STRATEGY_NAME, mode=mode
        )
        lp_oid = self.order_manager.place_smart_limit(
            symbol=lp_symbol, token=lp_token, qty=qty_units, initial_price=lp_ltp, 
            transaction_type="BUY", strategy_name=self.STRATEGY_NAME, mode=mode
        )

        if not lc_oid or not lp_oid:
            logger.error("Straddle Scalp: Straddle leg placement failed. Squaring off filled legs.")
            if lc_oid:
                fill_res = self._wait_fill(lc_oid, fallback=lc_ltp)
                entry_p = fill_res.get('price', lc_ltp)
                trade_repo.save_trade(lc_symbol, lc_token, "LC", qty_units, entry_p, 0.0, "BUY", mode, self.STRATEGY_NAME, status="OPEN")
                
                market_oid = self.order_manager.place_market(symbol=lc_symbol, token=lc_token, qty=qty_units, transaction_type="SELL", strategy_name=self.STRATEGY_NAME, mode=mode)
                market_fill = self._wait_fill(market_oid, fallback=entry_p)
                exit_p = market_fill.get('price', entry_p)
                pnl = (exit_p - entry_p) * qty_units
                trade_repo.close_trade(symbol=lc_symbol, exit_price=exit_p, pnl=pnl, exit_reason="ENTRY_FAILED")
            if lp_oid:
                fill_res = self._wait_fill(lp_oid, fallback=lp_ltp)
                entry_p = fill_res.get('price', lp_ltp)
                trade_repo.save_trade(lp_symbol, lp_token, "LP", qty_units, entry_p, 0.0, "BUY", mode, self.STRATEGY_NAME, status="OPEN")
                
                market_oid = self.order_manager.place_market(symbol=lp_symbol, token=lp_token, qty=qty_units, transaction_type="SELL", strategy_name=self.STRATEGY_NAME, mode=mode)
                market_fill = self._wait_fill(market_oid, fallback=entry_p)
                exit_p = market_fill.get('price', entry_p)
                pnl = (exit_p - entry_p) * qty_units
                trade_repo.close_trade(symbol=lp_symbol, exit_price=exit_p, pnl=pnl, exit_reason="ENTRY_FAILED")
            return False

        lc_fill = self._wait_fill(lc_oid, fallback=lc_ltp)
        lp_fill = self._wait_fill(lp_oid, fallback=lp_ltp)

        lc_entry = lc_fill.get('price', lc_ltp)
        lp_entry = lp_fill.get('price', lp_ltp)

        # Save trades
        lc_id = trade_repo.save_trade(lc_symbol, lc_token, "LC", qty_units, lc_entry, 0.0, "BUY", mode, self.STRATEGY_NAME)
        lp_id = trade_repo.save_trade(lp_symbol, lp_token, "LP", qty_units, lp_entry, 0.0, "BUY", mode, self.STRATEGY_NAME)

        # Establish state positions
        self.lc_position = {'id': lc_id, 'symbol': lc_symbol, 'token': lc_token, 'qty': qty_units, 'entry_price': lc_entry, 'sl_oid': None}
        self.lp_position = {'id': lp_id, 'symbol': lp_symbol, 'token': lp_token, 'qty': qty_units, 'entry_price': lp_entry, 'sl_oid': None}

        net_debit = lc_entry + lp_entry
        notifier.notify_straddle_entry(lc_symbol, lp_symbol, lc_entry, lp_entry, qty_units)
        logger.info(f"Straddle Scalp: Long Straddle basket filled. Net debit=₹{net_debit:.1f}")
        return True

    # ─────────────────────────────────────────────────────────────────────
    # Basket Monitoring
    # ─────────────────────────────────────────────────────────────────────

    def _monitor_straddle(self) -> str:
        """
        Monitor the Long Straddle basket value.
        Net Debit = LC_Entry + LP_Entry
        Current LTP = LC_LTP + LP_LTP
        
        Take Profit (25% expansion): Current LTP >= 1.25 * Net Debit
        Stop Loss (15% decay):       Current LTP <= 0.85 * Net Debit
        """
        lc_entry = self.lc_position['entry_price']
        lp_entry = self.lp_position['entry_price']

        net_debit = lc_entry + lp_entry
        target_val = net_debit * (1 + self.PROFIT_TARGET_PCT)
        sl_val     = net_debit * (1 - self.STOP_LOSS_PCT)

        # Fetch current LTPs
        lc_ltp = self.data_fetcher.get_ltp(self.lc_position['token'], exchange="NFO")
        lp_ltp = self.data_fetcher.get_ltp(self.lp_position['token'], exchange="NFO")

        if lc_ltp is None or lp_ltp is None:
            logger.warning("Straddle Scalp: ⚠️ Failed to fetch LTP for all legs. Skipping monitor iteration.")
            return "CONTINUE"

        current_val = lc_ltp + lp_ltp
        pnl = (current_val - net_debit) * self.lc_position['qty']
        pnl_pct = (pnl / (net_debit * self.lc_position['qty'])) * 100 if net_debit > 0 else 0.0

        if time.time() - self._last_log_ts >= 30:
            self._last_log_ts = time.time()
            logger.info(
                f"Straddle Scalp: 📊 MONITOR | "
                f"Current LTP={current_val:.1f} (Debit={net_debit:.1f}) | "
                f"PnL=₹{pnl:+.0f} ({pnl_pct:+.1f}%) | "
                f"Target={target_val:.1f} (+25%) SL={sl_val:.1f} (-15%)"
            )

        # 1. Take Profit
        if current_val >= target_val:
            logger.info(f"Straddle Scalp: 🎯 TAKE PROFIT HIT! Basket LTP={current_val:.1f} ≥ {target_val:.1f}")
            self._close_all("TARGET")
            return "PROFIT"

        # 2. Stop Loss
        if current_val <= sl_val:
            logger.warning(f"Straddle Scalp: 🛑 BASKET STOP LOSS HIT! Basket LTP={current_val:.1f} ≤ {sl_val:.1f}")
            self._close_all("STOPLOSS")
            return "LOSS"

        # 3. Expiry / End of Day Time Exit
        if datetime.datetime.now().time() >= datetime.time(15, 10):
            logger.info("Straddle Scalp: ⏰ 3:10 PM EOD limit reached. Performing strategic exit.")
            self._close_all("TIME_EXIT")
            return "TIME"

        return "CONTINUE"

    # ─────────────────────────────────────────────────────────────────────
    # Exit Execution
    # ─────────────────────────────────────────────────────────────────────

    def _close_all(self, reason: str):
        """
        Close both Long legs:
        1. Place parallel SELL limit/market orders to square off LC and LP.
        2. Update DB trades to CLOSED.
        """
        logger.info(f"Straddle Scalp: Closing Long Straddle Basket. Reason: {reason}")
        mode = "PAPER" if self.dry_run else "LIVE"

        total_pnl = 0.0
        for pos, name in [(self.lc_position, "LC"), (self.lp_position, "LP")]:
            if not pos: continue
            ltp = self.data_fetcher.get_ltp(pos['token'], exchange="NFO") or pos['entry_price']
            oid = self.order_manager.place_smart_limit(
                symbol=pos['symbol'], token=pos['token'], qty=pos['qty'], initial_price=ltp, 
                transaction_type="SELL", strategy_name=self.STRATEGY_NAME, mode=mode
            )
            fill = self._wait_fill(oid, fallback=ltp)
            exit_price = fill.get('price', ltp)
            
            # PnL for long leg: Exit - Entry
            pnl = (exit_price - pos['entry_price']) * pos['qty']
            total_pnl += pnl
            trade_repo.close_trade(trade_id=pos.get('id'), symbol=pos['symbol'], exit_price=exit_price, pnl=pnl, exit_reason=reason)
            logger.info(f"Straddle Scalp: {name} Long Closed @ ₹{exit_price:.1f} | P&L: ₹{pnl:+.0f}")

        notifier.notify_straddle_exit(reason, total_pnl)
        logger.info(f"Straddle Scalp: Long Straddle basket closed. Total P&L=₹{total_pnl:.0f}")

        # Clear state
        self.lc_position = None
        self.lp_position = None

    # ─────────────────────────────────────────────────────────────────────
    # Order Fill Handler
    # ─────────────────────────────────────────────────────────────────────

    def _wait_fill(self, order_id, fallback: float = 50.0) -> dict:
        if self.dry_run or order_id is None:
            return {'status': 'FILLED', 'price': fallback}
        
        from bot.core.order_feed import order_feed
        
        # Check cache/WebSocket first
        status = order_feed.get_order_status(order_id)
        if status and status.get('status') == 'FILLED':
            return {'status': 'FILLED', 'price': status.get('price', fallback)}
            
        # Wait on WebSocket
        res = order_feed.wait_for_fill(order_id, timeout=10)
        if res.get('status') == 'FILLED':
            return {'status': 'FILLED', 'price': res.get('price', fallback)}
            
        # REST fallback
        try:
            from bot.utils.rate_limiter import rate_limiter
            rate_limiter.wait()
            ob = self.api.orderBook()
            if ob and ob.get('data'):
                for o in ob['data']:
                    if str(o.get('orderid')) == str(order_id):
                        if o.get('status') == 'complete':
                            return {'status': 'FILLED', 'price': float(o.get('averageprice', fallback))}
                        else:
                            return {'status': o.get('status', '').upper(), 'price': float(o.get('averageprice', 0.0) or 0.0)}
        except Exception as e:
            logger.error(f"Error checking order book: {e}")
            
        logger.warning(f"Straddle Scalp: Fill timeout for {order_id}. Using fallback ₹{fallback:.1f}")
        return {'status': 'TIMEOUT', 'price': fallback}
