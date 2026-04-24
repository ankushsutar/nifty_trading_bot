import time
import datetime
import pandas as pd
from bot.config.settings import Config
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.trade_repo import trade_repo
from bot.core.data_fetcher import DataFetcher
from bot.core.order_manager import OrderManager
from bot.core.oi_analyzer import OIAnalyzer
from bot.core.regime_classifier import RegimeClassifier
from bot.utils.logger import logger
from bot.config.instruments import get_instrument
from bot.strategies.base_strategy import BaseStrategy

class GammaBlastStrategy(BaseStrategy):
    """
    Gamma Blast (OTM Momentum) Strategy.
    Designed for "Hero-to-Zero" exponential returns during parabolic trends.
    Trigger: ADX > 35 + Strong OI Bias + VWAP/EMA Confluence.
    Target: 3:1 or 5:1 Risk/Reward using high-leverage OTM options.
    """
    def __init__(self, api, token_loader, dry_run=False):
        super().__init__(api, token_loader, "GAMMA_BLAST", dry_run)
        self.last_trend_fade_check = 0    # Throttle market_service calls in monitor

    def execute(self, expiry, action="BUY"):
        logger.info(f"🚀 --- GAMMA BLAST OTM STRATEGY ACTIVATED ({expiry}) ---")
        
        while self.running:
            # 1.5 Global Safety Guards
            if not self.gatekeeper.is_market_open():
                logger.warning("Gamma Blast: 🛑 Execution Aborted - Market is Closed.")
                break
            if self.gatekeeper.is_blackout_period():
                logger.info("Gamma Blast: ⏸️ Execution Suspended - Mid-day Blackout.")
                time.sleep(60)
                continue
            if not self.gatekeeper.check_max_daily_loss(0.0):
                logger.critical("Gamma Blast: 🛑 Execution Blocked - Max Daily Loss reached.")
                break

            # --- MASTER SHEET TIMING GATE ---
            if not self.gatekeeper.is_gamma_window():
                # Only log every 10 mins to avoid noise
                if int(time.time()) % 600 < 30:
                    logger.info("Gamma Blast: ⏳ Waiting for Master Window (1:45 PM – 2:15 PM)...")
                time.sleep(30)
                continue

            # Post-SL cooldown: wait 5 min before re-entering after a stop-loss hit.
            # Prevents revenge trading on bounces and ensures OI data refreshes.
            _SL_COOLDOWN_SECS = 300
            _secs_since_sl = time.time() - self._last_sl_hit_time
            if self._last_sl_hit_time > 0 and _secs_since_sl < _SL_COOLDOWN_SECS:
                _remaining = int(_SL_COOLDOWN_SECS - _secs_since_sl)
                logger.info(f"Gamma Blast: ⏳ Post-SL cooldown — {_remaining}s remaining before next entry.")
                time.sleep(30)
                continue

            # 1. Check for Resumption (DB check)
            mode = "PAPER" if self.dry_run else "LIVE"
            active_trade = trade_repo.get_active_trade(mode=mode, strategy="GAMMA_BLAST")

            if active_trade:
                # If the trade is still pending fill from a previous crash/timeout, entry_price might be 0.0
                if active_trade.get('entry_price', 0.0) == 0.0:
                    logger.warning(f">>> [Resumption] Found Ghost Trade (entry=0.0): {active_trade['symbol']}. Closing.")
                    trade_repo.collection.delete_one({"id": active_trade['id']})
                    # Do not return; continue loop to look for new signals
                    continue
                
                logger.info(f">>> [Resumption] Found Open Trade: {active_trade['symbol']} | OID: {active_trade.get('sl_order_id')} | Stage: {active_trade.get('monitoring_stage', 0)}")
                self.monitor_position(
                    active_trade['symbol'], 
                    active_trade['token'], 
                    active_trade['qty'],
                    active_trade['sl_price'],
                    active_trade['entry_price'],
                    active_trade['id'],
                    active_trade.get('sl_order_id'),
                    active_trade.get('leg'),
                    stage=active_trade.get('monitoring_stage', 0),
                    remaining_qty=active_trade.get('remaining_qty')
                )
                break # Monitoring finished or trade closed

            # (Though DecisionEngine already checked, we double check local indicators)
            from backend.market_service import market_service
            market_data = market_service.get_market_data()
            analysis = market_data.get('analysis', {})
            instr = get_instrument(Config.ACTIVE_SYMBOL)

            if not analysis or analysis.get('regime') == 'UNKNOWN':
                logger.error(f"Gamma Blast: Market analysis unavailable for {instr.name}. Fallback to safety check.")
                # Final fallback to direct fetch only if market_service is failing
                df = self.data_fetcher.fetch_latest_candles(instr.analysis_token, interval="FIVE_MINUTE", exchange=instr.exchange)
                if df is None or len(df) < 20:
                    time.sleep(30)
                    continue
                ltp = df.iloc[-1]['close']
                adx = self.calculate_adx(df).iloc[-1]
                ema9 = df['close'].ewm(span=9, adjust=False).mean().iloc[-1]
                ema21 = df['close'].ewm(span=21, adjust=False).mean().iloc[-1]
            else:
                ltp = market_data.get(instr.name.lower(), 0)
                adx = analysis.get('adx', 0)
                ema9 = analysis.get('ema9', 0)
                ema21 = analysis.get('ema21', 0)
                logger.info(f"Gamma Blast: Using Shared Analysis (ADX: {adx:.1f} | Regime: {analysis.get('regime')})")

            if not ltp or ltp <= 0:
                logger.warning("Gamma Blast: NIFTY LTP is 0 or unavailable. Skipping.")
                time.sleep(30)
                continue

            if not ema9 or not ema21:
                logger.warning(f"Gamma Blast: EMA data unavailable (ema9={ema9}, ema21={ema21}). Skipping.")
                time.sleep(30)
                continue

            # ADX gate from capital tier — no hardcoded threshold
            from bot.config.settings import Config as _Cfg
            _capital = self.gatekeeper.get_current_capital()
            _tier = _Cfg.get_tier(_capital)
            if adx < _tier.min_adx_to_trade:
                logger.warning(
                    f"Gamma Blast: Trend strength (ADX: {adx:.1f}) below "
                    f"[{_tier.name}] threshold ({_tier.min_adx_to_trade}). Aborting."
                )
                time.sleep(30)
                continue

            # 3. Determine Leg (Trend Direction)
            leg = "CE" if ema9 > ema21 else "PE"

            # --- OI BIAS CONFIRMATION (fresh fetch, not market_service cache) ---
            from bot.utils.expiry_calculator import get_next_weekly_expiry as _get_expiry
            _expiry_now = expiry if expiry else _get_expiry(target_weekday=instr.expiry_day)
            _atm_now = round(ltp / instr.strike_step) * instr.strike_step
            try:
                _fresh_oi = self.oi_analyzer.get_market_sentiment(_expiry_now, _atm_now, symbol=instr.name)
                oi_bias = _fresh_oi.get('bias', 'NEUTRAL')
                logger.info(
                    f"Gamma Blast: 🔍 Fresh OI: bias={oi_bias} | "
                    f"PCR={_fresh_oi.get('pcr', '?')} | ΔR={_fresh_oi.get('delta_ratio', '?')}"
                )
            except Exception as _oe:
                logger.warning(f"Gamma Blast: Fresh OI fetch failed: {_oe}. Using market_service cache.")
                oi_data = market_data.get('oi_data', {})
                oi_bias = oi_data.get('bias', 'NEUTRAL')

            if (leg == "CE" and oi_bias == "BEARISH") or (leg == "PE" and oi_bias == "BULLISH"):
                if instr.asset_type == "COMMODITY":
                    logger.info(f"Gamma Blast: ⚠️ OI Bias Conflict ({oi_bias}) — Proceeding with {leg} entry for {instr.name} (Technicals given priority for commodities).")
                else:
                    logger.warning(
                        f"Gamma Blast: ⚠️ OI Bias Conflict — Leg={leg} but institutions say {oi_bias}. "
                        "Skipping entry to avoid trading against smart money."
                    )
                    time.sleep(30)
                    continue

            # --- MASTER SHEET ROC OI TRIGGER ---
            # ROC in OI > 10% (0.10) signifies aggressive short covering (Gamma Blast trigger).
            oi_roc = self.oi_analyzer.get_oi_roc(_expiry_now, _atm_now, symbol=instr.name, window_mins=5)
            _tier_roc = _Cfg.get_tier(self.gatekeeper.get_current_capital()).gamma_oi_roc_threshold
            if oi_roc < _tier_roc:
                logger.info(f"Gamma Blast: ⏳ Waiting for OI ROC Spike (Current: {oi_roc*100:.1f}% < Threshold: {_tier_roc*100:.1f}%)")
                time.sleep(30)
                continue
            
            logger.info(f"Gamma Blast: 🔥 OI ROC SPIKE DETECTED ({oi_roc*100:.1f}%)! Gamma Trigger Active.")

            # --- CANDLE MOMENTUM FILTER ---
            try:
                _df_gb = self.data_fetcher.fetch_latest_candles(instr.analysis_token, exchange=instr.exchange)
                if _df_gb is not None and len(_df_gb) >= 3:
                    _l3 = _df_gb.tail(3)
                    _bull = (_l3['close'] > _l3['open']).sum()
                    _bear = (_l3['close'] < _l3['open']).sum()
                    if leg == "CE" and _bull < 2:
                        logger.warning(
                            f"Gamma Blast: 🛑 Candle Momentum Filter: {_bull}/3 bullish candles. "
                            "Waiting for stronger confirmation."
                        )
                        time.sleep(30)
                        continue
                    if leg == "PE" and _bear < 2:
                        logger.warning(
                            f"Gamma Blast: 🛑 Candle Momentum Filter: {_bear}/3 bearish candles. "
                            "Waiting for stronger confirmation."
                        )
                        time.sleep(30)
                        continue
            except Exception as _ce:
                logger.warning(f"Gamma Blast: Candle momentum filter error: {_ce}")
                _df_gb = None  # Ensure downstream filters know df is unavailable

            # --- VWAP POSITION FILTER ---
            # On parabolic days institutions drive the move — VWAP confirms which side
            # they're on. CE when below VWAP or PE when above VWAP = fighting the flow.
            try:
                if _df_gb is not None and len(_df_gb) >= 1 and 'volume' in _df_gb.columns:
                    _vol_gb = _df_gb['volume']
                    if _vol_gb.sum() > 0:
                        _typical_gb = (_df_gb['high'] + _df_gb['low'] + _df_gb['close']) / 3
                        _vwap_gb = (_typical_gb * _vol_gb).sum() / _vol_gb.sum()
                        logger.info(
                            f"Gamma Blast: 📏 VWAP={_vwap_gb:.1f} | {instr.name}={ltp:.1f} | Leg={leg}"
                        )
                        if leg == "CE" and ltp < _vwap_gb:
                            logger.warning(
                                f"Gamma Blast: 🛑 VWAP Filter: {instr.name} {ltp:.0f} < VWAP {_vwap_gb:.0f} — "
                                "CE blocked. Price below institutional anchor."
                            )
                            time.sleep(30)
                            continue
                        if leg == "PE" and ltp > _vwap_gb:
                            logger.warning(
                                f"Gamma Blast: 🛑 VWAP Filter: {instr.name} {ltp:.0f} > VWAP {_vwap_gb:.0f} — "
                                "PE blocked. Price above institutional anchor."
                            )
                            time.sleep(30)
                            continue
            except Exception as _ve:
                logger.warning(f"Gamma Blast: VWAP filter error: {_ve}")

            # --- ADX SLOPE FILTER ---
            # ADX must be rising — a declining ADX on a "parabolic day" means the
            # parabola has already peaked. No entry into an exhausting trend.
            try:
                if _df_gb is not None and len(_df_gb) >= 20:
                    _adx_s_gb = self.regime_classifier._calculate_adx(_df_gb)
                    if len(_adx_s_gb) >= 3:
                        _curr_adx = _adx_s_gb.iloc[-1]
                        _prev_adx = _adx_s_gb.iloc[-2]
                        _decline = _prev_adx - _curr_adx
                        
                        # ── RELAXED ADX SLOPE ──────────────────────────────────────────
                        # 1. If ADX > 45, the move is parabolic; minor dips are common.
                        # 2. If decline < 0.3, it's noise, not an exhaustion signal.
                        if _decline > 0.3 and _curr_adx < 45:
                            logger.warning(
                                f"Gamma Blast: 🛑 ADX Slope Filter: ADX declining significantly "
                                f"({_prev_adx:.1f} → {_curr_adx:.1f}). "
                                "Trend losing strength — skipping entry."
                            )
                            time.sleep(30)
                            continue
                        elif _decline > 0:
                            logger.info(
                                f"Gamma Blast: ℹ️ ADX minor decline ({_prev_adx:.1f} → {_curr_adx:.1f}) "
                                f"ignored due to strong trend (ADX={_curr_adx:.1f})."
                            )
            except Exception as _ae:
                logger.warning(f"Gamma Blast: ADX slope filter error: {_ae}")

            # --- MASTER SHEET PREMIUM-BASED STRIKE SELECTION ---
            # Target premiums between ₹3 and ₹6 (Nifty Expiry).
            # This ensures we get the high-gamma leverage discussed in the Master Sheet.
            target_strike = None
            for depth in range(4, 10): # Start at 4 OTM and go deeper
                test_strike = atm_strike + (depth * instr.strike_step * (1 if leg == "CE" else -1))
                test_token, _ = self.token_loader.get_token(instr.name, _expiry_now, test_strike, leg, instrument_type=instr.trading_type, exchange=instr.exchange)
                if not test_token: continue
                
                test_ltp = self.data_fetcher.get_ltp(test_token, exchange=instr.exchange)
                if test_ltp and 3.0 <= test_ltp <= 6.5:
                    target_strike = test_strike
                    quote_ltp = test_ltp
                    token = test_token
                    symbol = _ # Wait, get_token returns (token, symbol)
                    break
            
            # Fallback if no strike found in ₹3-₹6 range (unlikely during gamma window)
            if not target_strike:
                logger.warning("Gamma Blast: No strike found in ₹3-₹6 range. Falling back to default OTM depth.")
                if adx < 50: otm_depth = 1
                elif adx < 55: otm_depth = 2
                else: otm_depth = 3
                target_strike = atm_strike + (otm_depth * instr.strike_step * (1 if leg == "CE" else -1))
                token, symbol = self.token_loader.get_token(instr.name, _expiry_now, target_strike, leg, instrument_type=instr.trading_type, exchange=instr.exchange)
                quote_ltp = self.data_fetcher.get_ltp(token, exchange=instr.exchange) or 5.0
            
            strike = target_strike
            logger.info(f"🎯 Master Strike Selected: {strike} {leg} @ ₹{quote_ltp:.1f}")

            token, symbol = self.token_loader.get_token(instr.name, expiry, strike, leg, instrument_type=instr.trading_type, exchange=instr.exchange)
            if not token:
                logger.error(f"Gamma Blast: Token not found for {strike} {leg}")
                time.sleep(30)
                continue

            # Fetch Option LTP for early record and price estimate
            quote_ltp = self.data_fetcher.get_ltp(token, exchange=instr.exchange) or 50.0

            # 5. Position Sizing — ATR Risk Engine (Master Sheet)
            atr = analysis.get('atr', 20.0) # Fallback to 20 pts
            capital = self.gatekeeper.get_current_capital()
            tier = _Cfg.get_tier(capital)
            risk_amount = capital * tier.risk_per_trade_pct
            
            lots = self.gatekeeper.get_atr_lots(risk_amount, atr, multiplier=1.0)
            qty = lots * instr.lot_size

            # --- TRADE VIABILITY CHECK ---
            # Ensure brokerage (₹60 round-trip) doesn't exceed 15% of trade value.
            # Catches cheap deep-OTM options where costs eat the profit.
            if not self.gatekeeper.check_trade_viability(quote_ltp, qty):
                logger.warning("Gamma Blast: ❌ Trade viability check failed (brokerage ratio too high). Skipping.")
                time.sleep(60)
                continue

            self.place_entry(expiry, strike, leg, qty, quote_ltp)

            # If we didn't enter or monitoring finished, loop again after sleep
            time.sleep(30) # Throttle loop

    def monitor_position(self, symbol, token, qty, sl, entry_price, trade_id, sl_oid, leg, stage=0, remaining_qty=None):
        """
        Progressive 3-stage trailing exit — replaces fixed 3:1 target.

        Stage 0 → 1  (ltp ≥ entry + 1R): SL → breakeven.  Trail: 1.0R below LTP.
        Stage 1 → 2  (ltp ≥ entry + 2R): Book 50% at market. SL → entry+0.5R. Trail: 0.75R.
        Stage 2 → 3  (ltp ≥ entry + 3R): Tighten trail to 0.50R. Let the trend run.

        No fixed profit target. The trailing SL decides when the move is over.
        """
        if remaining_qty is None:
            remaining_qty = qty
            
        risk          = abs(entry_price - sl)   # Initial risk distance — reference point

        logger.info(
            f"Gamma Blast: 🎯 PROGRESSIVE TRAIL | "
            f"Entry={entry_price} | SL={sl} | Risk={risk:.1f}pts | Qty={qty}"
        )

        while self.running:
            try:
                # ── Periodic broker sync ──────────────────────────────────
                if not self.dry_run and time.time() - self.last_sync_time > 15:
                    self.sync_state()
                    self.last_sync_time = time.time()
                    if not self.active_position:
                        logger.warning("Gamma Blast: Sync found no active position. Stopping monitor.")
                        break

                time.sleep(0.5)
                instr = get_instrument(Config.ACTIVE_SYMBOL)
                ltp = self.data_fetcher.get_ltp(token, exchange=instr.exchange)
                if not ltp:
                    continue

                # ── Global kill switch ────────────────────────────────────
                unrealized_pnl = (ltp - entry_price) * remaining_qty
                if not self.gatekeeper.check_max_daily_loss(unrealized_pnl):
                    logger.critical("🛑 EMERGENCY: Account Daily Loss Limit Breach in Gamma Blast!")
                    self.exit_market(token, symbol, remaining_qty, "MAX_DAILY_LOSS", trade_id, sl_oid)
                    break

                # ── Trend-fade check + status heartbeat (throttled to every 30s) ──
                # market_service data refreshes every ~3 min — polling faster wastes resources.
                if time.time() - self.last_trend_fade_check > 30:
                    self.last_trend_fade_check = time.time()
                    from backend.market_service import market_service
                    from bot.config.settings import Config as _Cfg
                    analysis = market_service.get_market_data().get('analysis', {})
                    curr_adx = analysis.get('adx', 0)
                    _tier    = _Cfg.get_tier(self.gatekeeper.get_current_capital())
                    if curr_adx > 0 and curr_adx < _tier.adx_trend_fade_exit:
                        logger.info(
                            f"Gamma Blast: ⚠️ Trend Fading (ADX={curr_adx:.1f} < {_tier.adx_trend_fade_exit} "
                            f"[{_tier.name}]). Exiting {remaining_qty} qty."
                        )
                        if self.exit_market(token, symbol, remaining_qty, "TREND_FADE", trade_id, sl_oid):
                            sl_oid = None
                            self.active_position = None
                            break
                        continue

                    # ── 30s status heartbeat ──────────────────────────────────────
                    instr = get_instrument(Config.ACTIVE_SYMBOL)
                    _be_mult  = 0.5 if (qty >= 4 * instr.lot_size) else 1.0
                    _pnl      = round((ltp - entry_price) * remaining_qty, 2)
                    _pnl_pct  = round((_pnl / (entry_price * remaining_qty)) * 100, 2) if entry_price > 0 else 0

                    if stage == 0:
                        _next_label = f"BE trigger"
                        _next_price = round(entry_price + _be_mult * risk, 1)
                    elif stage == 1:
                        _next_label = f"2R partial-book"
                        _next_price = round(entry_price + 2 * risk, 1)
                    elif stage == 2:
                        _next_label = f"3R tight-trail"
                        _next_price = round(entry_price + 3 * risk, 1)
                    else:
                        _next_label = "Tight trail active"
                        _next_price = None

                    _next_str = (
                        f"{_next_label} @ ₹{_next_price} "
                        f"({abs(_next_price - ltp):.1f}pts {'away' if _next_price > ltp else 'PASSED'})"
                        if _next_price else _next_label
                    )

                    logger.info(
                        f"Gamma Blast: 📊 MONITOR | LTP=₹{ltp:.1f} | Entry=₹{entry_price} | "
                        f"SL=₹{sl:.1f} | Qty={remaining_qty} | Stage={stage} | "
                        f"P&L=₹{_pnl:+,.0f} ({_pnl_pct:+.1f}%) | Next: {_next_str}"
                    )
                    
                    # SYNC PERSISTENCE: Keep DB updated with current monitoring state
                    if trade_id:
                        trade_repo.update_monitoring_state(trade_id, stage, remaining_qty)

                # ── Stage 1: Breakeven at 1R (or 0.5R for high qty) ───
                instr = get_instrument(Config.ACTIVE_SYMBOL)
                be_trigger_mult = 0.5 if (qty >= 4 * instr.lot_size) else 1.0
                if stage < 1 and ltp >= entry_price + (be_trigger_mult * risk):
                    logger.info(f"Gamma Blast: 🛡️ Stage 1 ({be_trigger_mult}R). SL → Breakeven ({entry_price})")
                    sl    = entry_price
                    stage = 1
                    trade_repo.update_sl(trade_id, sl)
                    trade_repo.update_monitoring_state(trade_id, stage, remaining_qty)
                    if sl_oid and not self.dry_run:
                        self.order_manager.modify_sl_order(sl_oid, sl, symbol, token, remaining_qty, exchange=instr.exchange)

                # ── Stage 2: Book 50% at 2R ───────────────────────────────
                if stage < 2 and ltp >= entry_price + 2 * risk:
                    instr = get_instrument(Config.ACTIVE_SYMBOL)
                    lot_size  = instr.lot_size
                    half_lots = max(0, (remaining_qty // lot_size) // 2)
                    half_qty  = half_lots * lot_size

                    if half_qty >= lot_size and remaining_qty > lot_size:
                        partial_pnl = round((ltp - entry_price) * half_qty, 2)
                        logger.info(
                            f"💰 Gamma Blast: Stage 2 (2R). Booking {half_qty} qty @ ₹{ltp:.1f} "
                            f"(locked P&L: ₹{partial_pnl:+,.0f}). "
                            f"Remaining {remaining_qty - half_qty} qty runs free."
                        )
                        partial_confirmed = self.dry_run  # dry_run always confirms
                        if not self.dry_run:
                            partial_limit = round(ltp * 0.98, 1)
                            instr = get_instrument(Config.ACTIVE_SYMBOL)
                            partial_params = {
                                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                                "transactiontype": "SELL", "exchange": instr.exchange,
                                "ordertype": "LIMIT", "price": partial_limit,
                                "producttype": "INTRADAY", "duration": "DAY", "quantity": half_qty,
                            }
                            p_oid = self.order_manager.place_order(partial_params)
                            if p_oid:
                                fill = self.wait_for_fill(p_oid)
                                if fill['status'] == 'FILLED':
                                    partial_pnl = round((fill['price'] - entry_price) * half_qty, 2)
                                    partial_confirmed = True
                                else:
                                    logger.warning(
                                        f"Gamma Blast: ⚠️ Partial booking order {p_oid} not filled "
                                        f"(status={fill['status']}). Keeping full position active."
                                    )

                        # Only update state if the broker actually filled the partial sell.
                        # Avoids desync where local state shows half-position but broker holds full.
                        if partial_confirmed:
                            trade_repo.reduce_position(
                                trade_id=trade_id,
                                reduction_qty=half_qty,
                                exit_price=ltp,
                                pnl_segment=partial_pnl,
                                reason="PARTIAL_PROFIT_2R",
                            )
                            remaining_qty -= half_qty

                    # Raise SL floor to lock 0.5R on the remaining position
                    sl = round(entry_price + 0.5 * risk, 1)
                    stage = 2
                    trade_repo.update_sl(trade_id, sl)
                    trade_repo.update_monitoring_state(trade_id, stage, remaining_qty)
                    if sl_oid and not self.dry_run:
                        self.order_manager.modify_sl_order(sl_oid, sl, symbol, token, remaining_qty, exchange=instr.exchange)

                # ── Stage 3: Tighten trail at 3R ──────────────────────────
                if stage < 3 and ltp >= entry_price + 3 * risk:
                    logger.info(
                        f"Gamma Blast: 💎 Stage 3 (3R+). "
                        f"Activating tight trail on {remaining_qty} qty. No target cap."
                    )
                    stage = 3
                    trade_repo.update_monitoring_state(trade_id, stage, remaining_qty)

                # ── Progressive trailing SL ───────────────────────────────
                # Trail distance shrinks as profit grows so winners run further:
                #   Stage 1 (1R–2R):  trail at 1.0R  — wide, avoids post-breakeven whipsaws
                #   Stage 2 (2R–3R):  trail at 0.75R — tighter, profit locked, let it breathe
                #   Stage 3 (3R+):    trail at 0.50R  — very tight, milk every point
                if stage >= 1:
                    trail_dist = risk * (1.0 if stage == 1 else 0.75 if stage == 2 else 0.5)
                    trail_dist = max(trail_dist, 3.0)   # never trail closer than ₹3 (bid/ask noise)
                    new_sl = round(ltp - trail_dist, 1)
                    if new_sl > sl:
                        logger.info(
                            f"Gamma Blast: 📈 Trail SL {sl} → {new_sl} "
                            f"(LTP={ltp:.1f}, dist={trail_dist:.1f}, stage={stage})"
                        )
                        sl = new_sl
                        trade_repo.update_sl(trade_id, sl)
                        if sl_oid and not self.dry_run:
                            self.order_manager.modify_sl_order(sl_oid, sl, symbol, token, remaining_qty, exchange=instr.exchange)

                # ── SL hit → exit remaining ───────────────────────────────
                if ltp <= sl:
                    reason = "TRAIL_SL_HIT" if stage > 0 else "SL_HIT"
                    logger.info(
                        f"Gamma Blast: {reason} at ₹{ltp:.1f} (SL={sl:.1f}). "
                        f"Exiting {remaining_qty} qty."
                    )
                    # Step 1: Cancel broker SL FIRST so it can't double-fire
                    if sl_oid:
                        self.order_manager.cancel_order(sl_oid, variety="STOPLOSS")
                        sl_oid = None
                    # Step 2: Send market exit and capture actual fill price
                    actual_exit_price = ltp  # fallback
                    if not self.dry_run:
                        instr = get_instrument(Config.ACTIVE_SYMBOL)
                        exit_params = {
                            "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                            "transactiontype": "SELL", "exchange": instr.exchange,
                            "ordertype": "MARKET", "price": 0,
                            "producttype": "INTRADAY", "duration": "DAY", "quantity": remaining_qty,
                        }
                        exit_oid = self.order_manager.place_order(exit_params)
                        if exit_oid:
                            fill = self.wait_for_fill(exit_oid)
                            if fill['status'] == 'FILLED' and fill.get('price', 0) > 0:
                                actual_exit_price = fill['price']
                    # Step 3: Close DB with real fill price (PnL auto-calculated)
                    trade_repo.close_trade(trade_id=trade_id, exit_price=actual_exit_price, exit_reason=reason)
                    self.active_position = None
                    self._last_sl_hit_time = time.time()
                    break

                # ── Time exit based on instrument hours ──────────────────
                if not self.gatekeeper.is_market_open():
                    if self.exit_market(token, symbol, remaining_qty, "TIME", trade_id, sl_oid):
                        sl_oid = None
                        self.active_position = None
                        break

            except Exception as e:
                logger.error(f"Gamma Blast Monitor Error: {e}")
                time.sleep(2)

    def exit_market(self, token, symbol, qty, reason, trade_id, sl_oid):
        """Institutional Exit: Use buffered LIMIT instead of MARKET for OTM safety."""
        try:
            if sl_oid: self.order_manager.cancel_order(sl_oid, variety="STOPLOSS")
            
            instr = get_instrument(Config.ACTIVE_SYMBOL)
            # Note: instr.exchange will be MCX for commodities, NFO for indices
            ltp = self.data_fetcher.get_ltp(token, exchange=instr.exchange) or 0
            # Set limit 2% below LTP to act as market but with a 'flash-crash' floor
            # 10% was too wide and triggered AB1007 LPP. 2% is the exchange sweet spot.
            limit_price = round(ltp * 0.98, 1) if ltp > 0 else 0
            
            instr = get_instrument(Config.ACTIVE_SYMBOL)
            orderparams = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "SELL", "exchange": instr.exchange, 
                "ordertype": "LIMIT" if limit_price > 0 else "MARKET",
                "price": limit_price,
                "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
            }
            oid = self.order_manager.place_order(orderparams)
            
            # Use WebSocket to wait for final exit price for the ledger
            if oid:
                fill = self.wait_for_fill(oid)
                
                # REST FALLBACK FOR EXIT IF TIMEOUT
                if fill['status'] == 'TIMEOUT' and not self.dry_run:
                    logger.info(f"Gamma Blast: ⏳ Exit order {oid} timed out. Doing one final REST API check...")
                    try:
                        ob_res = self.api.orderBook()
                        if ob_res and ob_res.get('status'):
                            for ord_info in ob_res.get('data', []):
                                if ord_info.get('orderid') == oid:
                                    rest_status = ord_info.get('status', '').lower()
                                    if rest_status == 'complete':
                                        logger.info(f"Gamma Blast: ✅ Exit order {oid} actually FILLED on REST check!")
                                        fill = {'status': 'FILLED', 'price': float(ord_info.get('averageprice', 0))}
                                    break
                    except Exception as e:
                        logger.error(f"Gamma Blast Final REST Check Error: {e}")

                # Ensure the exit limit actually filled, so we don't abandon the order
                if fill['status'] == 'TIMEOUT':
                    logger.warning(f"Gamma Blast: Exit order {oid} TIMEOUT permanently. Canceling and retrying monitor mode.")
                    self.order_manager.cancel_order(oid, variety="NORMAL")
                    return False
                
                exit_price = fill.get('price', ltp)
                trade_repo.close_trade(trade_id=trade_id, exit_price=exit_price, exit_reason=reason)
                return True
            else:
                trade_repo.close_trade(trade_id=trade_id, exit_reason=reason)
                return True

        except Exception as e:
            logger.error(f"Gamma Blast Exit Failed: {e}")

    def calculate_adx(self, df, period=14):
        # Local ADX calc or use analysis file
        try:
            df = df.copy()
            df['up'] = df['high'] - df['high'].shift(1)
            df['dn'] = df['low'].shift(1) - df['low']
            df['pdm'] = df['up'].where((df['up'] > df['dn']) & (df['up'] > 0), 0)
            df['ndm'] = df['dn'].where((df['dn'] > df['up']) & (df['dn'] > 0), 0)
            df['tr1'] = df['high'] - df['low']
            df['tr2'] = abs(df['high'] - df['close'].shift(1))
            df['tr3'] = abs(df['low'] - df['close'].shift(1))
            df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
            df['atr'] = df['tr'].ewm(alpha=1/period, adjust=False).mean()
            df['pdi'] = 100 * (df['pdm'].ewm(alpha=1/period, adjust=False).mean() / df['atr'])
            df['ndi'] = 100 * (df['ndm'].ewm(alpha=1/period, adjust=False).mean() / df['atr'])
            df['dx'] = 100 * abs(df['pdi'] - df['ndi']) / (df['pdi'] + df['ndi'])
            return df['dx'].ewm(alpha=1/period, adjust=False).mean()
        except: return pd.Series([0]*len(df))

