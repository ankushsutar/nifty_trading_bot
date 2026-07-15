import time
import datetime
import pandas as pd
import random
import json
import os

from bot.config.settings import Config
from bot.core.session import get_session
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.levels_provider import levels_provider
from bot.core.data_fetcher import DataFetcher
from bot.core.regime_classifier import RegimeClassifier
from bot.core.oi_analyzer import OIAnalyzer
from bot.utils.logger import logger
from bot.config.instruments import get_instrument
from bot.utils.expiry_calculator import get_next_weekly_expiry
from bot.utils.trade_journal import TradeJournal
from bot.core.trade_repo import trade_repo
from bot.core.order_manager import OrderManager
from bot.core.market_feed import market_feed
from bot.utils.notifier import notifier
from bot.core.position_manager import LadderedTrailingManager

class MomentumStrategy:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(self.api, dry_run=self.dry_run)
        self.data_fetcher = DataFetcher(self.api)
        self.regime_classifier = RegimeClassifier()
        self.oi_analyzer = OIAnalyzer(self.api, self.token_loader)
        self.order_manager = OrderManager(self.api, dry_run=self.dry_run)
        self.trailing_manager = LadderedTrailingManager(self.order_manager, self.data_fetcher)
        
        self.data_failure_count = 0
        self.active_position = None
        self.running = True  # Flag for graceful shutdown
        self.last_sync_time = 0
        self.last_analysis = {} # Stores EMA9, RSI, etc for logging
        self.last_oi_scan = 0
        self.last_trailing_check = 0 # Throttle Trailing Stop Checks
        self.oi_data = {}
        self._ltp_cache = {} # SafeLTP Cache
        self.risk_multiplier = 1.0
        self._last_status_log  = 0  # Throttle for periodic monitor heartbeat
        self._last_sl_hit_time = 0  # Timestamp of last SL hit — gates re-entry
        
        self.sync_state() # Initial Sync with Broker

    def export_state(self):
        """Exports current strategy state to JSON for UI consumption."""
        try:
            data_dir = "data"
            if not os.path.exists(data_dir):
                os.makedirs(data_dir)
            
            state = {
                "timestamp": datetime.datetime.now().isoformat(),
                "analysis": self.last_analysis,
                "oi_data": self.oi_data,
                "active_position": self.active_position,
                "dry_run": self.dry_run
            }
            
            with open(os.path.join(data_dir, "market_status.json"), "w") as f:
                json.dump(state, f, default=str) 
        except Exception as e:
            logger.error(f"State Export Error: {e}")

    def sync_state(self):
        """
        Synchronizes active position from Broker API.
        Current Rule: Looks for the FIRST active NIFTY Intraday position.
        """
        if self.dry_run:
            if self.active_position is None:
                db_trade = trade_repo.get_active_trade(mode="PAPER", strategy="MOMENTUM")
                if db_trade:
                    sym = db_trade['symbol']
                    if trade_repo._is_symbol_expired(sym):
                        logger.warning(f"⚠️ [Momentum] PAPER recovery: {sym} is from a past expiry. Closing in DB.")
                        trade_repo.close_trade(trade_id=db_trade['id'], exit_price=0.0, pnl=0.0, exit_reason="EXPIRED_CONTRACT_ON_RECOVERY")
                    else:
                        self.active_position = {
                            'id': db_trade['id'],
                            'leg': db_trade['leg'],
                            'symbol': sym,
                            'token': db_trade['token'],
                            'qty': db_trade['qty'],
                            'entry_price': db_trade['entry_price'],
                            'sl_price': db_trade['sl_price'],
                            'sl_order_id': db_trade.get('sl_order_id'),
                            'atr': 0.0,
                            'partially_booked': db_trade.get('partially_booked', False)
                        }
                        logger.info(f"♻️ PAPER RECOVERY: Found Active Trade in DB! {sym}")
            return
        
        try:
            from bot.utils.rate_limiter import rate_limiter
            wait_time = rate_limiter.check_circuit_breaker()
            if wait_time > 0:
                logger.warning(f"⚠️ Sync Skipped due to Circuit Breaker (Wait {wait_time:.1f}s)")
                return

            pos_resp = self.order_manager.get_positions()
            
            # transients (DNS, timeout) return None or False status
            if pos_resp is None or not pos_resp.get('status'):
                logger.warning("⚠️ Sync State: API failure. Skipping sync to preserve local state.")
                if pos_resp and ("Access denied" in str(pos_resp.get('message', '')) or "AB1004" in str(pos_resp.get('message', ''))):
                    rate_limiter.trigger_circuit_breaker()
                return

            # If we reach here, the API call was successful
            found_active = None
            pos_data = pos_resp.get('data') or []
            
            for pos in pos_data:
                if (pos.get('symbolname') == 'NIFTY' and 
                    pos.get('producttype') == 'INTRADAY' and 
                    int(pos.get('netqty', 0)) != 0):
                    
                    symbol = pos['tradingsymbol']

                    # 📅 EXPIRY GUARD: skip broker positions from past expiry dates
                    if trade_repo._is_symbol_expired(symbol):
                        logger.warning(f"⏩ [Momentum] Skipping past-expiry contract from broker positions: {symbol}")
                        continue
                    
                    # 🛡️ STRATEGY FILTER GUARD: Does this position belong to another strategy in DB?
                    existing_trade = trade_repo.get_active_trade(symbol=symbol, mode="LIVE")
                    if existing_trade and existing_trade.get('strategy') not in ["MOMENTUM"]:
                        logger.info(f"⏩ [Momentum] Skipping {symbol} (Belongs to Strategy: {existing_trade.get('strategy')})")
                        continue
                    
                    qty = int(pos['netqty'])
                    
                    found_active = {
                        'leg': "CE" if "CE" in pos.get('tradingsymbol', '') else "PE", 
                        'symbol': symbol,
                        'token': pos['symboltoken'],
                        'qty': abs(qty),
                        'entry_price': float(pos['avgnetprice']),
                        'sl_price': float(pos['avgnetprice']) - min(20, float(pos['avgnetprice']) * 0.2) if self.active_position is None else self.active_position.get('sl_price', 0)
                    }
                    
                    # Sync internal trackers with found active to preserve pre-existing DB mapping
                    if existing_trade and existing_trade.get('strategy') == "MOMENTUM":
                        found_active['id'] = existing_trade['id']
                        found_active['sl_price'] = existing_trade.get('sl_price', found_active['sl_price'])
                        found_active['sl_order_id'] = existing_trade.get('sl_order_id')
                    
                    if self.active_position is None:
                        logger.info(f"♻️ RECOVERY: Found Active Trade on Broker! {found_active['symbol']}")
                    
                    break
            
            if found_active:
                if self.active_position is None:
                    self.active_position = found_active
                    logger.info(f"♻️ RECOVERY: Found Active Trade on Broker! {found_active['symbol']}")
                else:
                    # Maintain local source of truth for entry price
                    pass
            
            elif self.active_position is not None:
                # Check for broker-side SL hit
                sl_oid = self.active_position.get('sl_order_id')
                if sl_oid:
                    status_info = self.order_manager.get_order_status(sl_oid)
                    if status_info and status_info.get('status') == 'COMPLETE':
                        fill_price = status_info.get('price', self.active_position['sl_price'])
                        logger.info(f"🛡️ SYNC: Broker-Side SL Hit detected for {self.active_position['symbol']} @ ₹{fill_price}")
                        self._last_sl_hit_time = time.time()
                        
                        trade_id = self.active_position.get('id')
                        entry_p = self.active_position['entry_price']
                        qty = self.active_position['qty']
                        pnl = (fill_price - entry_p) * qty
                        
                        trade_repo.close_trade(trade_id=trade_id, symbol=self.active_position['symbol'], exit_price=fill_price, pnl=round(pnl, 2), exit_reason="BROKER_SL_HIT")
                        self.active_position = None
                        return

                logger.warning("⚠️ SYNC: Active Position closed externally! Resetting State.")
                trade_repo.close_trade(symbol=self.active_position['symbol'], exit_reason="EXTERNAL_SYNC_RESET")
                self.active_position = None
            
            if self.active_position and 'id' not in self.active_position:
                db_trade = trade_repo.get_active_trade(mode="LIVE", strategy="MOMENTUM")
                if db_trade and db_trade['symbol'] == self.active_position['symbol']:
                    self.active_position['id'] = db_trade['id']
                    self.active_position['partially_booked'] = db_trade.get('partially_booked', False)
                    self.active_position['sl_order_id'] = db_trade.get('sl_order_id')
                    logger.info(f"Sync: Linked to DB Trade ID {db_trade['id']} | SL-OID: {self.active_position['sl_order_id']} | Partial: {self.active_position['partially_booked']}")
                     
        except Exception as e:
            logger.error(f"Sync State Error: {e}")
            if "Access denied" in str(e) or "AB1004" in str(e):
                from bot.utils.rate_limiter import rate_limiter
                rate_limiter.trigger_circuit_breaker()

    def stop(self):
        """Signals the loop to stop and closes open positions."""
        self.running = False
        logger.info("[Control] Stop Requested from API.")
        
        if self.active_position:
            logger.warning("[Control] 🛑 Force Closing Open Position due to Stop Signal.")
            self.close_position("USER_STOPPED")

    def execute(self, expiry, action="BUY"):
        """
        Momentum Logic (EMA Crossover + RSI)
        """
        logger.info(f"--- EMA CROSSOVER + RSI STRATEGY ({expiry}) ---")
        
        # 0. Sync and Recover
        self.sync_state()

        # 0. Global Safety Guards (Strict Enforcement)
        if not self.gatekeeper.is_market_open():
            logger.warning("Momentum: 🛑 Execution Aborted - Market is Closed.")
            return
        if self.gatekeeper.is_blackout_period():
            logger.info("Momentum: ⏸️ Execution Suspended - Mid-day Blackout.")
            return
        if not self.gatekeeper.check_max_daily_loss(0.0):
            if self.gatekeeper.last_breaker_triggered == "PROFIT_PROTECTION":
                logger.critical("Momentum: 🛑 Execution Blocked - Profit Protection locked.")
            else:
                logger.critical("Momentum: 🛑 Execution Blocked - Max Daily Loss reached.")
            return

        if not self.gatekeeper.check_funds(required_margin_per_lot=5000): return

        logger.info("Starting Smart Monitor Loop (Safety: 1s | Trend: 5m Sync)...")
        
        now = datetime.datetime.now()
        minute = now.minute
        remainder = minute % 5
        minutes_to_add = 5 - remainder
        next_check = now + datetime.timedelta(minutes=minutes_to_add)
        next_check = next_check.replace(second=5, microsecond=0)
        
        if not self.running:
            logger.info("[Control] Stopping Strategy Loop.")
            return

        try:
            logger.info("📡 Performing Initial Market Analysis Pulse...")
            trend, ema9, ema21, rsi, adx, atr, regime, bbw = self.analyze_market_trend()
            htf_trend = self.calculate_htf_trend()
            
            # Initial pulse now gets bbw directly from analyze_market_trend

            pcr = self.oi_data.get('pcr', 0.0)
            sentiment_bias = self.oi_data.get('bias', 'NEUTRAL')

            self.last_analysis = {
                "ema9": ema9, "ema21": ema21, "rsi": rsi, 
                "htf_trend": htf_trend, "adx": adx,
                "atr": atr, "regime": regime,
                "bbw": bbw, # Use shared BBW return value
                "pcr": pcr, "sentiment": sentiment_bias
            }
            self.export_state()
            logger.info(f"✅ Initial Pulse Complete. Regime: {regime}")
        except Exception as e:
            logger.error(f"Initial Pulse Error: {e}")

        while self.running:
            if not self.running:
                logger.info("[Control] Stopping Strategy Loop.")
                break

            try:
                # --- FAST LOOP (Safety & Management) ---
                if not self.dry_run and time.time() - self.last_sync_time > 15:
                     self.sync_state()
                     self.last_sync_time = time.time()

                if self.active_position:
                    # SAFETY KILL SWITCH (Zero-Throttle)
                    # We check P&L every iteration (0.5s) to prevent runaway losses.
                    try:
                        token = self.active_position['token']
                        symbol = self.active_position['symbol']
                        entry_price = self.active_position['entry_price']
                        qty = self.active_position['qty']

                        curr_ltp = self.data_fetcher.get_ltp(token, exchange="NFO")

                        if curr_ltp and curr_ltp > 0:
                            curr_unrealized_pnl = (curr_ltp - entry_price) * qty
                            # Phase 5: Push unrealized P&L to metrics exporter
                            try:
                                from bot.core.metrics_exporter import metrics_exporter
                                metrics_exporter.push_unrealized(symbol, curr_unrealized_pnl)
                            except Exception:
                                pass
                            # Global Safety Check (Realized + This Unrealized)
                            if not self.gatekeeper.check_max_daily_loss(curr_unrealized_pnl):
                                if self.gatekeeper.last_breaker_triggered == "PROFIT_PROTECTION":
                                    logger.critical(f"🛑 EMERGENCY EXIT: Profit Protection Triggered.")
                                    self.close_position("PROFIT_PROTECTION")
                                else:
                                    logger.critical(f"🛑 EMERGENCY EXIT: Global Loss Limit Breached.")
                                    self.close_position("MAX_DAILY_LOSS")
                                break
                    except Exception as e:
                        logger.error(f"Global Safety Check Error: {e}")

                    # TRAILING STOP & SYNC (Throttled)
                    if time.time() - self.last_trailing_check > 3:
                        self.check_trailing_stop()
                        self.last_trailing_check = time.time()

                now_time = datetime.datetime.now().time()
                if self.trailing_manager.is_killswitch_time():
                    logger.info("⏰ Time Killswitch (15:10) triggered. Force exiting all positions.")
                    if self.active_position:
                        self.close_position("TIME_KILLSWITCH")
                    break

                if not self.dry_run and now_time >= datetime.time(*Config.STRATEGY_EXIT_TIME):
                    logger.info(f"Market Closed ({datetime.time(*Config.STRATEGY_EXIT_TIME).strftime('%H:%M')}). Stopping Strategy.")
                    if self.active_position:
                        self.close_position("TIME_EXIT")
                    break

                today_str = datetime.datetime.now().strftime("%d%b%Y").upper()
                is_expiry_day = (expiry == today_str)
                if is_expiry_day and now_time >= datetime.time(*Config.EXPIRY_ENTRY_BLOCK_TIME) and not self.active_position:
                    logger.info(f"Expiry Day Block Time reached ({datetime.time(*Config.EXPIRY_ENTRY_BLOCK_TIME).strftime('%H:%M')}) with no active position. Exiting Momentum strategy.")
                    break

                # --- SLOW LOOP (Trend Analysis) ---
                if datetime.datetime.now() >= next_check:
                    # Recalculate next check boundary
                    _now = datetime.datetime.now()
                    _minute = _now.minute
                    _remainder = _minute % 5
                    _minutes_to_add = 5 - _remainder
                    next_check = _now + datetime.timedelta(minutes=_minutes_to_add)
                    next_check = next_check.replace(second=5, microsecond=0)

                    # 1. Check Max Daily Loss Limit before running analysis
                    if not self.gatekeeper.check_max_daily_loss(0.0):
                        logger.critical("Momentum: 🛑 Skipped trend check — Max Daily Loss limit reached.")
                        time.sleep(10)
                        continue

                    # 2. Check Shared Intelligence Freshness
                    state_file = "data/market_analysis.json"
                    if not self.dry_run and os.path.exists(state_file):
                        file_age = time.time() - os.path.getmtime(state_file)
                        if file_age > 600:  # 10 minutes
                            logger.warning(f"Momentum: 🛑 Skipped setup check because shared market analysis is stale ({file_age / 60:.1f} mins old).")
                            time.sleep(10)
                            continue

                    if not self.dry_run:
                        try:
                            spot_tok = get_instrument(Config.ACTIVE_SYMBOL).analysis_token
                            _df_fresh = self.data_fetcher.fetch_latest_candles(spot_tok)
                            if _df_fresh is not None and not _df_fresh.empty:
                                _last_ts = _df_fresh.iloc[-1]['timestamp']
                                _last_ts_dt = _last_ts.to_pydatetime() if hasattr(_last_ts, 'to_pydatetime') else _last_ts
                                _last_ts_naive = _last_ts_dt.astimezone().replace(tzinfo=None) if _last_ts_dt.tzinfo is not None else _last_ts_dt
                                _age_mins = (datetime.datetime.now() - _last_ts_naive).total_seconds() / 60
                                if _age_mins > 15:
                                    logger.warning(f"Momentum: 🛑 Skipped setup check because candle data is stale (age: {_age_mins:.1f} mins).")
                                    time.sleep(10)
                                    continue
                        except Exception as e:
                            logger.warning(f"Failed to verify candle freshness: {e}")

                    logger.info(f"⏰ Candle Closed. Running Trend Analysis...")
                    
                    trend, ema9, ema21, rsi, adx, atr, regime, bbw = self.analyze_market_trend()
                    htf_trend = self.calculate_htf_trend()
                    
                    # BBW is now part of the shared analysis returned by analyze_market_trend()
                    bbw = float(self.last_analysis.get('bbw', 0.0))
                    
                    pcr = self.oi_data.get('pcr', 0.0)
                    sentiment_bias = self.oi_data.get('bias', 'NEUTRAL')

                    self.last_analysis = {
                        "ema9": ema9, "ema21": ema21, "rsi": rsi, 
                        "htf_trend": htf_trend, "adx": adx,
                        "atr": atr, "regime": regime,
                        "bbw": round(float(bbw), 4), "pcr": pcr, "sentiment": sentiment_bias
                    }
                    self.export_state()
                    
                    logger.info(f"[Analysis] {trend} | Regime: {regime} | EMA9: {ema9:.2f} | RSI: {rsi:.2f} | ATR: {atr:.2f}")
                    
                    logger.info(f"[Analysis] 5m Trend: {trend} | 15m Trend: {htf_trend} | EMA9: {ema9:.2f} | RSI: {rsi:.2f}")
                    logger.info(f"[Active] {self.active_position['leg'] if self.active_position else 'None'}")
                    
                    if trend == "NEUTRAL" and rsi == 0 and self.active_position:
                        self.data_failure_count += 1
                        logger.warning(f"⚠️ Blind Mode Active ({self.data_failure_count}/3). Keeping Position.")
                        
                        if self.data_failure_count >= 3:
                            logger.error("🛑 Max Data Failures Reached. Force Exiting.")
                            self.close_position("DATA_LOSS_SAFETY")
                            break 
                    else:
                        self.data_failure_count = 0 
                    
                    if not self.active_position:
                        _tier_now = Config.get_tier(self.gatekeeper.get_current_capital())
                        _bbw = float(self.last_analysis.get('bbw', 0.0))
                        _oi_bias = self.last_analysis.get('sentiment', 'NEUTRAL')
                        _adx_now = self.last_analysis.get('adx', 0)
                        
                        # Phase 3: STRICT Multi-Timeframe Confluence Tracking
                        checks = []
                        
                        # 1. ADX Strength (dynamic chop hours boost)
                        current_time = datetime.datetime.now().time()
                        is_chop_hours = (datetime.time(10, 30) <= current_time < datetime.time(11, 30)) or \
                                         (datetime.time(13, 0) <= current_time < datetime.time(13, 30))
                        required_adx = _tier_now.min_adx_to_trade
                        if is_chop_hours:
                            required_adx += 5.0
                        
                        if _adx_now >= required_adx:
                            checks.append(("ADX Strength", True, f"{_adx_now:.1f}"))
                        else:
                            label = f"{_adx_now:.1f} < {required_adx:.1f} (Chop Hour Boosted)" if is_chop_hours else f"{_adx_now:.1f} < {required_adx:.1f}"
                            checks.append(("ADX Strength", False, label))

                        # 2. Trend Presence
                        if trend in ("BULLISH", "BEARISH"):
                            checks.append(("Signal Presence", True, trend))
                        else:
                            checks.append(("Signal Presence", False, "NEUTRAL"))

                        if trend != "NEUTRAL":
                            # 3. MTF Confluence
                            if trend == htf_trend:
                                checks.append(("MTF Alignment", True, htf_trend))
                            else:
                                checks.append(("MTF Alignment", False, f"5m:{trend} vs 15m:{htf_trend}"))

                            # 4. Squeeze Filter
                            _bbw_threshold = _tier_now.min_bbw_to_trade
                            if _bbw >= _bbw_threshold:
                                checks.append(("Squeeze Filter", True, f"{_bbw:.4f}"))
                            else:
                                checks.append(("Squeeze Filter", False, f"BBW={_bbw:.4f} (Too Tight, need >{_bbw_threshold})"))

                            # 5. Sentiment Filter
                            sentiment_ok = (trend == "BULLISH" and _oi_bias != "BEARISH") or \
                                          (trend == "BEARISH" and _oi_bias != "BULLISH")
                            if sentiment_ok:
                                checks.append(("Sentiment Bias", True, _oi_bias))
                            else:
                                checks.append(("Sentiment Bias", False, f"Opposite (Trend:{trend} vs OI:{_oi_bias})"))

                            # 6. RSI Buffer
                            if trend == "BULLISH":
                                rsi_ok = rsi < 70
                                checks.append(("RSI Limit", rsi_ok, f"{rsi:.1f} < 70" if rsi_ok else f"{rsi:.1f} > 70"))
                            else:
                                rsi_ok = rsi > 30
                                checks.append(("RSI Limit", rsi_ok, f"{rsi:.1f} > 30" if rsi_ok else f"{rsi:.1f} < 30"))
                            
                            # 7. PRO-TRADER: Volume Confirmation
                            # Fetch fresh 5m candles for volume analysis
                            spot_tok = get_instrument(Config.ACTIVE_SYMBOL).analysis_token
                            _df_vol = self.data_fetcher.fetch_latest_candles(spot_tok, interval="FIVE_MINUTE", days=1)
                            if _df_vol is not None and not _df_vol.empty:
                                avg_vol = _df_vol['volume'].tail(6).iloc[:-1].mean()
                                curr_vol = _df_vol['volume'].iloc[-1]
                                vol_ok = curr_vol > (avg_vol * 1.1)
                            else:
                                vol_ok = False # Fail-safe: No data = No volume confirmation
                                
                            checks.append(("Volume Confirmation", vol_ok, f"Confirmed" if vol_ok else "Low Volume/No Data"))

                        # Calculate Confluence Score
                        passed_names = [c[0] for c in checks if c[1]]
                        score = len(passed_names)
                        total = len(checks)
                        
                        # --- MANDATORY CHECKS ---
                        mtf_aligned = "MTF Alignment" in passed_names
                        signal_valid = "Signal Presence" in passed_names
                        
                        failed = [c for c in checks if not c[1]]
                        if failed:
                            logger.info(f"🔍 Confluence: {score}/{total} | Missing: {', '.join([f'{c[0]} [{c[2]}]' for c in failed])}")
                        
                        # Execution Logic: 6/7 score AND Mandatory Alignment
                        if score >= 6 and mtf_aligned and signal_valid:
                            if not self.active_position:
                                logger.info(f"🔥 A+ SETUP DETECTED: Confluence {score}/{total} with MTF & Volume. Firing Entry.")
                                if trend == "BULLISH":
                                    self.enter_position(expiry, "CE")
                                elif trend == "BEARISH":
                                    self.enter_position(expiry, "PE")
                            else:
                                # SCALE-IN LOGIC: If already in a winner, add to it!
                                current_stage = self.active_position.get('ladder_stage', 0)
                                if current_stage >= 2 and not self.active_position.get('scaled_in', False):
                                    logger.info(f"🚀 SCALE-IN SIGNAL: High Confluence in a Stage {current_stage} winner. Adding 1 lot.")
                                    self.enter_position(expiry, self.active_position['leg'], is_scale_in=True)
                        elif trend != "NEUTRAL":
                            reason = "MTF Misalignment" if not mtf_aligned else f"Low Confluence ({score}/7)"
                            logger.info(f"⏸️ Skipping — {reason}. Waiting for A+ setup.")
                    
                        # --- INSTITUTIONAL LEVEL AWARENESS & EXIT LOGIC ---
                        if self.active_position:
                            levels = levels_provider.get_levels()
                            spot_tok = get_instrument(Config.ACTIVE_SYMBOL).analysis_token
                            nifty_ltp = self.data_fetcher.get_ltp(spot_tok)
                            is_retesting_level = False
                            current_leg = self.active_position['leg']
                            current_stage = self.active_position.get('ladder_stage', 0)
                            
                            if levels and nifty_ltp:
                                pdh = levels.get('pdh', 0)
                                pdl = levels.get('pdl', 0)
                                # If we are within 0.1% of PDH/PDL after a breakout, we are retesting
                                if current_leg == "CE" and nifty_ltp > pdh and nifty_ltp < pdh * 1.002:
                                    is_retesting_level = True
                                    logger.info(f"🏛️ [Levels] Nifty is retesting PDH ({pdh}). Holding through potential bounce.")
                                elif current_leg == "PE" and nifty_ltp < pdl and nifty_ltp > pdl * 0.998:
                                    is_retesting_level = True
                                    logger.info(f"🏛️ [Levels] Nifty is retesting PDL ({pdl}). Holding through potential bounce.")

                            is_exhausted = self.last_analysis.get('is_exhausted', False)
                            
                            # --- THE POWER-TREND EXIT LOGIC ---
                            
                            # 1. Exhaustion Exit (Peak Booking)
                            if is_exhausted:
                                logger.info(f"🔥 EXHAUSTION DETECTED (RSI Extreme). Booking 50% profit immediately to capture peak.")
                                half_qty = max(1, self.active_position['qty'] // 2)
                                self.close_position("EXHAUSTION", override_qty=half_qty)
                            
                            # 2. Hierarchical Reversal Exit
                            if current_leg == "CE" and trend == "BEARISH":
                                # If in Stage 1 or above, OR retesting a major level, we ignore 5m trend reversals
                                if current_stage >= 1 or is_retesting_level:
                                    reason = f"Stage {current_stage}" if current_stage >= 1 else "Level Retest"
                                    logger.info(f"Signal: 5m Trend Reversed to BEARISH, but {reason} is active. Ignoring noise.")
                                else:
                                    # For trades not yet in profit, require 15m HTF confirmation to prevent fake-outs
                                    if htf_trend == "BEARISH":
                                        logger.info("Signal: Trend Reversed to BEARISH (Confirmed by 15m HTF). Exiting CE.")
                                        self.close_position("REVERSAL")
                                    else:
                                        logger.info(f"Signal: 5m Trend Reversed to BEARISH, but 15m HTF is still {htf_trend}. Keeping CE.")

                            elif current_leg == "PE" and trend == "BULLISH":
                                if current_stage >= 1 or is_retesting_level:
                                    reason = f"Stage {current_stage}" if current_stage >= 1 else "Level Retest"
                                    logger.info(f"Signal: 5m Trend Reversed to BULLISH, but {reason} is active. Ignoring noise.")
                                else:
                                    if htf_trend == "BULLISH":
                                        logger.info("Signal: Trend Reversed to BULLISH (Confirmed by 15m HTF). Exiting PE.")
                                        self.close_position("REVERSAL")
                                    else:
                                        logger.info(f"Signal: 5m Trend Reversed to BULLISH, but 15m HTF is still {htf_trend}. Keeping PE.")

                    now = datetime.datetime.now()
                    minute = now.minute
                    remainder = minute % 5
                    minutes_to_add = 5 - remainder
                    next_check = now + datetime.timedelta(minutes=minutes_to_add)
                    next_check = next_check.replace(second=5, microsecond=0)
                    logger.info(f"⏳ Next Trend Check scheduled for: {next_check.strftime('%H:%M:%S')}")

                time.sleep(0.5)
                
            except KeyboardInterrupt:
                logger.info("User Manual Stop.")
                break
            except Exception as e:
                logger.error(f"Loop Error: {e}")
                time.sleep(1)

    def analyze_market_trend(self):
        from backend.market_service import market_service
        market_data = market_service.get_market_data()
        if market_data and 'oi_data' in market_data:
            self.oi_data = market_data['oi_data']
        analysis = market_data.get('analysis', {})
        
        if analysis and analysis.get('regime') != 'UNKNOWN':
            return (
                analysis.get('trend', 'NEUTRAL'),
                analysis.get('ema9', 0),
                analysis.get('ema21', 0),
                analysis.get('rsi', 0),
                analysis.get('adx', 0),
                analysis.get('atr', 20.0),
                analysis.get('regime', 'UNKNOWN'),
                analysis.get('bbw', 0.0)
            )

        is_mock_api = self.api.__class__.__name__ == 'MockSmartConnect'
        
        if is_mock_api:
            df = self.get_mock_df()
        else:
            spot_tok = get_instrument(Config.ACTIVE_SYMBOL).analysis_token
            df = self.data_fetcher.fetch_latest_candles(spot_tok)
            # Cache df for reuse within the same analysis cycle (e.g., BBW calculation)
            self._last_df = df
            
        if df is None or df.empty: 
            return "NEUTRAL", 0, 0, 0, 0, 0, "UNKNOWN", 0.0
        
        regime_meta = self.regime_classifier.classify(df)
        
        now = time.time()
        if now - self.last_oi_scan > 300: 
            try:
                if market_data.get('oi_data'):
                    self.oi_data = market_data['oi_data']
                    self.last_oi_scan = now
                else:
                    ltp = df.iloc[-1]['close']
                    instr = get_instrument(Config.ACTIVE_SYMBOL)
                    strike = int(round(ltp / instr.strike_step) * instr.strike_step)
                    expiry = get_next_weekly_expiry()
                    self.oi_data = self.oi_analyzer.get_market_sentiment(expiry, strike)
                    self.last_oi_scan = now
            except Exception as e:
                logger.error(f"Periodic OI Scan Error: {e}")

        signal = "NEUTRAL"
        if regime_meta['regime'] == "TRENDING":
            signal = regime_meta['trend']
        
        # Calculate BBW locally as fallback
        bbw = self.calculate_bbw(df).iloc[-1]
        
        return (
            signal, 
            regime_meta['ema9'], 
            regime_meta['ema21'], 
            regime_meta['rsi'], 
            regime_meta['adx'], 
            regime_meta['atr'], 
            regime_meta['regime'],
            bbw
        )

    def calculate_htf_trend(self):
        """
        Derives Higher-Timeframe (15m) trend direction.
        Prefers shared market_analysis.json (written by MASTER backend every 3 min)
        to avoid firing a separate FIFTEEN_MINUTE REST call that triggers AB1004.
        Falls back to REST only if the shared file is stale or missing.
        """
        # --- PRIMARY: Read from shared intelligence file (no API call) ---
        try:
            state_file = os.path.join(os.getcwd(), "data", "market_analysis.json")
            if os.path.exists(state_file):
                age = time.time() - os.path.getmtime(state_file)
                if age < 600:  # Use if < 10 mins old
                    with open(state_file, "r") as f:
                        shared = json.load(f)
                    analysis = shared.get("analysis", {})
                    trend = analysis.get("trend", "NEUTRAL")
                    regime = analysis.get("regime", "UNKNOWN")
                    if regime != "UNKNOWN":
                        logger.debug(f"[HTF] Using shared intelligence: {trend}")
                        return trend
        except Exception as e:
            logger.warning(f"[HTF] Could not read shared state: {e}")

        spot_tok = get_instrument(Config.ACTIVE_SYMBOL).analysis_token
        df = self.data_fetcher.fetch_latest_candles(spot_tok, interval="FIFTEEN_MINUTE")

        if df is None or len(df) < 3:
            return "NEUTRAL"

        df['EMA9'] = df['close'].ewm(span=min(9, len(df)), adjust=False).mean()
        df['EMA21'] = df['close'].ewm(span=min(21, len(df)), adjust=False).mean()

        # Use last confirmed closed candle (iloc[-2]) if available, else last
        idx = -2 if len(df) >= 2 else -1
        last_closed = df.iloc[idx]
        ema9 = last_closed['EMA9']
        ema21 = last_closed['EMA21']

        if ema9 > ema21: return "BULLISH"
        if ema9 < ema21: return "BEARISH"
        return "NEUTRAL"

    def calculate_atr(self, df, period=14):
        try:
            df = df.copy()
            df['tr1'] = df['high'] - df['low']
            df['tr2'] = abs(df['high'] - df['close'].shift(1))
            df['tr3'] = abs(df['low'] - df['close'].shift(1))
            df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
            atr = df['tr'].ewm(alpha=1/period, adjust=False).mean()
            return atr.fillna(0)
        except Exception as e:
            logger.error(f"ATR Calc Error: {e}")
            return pd.Series([0]*len(df))

    def calculate_bbw(self, df, period=20, std=2):
        try:
            sma = df['close'].rolling(window=period).mean()
            std_dev = df['close'].rolling(window=period).std()
            upper = sma + (std * std_dev)
            lower = sma - (std * std_dev)
            
            bbw = (upper - lower) / sma
            return bbw.fillna(0)
        except Exception as e:
            logger.error(f"BBW Calc Error: {e}")
            return pd.Series([0]*len(df))

    def calculate_adx(self, df, period=14):
        try:
            df = df.copy()
            df['up_move'] = df['high'] - df['high'].shift(1)
            df['down_move'] = df['low'].shift(1) - df['low']
            
            df['pdm'] = 0.0
            df['ndm'] = 0.0
            
            df.loc[(df['up_move'] > df['down_move']) & (df['up_move'] > 0), 'pdm'] = df['up_move']
            df.loc[(df['down_move'] > df['up_move']) & (df['down_move'] > 0), 'ndm'] = df['down_move']
            
            df['tr1'] = df['high'] - df['low']
            df['tr2'] = abs(df['high'] - df['close'].shift(1))
            df['tr3'] = abs(df['low'] - df['close'].shift(1))
            df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
            
            df['atr'] = df['tr'].ewm(alpha=1/period, adjust=False).mean()
            
            df['pdm_s'] = df['pdm'].ewm(alpha=1/period, adjust=False).mean()
            df['ndm_s'] = df['ndm'].ewm(alpha=1/period, adjust=False).mean()
            
            df['pdi'] = 100 * (df['pdm_s'] / df['atr'])
            df['ndi'] = 100 * (df['ndm_s'] / df['atr'])
            
            df['dx'] = 100 * abs(df['pdi'] - df['ndi']) / (df['pdi'] + df['ndi'])
            
            return df['dx'].ewm(alpha=1/period, adjust=False).mean().fillna(0)
            
        except Exception as e:
            logger.error(f"ADX Calc Error: {e}")
            return pd.Series([0]*len(df))

    def calculate_rsi(self, df, period=14):
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/period, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/period, adjust=False).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50)

    def enter_position(self, expiry, leg, is_scale_in=False):
        # 0. Check daily loss limit
        if not self.gatekeeper.check_max_daily_loss(0.0):
            logger.warning("Momentum: 🛑 Skipped entry because Max Daily Loss limit has been reached.")
            return

        # Post-SL cooldown: 5 min after any SL hit, no re-entry (matches gamma blast gate).
        _SL_COOLDOWN_SECS = 300
        if self._last_sl_hit_time > 0 and time.time() - self._last_sl_hit_time < _SL_COOLDOWN_SECS:
            _remaining = int(_SL_COOLDOWN_SECS - (time.time() - self._last_sl_hit_time))
            logger.info(f"Momentum: ⏳ Post-SL cooldown — {_remaining}s remaining. Skipping entry.")
            return

        # 1. RISK CALCULATION
        nifty_ltp = self.get_nifty_ltp()
        if not nifty_ltp:
            logger.error("Could not fetch Nifty LTP for Entry.")
            return

        atr = self.last_analysis.get('atr', 20.0)
        if atr == 0: atr = 20.0

        # --- ADAPTIVE RSI OVEREXTENSION FILTER ---
        # In TRENDING regimes, momentum often keeps RSI high/low for longer.
        # We relax the cap to 72 (from 68) during strong trends to avoid missing the move.
        _regime = self.last_analysis.get('regime', 'UNKNOWN')
        _rsi_cap = 72 if _regime == "TRENDING" else 68
        _rsi_floor = 28 if _regime == "TRENDING" else 32

        entry_rsi = self.last_analysis.get('rsi', 50.0)
        if leg == "CE" and entry_rsi > _rsi_cap:
            logger.warning(
                f"🛑 RSI Overextension Filter: RSI={entry_rsi:.1f} > {_rsi_cap} (overbought). "
                "Skipping CE entry — not chasing an exhausted move."
            )
            return
        if leg == "PE" and entry_rsi < _rsi_floor:
            logger.warning(
                f"🛑 RSI Overextension Filter: RSI={entry_rsi:.1f} < {_rsi_floor} (oversold). "
                "Skipping PE entry — not chasing an exhausted move."
            )
            return

        # ── FRESH OI ALIGNMENT GATE ────────────────────────────────────────────
        # Force-fetch current OI at every entry — never rely on the 5-min cache.
        # Root cause of 2026-04-09 bad trade: stale OI showed BEARISH while market
        # had already reversed to BULLISH after the first SL hit.
        _fresh_bias = 'NEUTRAL'
        _oi_speed = 0.0
        try:
            _fresh_atm = round(nifty_ltp / 50) * 50
            _fresh_oi = self.oi_analyzer.get_oi_velocity(expiry, _fresh_atm)
            _fresh_bias = _fresh_oi.get('bias', 'NEUTRAL')
            _oi_speed = _fresh_oi.get('pcr_velocity', 0.0)
            logger.info(
                f"🔍 Fresh OI at entry: bias={_fresh_bias} | "
                f"PCR={_fresh_oi.get('pcr', '?')} | Vel={_oi_speed:.4f}"
            )
        except Exception as _e:
            logger.warning(f"Fresh OI fetch failed: {_e}. Falling back to cached bias.")
            _fresh_bias = self.oi_data.get('bias', 'NEUTRAL')
            _oi_speed = 0.0

        _is_squeeze = False
        _squeeze_threshold = 0.03 if _regime == "TRENDING" else 0.05
        if leg == "CE" and _oi_speed > _squeeze_threshold:
            _is_squeeze = True
            logger.info(f"🔥 SQUEEZE DETECTED: PCR Velocity = {_oi_speed:.4f} (Short Covering). Permitting CE entry.")
        elif leg == "PE" and _oi_speed < -_squeeze_threshold:
            _is_squeeze = True
            logger.info(f"🔥 SQUEEZE DETECTED: PCR Velocity = {_oi_speed:.4f} (Long Unwinding). Permitting PE entry.")

        _is_extreme_trend = False  # Deactivated: Extreme Trend Override disabled to enforce strict OI sentiment alignment

        if not _is_squeeze and not _is_extreme_trend:
            if leg == "CE" and _fresh_bias == "BEARISH":
                logger.warning(
                    "🛑 OI Alignment Gate: Fresh OI=BEARISH — CE blocked. "
                    "Institutions are against the bullish thesis. Skipping."
                )
                return
            if leg == "PE" and _fresh_bias == "BULLISH":
                logger.warning(
                    "🛑 OI Alignment Gate: Fresh OI=BULLISH — PE blocked. "
                    "Institutions are against the bearish thesis. Skipping."
                )
                return

        # ── CANDLE MOMENTUM FILTER ──────────────────────────────────────────────
        # Require at least 2 of the last 3 completed 5-min candles to close in
        # the trade direction. Prevents entering on a single spike/bounce candle
        # that flips the EMAs without real sustained momentum behind it.
        _df_entry = None
        try:
            spot_tok = get_instrument(Config.ACTIVE_SYMBOL).analysis_token
            _df_entry = self.data_fetcher.fetch_latest_candles(spot_tok)
            if _df_entry is not None and len(_df_entry) >= 3:
                _last3 = _df_entry.tail(3)
                _bull_count = (_last3['close'] > _last3['open']).sum()
                _bear_count = (_last3['close'] < _last3['open']).sum()
                
                # In strong TRENDING regimes with HTF (15m) alignment, we relax confirmation.
                _htf_trend = self.calculate_htf_trend()
                _htf_aligned = (_htf_trend == ("BULLISH" if leg == "CE" else "BEARISH"))
                
                # SQUEEZE/EXTREME TREND BYPASS: Removed to guarantee candle confirmation under all overrides
                _min_candles = 1 if (_regime == "TRENDING" and _htf_aligned) else 2

                if leg == "CE" and _bull_count < _min_candles:
                    logger.warning(
                        f"🛑 Candle Momentum Filter: {_bull_count}/{_min_candles} bullish candles — "
                        "weak confirmation for CE entry. Skipping."
                    )
                    return
                if leg == "PE" and _bear_count < _min_candles:
                    logger.warning(
                        f"🛑 Candle Momentum Filter: {_bear_count}/{_min_candles} bearish candles — "
                        "weak confirmation for PE entry. Skipping."
                    )
                    return
                self._last_df = _df_entry  # Update cache for ADX slope check below
        except Exception as _e:
            logger.warning(f"Candle momentum filter error: {_e}")

        # ── VWAP POSITION FILTER ───────────────────────────────────────────────
        # VWAP is the primary intraday reference for institutions and HFTs.
        # CE entry when NIFTY is below VWAP = buying into institutional sell pressure.
        # PE entry when NIFTY is above VWAP = shorting into institutional buy flow.
        # We use the already-fetched 5-min candles to compute session VWAP — no extra API call.
        try:
            _df_vwap = _df_entry if _df_entry is not None else getattr(self, '_last_df', None)
            if _df_vwap is not None and len(_df_vwap) >= 1 and 'volume' in _df_vwap.columns:
                _vol = _df_vwap['volume']
                if _vol.sum() > 0:
                    _typical = (_df_vwap['high'] + _df_vwap['low'] + _df_vwap['close']) / 3
                    _vwap = (_typical * _vol).sum() / _vol.sum()
                    logger.info(f"📏 VWAP={_vwap:.1f} | NIFTY={nifty_ltp:.1f} | Leg={leg}")
                    # SQUEEZE BYPASS: Squeezes often happen when price is on the "wrong" side of VWAP before flipping it.
                    if _is_squeeze:
                        logger.info("🔥 Squeeze detected: Bypassing VWAP Filter.")
                    elif leg == "CE" and nifty_ltp < _vwap:
                        logger.warning(
                            f"🛑 VWAP Filter: NIFTY {nifty_ltp:.0f} < VWAP {_vwap:.0f} — "
                            "CE blocked. Price trading below institutional anchor."
                        )
                        return
                    elif leg == "PE" and nifty_ltp > _vwap:
                        logger.warning(
                            f"🛑 VWAP Filter: NIFTY {nifty_ltp:.0f} > VWAP {_vwap:.0f} — "
                            "PE blocked. Price trading above institutional anchor."
                        )
                        return
        except Exception as _e:
            logger.warning(f"VWAP filter error: {_e}")

        # ── ADX SLOPE FILTER ────────────────────────────────────────────────────
        # ADX must be rising (trend is strengthening, not exhausting).
        # ADX above the gate threshold but declining = bad entry timing.
        try:
            _df_adx = _df_entry if _df_entry is not None else getattr(self, '_last_df', None)
            if _df_adx is not None and len(_df_adx) >= 20:
                _adx_s = self.regime_classifier._calculate_adx(_df_adx)
                if len(_adx_s) >= 3:
                    curr_adx_m = _adx_s.iloc[-1]
                    prev_adx_m = _adx_s.iloc[-2]

                    # NOISE TOLERANCE: On parabolic days, minor ADX dips are expected noise.
                    _is_declining_m = False
                    
                    if _is_squeeze:
                         logger.info("🔥 Squeeze detected: Bypassing ADX Slope Filter.")
                    elif curr_adx_m > 45:
                        # Extreme trend: Allow significant cooling off (-3.0)
                        _is_declining_m = (curr_adx_m - prev_adx_m) < -3.0
                    elif curr_adx_m > 35:
                        # Strong trend: Allow moderate cooling off (-1.5)
                        _is_declining_m = (curr_adx_m - prev_adx_m) < -1.5
                    elif curr_adx_m > 25:
                        # Moderate trend: Allow small cooling off (-0.5)
                        _is_declining_m = (curr_adx_m - prev_adx_m) < -0.5
                    else:
                        # Developing trend: Require rising momentum (positive slope)
                        _is_declining_m = (curr_adx_m - prev_adx_m) < -0.05

                    if _is_declining_m:
                        logger.warning(
                            f"🛑 ADX Slope Filter: ADX declining "
                            f"({prev_adx_m:.2f} → {curr_adx_m:.2f}). "
                            "Trend is losing momentum — skipping entry."
                        )
                        return
        except Exception as _e:
            logger.warning(f"ADX slope filter error: {_e}")

        sl_points = 2 * atr

        direction = "LONG" if leg == "CE" else "SHORT"
        if _is_squeeze:
             logger.info("🔥 Squeeze detected: Bypassing Sentiment Risk Check.")
        elif not self.gatekeeper.check_sentiment_risk(direction):
             logger.warning(f"Trade Skipped due to Sentiment Risk.")
             return

        try:
             today_str = datetime.datetime.now().strftime("%d%b%Y").upper()
             if expiry == today_str:
                 now = datetime.datetime.now().time()
                 if not Config.TRADE_FULL_DAY and now >= datetime.time(*Config.EXPIRY_ENTRY_BLOCK_TIME):
                     logger.warning(f"⛔ Expiry Day Safety: Blocking new entries after {datetime.time(*Config.EXPIRY_ENTRY_BLOCK_TIME).strftime('%H:%M')}.")
                     return
        except Exception as e:
             logger.error(f"Expiry Guard Check Error: {e}")

        # Phase 3: Delta-Based Strike Selection (Target: 0.40 Delta)
        target_delta = 0.40
        from backend.market_service import market_service
        vix_data = market_service.get_market_data()
        if isinstance(vix_data, dict):
            vix = vix_data.get('vix', 15.0)
        else:
            vix = 15.0
        if not isinstance(vix, (int, float)) or vix <= 0:
            vix = 15.0
        
        from bot.utils.greeks import select_strike_by_delta
        active_sym = Config.ACTIVE_SYMBOL
        instr = get_instrument(active_sym)
        strike_diff = instr.strike_step

        strike, token, symbol = select_strike_by_delta(self.token_loader, nifty_ltp, expiry, vix, leg, target_delta, active_sym)
        
        if not strike or not token:
            logger.warning("⚠️ Delta-based strike selection failed. Falling back to ATR-based calculation.")
            atm_strike = round(nifty_ltp / strike_diff) * strike_diff
            if atr < 15:
                otm_offset = 0
            elif atr < 30:
                otm_offset = 1
            else:
                otm_offset = 2

            # --- IV RANK: OTM DEPTH REDUCTION ---
            iv_rank = self.gatekeeper.get_iv_rank()
            if iv_rank > 0.70 and otm_offset > 0:
                otm_offset = max(0, otm_offset - 1)

            # ── OTM CAP FOR SMALL/MICRO TIER ──
            _cap_tier = Config.get_tier(self.gatekeeper.get_current_capital())
            if _cap_tier.name in ("MICRO", "SMALL") and otm_offset > 1:
                otm_offset = 1

            strike_direction = 1 if leg == "CE" else -1
            strike = atm_strike + strike_direction * otm_offset * strike_diff
            token, symbol = self.token_loader.get_token(instr.name, expiry, strike, leg, instrument_type=instr.instrument_type, exchange=instr.option_exchange)

        if not token: 
            logger.error(f"Token not found for {strike} {leg}")
            return

        
        quote_ltp = 0
        try:
             from bot.utils.rate_limiter import rate_limiter
             rate_limiter.wait()
             q_resp = self.api.ltpData("NFO", symbol, token)
             if q_resp and q_resp.get('status'):
                 quote_ltp = float(q_resp['data']['ltp'])
        except Exception as e:
            logger.warning(f"Could not fetch option LTP for margin check: {e}")
            
        # Position Sizing based on Capital and Actual Option Price
        capital = self.gatekeeper.get_current_capital()
        if capital <= 0: capital = Config.SIMULATION_CAPITAL # Fallback

        # All risk parameters come from the capital tier — no hardcoded values
        tier = Config.get_tier(capital)

        risk_per_trade = capital * tier.risk_per_trade_pct
        if risk_per_trade < tier.min_risk_floor:
            risk_per_trade = tier.min_risk_floor

        # Standard proxy: Weekly Nifty option ATR is approximately 0.5x Index ATR
        option_sl_points = atr * 0.5
        if option_sl_points < 5: option_sl_points = 5

        # Calculate actual margin per lot. Fallback to half the tier threshold if LTP unknown.
        margin_per_lot = (quote_ltp * Config.NIFTY_LOT_SIZE) if quote_ltp > 0 else (tier.min_capital_threshold * 0.5)
        
        # Apply Compounding (Exponential Scaling) using real estimated cost
        if is_scale_in:
            lots = 1 # Only add 1 lot on scale-in for safety
            self.active_position['scaled_in'] = True
        else:
            lots = self.gatekeeper.get_compounded_lots(margin_per_lot=margin_per_lot, multiplier=self.risk_multiplier)
        
        # Phase 3: Capital & Lot Sizing (Uses Gatekeeper's dynamic tier-based calculation)
        # This allows for 4-5 lots on ₹35k capital, ensuring ₹1,500 profit is achievable.
        # (Hardcoded 1-2 lot restriction removed for better capital efficiency)

        qty = lots * Config.NIFTY_LOT_SIZE
        
        if is_scale_in:
             logger.info(f"🚀 SCALING IN: Adding 1 lot ({qty} qty) to existing {leg} position.")
        else:
             logger.info(f"⚖️ Sizing: ATR={atr:.2f} | Method=Exponential Compounding | Multiplier={self.risk_multiplier}x | Qty={qty} ({lots} lots)")

        # 1.5 Cost Viability Check (Small Account Protection)
        if not self.gatekeeper.check_trade_viability(quote_ltp, qty):
             return
            
        estimated_cost = quote_ltp * qty
        
        # No extra capping needed here. Compounding sizing already handles margin limits.

        if estimated_cost > 0:
             if not self.dry_run and not self.gatekeeper.check_trade_margin(estimated_cost):
                 logger.warning(f"Risk: Insufficient Funds (Cost: {estimated_cost})")
                 return
        
        # 3. SAFETY GATE: Instrument Cooldown (Anti-Revenge Trading)
        if not self.gatekeeper.check_instrument_cooldown(symbol):
            return
            
        # 4. Ready to place order
        logger.info(f">>> [Momentum] Entry Signal Confirmed for {symbol} @ ₹{quote_ltp}")
        
        trade_context = {
            'entry_ema9': self.last_analysis.get('ema9', 0),
            'entry_ema21': self.last_analysis.get('ema21', 0),
            'entry_rsi': self.last_analysis.get('rsi', 0),
            'entry_adx': self.last_analysis.get('adx', 0),
            'entry_atr': atr,
            'regime': self.last_analysis.get('regime', 'UNKNOWN'),
            'htf_trend': self.last_analysis.get('htf_trend', "N/A"),
            'entry_bbw': self.last_analysis.get('bbw', 0),
            'oi_pcr': self.last_analysis.get('pcr', 0),
            'oi_sentiment': self.last_analysis.get('sentiment', "NEUTRAL")
        }

        # Phase 3: Dynamic RR based on Market Regime
        # TRENDING:   Tight breakeven (1:1) but let winner run to 5:1 target.
        # RANGEBOUND: Fixed 1.5:1 target; exit at first sign of reversal.
        current_regime = self.last_analysis.get("regime", "UNKNOWN")
        if current_regime == "TRENDING":
            # Professional: robust SL (1.5x ATR), big target (7x ATR in index → ~3.5x in option)
            sl_option_pts  = option_sl_points * 1.5    # 1.5x ATR on option space for noise cushion
            tgt_option_pts = option_sl_points * 3.5    # ~3.5x option leverage for massive trend riding
            dynamic_rr     = "TRENDING_3.5:1.5"
        else:
            # Conservative: 1.5:1 in option space
            sl_option_pts  = option_sl_points
            tgt_option_pts = option_sl_points * 1.5
            dynamic_rr     = "RANGEBOUND_1.5:1"

        logger.info(
            f"📐 Dynamic RR ({current_regime}): SL={sl_option_pts:.1f}pts | "
            f"Target={tgt_option_pts:.1f}pts | Mode={dynamic_rr}"
        )

        actual_sl_points = sl_option_pts
        
        # --- HARD SL FLOOR (Tier-based safety cap) ---
        # We cap the SL to protect capital, but we also ensure a MINIMUM floor
        # so the trade has room to breathe on low premiums.
        max_allowed_sl_pts = quote_ltp * tier.sl_pct
        sl_floor = min(tier.min_sl_points, quote_ltp * 0.5) # Never floor > 50% of premium
        
        if actual_sl_points > max_allowed_sl_pts:
            # Truncate to the cap, but never below the floor
            new_sl = max(max_allowed_sl_pts, sl_floor)
            logger.warning(
                f"🛡️ Hard SL Triggered: Truncating {actual_sl_points:.1f}pts "
                f"to {new_sl:.1f}pts (Cap: {tier.sl_pct*100}% | Floor: {sl_floor}pts)"
            )
            actual_sl_points = new_sl

        sl_price = max(0.1, quote_ltp - actual_sl_points)
        target_price = quote_ltp + tgt_option_pts

        # Worst-case loss projection daily limit check
        worst_case_loss = actual_sl_points * qty
        if not self.gatekeeper.check_max_daily_loss(0.0, worst_case_new_loss=worst_case_loss):
            logger.critical(f"Momentum: 🛑 Skipped entry because worst-case loss of ₹{worst_case_loss:.2f} would breach daily limit.")
            return

        if self.dry_run:
            oid = self.order_manager.place_smart_limit(
                symbol, token, qty, quote_ltp,
                transaction_type="BUY",
                strategy_name="MOMENTUM"
            )

            self.active_position = {
                'leg': leg, 'symbol': symbol, 'qty': qty, 'token': token,
                'entry_price': quote_ltp,
                'sl_price': sl_price,
                'target_price': target_price,
                'dynamic_rr': dynamic_rr,
                'atr': option_sl_points,
                'context': trade_context
            }

            # Update DB Fill (Simulation)
            tid = self.order_manager.update_trade_fill(symbol, "MOMENTUM", quote_ltp)
            if tid: 
                self.active_position['id'] = tid
                trade_repo.update_trade_context(tid, trade_context)
            return

        try:
            # Snap to 0.05 tick size AND eliminate float artifacts (e.g. 116.60000000000001)
            _raw_limit = quote_ltp * (1.0 + tier.entry_slippage_pct)
            limit_price = round(round(_raw_limit / 0.05) * 0.05, 2)
                
            oid = self.order_manager.place_smart_limit(
                symbol, token, qty, limit_price, 
                transaction_type="BUY", 
                strategy_name="MOMENTUM"
            )
            
            if not oid:
                logger.error("❌ Smart-Limit Order Placement Failed! (API returned None).")
                return

            logger.info(f"Success: Order Placed: {oid}")
           
            # 2. Wait for fill
            fill_result = self.wait_for_fill(oid)
            
            if fill_result['status'] in ['REJECTED', 'CANCELLED']:
                logger.error(f"❌ Order {oid} was {fill_result['status']}. Reason: {fill_result.get('message', 'Unknown')}")
                return 

            fill_price = fill_result['price']
            if not fill_price:
                fill_price = quote_ltp
                logger.warning(f"Momentum: Fill price not captured, using estimate: ₹{fill_price}")

            actual_sl = max(0.1, fill_price - actual_sl_points)

            # 3. Finalize Local State & Update DB
            if is_scale_in:
                trade_id = self.active_position.get('id')
                if trade_id:
                    trade_repo.scale_in_position(trade_id, qty, fill_price)
                
                # Update Local State (Weighted Average)
                old_qty = self.active_position['qty']
                old_price = self.active_position['entry_price']
                new_qty = old_qty + qty
                new_avg = ((old_price * old_qty) + (fill_price * qty)) / new_qty
                
                self.active_position['qty'] = new_qty
                self.active_position['entry_price'] = round(new_avg, 2)
                
                # IMPORTANT: Update Broker SL for the NEW total quantity
                sl_oid = self.active_position.get('sl_order_id')
                if sl_oid and not self.dry_run:
                    self.order_manager.modify_sl_order(sl_oid, self.active_position['sl_price'], symbol, token, new_qty)
                
                logger.info(f"🚀 SCALE-IN COMPLETE: New Qty={new_qty} | New Avg={self.active_position['entry_price']}")
                return

            trade_id = self.order_manager.update_trade_fill(symbol, "MOMENTUM", fill_price, expected_price=quote_ltp)
            
            if trade_id:
                trade_repo.update_trade_context(trade_id, trade_context)

            actual_sl = max(0.1, fill_price - actual_sl_points)

            actual_target = fill_price + tgt_option_pts
            self.active_position = {
                'id': trade_id,
                'leg': leg, 'symbol': symbol, 'qty': qty, 'token': token,
                'entry_price': fill_price,
                'sl_price': actual_sl,
                'target_price': actual_target,
                'dynamic_rr': dynamic_rr,
                'atr': option_sl_points,
                'context': trade_context,
                'initial_risk': actual_sl_points
            }
            
            if trade_id:
                trade_repo.update_sl(trade_id, actual_sl)
            
            # Place Hard SL (Broker-Side)
            sl_oid = self.order_manager.place_sl_order(symbol, token, qty, actual_sl, leg)
            if sl_oid:
                self.active_position['sl_order_id'] = sl_oid
                if trade_id: trade_repo.update_sl_order_id(trade_id, sl_oid)
            # SL VERIFICATION: If SL placement failed, we cannot hold the position safely.
            if not sl_oid and not self.dry_run:
                logger.critical(f"🚨 MOMENTUM: SL placement FAILED for {symbol}. Emergency exiting position for safety!")
                mode = "PAPER" if self.dry_run else "LIVE"
                self.order_manager.place_market(
                    symbol=symbol, token=token, qty=qty, 
                    transaction_type="SELL", strategy_name="MOMENTUM", mode=mode
                )
                return

            if sl_oid:
                self.active_position['sl_order_id'] = sl_oid
                if trade_id:
                    trade_repo.update_sl_order_id(trade_id, sl_oid)
                logger.info(f"🛡️ Broker-Side SL Placed: {sl_oid}")
            
            # Notify
            notifier.notify_trade_entry("MOMENTUM", symbol, "BUY", qty, fill_price, sl=actual_sl, target=actual_target)
                
        except Exception as e:
            logger.error(f"Enter Order Failure: {e}")

    def wait_for_fill(self, order_id):
        """Uses WebSocket Order Feed for sub-second fill detection."""
        if self.dry_run: return {'status': 'FILLED', 'price': 100.0}
        
        from bot.core.order_feed import order_feed
        logger.info(f">>> [Momentum] Waiting for WebSocket Fill Event ({order_id})...")
        
        result = order_feed.wait_for_fill(order_id, timeout=10)
        
        if result['status'] == 'TIMEOUT':
             logger.warning(f"⚠️ Order {order_id} fill TIMEOUT via WebSocket.")
        
        return result

    def close_position(self, reason, override_qty=None, exit_type="MARKET"):
        if not self.active_position: return
        
        symbol = self.active_position['symbol']
        token = self.active_position['token']
        qty = override_qty if override_qty else self.active_position['qty']
        
        is_partial = override_qty is not None and override_qty < self.active_position['qty']
        
        logger.info(f"Exit: Closing {qty} shares of {symbol} (Reason: {reason}) | Type: {exit_type}")
        
        exit_price = 0
        try:
             from bot.utils.rate_limiter import rate_limiter
             rate_limiter.wait()
             q_resp = self.api.ltpData("NFO", symbol, token)
             if q_resp and q_resp.get('status'):
                 exit_price = float(q_resp['data']['ltp'])
        except: pass
        if exit_price == 0: exit_price = self.active_position.get('entry_price', 0)

        # Cancel Pending Broker SL
        sl_oid = self.active_position.get('sl_order_id')
        if sl_oid and not self.dry_run:
            cancel_success = self.order_manager.cancel_order(sl_oid, variety="STOPLOSS")
            if not cancel_success:
                # If cancel failed, check if it was already filled
                sl_status = self.order_manager.get_order_status(sl_oid)
                if sl_status and sl_status.get('status') in ['FILLED', 'COMPLETE']:
                    logger.warning(f"🛡️ [Double-Exit Protection] SL Order {sl_oid} was already FILLED. Skipping market exit order.")
                    exit_price = sl_status.get('price', exit_price)
                    trade_repo.close_trade(trade_id=self.active_position['id'], exit_price=exit_price, exit_reason="SL_HIT")
                    self.active_position = None
                    return

        if not self.dry_run:
            try:
                mode = "PAPER" if self.dry_run else "LIVE"
                if exit_type == "LIMIT":
                    orderparams = {
                        "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                        "transactiontype": "SELL", "exchange": "NFO", 
                        "ordertype": "LIMIT",
                        "price": self.active_position.get('sl_price', 0),
                        "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
                    }
                    oid = self.order_manager.place_order(orderparams)
                else:
                    oid = self.order_manager.place_market(
                        symbol=symbol, token=token, qty=qty, 
                        transaction_type="SELL", strategy_name="MOMENTUM", mode=mode
                    )
                
                if not oid:
                    logger.error("❌ Exit Order API returned None! Retrying next loop...")
                    return

                logger.info(f"Success: Exit Order Placed: {oid}. Waiting for Fill...")
                
                fill_result = self.wait_for_fill(oid)
                
                if fill_result['status'] == 'FILLED':
                    exit_price = fill_result['price']
                    logger.info(f"✅ Exit Filled @ {exit_price}")

                elif fill_result['status'] in ['REJECTED', 'CANCELLED']:
                    logger.error(f"❌ Exit Order {oid} REJECTED. Reason: {fill_result.get('message')}")
                    msg = str(fill_result.get('message', '')).lower()
                    if "no open position" in msg or "no net position" in msg:
                        logger.warning("⚠️ Broker says no position. Force closing local state.")
                    else:
                        logger.warning("⚠️ Exit Failed. Keeping position active for retry.")
                        return 

                else:
                    logger.warning(f"⚠️ Exit Order {oid} status: {fill_result['status']}. Checking Broker Sync...")
                    return

            except Exception as e:
                logger.error(f"Exit Order Exception: {e}")
                return 
        else:
            logger.info(f"Dry Run: Simulated Exit for {symbol}")

        try:
             entry_price = self.active_position['entry_price']
             pnl = (exit_price - entry_price) * qty
             
             # Notify
             notifier.notify_trade_exit("MOMENTUM", symbol, pnl, reason)
        except Exception as e:
             logger.error(f"Notification Error: {e}")

        try:
             entry_p = self.active_position['entry_price']
             pnl_val = (exit_price - entry_p) * qty
             trade_id = self.active_position.get('id')
             
             if is_partial:
                 if trade_id:
                     trade_repo.reduce_position(
                         trade_id=trade_id,
                         reduction_qty=qty,
                         exit_price=exit_price,
                         pnl_segment=round(pnl_val, 2),
                         reason=reason
                     )
                 
                 self.active_position['qty'] -= qty
                 self.active_position['partially_booked'] = True
                 logger.info(f"⚖️ Position Reduced. Remaining Qty: {self.active_position['qty']}")
             else:
                 if trade_id:
                     trade_repo.close_trade(
                         trade_id=trade_id,
                         exit_price=exit_price, 
                         pnl=round(pnl_val, 2), 
                         exit_reason=reason
                     )
                 else:
                     trade_repo.close_trade(
                         symbol=symbol, 
                         exit_price=exit_price, 
                         pnl=round(pnl_val, 2), 
                         exit_reason=reason
                     )
                 self.active_position = None
                 logger.info("✅ Strategy State: Trade Closed.")
                 # Phase 5: Clear unrealized P&L from metrics exporter
                 try:
                     from bot.core.metrics_exporter import metrics_exporter
                     metrics_exporter.clear_unrealized(symbol)
                 except Exception:
                     pass
        except Exception as e:
            logger.error(f"DB Update Error: {e}")
        
        # FINAL CLEAR: If we reached here, the broker exit attempt has been made.
        # We must clear the state to prevent infinite exit loops.
        self.active_position = None
        logger.info("✅ Strategy State: Internal Memory Cleared.")

    def check_trailing_stop(self):
        """
        Refactored to use LadderedTrailingManager (Stage-Gate system).
        """
        if not self.active_position: return False

        token = self.active_position['token']
        symbol = self.active_position['symbol']
        entry_price = self.active_position.get('entry_price', 0.0)

        ltp = self.data_fetcher.get_ltp(token, exchange="NFO")
        if not ltp or ltp == 0:
            return False

        # ── 30s status heartbeat ──────────────────────────────────────────────
        if time.time() - self._last_status_log > 30:
            self._last_status_log = time.time()
            _qty     = self.active_position.get('qty', 0)
            _pnl     = round((ltp - entry_price) * _qty, 2)
            _pnl_pct = round((_pnl / (entry_price * _qty)) * 100, 2) if entry_price > 0 and _qty > 0 else 0
            _current_sl = self.active_position.get('sl_price', 0.0)
            _stage = self.active_position.get('ladder_stage', 0)

            logger.info(
                f"Momentum: 📊 MONITOR | LTP=₹{ltp:.1f} | Entry=₹{entry_price:.1f} | "
                f"SL=₹{_current_sl:.1f} | Qty={_qty} | P&L=₹{_pnl:+,.0f} ({_pnl_pct:+.1f}%) | Stage={_stage}"
            )

        # Use the LadderedTrailingManager
        should_close, exit_type = self.trailing_manager.update_trailing_sl("MOMENTUM", self.active_position, ltp)

        if should_close:
            logger.info(f"🛑 Laddered Exit Triggered ({exit_type})")
            self.close_position(f"LADDERED_SL_{exit_type}", exit_type=exit_type)
            self._last_sl_hit_time = time.time()
            return True

        return False


    def update_sl(self, new_sl, ltp):
        """Proxy to trailing_manager for manual SL updates if needed."""
        self.trailing_manager._apply_sl_update("MOMENTUM", self.active_position, new_sl)

    def get_nifty_ltp(self):
        try:
             from backend.market_service import market_service
             data = market_service.get_market_data()
             return data.get('nifty', 0.0)
        except Exception as e:
            logger.error(f"LTP Fetch Error: {e}")
            return 0.0

    def get_mock_df(self):
         close = 22000 + random.randint(-50, 50)
         data = []
         for i in range(100): 
             c = 22000 + (i * 10) + random.randint(-5, 5)
             data.append({
                 'open': c - 2,
                 'high': c + 5,
                 'low': c - 5,
                 'close': c
             })
         
         return pd.DataFrame(data) 

    def relogin(self):
        logger.info("System: 🔄 Attempting Session Re-login...")
        new_api = get_session()
        if new_api:
            self.api = new_api
            self.gatekeeper.api = new_api
            self.data_fetcher.api = new_api 
            logger.info("System: ✅ Re-login Successful! Session refreshed.")
            return True
        else:
            logger.error("System: ❌ Re-login Failed.")
            return False

    def get_current_position(self):
        """Returns the active position details for UI."""
        return self.active_position
