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
from bot.core.position_manager import LadderedTrailingManager

class GammaBlastStrategy:
    """
    Gamma Blast (OTM Momentum) Strategy.
    Designed for "Hero-to-Zero" exponential returns during parabolic trends.
    Trigger: ADX > 35 + Strong OI Bias + VWAP/EMA Confluence.
    Target: 3:1 or 5:1 Risk/Reward using high-leverage OTM options.
    """
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(self.api, dry_run=self.dry_run)
        self.data_fetcher = DataFetcher(self.api)
        self.order_manager = OrderManager(self.api, dry_run=self.dry_run)
        self.oi_analyzer = OIAnalyzer(self.api, self.token_loader)
        self.regime_classifier = RegimeClassifier()
        self.trailing_manager = LadderedTrailingManager(self.order_manager, self.data_fetcher)
        self.running = True
        self.active_position = None
        self.last_sync_time = 0
        self.risk_multiplier = 1.0        # Set by DecisionEngine before execute()
        self.last_trend_fade_check = 0    # Throttle market_service calls in monitor
        self._last_sl_hit_time = 0        # Timestamp of last SL hit — gates re-entry
        self._last_sl_nifty_spot = 0      # Nifty Spot price when last SL triggered
        self._last_sl_leg = None          # "CE" or "PE" of the SL'd trade
        self._last_status_log  = 0        # Throttle for periodic monitor heartbeat

    def sync_state(self):
        """
        Synchronizes active position from Broker API.
        Looks for the FIRST active NIFTY Intraday position that matches Gamma Blast style (OTM).
        """
        if self.dry_run:
            if self.active_position is None:
                db_trade = trade_repo.get_active_trade(mode="PAPER", strategy="GAMMA_BLAST")
                if db_trade:
                    self.active_position = {
                        'id': db_trade['id'],
                        'leg': db_trade['leg'],
                        'symbol': db_trade['symbol'],
                        'token': db_trade['token'],
                        'qty': db_trade['qty'],
                        'entry_price': db_trade['entry_price'],
                        'sl_price': db_trade['sl_price'],
                        'sl_order_id': None
                    }
                    logger.info(f"♻️ [Gamma Blast] PAPER RECOVERY: Found Active Trade in DB! {db_trade['symbol']}")
            return

        try:
            from bot.utils.rate_limiter import rate_limiter
            rate_limiter.wait()
            pos_resp = self.order_manager.get_positions()
            
            # transients (DNS, timeout) return None or False status
            if pos_resp is None or not pos_resp.get('status'):
                logger.warning("⚠️ [Gamma Blast] Sync State: API failure. Skipping sync to preserve local state.")
                return

            # If we reach here, the API call was successful
            found_active = None
            pos_data = pos_resp.get('data') or []
            
            for pos in pos_data:
                # Look for NIFTY Intraday options with non-zero quantity
                if (pos.get('symbolname') == 'NIFTY' and 
                    pos.get('producttype') == 'INTRADAY' and 
                    int(pos.get('netqty', 0)) != 0):
                    
                    symbol = pos['tradingsymbol']
                    
                    # 🛡️ STRATEGY FILTER GUARD: Does this position belong to another strategy in DB?
                    existing_trade = trade_repo.get_active_trade(symbol=symbol, mode="LIVE")
                    if existing_trade and existing_trade.get('strategy') not in ["GAMMA_BLAST"]:
                        logger.info(f"⏩ [Gamma Blast] Skipping {symbol} (Belongs to Strategy: {existing_trade.get('strategy')})")
                        continue
                        
                    qty = int(pos['netqty'])
                    
                    found_active = {
                        'leg': "CE" if "CE" in symbol else "PE", 
                        'symbol': symbol,
                        'token': pos['symboltoken'],
                        'qty': abs(qty),
                        'entry_price': float(pos['avgnetprice']),
                        # If no local sl_price, default to 20% stop
                        'sl_price': float(pos['avgnetprice']) * 0.8
                    }
                    
                    # Match with DB record to get correct sl_price if available
                    db_trade = existing_trade if existing_trade and existing_trade.get('strategy') == "GAMMA_BLAST" else \
                               trade_repo.get_active_trade(mode="LIVE", strategy="GAMMA_BLAST", symbol=found_active['symbol'])
                               
                    if db_trade:
                        found_active['id'] = db_trade['id']
                        found_active['sl_price'] = db_trade.get('sl_price', found_active['sl_price'])
                        found_active['sl_order_id'] = db_trade.get('sl_order_id') # FIX: Restore SL OID for logic below
                        logger.info(f"♻️ [Gamma Blast] RECOVERY: Linked to DB Trade #{db_trade['id']}")
                    
                    if self.active_position is None:
                        logger.info(f"♻️ [Gamma Blast] RECOVERY: Found Active Trade on Broker! {found_active['symbol']}")
                    
                    break 
            
            if found_active:
                if self.active_position is None:
                    self.active_position = found_active
                    logger.info(f"♻️ [Gamma Blast] RECOVERY: Found Active Trade on Broker! {found_active['symbol']}")
                else:
                    # Maintain local source of truth for entry price
                    pass
            
            elif self.active_position is not None:
                # Local says we have a position, but Broker says we don't.
                # Check if it was a broker-side SL hit.
                sl_oid = self.active_position.get('sl_order_id')
                if sl_oid:
                    status_info = self.order_manager.get_order_status(sl_oid)
                    if status_info and status_info.get('status') == 'COMPLETE':
                        fill_price = status_info.get('price', self.active_position['sl_price'])
                        logger.info(f"🛡️ [Gamma Blast] SYNC: Broker-Side SL Hit detected for {self.active_position['symbol']} @ ₹{fill_price}")
                        self._last_sl_hit_time = time.time()
                        
                        trade_id = self.active_position.get('id')
                        entry_p = self.active_position['entry_price']
                        qty = self.active_position['qty']
                        pnl = (fill_price - entry_p) * qty
                        
                        trade_repo.close_trade(trade_id=trade_id, symbol=self.active_position['symbol'], exit_price=fill_price, pnl=round(pnl, 2), exit_reason="BROKER_SL_HIT")
                        self.active_position = None
                        return

                logger.warning("⚠️ [Gamma Blast] SYNC: Active Position closed externally! Resetting State.")
                trade_repo.close_trade(symbol=self.active_position['symbol'], exit_reason="EXTERNAL_SYNC_RESET")
                self.active_position = None

                    
        except Exception as e:
            logger.error(f"[Gamma Blast] Sync State Error: {e}")

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

            # 2. Market analysis & Final Confirmation
            # (Though DecisionEngine already checked, we double check local indicators)
            from backend.market_service import market_service
            market_data = market_service.get_market_data()
            analysis = market_data.get('analysis', {})

            if not analysis or analysis.get('regime') == 'UNKNOWN':
                logger.error("Gamma Blast: Market analysis unavailable. Fallback to safety check.")
                # Final fallback to direct fetch only if market_service is failing
                df = self.data_fetcher.fetch_latest_candles("99926000", interval="FIVE_MINUTE")
                if df is None or len(df) < 20:
                    time.sleep(30)
                    continue
                ltp = df.iloc[-1]['close']
                adx = self.calculate_adx(df).iloc[-1]
                ema9 = df['close'].ewm(span=9, adjust=False).mean().iloc[-1]
                ema21 = df['close'].ewm(span=21, adjust=False).mean().iloc[-1]
            else:
                ltp = market_data.get('nifty', 0)
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

            # ── REGIME DRIFT YIELD ──────────────────────────────────────────────────
            # If conditions leave Gamma Blast territory, yield control back to the Brain.
            # Uses a 5-point buffer (e.g., 42.0 -> 37.0) to prevent jitter/thrashing.
            _regime_now = analysis.get('regime', 'UNKNOWN')
            _drift_threshold = _tier.adx_gamma_blast - 5.0
            if adx < _drift_threshold or _regime_now in ["CHOP", "SIDEWAYS"]:
                logger.warning(
                    f"🔄 [Gamma Blast] Regime Drift detected (ADX: {adx:.1f} | Regime: {_regime_now}). "
                    f"Yielding control back to Decision Engine for re-evaluation."
                )
                break # Terminate execution loop, allowing Lifecycle Manager to restart and switch strategies

            # 3. Determine Leg (Trend Direction)
            leg = "CE" if ema9 > ema21 else "PE"
            
            # --- STRUCTURAL RESET GATE (PRO-TRADER MODE) ---
            # If the last trade was an SL, and we're attempting to re-enter in SAME direction:
            # Mandate that price has decisively cleared the 'shakeout zone' (+/- 5 pts).
            # Prevents 'Revenge Averaging' into the same failing consolidation structure.
            if self._last_sl_nifty_spot > 0 and self._last_sl_leg == leg:
                _buffer = 5.0
                if leg == "CE" and ltp < (self._last_sl_nifty_spot + _buffer):
                    logger.warning(
                        f"🛡️ Structural Block (CE): Spot {ltp:.1f} hasn't cleared "
                        f"last SL anchor ({self._last_sl_nifty_spot:.1f} + {_buffer}). Waiting for breakout."
                    )
                    time.sleep(30)
                    continue
                elif leg == "PE" and ltp > (self._last_sl_nifty_spot - _buffer):
                    logger.warning(
                        f"🛡️ Structural Block (PE): Spot {ltp:.1f} hasn't cleared "
                        f"last SL anchor ({self._last_sl_nifty_spot:.1f} - {_buffer}). Waiting for breakdown."
                    )
                    time.sleep(30)
                    continue
                else:
                     # Structure cleared! Reset anchor to allow entry
                     logger.info(f"🚀 Structural Clear: Price has cleanly cleared previous SL anchor. Resuming operations.")
                     self._last_sl_nifty_spot = 0 
                     self._last_sl_leg = None

            # --- OI BIAS CONFIRMATION (fresh fetch, not market_service cache) ---
            # Force-fetch current OI at entry — market_service oi_data can be up to
            # 300s old. On volatile days, institutions can flip in minutes.
            from bot.utils.expiry_calculator import get_next_weekly_expiry as _get_expiry
            _expiry_now = expiry if expiry else _get_expiry()
            _atm_now = round(ltp / 50) * 50
            try:
                _fresh_oi = self.oi_analyzer.get_oi_velocity(_expiry_now, _atm_now)
                oi_bias = _fresh_oi.get('bias', 'NEUTRAL')
                oi_speed = _fresh_oi.get('pcr_velocity', 0.0)
                logger.info(
                    f"Gamma Blast: 🔍 Fresh OI: bias={oi_bias} | "
                    f"PCR={_fresh_oi.get('pcr', '?')} | Vel={oi_speed:.4f}"
                )
            except Exception as _oe:
                logger.warning(f"Gamma Blast: Fresh OI fetch failed: {_oe}. Using market_service cache.")
                oi_data = market_data.get('oi_data', {})
                oi_bias = oi_data.get('bias', 'NEUTRAL')
                oi_speed = 0.0

            self._is_squeeze = False
            if leg == "CE" and oi_speed > 0.05:
                self._is_squeeze = True
                logger.info(f"🔥 SQUEEZE DETECTED: PCR Velocity = {oi_speed:.4f} (Short Covering). Permitting CE entry.")
            elif leg == "PE" and oi_speed < -0.05:
                self._is_squeeze = True
                logger.info(f"🔥 SQUEEZE DETECTED: PCR Velocity = {oi_speed:.4f} (Long Unwinding). Permitting PE entry.")
            
            _is_squeeze = self._is_squeeze

            # EXTREME TREND OVERRIDE: Cap at 60.0 to avoid entering at climax exhaustion.
            _is_extreme_trend = 45.0 <= adx <= 60.0
            if _is_extreme_trend and not _is_squeeze and ((leg == "CE" and oi_bias == "BEARISH") or (leg == "PE" and oi_bias == "BULLISH")):
                logger.info(
                    f"🚀 EXTREME TREND OVERRIDE: ADX={adx:.1f} is extreme (>=45.0). Bypassing Strict OI Gate "
                    f"to capture parabolic move despite contradicting institutional bias ({oi_bias})."
                )

            if not _is_squeeze and not _is_extreme_trend and ((leg == "CE" and oi_bias == "BEARISH") or (leg == "PE" and oi_bias == "BULLISH")):
                logger.warning(
                    f"Gamma Blast: 🛑 Strict OI Gate — Price Action says {leg} but institutions say {oi_bias}. "
                    f"Contradicting signals on ADX={adx:.1f} day. Skipping entry to protect capital."
                )
                time.sleep(30)
                continue
            # --- CANDLE MOMENTUM FILTER ---
            # At least 2 of the last 3 completed 5-min candles must close in the
            # trade direction. Prevents entering on an EMA crossover from a single
            # spike or post-SL bounce candle.
            _df_gb = None
            if _is_squeeze or _is_extreme_trend:
                logger.info(f"🚀 Candle Momentum Filter: Bypassing due to {'Squeeze' if _is_squeeze else 'Extreme Trend Override'} (ADX={adx:.1f}).")
            else:
                try:
                    _df_gb = self.data_fetcher.fetch_latest_candles("99926000")
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
                            f"Gamma Blast: 📏 VWAP={_vwap_gb:.1f} | NIFTY={ltp:.1f} | Leg={leg}"
                        )
                        if leg == "CE" and ltp < _vwap_gb:
                            logger.warning(
                                f"Gamma Blast: 🛑 VWAP Filter: NIFTY {ltp:.0f} < VWAP {_vwap_gb:.0f} — "
                                "CE blocked. Price below institutional anchor."
                            )
                            time.sleep(30)
                            continue
                        if leg == "PE" and ltp > _vwap_gb:
                            logger.warning(
                                f"Gamma Blast: 🛑 VWAP Filter: NIFTY {ltp:.0f} > VWAP {_vwap_gb:.0f} — "
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
                        curr_adx_s = _adx_s_gb.iloc[-1]
                        prev_adx_s = _adx_s_gb.iloc[-2]
                        
                        # NOISE TOLERANCE: Parabolic days have minor ADX fluctuations.
                        # 1. If ADX > 50, ignore slope (trend is extreme).
                        # 2. If ADX > 35, allow small decline up to 0.2pts (noise).
                        # 3. Otherwise, require at least flat (diff > -0.05).
                        _is_declining = False
                        if self._is_squeeze:
                            logger.info("🔥 Squeeze detected: Bypassing ADX Slope Filter.")
                        elif curr_adx_s > 50:
                            _is_declining = (curr_adx_s - prev_adx_s) < -3.0
                        elif curr_adx_s > 35:
                            _is_declining = (curr_adx_s - prev_adx_s) < -1.5
                        else:
                            _is_declining = (curr_adx_s - prev_adx_s) < -0.05

                        if _is_declining:
                            logger.warning(
                                f"Gamma Blast: 🛑 ADX Slope Filter: ADX declining "
                                f"({prev_adx_s:.2f} → {curr_adx_s:.2f}). "
                                "Trend losing strength — skipping entry."
                            )
                            time.sleep(30)
                            continue
            except Exception as _ae:
                logger.warning(f"Gamma Blast: ADX slope filter error: {_ae}")

            # 4. Strike Selection — Dynamic Volatility Adjustment
            atm_strike = round(ltp / 50) * 50
            vix = market_data.get('vix', 0)

            if getattr(self, '_is_squeeze', False):
                # SQUEEZE DETECTED: Force ATM (0 depth) for max delta acceleration
                otm_depth = 0
                logger.info("🔥 SQUEEZE OVERRIDE: Lock strike at ATM to catch parabolic delta surge.")
            else:
                # A. Base Depth from ADX Scale
                if adx < 50:
                    otm_depth = 1
                elif adx < 55:
                    otm_depth = 2
                else:
                    otm_depth = 3

                # B. Dynamic Volatility Capping (Institutional Guard)
                # VIX > 20 signifies explosive extrinsic premium (high theta risk).
                # Never buy >1 OTM depth when VIX is elevated; options are too rich.
                if vix > 20 and otm_depth > 1:
                    logger.warning(f"📉 Volatility Risk: VIX={vix:.1f} > 20. Hard-capping OTM depth to 1 to avoid Vega trap.")
                    otm_depth = 1
                
                # C. IV Rank Convergence
                # If IV Rank is extremely high, bias strictly towards ATM as mean-reversion crushes OTM faster.
                iv_rank = self.gatekeeper.get_iv_rank()
                if iv_rank > 0.75:
                    original_depth = otm_depth
                    otm_depth = min(1, otm_depth)
                    if original_depth != otm_depth:
                        logger.info(f"🛡️ IV Rank Critical ({iv_rank:.0%}): Compressing OTM depth {original_depth} -> {otm_depth}.")
                elif iv_rank > 0.60 and otm_depth > 1:
                     otm_depth = otm_depth - 1
                     logger.info(f"🛡️ IV Rank Elevated ({iv_rank:.0%}): Lowering depth to {otm_depth} strikes.")

            strike = atm_strike + (otm_depth * 50 * (1 if leg == "CE" else -1))

            logger.info(
                f"🎯 Analysis: ADX={adx:.1f} | Leg={leg} | "
                f"OTM depth={otm_depth} strikes | Strike={strike}"
            )

            token, symbol = self.token_loader.get_token("NIFTY", expiry, strike, leg)
            if not token:
                logger.error(f"Gamma Blast: Token not found for {strike} {leg}")
                time.sleep(30)
                continue

            # Fetch Option LTP for early record and price estimate
            quote_ltp = self.data_fetcher.get_ltp(token, exchange="NFO") or 50.0

            # 5. Position Sizing — Uncapped Deployment for Squeezes
            # Reuse _tier already resolved above — no extra API call needed.
            margin_per_lot = (quote_ltp * Config.NIFTY_LOT_SIZE) if quote_ltp > 0 else (_tier.min_capital_threshold * 0.5)
            
            raw_lots = self.gatekeeper.get_compounded_lots(margin_per_lot=margin_per_lot, multiplier=self.risk_multiplier)
            if getattr(self, '_is_squeeze', False):
                lots = int(raw_lots)
                logger.info(f"🔥 SQUEEZE DEPLOYMENT: Bypassing lot fraction handicap. Deploying 100% of risk-parity lots ({lots}).")
            else:
                lots = int(raw_lots * _tier.gamma_blast_lot_pct)
            if lots < 1:
                # Only force 1 lot if capital can actually cover a single lot
                estimated_cost = margin_per_lot
                if self.gatekeeper.check_trade_margin(estimated_cost, silent=True):
                    lots = 1
                else:
                    logger.warning(f"Gamma Blast: ❌ Insufficient capital for 1 lot (₹{estimated_cost:,.0f} required). Skipping.")
                    time.sleep(60)
                    continue
            qty = lots * Config.NIFTY_LOT_SIZE

            # --- TRADE VIABILITY CHECK ---
            if not self.gatekeeper.check_trade_viability(quote_ltp, qty):
                logger.warning("Gamma Blast: ❌ Trade viability check failed. Skipping.")
                time.sleep(60)
                continue

            # --- SAFETY GATE: Instrument Cooldown (Anti-Revenge Trading) ---
            if not self.gatekeeper.check_instrument_cooldown(symbol):
                time.sleep(60)
                continue

            self.place_entry(expiry, strike, leg, qty, quote_ltp)



            # If we didn't enter or monitoring finished, loop again after sleep
            time.sleep(30) # Throttle loop

    def place_entry(self, expiry, strike, leg, qty, quote_ltp):
        token, symbol = self.token_loader.get_token("NIFTY", expiry, strike, leg)
        if not token:
            logger.error(f"Gamma Blast: Token not found for {strike} {leg}")
            return

        # Hard margin check before touching the broker API
        estimated_cost = quote_ltp * qty
        if not self.gatekeeper.check_trade_margin(estimated_cost):
            logger.warning(f"Gamma Blast: ❌ Margin check failed. Need ₹{estimated_cost:,.0f}. Aborting entry.")
            return

        # Place Smart-Limit Order — slippage buffer from capital tier config.
        # Smaller accounts (MICRO/SMALL) use a tighter buffer; avoids over-paying for OTM options.
        from bot.config.settings import Config as _Cfg
        _entry_tier = _Cfg.get_tier(self.gatekeeper.get_current_capital())
        limit_price = round(quote_ltp * (1.0 + _entry_tier.entry_slippage_pct), 1)
        
        logger.info(f">>> [Trade] Entering {symbol} (Qty: {qty}) via Smart-Limit @ ₹{limit_price}")
        
        oid = self.order_manager.place_smart_limit(
            symbol, token, qty, limit_price, 
            transaction_type="BUY", 
            strategy_name="GAMMA_BLAST"
        )
        if not oid: return

        # 2. Wait for fill (WebSocket or REST fallback)
        fill_result = self.wait_for_fill(oid)
        
        # FINAL REST FALLBACK IF TIMEOUT: The order might have filled right as timeout hit
        if fill_result['status'] == 'TIMEOUT' and not self.dry_run:
            logger.info(f"Gamma Blast: ⏳ Order {oid} timed out. Doing one final REST API check before aborting...")
            try:
                ob_res = self.api.orderBook()
                if ob_res and ob_res.get('status'):
                    for ord_info in ob_res.get('data', []):
                        if ord_info.get('orderid') == oid:
                            rest_status = ord_info.get('status', '').lower()
                            if rest_status == 'complete':
                                logger.info(f"Gamma Blast: ✅ Order {oid} actually FILLED on REST check!")
                                try:
                                    avg_price = float(ord_info.get('averageprice') or 0)
                                except (ValueError, TypeError):
                                    avg_price = 0.0
                                fill_result = {'status': 'FILLED', 'price': avg_price}
                            break
            except Exception as e:
                logger.error(f"Gamma Blast Final REST Check Error: {e}")

        if fill_result['status'] != 'FILLED':
            logger.warning(f"Gamma Blast: Entry failed or timed out permanently. Status: {fill_result['status']}")
            
            cancel_success = True
            if fill_result['status'] == 'TIMEOUT':
                cancel_success = self.order_manager.cancel_order(oid, variety="NORMAL")
                
            if cancel_success or fill_result['status'] in ['REJECTED', 'CANCELLED']:
                # CRITICAL FIX: Delete the zombie database record if entry failed and was cancelled
                failed_trade = trade_repo.get_active_trade(strategy="GAMMA_BLAST", symbol=symbol)
                if failed_trade:
                    trade_repo.collection.delete_one({"id": failed_trade['id']})
                    logger.info(f"Gamma Blast: Cleaned up failed entry record #{failed_trade['id']} from database.")
                return
            else:
                logger.critical(f"Gamma Blast: 🚨 DANGER! Order {oid} timed out, but CANCEL FAILED! It might be filling! Transitioning to monitor mode just in case.")
                fill_result = {'status': 'FILLED', 'price': limit_price} # Assume limit price fill to survive
                
        fill_price = fill_result['price']

        # Guard: if fill_price is 0 (bad REST data), fall back to limit_price
        if not fill_price or fill_price <= 0:
            logger.warning(f"Gamma Blast: fill_price is 0 — using limit_price {limit_price} as fallback.")
            fill_price = limit_price

        # Update Trade with Actual Fill & Mark OPEN — SL% from capital tier
        from bot.config.settings import Config as _Cfg
        _tier = _Cfg.get_tier(self.gatekeeper.get_current_capital())
        
        # Calculate SL points based on percentage
        sl_points = fill_price * _tier.sl_pct
        
        # Apply Floor (Minimum SL points)
        sl_floor = min(_tier.min_sl_points, fill_price * 0.5)
        if sl_points < sl_floor:
            logger.info(f"🛡️ [Gamma Blast] SL Floor Triggered: Increasing {sl_points:.1f}pts to {sl_floor:.1f}pts floor.")
            sl_points = sl_floor
            
        sl_price = round(fill_price - sl_points, 1)

        # Link and Update DB Record
        trade_id = self.order_manager.update_trade_fill(symbol, "GAMMA_BLAST", fill_price, expected_price=quote_ltp)
        if trade_id:
            trade_repo.update_sl(trade_id, sl_price)
        else:
            logger.warning("Gamma Blast: Could not link fill to DB record. Status might be out of sync.")

        if not trade_id:
            logger.critical(f"Gamma Blast: 🚨 trade_id is None after fill! Cannot track trade safely. Exiting position.")
            exit_params = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "SELL", "exchange": "NFO",
                "ordertype": "MARKET", "price": 0,
                "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
            }
            self.order_manager.place_order(exit_params)
            return

        # Place Broker SL
        sl_oid = self.order_manager.place_sl_order(symbol, token, qty, sl_price, leg)
        
        # SL VERIFICATION: If SL placement failed, we cannot hold the position safely.
        if not sl_oid and not self.dry_run:
            logger.critical(f"🚨 GAMMA BLAST: SL placement FAILED for {symbol}. Emergency exiting position for safety!")
            exit_params = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "SELL", "exchange": "NFO",
                "ordertype": "MARKET", "price": 0,
                "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
            }
            self.order_manager.place_order(exit_params)
            return

        # PERSIST SL OID: Critical for recovery after restarts
        if trade_id and sl_oid:
            trade_repo.update_sl_order_id(trade_id, sl_oid)

        # CRITICAL FIX: Fully initialize local state tracker before entering monitor loop
        self.active_position = {
            'id': trade_id,
            'leg': leg,
            'symbol': symbol,
            'token': token,
            'qty': qty,
            'entry_price': fill_price,
            'sl_price': sl_price,
            'sl_order_id': str(sl_oid)
        }

        self.monitor_position(symbol, token, qty, sl_price, fill_price, trade_id, sl_oid, leg)

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
                ltp = self.data_fetcher.get_ltp(token, exchange="NFO")
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
                    _be_mult  = 0.5 if (qty >= 4 * Config.NIFTY_LOT_SIZE) else 1.0
                    _pnl      = round((ltp - entry_price) * remaining_qty, 2)
                    _pnl_pct  = round((_pnl / (entry_price * remaining_qty)) * 100, 2) if entry_price > 0 else 0

                    if stage == 0:
                        _next_label = f"BE trigger"
                        _next_price = round(entry_price + _be_mult * risk, 1)
                    elif stage == 1:
                        _next_label = f"2R trail tight"
                        _next_price = round(entry_price + 2 * risk, 1)
                    elif stage == 2:
                        _next_label = f"3R hyper-trail"
                        _next_price = round(entry_price + 3 * risk, 1)
                    else:
                        _next_label = "Hyper-trail active"
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

                # ── Time killswitch (15:10) ────────────────────────────────────
                if self.trailing_manager.is_killswitch_time():
                    logger.info("⏰ Gamma Blast: Time Killswitch (15:10) triggered. Force exiting.")
                    if self.exit_market(token, symbol, remaining_qty, "TIME_KILLSWITCH", trade_id, sl_oid):
                        sl_oid = None
                        self.active_position = None
                        break

                # ── Use LadderedTrailingManager ───────────────────────────────
                should_close, exit_type = self.trailing_manager.update_trailing_sl("GAMMA_BLAST", self.active_position, ltp)

                if should_close:
                    logger.info(f"🛑 Gamma Blast: Laddered Exit Triggered ({exit_type})")
                    self.exit_market(token, symbol, remaining_qty, f"LADDERED_SL_{exit_type}", trade_id, sl_oid, exit_type=exit_type)
                    sl_oid = None
                    
                    # --- STRUCTURAL RESET RECORDING ---
                    from backend.market_service import market_service
                    md = market_service.get_market_data()
                    
                    self.active_position = None
                    self._last_sl_hit_time = time.time()
                    self._last_sl_nifty_spot = md.get('nifty', 0)
                    self._last_sl_leg = leg # Captured from parameter scope
                    
                    logger.warning(
                        f"🛡️ Captured Structural Reset Anchor: Leg={leg} | Spot={self._last_sl_nifty_spot:.1f}. "
                        "Blocking same-direction re-entries until structural resolution."
                    )
                    break

                # Update local sl for logging
                sl = self.active_position.get('sl_price', sl)
                stage = self.active_position.get('ladder_stage', stage)

                # Time exit at 15:15 (Configurable)

                # ── Time exit at 15:15 (Configurable) ────────────────────────────────────
                if datetime.datetime.now().time() >= datetime.time(*Config.STRATEGY_EXIT_TIME):
                    if self.exit_market(token, symbol, remaining_qty, "TIME", trade_id, sl_oid):
                        sl_oid = None
                        self.active_position = None
                        break

            except Exception as e:
                logger.error(f"Gamma Blast Monitor Error: {e}")
                time.sleep(2)

    def exit_market(self, token, symbol, qty, reason, trade_id, sl_oid, exit_type="MARKET"):
        """Institutional Exit: Use buffered LIMIT instead of MARKET for OTM safety if requested."""
        try:
            if sl_oid: self.order_manager.cancel_order(sl_oid, variety="STOPLOSS")
            
            ltp = self.data_fetcher.get_ltp(token, exchange="NFO") or 0
            
            # Smart-Exit logic: Use LIMIT at SL price if exit_type is LIMIT
            if exit_type == "LIMIT":
                limit_price = self.active_position.get('sl_price', ltp)
            else:
                # Default institutional behavior: 2% buffer limit
                limit_price = round(ltp * 0.98, 1) if ltp > 0 else 0
            
            orderparams = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "SELL", "exchange": "NFO", 
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

    def wait_for_fill(self, order_id):
        """Uses WebSocket Order Feed for sub-second fill detection."""
        if self.dry_run: return {'status': 'FILLED', 'price': 50.0}
        
        from bot.core.order_feed import order_feed
        logger.info(f">>> [Gamma Blast] Waiting for WebSocket Fill Event ({order_id})...")
        
        result = order_feed.wait_for_fill(order_id, timeout=30)
        
        if result['status'] == 'TIMEOUT':
             logger.warning(f"⚠️ Order {order_id} fill TIMEOUT via WebSocket.")
        
        return result

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

    def stop(self):
        self.running = False
