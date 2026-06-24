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
    Hedged Option Writing (Iron Condor) Strategy.
    Designed to harvest premium decay (theta) on range-bound / sideways days (ADX < 25.0).
    
    To trade within small accounts (e.g. ₹50,000 tier), it utilizes an Iron Condor:
      - Short Call (SC): ATM Strike + 100 points
      - Long Call (LC - Hedge): Short Call Strike + 100 points
      - Short Put (SP): ATM Strike - 100 points
      - Long Put (LP - Hedge): Short Put Strike - 100 points

    Sequential Margin Protocol:
      1. Buy protection wings (LC, LP) first to unlock margin benefit.
      2. Sell premium wings (SC, SP) second.
      3. If any leg fails to fill, execute emergency rollback.
      
    Disaster Stop Losses:
      - Hard SL placed on broker for short legs at 150% of entry premium.
      
    Exit Monitoring:
      - Monitor combined premium basket.
      - Take Profit: Combined LTP <= 50% of net credit.
      - Stop Loss: Combined LTP >= 200% of net credit (100% loss of credit).
    """

    STRATEGY_NAME      = "STRADDLE_SCALP"
    PROFIT_TARGET_PCT  = 0.50   # Close at 50% premium decay of net credit
    STOP_LOSS_PCT      = 1.00   # Close at 100% premium expansion (cost doubles)
    MAX_ADX_TO_ENTER   = 25.0   # Sideways filter
    MAX_ENTRY_TIME     = datetime.time(11, 0)
    MAX_ENTRY_EXPIRY   = datetime.time(12, 30)
    TREND_KILL_ADX     = 30.0   # Exit if trend starts to run

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

        # Position tracking variables
        self.lc_position = None  # Long Call Protection: symbol, token, qty, entry_price, id
        self.sc_position = None  # Short Call Premium
        self.sp_position = None  # Short Put Premium
        self.lp_position = None  # Long Put Protection

    @property
    def active_position(self):
        """Shim used by main.py shutdown handler."""
        return self.lc_position or self.sc_position or self.sp_position or self.lp_position

    # ─────────────────────────────────────────────────────────────────────
    # Lifecycle & Recovery
    # ─────────────────────────────────────────────────────────────────────

    def stop(self):
        self.running = False
        if self.active_position:
            logger.warning("Straddle Scalp: 🛑 Stop requested — closing Iron Condor legs.")
            self._close_all("USER_STOPPED")

    def sync_state(self):
        """Recover open Iron Condor legs from DB after a restart."""
        mode = "PAPER" if self.dry_run else "LIVE"
        try:
            query = {"status": {"$in": ["OPEN", "PLACED"]}, "mode": mode, "strategy": self.STRATEGY_NAME}
            open_trades = list(trade_repo.collection.find(query))
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
                elif leg_type == 'SC' and self.sc_position is None:
                    self.sc_position = pos
                    logger.info(f"♻️ [Straddle Scalp] Recovered Short Call: {t['symbol']} @ ₹{t['entry_price']}")
                elif leg_type == 'SP' and self.sp_position is None:
                    self.sp_position = pos
                    logger.info(f"♻️ [Straddle Scalp] Recovered Short Put: {t['symbol']} @ ₹{t['entry_price']}")
                elif leg_type == 'LP' and self.lp_position is None:
                    self.lp_position = pos
                    logger.info(f"♻️ [Straddle Scalp] Recovered Long Put: {t['symbol']} @ ₹{t['entry_price']}")
        except Exception as e:
            logger.error(f"Straddle Scalp sync_state error: {e}")

    # ─────────────────────────────────────────────────────────────────────
    # Main Loop
    # ─────────────────────────────────────────────────────────────────────

    def execute(self, expiry, action="SELL"):
        logger.info(f"🎯 --- STRADDLE SCALP STRATEGY ACTIVE (Iron Condor, Expiry: {expiry}) ---")
        self.sync_state()

        today_str  = datetime.datetime.now().strftime("%d%b%Y").upper()
        is_expiry  = (expiry == today_str)
        entry_cutoff = self.MAX_ENTRY_EXPIRY if is_expiry else self.MAX_ENTRY_TIME
        logger.info(
            f"Straddle Scalp: Entry window → {entry_cutoff} "
            f"({'Expiry day' if is_expiry else 'Normal day'})"
        )

        while self.running:
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
            has_positions = (self.lc_position and self.sc_position and self.sp_position and self.lp_position)
            if has_positions:
                result = self._monitor_condor()
                if result in ("PROFIT", "LOSS", "TIME", "TREND_KILL"):
                    break
                time.sleep(10)
                continue

            # 3. Handle incomplete basket (crash recovery / execution gap)
            if self.lc_position or self.sc_position or self.sp_position or self.lp_position:
                logger.warning("Straddle Scalp: ⚠️ Incomplete Iron Condor basket detected. Closing all legs for safety.")
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
                "Entering Iron Condor."
            )
            self._enter_condor(expiry)
            time.sleep(30)

    # ─────────────────────────────────────────────────────────────────────
    # Entry Execution
    # ─────────────────────────────────────────────────────────────────────

    def _enter_condor(self, expiry):
        """
        Execute 4-leg Iron Condor with sequential margin protection:
        1. Place BUY orders for protection wings (Long Call & Long Put)
        2. Wait for BUY orders to fill.
        3. Place SELL orders for premium wings (Short Call & Short Put)
        4. Wait for SELL orders to fill.
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
            return

        atm_strike = round(nifty_ltp / strike_diff) * strike_diff
        
        # Calculate strikes (2 * strike_step offset, 2 * strike_step wing gap)
        offset = 2 * strike_diff
        short_call = atm_strike + offset
        long_call  = short_call + offset
        short_put  = atm_strike - offset
        long_put   = short_put - offset

        logger.info(
            f"Straddle Scalp: {active_sym}={nifty_ltp:.1f} | ATM={atm_strike}\n"
            f"    Call Side: Short {short_call} CE | Long {long_call} CE (Hedge)\n"
            f"    Put Side:  Short {short_put} PE | Long {long_put} PE (Hedge)"
        )

        lc_token, lc_symbol = self.token_loader.get_token(active_sym, expiry, long_call, "CE", instrument_type=instr.instrument_type, exchange=instr.option_exchange)
        sc_token, sc_symbol = self.token_loader.get_token(active_sym, expiry, short_call, "CE", instrument_type=instr.instrument_type, exchange=instr.option_exchange)
        sp_token, sp_symbol = self.token_loader.get_token(active_sym, expiry, short_put, "PE", instrument_type=instr.instrument_type, exchange=instr.option_exchange)
        lp_token, lp_symbol = self.token_loader.get_token(active_sym, expiry, long_put, "PE", instrument_type=instr.instrument_type, exchange=instr.option_exchange)

        if not all([lc_token, sc_token, sp_token, lp_token]):
            logger.error("Straddle Scalp: Strike token resolution failed. Aborting.")
            return

        # Cooldown check
        for sym in [lc_symbol, sc_symbol, sp_symbol, lp_symbol]:
            if not self.gatekeeper.check_instrument_cooldown(sym):
                logger.warning(f"Straddle Scalp: {sym} is in cooldown. Aborting.")
                return

        # Get LTPs to initialize smart limit order prices
        lc_ltp = self.data_fetcher.get_ltp(lc_token, exchange="NFO") or 0.0
        sc_ltp = self.data_fetcher.get_ltp(sc_token, exchange="NFO") or 0.0
        sp_ltp = self.data_fetcher.get_ltp(sp_token, exchange="NFO") or 0.0
        lp_ltp = self.data_fetcher.get_ltp(lp_token, exchange="NFO") or 0.0

        if any(ltp <= 0 for ltp in [lc_ltp, sc_ltp, sp_ltp, lp_ltp]):
            logger.error("Straddle Scalp: Cannot fetch LTPs for all legs. Aborting.")
            return

        # Size Position (using ₹35,000 margin per lot for Iron Condor)
        margin_per_lot = 35000.0
        qty_lots = int(
            self.gatekeeper.get_compounded_lots(
                margin_per_lot=margin_per_lot,
                multiplier=self.risk_multiplier
            )
        )
        qty_lots = max(1, qty_lots)
        qty_units = qty_lots * Config.NIFTY_LOT_SIZE

        # Margin check on live/simulation balance
        required_margin = qty_lots * margin_per_lot
        if not self.dry_run and not self.gatekeeper.check_trade_margin(required_margin):
            logger.warning(f"Straddle Scalp: ❌ Insufficient funds. Required: ₹{required_margin:,.2f}")
            return

        # Worst-case loss projection daily limit check
        est_net_credit = (sc_ltp + sp_ltp) - (lc_ltp + lp_ltp)
        est_worst_case = est_net_credit * qty_units
        if not self.gatekeeper.check_max_daily_loss(0.0, worst_case_new_loss=est_worst_case):
            logger.critical(f"Straddle Scalp: 🛑 Skipped entry because worst-case loss of ₹{est_worst_case:.2f} would breach daily limit.")
            return

        logger.info(f"Straddle Scalp: Placing Iron Condor Basket. Size: {qty_lots} lot(s) ({qty_units} units)")
        mode = "PAPER" if self.dry_run else "LIVE"

        # -----------------------------------------------------------------
        # STEP 1: Place Long protection legs first (to secure margin)
        # -----------------------------------------------------------------
        logger.info("🛡️ Step 1/2: Placing Long Hedges (LC and LP) to unlock margin...")
        lc_oid = self.order_manager.place_smart_limit(lc_symbol, lc_token, qty_units, lc_ltp, "BUY", self.STRATEGY_NAME, mode)
        lp_oid = self.order_manager.place_smart_limit(lp_symbol, lp_token, qty_units, lp_ltp, "BUY", self.STRATEGY_NAME, mode)

        if not lc_oid or not lp_oid:
            logger.error("Straddle Scalp: Long leg placement failed. Cancelling.")
            if lc_oid: self.order_manager.cancel_order(lc_oid)
            if lp_oid: self.order_manager.cancel_order(lp_oid)
            return

        lc_fill = self._wait_fill(lc_oid, fallback=lc_ltp)
        lp_fill = self._wait_fill(lp_oid, fallback=lp_ltp)

        if lc_fill['status'] != 'FILLED' or lp_fill['status'] != 'FILLED':
            logger.warning("Straddle Scalp: Long hedge legs failed to fill. Cleaning up.")
            self.order_manager.cancel_order(lc_oid)
            self.order_manager.cancel_order(lp_oid)
            if lc_fill['status'] == 'FILLED':
                self.order_manager.place_smart_limit(lc_symbol, lc_token, qty_units, lc_fill['price']*0.9, "SELL", self.STRATEGY_NAME, mode)
            if lp_fill['status'] == 'FILLED':
                self.order_manager.place_smart_limit(lp_symbol, lp_token, qty_units, lp_fill['price']*0.9, "SELL", self.STRATEGY_NAME, mode)
            return

        logger.info("🛡️ Step 1/2 Complete: Both Long hedges filled successfully.")

        # -----------------------------------------------------------------
        # STEP 2: Place Short premium legs second
        # -----------------------------------------------------------------
        logger.info("💰 Step 2/2: Placing Short Premium legs (SC and SP)...")
        sc_oid = self.order_manager.place_smart_limit(sc_symbol, sc_token, qty_units, sc_ltp, "SELL", self.STRATEGY_NAME, mode)
        sp_oid = self.order_manager.place_smart_limit(sp_symbol, sp_token, qty_units, sp_ltp, "SELL", self.STRATEGY_NAME, mode)

        if not sc_oid or not sp_oid:
            logger.critical("Straddle Scalp: Short leg placement failed. Triggering immediate emergency cleanup.")
            if sc_oid: self.order_manager.cancel_order(sc_oid)
            if sp_oid: self.order_manager.cancel_order(sp_oid)
            # Exit Long legs
            self.order_manager.place_smart_limit(lc_symbol, lc_token, qty_units, lc_fill['price']*0.9, "SELL", self.STRATEGY_NAME, mode)
            self.order_manager.place_smart_limit(lp_symbol, lp_token, qty_units, lp_fill['price']*0.9, "SELL", self.STRATEGY_NAME, mode)
            return

        sc_fill = self._wait_fill(sc_oid, fallback=sc_ltp)
        sp_fill = self._wait_fill(sp_oid, fallback=sp_ltp)

        if sc_fill['status'] != 'FILLED' or sp_fill['status'] != 'FILLED':
            logger.critical("Straddle Scalp: Short legs failed to fill. Initiating emergency rollback.")
            self.order_manager.cancel_order(sc_oid)
            self.order_manager.cancel_order(sp_oid)
            # Buy back any filled shorts
            if sc_fill['status'] == 'FILLED':
                self.order_manager.place_smart_limit(sc_symbol, sc_token, qty_units, sc_fill['price']*1.1, "BUY", self.STRATEGY_NAME, mode)
            if sp_fill['status'] == 'FILLED':
                self.order_manager.place_smart_limit(sp_symbol, sp_token, qty_units, sp_fill['price']*1.1, "BUY", self.STRATEGY_NAME, mode)
            # Sell Long hedges
            self.order_manager.place_smart_limit(lc_symbol, lc_token, qty_units, lc_fill['price']*0.9, "SELL", self.STRATEGY_NAME, mode)
            self.order_manager.place_smart_limit(lp_symbol, lp_token, qty_units, lp_fill['price']*0.9, "SELL", self.STRATEGY_NAME, mode)
            return

        # -----------------------------------------------------------------
        # STEP 3: Setup database and tracking state
        # -----------------------------------------------------------------
        lc_entry = lc_fill.get('price', lc_ltp)
        sc_entry = sc_fill.get('price', sc_ltp)
        sp_entry = sp_fill.get('price', sp_ltp)
        lp_entry = lp_fill.get('price', lp_ltp)

        # Save trades
        lc_id = trade_repo.save_trade(lc_symbol, lc_token, "LC", qty_units, lc_entry, 0.0, "BUY", mode, self.STRATEGY_NAME)
        sc_id = trade_repo.save_trade(sc_symbol, sc_token, "SC", qty_units, sc_entry, 0.0, "SELL", mode, self.STRATEGY_NAME)
        sp_id = trade_repo.save_trade(sp_symbol, sp_token, "SP", qty_units, sp_entry, 0.0, "SELL", mode, self.STRATEGY_NAME)
        lp_id = trade_repo.save_trade(lp_symbol, lp_token, "LP", qty_units, lp_entry, 0.0, "BUY", mode, self.STRATEGY_NAME)

        # Place disaster hard Stop Losses on the Short legs (SC & SP) at 150% premium
        sc_sl_price = round(sc_entry * 1.5, 1)
        sp_sl_price = round(sp_entry * 1.5, 1)
        
        logger.info(f"🛡️ Placing disaster stop-losses on exchange: SC SL @ {sc_sl_price} | SP SL @ {sp_sl_price}")
        sc_sl_oid = self.order_manager.place_sl_order(sc_symbol, sc_token, qty_units, sc_sl_price, "SC", transaction_type="BUY")
        sp_sl_oid = self.order_manager.place_sl_order(sp_symbol, sp_token, qty_units, sp_sl_price, "SP", transaction_type="BUY")

        if sc_id and sc_sl_oid: trade_repo.update_sl_order_id(sc_id, sc_sl_oid)
        if sp_id and sp_sl_oid: trade_repo.update_sl_order_id(sp_id, sp_sl_oid)

        # Establish state positions
        self.lc_position = {'id': lc_id, 'symbol': lc_symbol, 'token': lc_token, 'qty': qty_units, 'entry_price': lc_entry, 'sl_oid': None}
        self.sc_position = {'id': sc_id, 'symbol': sc_symbol, 'token': sc_token, 'qty': qty_units, 'entry_price': sc_entry, 'sl_oid': sc_sl_oid}
        self.sp_position = {'id': sp_id, 'symbol': sp_symbol, 'token': sp_token, 'qty': qty_units, 'entry_price': sp_entry, 'sl_oid': sp_sl_oid}
        self.lp_position = {'id': lp_id, 'symbol': lp_symbol, 'token': lp_token, 'qty': qty_units, 'entry_price': lp_entry, 'sl_oid': None}

        net_credit = (sc_entry + sp_entry) - (lc_entry + lp_entry)
        notifier.notify_condor_entry(
            sc_symbol, sp_symbol, lc_symbol, lp_symbol,
            sc_entry, sp_entry, lc_entry, lp_entry, qty_units
        )
        logger.info(f"Straddle Scalp: Iron Condor basket filled. Net credit=₹{net_credit:.1f}")

    # ─────────────────────────────────────────────────────────────────────
    # Basket Monitoring
    # ─────────────────────────────────────────────────────────────────────

    def _monitor_condor(self) -> str:
        """
        Monitor the Iron Condor basket value.
        Net Credit = (SC_Entry + SP_Entry) - (LC_Entry + LP_Entry)
        Current LTP = (SC_LTP + SP_LTP) - (LC_LTP + LP_LTP)
        
        Profit Target (50% Decay): Current LTP <= 0.50 * Net Credit
        Stop Loss (100% Rise):     Current LTP >= 2.00 * Net Credit
        """
        lc_entry = self.lc_position['entry_price']
        sc_entry = self.sc_position['entry_price']
        sp_entry = self.sp_position['entry_price']
        lp_entry = self.lp_position['entry_price']

        net_credit = (sc_entry + sp_entry) - (lc_entry + lp_entry)
        target_val = net_credit * (1 - self.PROFIT_TARGET_PCT)
        sl_val     = net_credit * (1 + self.STOP_LOSS_PCT)

        # Fetch current LTPs
        lc_ltp = self.data_fetcher.get_ltp(self.lc_position['token'], exchange="NFO") or lc_entry
        sc_ltp = self.data_fetcher.get_ltp(self.sc_position['token'], exchange="NFO") or sc_entry
        sp_ltp = self.data_fetcher.get_ltp(self.sp_position['token'], exchange="NFO") or sp_entry
        lp_ltp = self.data_fetcher.get_ltp(self.lp_position['token'], exchange="NFO") or lp_entry

        current_val = (sc_ltp + sp_ltp) - (lc_ltp + lp_ltp)
        pnl = (net_credit - current_val) * self.sc_position['qty']
        pnl_pct = (pnl / (net_credit * self.sc_position['qty'])) * 100 if net_credit > 0 else 0.0

        if time.time() - self._last_log_ts >= 30:
            self._last_log_ts = time.time()
            logger.info(
                f"Straddle Scalp: 📊 MONITOR | "
                f"Current Val={current_val:.1f} (Credit={net_credit:.1f}) | "
                f"PnL=₹{pnl:+.0f} ({pnl_pct:+.1f}%) | "
                f"Target={target_val:.1f} (50%) SL={sl_val:.1f} (100%)"
            )

        # 1. Take Profit
        if current_val <= target_val:
            logger.info(f"Straddle Scalp: 🎯 TAKE PROFIT HIT! Basket LTP={current_val:.1f} ≤ {target_val:.1f}")
            self._close_all("TARGET")
            return "PROFIT"

        # 2. Stop Loss
        if current_val >= sl_val:
            logger.warning(f"Straddle Scalp: 🛑 BASKET STOP LOSS HIT! Basket LTP={current_val:.1f} ≥ {sl_val:.1f}")
            self._close_all("STOPLOSS")
            return "LOSS"

        # 3. Expiry / End of Day Time Exit
        # We perform exit at 3:10 PM to prevent physical settlement of ITM options on expiry days.
        if datetime.datetime.now().time() >= datetime.time(15, 10):
            logger.info("Straddle Scalp: ⏰ 3:10 PM EOD limit reached. Performing strategic exit.")
            self._close_all("TIME_EXIT")
            return "TIME"

        # 4. Trend-Kill switch
        if time.time() - self._last_trend_check >= 60:
            self._last_trend_check = time.time()
            market_data = market_service.get_market_data()
            analysis = market_data.get('analysis', {})
            curr_adx = analysis.get('adx', 0.0)
            if curr_adx >= self.TREND_KILL_ADX:
                logger.warning(
                    f"Straddle Scalp: 🛡️ TREND-KILL TRIGGERED! ADX={curr_adx:.1f} ≥ {self.TREND_KILL_ADX}. "
                    "Market is breakout trending. Closing option selling basket."
                )
                self._close_all("TREND_KILL")
                return "TREND_KILL"

        return "CONTINUE"

    # ─────────────────────────────────────────────────────────────────────
    # Exit Execution
    # ─────────────────────────────────────────────────────────────────────

    def _close_all(self, reason: str):
        """
        Close all 4 legs:
        1. Cancel disaster stop losses on Short legs.
        2. Buy back the Short legs (SC, SP) first (to release short exposure & risk).
        3. Sell the Long protection legs (LC, LP) second.
        """
        logger.info(f"Straddle Scalp: Closing Iron Condor Basket. Reason: {reason}")
        mode = "PAPER" if self.dry_run else "LIVE"

        # Cancel stop loss orders
        for pos in [self.sc_position, self.sp_position]:
            if pos and pos.get('sl_oid'):
                logger.debug(f"Straddle Scalp: Cancelling SL {pos['sl_oid']} for {pos['symbol']}")
                self.order_manager.cancel_order(pos['sl_oid'])

        # 1. Close Short legs first (releasing margin liabilities)
        logger.info("💰 Step 1/2: Closing Short Premium legs...")
        short_pnl = 0.0
        for pos, name in [(self.sc_position, "SC"), (self.sp_position, "SP")]:
            if not pos: continue
            ltp = self.data_fetcher.get_ltp(pos['token'], exchange="NFO") or pos['entry_price']
            oid = self.order_manager.place_smart_limit(pos['symbol'], pos['token'], pos['qty'], ltp, "BUY", self.STRATEGY_NAME, mode)
            fill = self._wait_fill(oid, fallback=ltp)
            exit_price = fill.get('price', ltp)
            
            # PnL for short leg: Entry - Exit
            pnl = (pos['entry_price'] - exit_price) * pos['qty']
            short_pnl += pnl
            trade_repo.close_trade(symbol=pos['symbol'], exit_price=exit_price, pnl=pnl)
            logger.info(f"Straddle Scalp: {name} Short Closed @ ₹{exit_price:.1f} | P&L: ₹{pnl:+.0f}")

        # 2. Close Long legs second
        logger.info("🛡️ Step 2/2: Closing Long Hedge legs...")
        long_pnl = 0.0
        for pos, name in [(self.lc_position, "LC"), (self.lp_position, "LP")]:
            if not pos: continue
            ltp = self.data_fetcher.get_ltp(pos['token'], exchange="NFO") or pos['entry_price']
            oid = self.order_manager.place_smart_limit(pos['symbol'], pos['token'], pos['qty'], ltp, "SELL", self.STRATEGY_NAME, mode)
            fill = self._wait_fill(oid, fallback=ltp)
            exit_price = fill.get('price', ltp)
            
            # PnL for long leg: Exit - Entry
            pnl = (exit_price - pos['entry_price']) * pos['qty']
            long_pnl += pnl
            trade_repo.close_trade(symbol=pos['symbol'], exit_price=exit_price, pnl=pnl)
            logger.info(f"Straddle Scalp: {name} Long Closed @ ₹{exit_price:.1f} | P&L: ₹{pnl:+.0f}")

        total_pnl = short_pnl + long_pnl
        notifier.notify_condor_exit(reason, short_pnl, long_pnl, total_pnl)
        logger.info(f"Straddle Scalp: Iron Condor basket closed. Total P&L=₹{total_pnl:.0f}")

        # Clear state
        self.lc_position = None
        self.sc_position = None
        self.sp_position = None
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
