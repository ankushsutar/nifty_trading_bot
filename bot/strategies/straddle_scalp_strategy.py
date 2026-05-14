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
    ATM Straddle Scalp — profits from sharp moves in either direction.
    Buy ATM CE + PE together when the market is ranging (ADX < 20).
    Exit the pair when combined premium gains 20% or loses 15%.

    Best conditions:
      - ADX < 20 (SIDEWAYS / CHOP regime — direction unknown)
      - Entry before 10:30 AM (theta decay accelerates after that)
      - VIX elevated or approaching a known event (RBI, earnings, expiry)
    """

    STRATEGY_NAME      = "STRADDLE_SCALP"
    PROFIT_TARGET_PCT  = 0.20   # Exit when combined premium rises 20%
    STOP_LOSS_PCT      = 0.15   # Exit when combined premium falls 15%
    MAX_ADX_TO_ENTER   = 25.0   # Relaxed from 20.0 to capture more sideways days
    MAX_ENTRY_TIME     = datetime.time(11, 0)    # Normal days: no new entries after 11:00 AM
    MAX_ENTRY_EXPIRY   = datetime.time(12, 30)   # Expiry days: gamma stays high till noon
    TREND_KILL_ADX     = 30.0                    # Exit if market starts trending

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

        # Two separate leg positions — both None until entered
        self.ce_position = None  # dict: symbol, token, qty, entry_price, id
        self.pe_position = None

    @property
    def active_position(self):
        """Shim used by main.py shutdown handler."""
        return self.ce_position or self.pe_position

    # ─────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────────

    def stop(self):
        self.running = False
        if self.ce_position or self.pe_position:
            logger.warning("Straddle Scalp: 🛑 Stop requested — closing both legs.")
            self._close_both("USER_STOPPED")

    def sync_state(self):
        """Recover open straddle legs from DB after a restart."""
        mode = "PAPER" if self.dry_run else "LIVE"
        try:
            open_trades = trade_repo.get_open_trades(mode=mode, strategy=self.STRATEGY_NAME)
            for t in open_trades:
                pos = {
                    'id':          t['id'],
                    'symbol':      t['symbol'],
                    'token':       t['token'],
                    'qty':         t['qty'],
                    'entry_price': t['entry_price'],
                    'sl_oid':      t.get('sl_order_id')
                }
                if t.get('leg') == 'CE' and self.ce_position is None:
                    self.ce_position = pos
                    logger.info(f"♻️ [Straddle] CE RECOVERY: {t['symbol']} @ ₹{t['entry_price']}")
                elif t.get('leg') == 'PE' and self.pe_position is None:
                    self.pe_position = pos
                    logger.info(f"♻️ [Straddle] PE RECOVERY: {t['symbol']} @ ₹{t['entry_price']}")
        except Exception as e:
            logger.error(f"Straddle Scalp sync_state error: {e}")

    # ─────────────────────────────────────────────────────────────────────
    # Main loop
    # ─────────────────────────────────────────────────────────────────────

    def execute(self, expiry, action="BUY"):
        logger.info(f"🎯 --- STRADDLE SCALP STRATEGY ACTIVATED ({expiry}) ---")
        self.sync_state()

        today_str  = datetime.datetime.now().strftime("%d%b%Y").upper()
        is_expiry  = (expiry == today_str)
        entry_cutoff = self.MAX_ENTRY_EXPIRY if is_expiry else self.MAX_ENTRY_TIME
        logger.info(
            f"Straddle Scalp: Entry window → {entry_cutoff} "
            f"({'Expiry day' if is_expiry else 'Normal day'})"
        )

        while self.running:
            # Hard safety guards
            if not self.gatekeeper.is_market_open():
                logger.warning("Straddle Scalp: 🛑 Market Closed. Exiting.")
                break
            if not self.gatekeeper.check_max_daily_loss(0.0):
                logger.critical("Straddle Scalp: 🛑 Max Daily Loss hit. Halting.")
                break

            # Both legs open → monitor until exit condition
            if self.ce_position and self.pe_position:
                result = self._monitor_straddle()
                if result in ("PROFIT", "LOSS", "TIME"):
                    break
                time.sleep(10)
                continue

            # Orphan: one leg open without the other (crash mid-entry)
            if self.ce_position or self.pe_position:
                logger.warning("Straddle Scalp: ⚠️ Orphan leg detected — closing for safety.")
                self._close_both("ORPHAN")
                break

            # Entry time window gate
            now = datetime.datetime.now().time()
            if now >= entry_cutoff:
                logger.info(f"Straddle Scalp: ⏰ Past entry window ({entry_cutoff}). Exiting.")
                break

            # ADX + regime gate (Centralized Intelligence Integration)
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
                    "— trending market, not a straddle day. Waiting."
                )
                time.sleep(60)
                continue

            if regime not in ("SIDEWAYS", "CHOP", "RANGING"):
                logger.info(f"Straddle Scalp: ⏸️ Regime={regime} — need SIDEWAYS/CHOP. Waiting.")
                time.sleep(60)
                continue

            logger.info(
                f"Straddle Scalp: ✅ Entry conditions met — ADX={adx:.1f} | Regime={regime}. "
                "Entering ATM straddle."
            )
            self._enter_straddle(expiry)
            time.sleep(30)

    # ─────────────────────────────────────────────────────────────────────
    # Entry
    # ─────────────────────────────────────────────────────────────────────

    def _enter_straddle(self, expiry):
        """Buy ATM CE + ATM PE simultaneously."""
        nifty_ltp = self.data_fetcher.get_ltp("99926000", exchange="NSE")
        if not nifty_ltp:
            logger.error("Straddle Scalp: Cannot fetch NIFTY LTP. Aborting entry.")
            return

        atm_strike = round(nifty_ltp / 50) * 50
        logger.info(f"Straddle Scalp: NIFTY={nifty_ltp:.0f} | ATM={atm_strike}")

        ce_token, ce_symbol = self.token_loader.get_token("NIFTY", expiry, atm_strike, "CE")
        pe_token, pe_symbol = self.token_loader.get_token("NIFTY", expiry, atm_strike, "PE")

        if not ce_token or not pe_token:
            logger.error(f"Straddle Scalp: Token lookup failed for {atm_strike} CE/PE.")
            return

        # --- SAFETY GATE: Instrument Cooldown (Anti-Revenge Trading) ---
        if not self.gatekeeper.check_instrument_cooldown(ce_symbol) or \
           not self.gatekeeper.check_instrument_cooldown(pe_symbol):
            return


        ce_ltp = self.data_fetcher.get_ltp(ce_token, exchange="NFO") or 0.0
        pe_ltp = self.data_fetcher.get_ltp(pe_token, exchange="NFO") or 0.0

        if ce_ltp <= 0 or pe_ltp <= 0:
            logger.error(f"Straddle Scalp: Bad LTPs — CE=₹{ce_ltp}, PE=₹{pe_ltp}. Aborting.")
            return

        # Size: combined cost per lot = (CE + PE) premium × lot size.
        # Use 0.5× multiplier because we're deploying capital into TWO legs.
        _tier = Config.get_tier(self.gatekeeper.get_current_capital())
        combined_cost_per_lot = (ce_ltp + pe_ltp) * Config.NIFTY_LOT_SIZE
        qty_lots = int(
            self.gatekeeper.get_compounded_lots(
                margin_per_lot=combined_cost_per_lot,
                multiplier=self.risk_multiplier * 0.5
            )
        )
        qty_lots = max(1, qty_lots)
        qty_units = qty_lots * Config.NIFTY_LOT_SIZE

        if not self.gatekeeper.check_trade_viability(ce_ltp, qty_units) or \
           not self.gatekeeper.check_trade_viability(pe_ltp, qty_units):
            logger.warning("Straddle Scalp: Viability check failed — brokerage too high vs premium.")
            return

        # Margin Check before placing orders
        total_estimated_cost = (ce_ltp + pe_ltp) * qty_units
        if not self.dry_run and not self.gatekeeper.check_trade_margin(total_estimated_cost):
            logger.warning(f"Straddle Scalp: ❌ Insufficient Funds for Straddle. Required: ₹{total_estimated_cost:,.2f}")
            return

        logger.info(
            f"Straddle Scalp: 🎯 {qty_lots} lot(s) | "
            f"CE={ce_symbol}@₹{ce_ltp} | PE={pe_symbol}@₹{pe_ltp} | "
            f"Combined=₹{ce_ltp+pe_ltp:.1f}/unit | Total cost≈₹{combined_cost_per_lot*qty_lots:,.0f}"
        )

        mode = "PAPER" if self.dry_run else "LIVE"

        # Place both legs — CE first, then PE
        ce_oid = self.order_manager.place_smart_limit(
            ce_symbol, ce_token, qty_units, ce_ltp, "BUY",
            strategy_name=self.STRATEGY_NAME, mode=mode
        )
        pe_oid = self.order_manager.place_smart_limit(
            pe_symbol, pe_token, qty_units, pe_ltp, "BUY",
            strategy_name=self.STRATEGY_NAME, mode=mode
        )

        if not ce_oid or not pe_oid:
            logger.error("Straddle Scalp: Order placement failed for one or both legs. Rolling back.")
            if ce_oid:
                self.order_manager.cancel_order(ce_oid, variety="NORMAL")
            if pe_oid:
                self.order_manager.cancel_order(pe_oid, variety="NORMAL")
            return

        ce_fill = self._wait_fill(ce_oid, fallback=ce_ltp)
        pe_fill = self._wait_fill(pe_oid, fallback=pe_ltp)

        # Handle Timeout/Failure on fill for either leg to prevent orphan positions
        if ce_fill['status'] != 'FILLED' or pe_fill['status'] != 'FILLED':
            logger.warning(
                f"Straddle Scalp: ⚠️ One or both legs failed to fill. "
                f"CE Status: {ce_fill['status']} | PE Status: {pe_fill['status']}. Rolling back."
            )
            # Cancel both orders
            self.order_manager.cancel_order(ce_oid, variety="NORMAL")
            self.order_manager.cancel_order(pe_oid, variety="NORMAL")

            # Emergency Sell if one leg filled but the other didn't
            if ce_fill['status'] == 'FILLED' and pe_fill['status'] != 'FILLED':
                logger.critical(f"Straddle Scalp: 🚨 CE Leg filled but PE Leg failed. Emergency exiting CE Leg!")
                self.order_manager.place_smart_limit(ce_symbol, ce_token, qty_units, ce_fill['price'] * 0.95, "SELL", mode=mode)
            elif pe_fill['status'] == 'FILLED' and ce_fill['status'] != 'FILLED':
                logger.critical(f"Straddle Scalp: 🚨 PE Leg filled but CE Leg failed. Emergency exiting PE Leg!")
                self.order_manager.place_smart_limit(pe_symbol, pe_token, qty_units, pe_fill['price'] * 0.95, "SELL", mode=mode)
            return

        ce_entry = ce_fill.get('price', ce_ltp)
        pe_entry = pe_fill.get('price', pe_ltp)

        ce_id = trade_repo.save_trade(
            ce_symbol, ce_token, "CE", qty_units, ce_entry,
            sl_price=0.0, side="BUY", mode=mode, strategy=self.STRATEGY_NAME
        )
        pe_id = trade_repo.save_trade(
            pe_symbol, pe_token, "PE", qty_units, pe_entry,
            sl_price=0.0, side="BUY", mode=mode, strategy=self.STRATEGY_NAME
        )

        # --- INSTITUTIONAL SAFETY UPGRADE: Place Disaster Hard SLs (fallback) ---
        DISASTER_SL_PCT = 0.50
        ce_sl_price = round(ce_entry * (1 - DISASTER_SL_PCT), 1)
        pe_sl_price = round(pe_entry * (1 - DISASTER_SL_PCT), 1)
        
        logger.info(f"🛡️ Placing Broker Disaster SLs: CE @ {ce_sl_price} | PE @ {pe_sl_price}")
        ce_sl_oid = self.order_manager.place_sl_order(ce_symbol, ce_token, qty_units, ce_sl_price, "CE")
        pe_sl_oid = self.order_manager.place_sl_order(pe_symbol, pe_token, qty_units, pe_sl_price, "PE")
        
        # Persist SL IDs for recovery
        if ce_id and ce_sl_oid: trade_repo.update_sl_order_id(ce_id, ce_sl_oid)
        if pe_id and pe_sl_oid: trade_repo.update_sl_order_id(pe_id, pe_sl_oid)

        self.ce_position = {'id': ce_id, 'symbol': ce_symbol, 'token': ce_token,
                            'qty': qty_units, 'entry_price': ce_entry, 'sl_oid': ce_sl_oid}
        self.pe_position = {'id': pe_id, 'symbol': pe_symbol, 'token': pe_token,
                            'qty': qty_units, 'entry_price': pe_entry, 'sl_oid': pe_sl_oid}

        combined_entry = ce_entry + pe_entry
        notifier.send(
            f"🎯 Straddle Scalp ENTERED\n"
            f"CE: {ce_symbol} @ ₹{ce_entry:.1f}\n"
            f"PE: {pe_symbol} @ ₹{pe_entry:.1f}\n"
            f"Combined: ₹{combined_entry:.1f} | Qty: {qty_units}"
        )
        logger.info(f"Straddle Scalp: ✅ Both legs filled. Combined entry=₹{combined_entry:.1f}")

    # ─────────────────────────────────────────────────────────────────────
    # Monitor
    # ─────────────────────────────────────────────────────────────────────

    def _monitor_straddle(self) -> str:
        """
        Check combined LTP vs entry every 10 seconds.
        Returns: "PROFIT" | "LOSS" | "TIME" | "CONTINUE"
        """
        ce_entry  = self.ce_position['entry_price']
        pe_entry  = self.pe_position['entry_price']
        combined_entry = ce_entry + pe_entry
        profit_target  = combined_entry * (1 + self.PROFIT_TARGET_PCT)
        stop_level     = combined_entry * (1 - self.STOP_LOSS_PCT)

        ce_ltp = self.data_fetcher.get_ltp(self.ce_position['token'], exchange="NFO") or ce_entry
        pe_ltp = self.data_fetcher.get_ltp(self.pe_position['token'], exchange="NFO") or pe_entry
        combined_ltp = ce_ltp + pe_ltp
        pnl      = (combined_ltp - combined_entry) * self.ce_position['qty']
        pnl_pct  = (combined_ltp / combined_entry - 1) * 100

        # Heartbeat log every 30s
        if time.time() - self._last_log_ts >= 30:
            self._last_log_ts = time.time()
            logger.info(
                f"Straddle Scalp: 📊 MONITOR | "
                f"CE={ce_ltp:.1f} + PE={pe_ltp:.1f} = {combined_ltp:.1f} "
                f"(Entry={combined_entry:.1f}) | P&L=₹{pnl:+.0f} ({pnl_pct:+.1f}%) | "
                f"Target={profit_target:.1f} SL={stop_level:.1f}"
            )

        # Profit target
        if combined_ltp >= profit_target:
            logger.info(
                f"Straddle Scalp: 🎯 TARGET HIT! Combined={combined_ltp:.1f} ≥ {profit_target:.1f}"
            )
            self._close_both("TARGET")
            return "PROFIT"

        # Stop loss
        if combined_ltp <= stop_level:
            logger.warning(
                f"Straddle Scalp: 🛑 STOP HIT! Combined={combined_ltp:.1f} ≤ {stop_level:.1f}"
            )
            self._close_both("STOPLOSS")
            return "LOSS"

        # Time exit (Forced 1:15 PM Liquidity Flush to free margin for Power Hour)
        if datetime.datetime.now().time() >= datetime.time(13, 15):
            logger.info("Straddle Scalp: ⏰ 1:15 PM Power-Hour Limit Reached. Performing Mandatory Liquidity Flush.")
            self._close_both("TIME_FLUSH")
            return "TIME"

        # Trend-Kill Switch (Throttled every 60 seconds, centralized)
        if time.time() - self._last_trend_check >= 60:
            self._last_trend_check = time.time()
            market_data = market_service.get_market_data()
            analysis = market_data.get('analysis', {})
            curr_adx = analysis.get('adx', 0)
            if curr_adx > 0 and curr_adx >= self.TREND_KILL_ADX:
                logger.warning(
                    f"Straddle Scalp: 🛡️ TREND-KILL TRIGGERED! ADX={curr_adx:.1f} ≥ {self.TREND_KILL_ADX}. "
                    "Market is no longer sideways. Exiting for safety."
                )
                self._close_both("TREND_KILL")
                return "TREND_KILL"

        return "CONTINUE"

    # ─────────────────────────────────────────────────────────────────────
    # Exit
    # ─────────────────────────────────────────────────────────────────────

    def _close_both(self, reason: str):
        """SELL both CE and PE legs and close DB records."""
        logger.info(f"Straddle Scalp: Closing both legs — reason={reason}")
        mode = "PAPER" if self.dry_run else "LIVE"
        total_pnl = 0.0

        for pos, leg_name in [(self.ce_position, "CE"), (self.pe_position, "PE")]:
            if pos is None:
                continue
            
            # 🛡️ Institutional Upgrade: First cancel existing Broker Disaster SL to release holding
            sl_oid = pos.get('sl_oid')
            if sl_oid:
                logger.debug(f"Straddle Scalp: Cancelling Disaster SL ({sl_oid}) for {pos['symbol']} before exit.")
                self.order_manager.cancel_order(sl_oid, variety="STOPLOSS")

            ltp = self.data_fetcher.get_ltp(pos['token'], exchange="NFO") or pos['entry_price']
            oid = self.order_manager.place_smart_limit(
                pos['symbol'], pos['token'], pos['qty'],
                ltp, "SELL", strategy_name=self.STRATEGY_NAME, mode=mode
            )
            fill       = self._wait_fill(oid, fallback=ltp)
            exit_price = fill.get('price', ltp)
            pnl        = (exit_price - pos['entry_price']) * pos['qty']
            total_pnl += pnl
            trade_repo.close_trade(symbol=pos['symbol'], exit_price=exit_price, pnl=pnl)
            logger.info(
                f"Straddle Scalp: {leg_name} closed @ ₹{exit_price:.1f} | P&L=₹{pnl:+.0f}"
            )

        notifier.send(
            f"Straddle Scalp CLOSED ({reason})\n"
            f"CE: {self.ce_position['symbol'] if self.ce_position else 'N/A'}\n"
            f"PE: {self.pe_position['symbol'] if self.pe_position else 'N/A'}\n"
            f"Total P&L: ₹{total_pnl:+.0f}"
        )
        self.ce_position = None
        self.pe_position = None

    # ─────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────

    def _wait_fill(self, order_id, fallback: float = 50.0) -> dict:
        if self.dry_run or order_id is None:
            return {'status': 'FILLED', 'price': fallback}
        for _ in range(20):
            time.sleep(1)
            try:
                from bot.utils.rate_limiter import rate_limiter
                rate_limiter.wait()
                ob = self.api.orderBook()
                if ob and ob.get('data'):
                    for o in ob['data']:
                        if str(o.get('orderid')) == str(order_id) and o.get('status') == 'complete':
                            return {'status': 'FILLED', 'price': float(o.get('averageprice', fallback))}
            except Exception:
                pass
        logger.warning(f"Straddle Scalp: Fill timeout for {order_id}. Using fallback ₹{fallback:.1f}")
        return {'status': 'TIMEOUT', 'price': fallback}
