import time
import datetime
import pandas as pd
import random
import json
import os

from bot.config.settings import Config
from bot.core.angel_connect import get_angel_session
from bot.core.safety_checks import SafetyGatekeeper
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
                    self.active_position = {
                        'id': db_trade['id'],
                        'leg': db_trade['leg'],
                        'symbol': db_trade['symbol'],
                        'token': db_trade['token'],
                        'qty': db_trade['qty'],
                        'entry_price': db_trade['entry_price'],
                        'sl_price': db_trade['sl_price'],
                        'sl_order_id': db_trade.get('sl_order_id'),
                        'atr': 0.0,
                        'partially_booked': db_trade.get('partially_booked', False)
                    }
                    logger.info(f"♻️ PAPER RECOVERY: Found Active Trade in DB! {db_trade['symbol']}")
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
                     
                     qty = int(pos['netqty'])
                     
                     found_active = {
                         'leg': "CE" if "CE" in pos.get('tradingsymbol', '') else "PE", 
                         'symbol': pos['tradingsymbol'],
                         'token': pos['symboltoken'],
                         'qty': abs(qty),
                         'entry_price': float(pos['avgnetprice']),
                         'sl_price': float(pos['avgnetprice']) - min(20, float(pos['avgnetprice']) * 0.2) if self.active_position is None else self.active_position.get('sl_price', 0)
                     }
                     if self.active_position is None:
                         logger.info(f"♻️ RECOVERY: Found Active Trade on Broker! {found_active['symbol']}")
                     
                     break 
             
             if found_active:
                 self.active_position = found_active
             elif self.active_position is not None:
                 logger.warning("⚠️ SYNC: Active Position closed externally! Resetting State.")
                 trade_repo.close_trade(symbol=self.active_position['symbol'])
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

                        curr_ltp = market_feed.get_ltp(token)

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
                if not self.dry_run and now_time >= datetime.time(*Config.STRATEGY_EXIT_TIME):
                    logger.info(f"Market Closed ({datetime.time(*Config.STRATEGY_EXIT_TIME).strftime('%H:%M')}). Stopping Strategy.")
                    if self.active_position:
                        self.close_position("TIME_EXIT")
                    break

                # --- SLOW LOOP (Trend Analysis) ---
                if datetime.datetime.now() >= next_check:
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
                        
                        # 1. ADX Strength
                        if _adx_now >= _tier_now.min_adx_to_trade:
                            checks.append(("ADX Strength", True, f"{_adx_now:.1f}"))
                        else:
                            checks.append(("ADX Strength", False, f"{_adx_now:.1f} < {_tier_now.min_adx_to_trade}"))

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

                        # Calculate Confluence Score
                        passed = [c for c in checks if c[1]]
                        failed = [c for c in checks if not c[1]]
                        score = len(passed)
                        total = len(checks)

                        if failed:
                            logger.info(f"🔍 Confluence Score: {score}/{total} | Missing: {', '.join([f'{c[0]} [{c[2]}]' for c in failed])}")
                        
                        # Execution Logic based on Confluence
                        if score >= 4 and total >= 4:
                            if trend == "BULLISH":
                                self.enter_position(expiry, "CE")
                            elif trend == "BEARISH":
                                self.enter_position(expiry, "PE")
                        elif trend != "NEUTRAL":
                            logger.info(f"⏸️ Trade Opportunity Paused — waiting for full confluence.")
                    
                    else:
                        current_leg = self.active_position['leg']
                        if current_leg == "CE" and trend == "BEARISH":
                            logger.info("Signal: Trend Reversed to BEARISH. Exiting CE.")
                            self.close_position("REVERSAL")
                            # Cooldown: skip immediate re-entry — next 5-min candle will evaluate PE.
                            # Avoids whipsaws without blocking the global safety kill-switch (sleep removed).
                            logger.info("⏳ Reversal Cooldown: PE entry will be evaluated on the next candle.")

                        elif current_leg == "PE" and trend == "BULLISH":
                            logger.info("Signal: Trend Reversed to BULLISH. Exiting PE.")
                            self.close_position("REVERSAL")
                            # Cooldown: skip immediate re-entry — next 5-min candle will evaluate CE.
                            logger.info("⏳ Reversal Cooldown: CE entry will be evaluated on the next candle.")

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
            df = self.data_fetcher.fetch_latest_candles("99926000")
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
                    strike = int(round(ltp / 50) * 50)
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
                    if regime != "UNKNOWN" and trend != "NEUTRAL":
                        logger.debug(f"[HTF] Using shared intelligence: {trend}")
                        return trend
        except Exception as e:
            logger.warning(f"[HTF] Could not read shared state: {e}")

        # --- FALLBACK: REST call (only if shared file unavailable/stale) ---
        from bot.utils.rate_limiter import rate_limiter
        if rate_limiter.check_circuit_breaker() > 0:
            logger.warning("[HTF] Circuit breaker active — returning NEUTRAL")
            return "NEUTRAL"

        df = self.data_fetcher.fetch_latest_candles("99926000", interval="FIFTEEN_MINUTE")

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

    def enter_position(self, expiry, leg):
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

        # --- RSI OVEREXTENSION FILTER ---
        # Block entries when the move is already exhausted.
        # CE entry blocked if RSI > 68 (overbought); PE entry blocked if RSI < 32 (oversold).
        # Note: not applied to GAMMA_BLAST — parabolic days are supposed to be "overbought".
        entry_rsi = self.last_analysis.get('rsi', 50.0)
        if leg == "CE" and entry_rsi > 68:
            logger.warning(
                f"🛑 RSI Overextension Filter: RSI={entry_rsi:.1f} > 68 (overbought). "
                "Skipping CE entry — not chasing an exhausted move."
            )
            return
        if leg == "PE" and entry_rsi < 32:
            logger.warning(
                f"🛑 RSI Overextension Filter: RSI={entry_rsi:.1f} < 32 (oversold). "
                "Skipping PE entry — not chasing an exhausted move."
            )
            return

        # ── FRESH OI ALIGNMENT GATE ────────────────────────────────────────────
        # Force-fetch current OI at every entry — never rely on the 5-min cache.
        # Root cause of 2026-04-09 bad trade: stale OI showed BEARISH while market
        # had already reversed to BULLISH after the first SL hit.
        _fresh_bias = 'NEUTRAL'
        try:
            _fresh_atm = round(nifty_ltp / 50) * 50
            _fresh_oi = self.oi_analyzer.get_market_sentiment(expiry, _fresh_atm)
            _fresh_bias = _fresh_oi.get('bias', 'NEUTRAL')
            logger.info(
                f"🔍 Fresh OI at entry: bias={_fresh_bias} | "
                f"PCR={_fresh_oi.get('pcr', '?')} | ΔR={_fresh_oi.get('delta_ratio', '?')}"
            )
        except Exception as _e:
            logger.warning(f"Fresh OI fetch failed: {_e}. Falling back to cached bias.")
            _fresh_bias = self.oi_data.get('bias', 'NEUTRAL')

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
            _df_entry = self.data_fetcher.fetch_latest_candles("99926000")
            if _df_entry is not None and len(_df_entry) >= 3:
                _last3 = _df_entry.tail(3)
                _bull_count = (_last3['close'] > _last3['open']).sum()
                _bear_count = (_last3['close'] < _last3['open']).sum()
                if leg == "CE" and _bull_count < 2:
                    logger.warning(
                        f"🛑 Candle Momentum Filter: {_bull_count}/3 bullish candles — "
                        "weak confirmation for CE entry. Skipping."
                    )
                    return
                if leg == "PE" and _bear_count < 2:
                    logger.warning(
                        f"🛑 Candle Momentum Filter: {_bear_count}/3 bearish candles — "
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
                    if leg == "CE" and nifty_ltp < _vwap:
                        logger.warning(
                            f"🛑 VWAP Filter: NIFTY {nifty_ltp:.0f} < VWAP {_vwap:.0f} — "
                            "CE blocked. Price trading below institutional anchor."
                        )
                        return
                    if leg == "PE" and nifty_ltp > _vwap:
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
                    # 1. If ADX > 50, ignore slope (extreme trend regime).
                    # 2. If ADX > 35, allow a small decline up to 0.2pts.
                    # 3. Otherwise, require at least flat (diff > -0.05).
                    _is_declining_m = False
                    if curr_adx_m > 50:
                        _is_declining_m = False
                    elif curr_adx_m > 35:
                        _is_declining_m = (curr_adx_m - prev_adx_m) < -0.2
                    else:
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
        if not self.gatekeeper.check_sentiment_risk(direction):
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

        # Phase 3: Volatility-Adjusted Strike Selection
        # High ATR → go deeper OTM for more leverage (accepts lower delta).
        # Low ATR  → stay near ATM for higher fill probability and delta.
        atm_strike = round(nifty_ltp / 50) * 50
        if atr < 15:
            otm_offset = 0      # ATM — tight market, maximise delta
        elif atr < 30:
            otm_offset = 1      # 1 strike OTM (~50 pts)
        else:
            otm_offset = 2      # 2 strikes OTM (~100 pts) — high vol, wide swings expected

        # --- IV RANK: OTM DEPTH REDUCTION ---
        # When options are expensive (high IV Rank), going OTM means paying a fat premium
        # for low delta. Pull back 1 strike toward ATM to improve cost vs. payoff.
        iv_rank = self.gatekeeper.get_iv_rank()
        if iv_rank > 0.70 and otm_offset > 0:
            otm_offset = max(0, otm_offset - 1)
            logger.info(
                f"📉 IV Rank={iv_rank:.0%} (>70%): Options expensive, "
                f"pulling OTM depth back to {otm_offset} strike(s) — prefer near-ATM."
            )

        # ── OTM CAP FOR SMALL/MICRO TIER ───────────────────────────────────────
        # Small accounts go max 1-OTM (50pts from ATM). Going 2-OTM requires the
        # underlying to move 150pts+ to reach a reasonable P&L on thin capital.
        _cap_tier = Config.get_tier(self.gatekeeper.get_current_capital())
        if _cap_tier.name in ("MICRO", "SMALL") and otm_offset > 1:
            logger.info(
                f"[{_cap_tier.name} tier] OTM depth capped: {otm_offset} → 1 "
                "(max 1-OTM for small accounts)."
            )
            otm_offset = 1

        strike_direction = 1 if leg == "CE" else -1
        strike = atm_strike + strike_direction * otm_offset * 50
        logger.info(
            f"⚡ Vol-Adjusted Strike: ATR={atr:.1f} → "
            f"{'ATM' if otm_offset == 0 else f'{otm_offset} OTM'} | "
            f"Strike={strike}"
        )
        instr = get_instrument(Config.ACTIVE_SYMBOL)
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

        option_sl_points = atr
        if option_sl_points < 5: option_sl_points = 5

        # Calculate actual margin per lot. Fallback to half the tier threshold if LTP unknown.
        margin_per_lot = (quote_ltp * Config.NIFTY_LOT_SIZE) if quote_ltp > 0 else (tier.min_capital_threshold * 0.5)
        
        # Apply Compounding (Exponential Scaling) using real estimated cost
        lots = self.gatekeeper.get_compounded_lots(margin_per_lot=margin_per_lot, multiplier=self.risk_multiplier)
        qty = lots * Config.NIFTY_LOT_SIZE
        
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
        
        logger.info(f"Trade: Entering {leg} ({symbol}) Qty: {qty} Price: {quote_ltp} Cost: {estimated_cost}")
        
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
            # Aggressive: tight SL (1x ATR), big target (5x ATR in index → ~2.5x in option)
            sl_option_pts  = option_sl_points          # 1 ATR on option space
            tgt_option_pts = option_sl_points * 2.5    # 5:1 index → ~2.5x option leverage
            dynamic_rr     = "TRENDING_5:1"
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
        
        # --- HARD SL FLOOR (20% safety cap) ---
        max_allowed_sl_pts = quote_ltp * tier.sl_pct
        if actual_sl_points > max_allowed_sl_pts:
            logger.warning(
                f"🛡️ Hard SL Triggered: Truncating {actual_sl_points:.1f}pts "
                f"to {max_allowed_sl_pts:.1f}pts ({tier.sl_pct*100}% cap)"
            )
            actual_sl_points = max_allowed_sl_pts

        sl_price = max(0.1, quote_ltp - actual_sl_points)
        target_price = quote_ltp + tgt_option_pts

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
                'atr': atr,
                'context': trade_context
            }

            # Update DB Fill (Simulation)
            tid = self.order_manager.update_trade_fill(symbol, "MOMENTUM", quote_ltp)
            if tid: self.active_position['id'] = tid
            return

        try:
            # For BUY orders, pay slightly above LTP to improve fill probability.
            # Slippage buffer from capital tier (smaller accounts use 1%, larger use 0.5%).
            limit_price = quote_ltp * (1.0 + tier.entry_slippage_pct)
                
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
            trade_id = self.order_manager.update_trade_fill(symbol, "MOMENTUM", fill_price, expected_price=quote_ltp)
            
            actual_sl = max(0.1, fill_price - actual_sl_points)

            actual_target = fill_price + tgt_option_pts
            self.active_position = {
                'id': trade_id,
                'leg': leg, 'symbol': symbol, 'qty': qty, 'token': token,
                'entry_price': fill_price,
                'sl_price': actual_sl,
                'target_price': actual_target,
                'dynamic_rr': dynamic_rr,
                'atr': atr,
                'context': trade_context
            }
            
            if trade_id:
                trade_repo.update_sl(trade_id, actual_sl)
            
            # Place Hard SL (Broker-Side)
            sl_oid = self.order_manager.place_sl_order(symbol, token, qty, actual_sl, leg)
            
            # SL VERIFICATION: If SL placement failed, we cannot hold the position safely.
            if not sl_oid and not self.dry_run:
                logger.critical(f"🚨 MOMENTUM: SL placement FAILED for {symbol}. Emergency exiting position for safety!")
                exit_params = {
                    "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                    "transactiontype": "SELL", "exchange": "NFO",
                    "ordertype": "MARKET", "price": 0,
                    "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
                }
                self.order_manager.place_order(exit_params)
                return

            if sl_oid:
                self.active_position['sl_order_id'] = sl_oid
                if trade_id:
                    trade_repo.update_sl_order_id(trade_id, sl_oid)
                logger.info(f"🛡️ Broker-Side SL Placed: {sl_oid}")
            
            # Notify
            notifier.notify_trade_entry("MOMENTUM", symbol, "BUY", qty, fill_price)
                
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

    def close_position(self, reason, override_qty=None):
        if not self.active_position: return
        
        symbol = self.active_position['symbol']
        token = self.active_position['token']
        qty = override_qty if override_qty else self.active_position['qty']
        
        is_partial = override_qty is not None and override_qty < self.active_position['qty']
        
        logger.info(f"Exit: Closing {qty} shares of {symbol} (Reason: {reason})")
        
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
            self.order_manager.cancel_order(sl_oid, variety="STOPLOSS")

        if not self.dry_run:
            try:
                orderparams = {
                    "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                    "transactiontype": "SELL", "exchange": "NFO", "ordertype": "MARKET",
                    "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
                }
                oid = self.order_manager.place_order(orderparams)
                
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
             pnl_pct = (exit_price - entry_price) / entry_price * 100
             result = "WIN" if pnl > 0 else "LOSS"
             
             trade_record = {
                 "strategy": "MOMENTUM",
                 "symbol": symbol,
                 "action": "SELL",
                 "qty": qty,
                 "entry_price": entry_price,
                 "exit_price": exit_price,
                 "pnl": round(pnl, 2),
                 "pnl_percent": round(pnl_pct, 2),
                 "result": result,
                 "exit_reason": reason
             }
             if 'context' in self.active_position:
                 trade_record.update(self.active_position['context'])
             
             TradeJournal.log_trade(trade_record)
             
             # Notify
             notifier.notify_trade_exit("MOMENTUM", symbol, pnl, reason)
        except Exception as e:
             logger.error(f"Journal Error: {e}")

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

    def check_trailing_stop(self):
        """
        Phase 3: Dynamic RR Trailing Stop.

        TRENDING  (5:1 target) regime:
          Stage 1: @ 1.0 ATR profit → move SL to breakeven immediately (fast pivot).
          Stage 2: @ 2.5 ATR profit → close 50% and trail the rest.
          Target  : honour target_price from active_position.

        RANGEBOUND (1.5:1 target) regime:
          Stage 1: @ 0.75 ATR profit → breakeven.
          Exit early on any sign of structural reversal (trend fade).
          Stage 2: @ 1.5 ATR → full exit.
        """
        if not self.active_position: return False

        token        = self.active_position['token']
        symbol       = self.active_position['symbol']
        entry_price  = self.active_position.get('entry_price', 0.0)
        current_sl   = self.active_position.get('sl_price', 0.0)
        atr_at_entry = self.active_position.get('atr', 20.0)
        is_partial   = self.active_position.get('partially_booked', False)
        target_price = self.active_position.get('target_price', 0.0)
        dynamic_rr   = self.active_position.get('dynamic_rr', 'RANGEBOUND_1.5:1')
        is_trending  = dynamic_rr.startswith('TRENDING')

        if entry_price == 0: return False

        ltp = self.data_fetcher.get_ltp(token, exchange="NFO")
        if not ltp:
            try:
                from bot.utils.rate_limiter import rate_limiter
                rate_limiter.wait()
                q_resp = self.api.ltpData("NFO", symbol, token)
                if q_resp and q_resp.get('status'):
                    ltp = float(q_resp['data']['ltp'])
            except Exception as e:
                logger.warning(f"Trailing Stop LTP fetch error: {e}")
                return False

        if not ltp or ltp == 0: return False

        # ── 30s status heartbeat ──────────────────────────────────────────────
        if time.time() - self._last_status_log > 30:
            self._last_status_log = time.time()
            _qty     = self.active_position.get('qty', 0)
            _pnl     = round((ltp - entry_price) * _qty, 2)
            _pnl_pct = round((_pnl / (entry_price * _qty)) * 100, 2) if entry_price > 0 and _qty > 0 else 0
            _tgt     = self.active_position.get('target_price', 0)
            _rr      = self.active_position.get('dynamic_rr', '?')
            _is_part = self.active_position.get('partially_booked', False)

            _sl_dist  = round(ltp - current_sl, 1)  if current_sl > 0 else 0
            _tgt_dist = round(_tgt - ltp, 1)        if _tgt > 0 else 0
            _tgt_str  = (
                f"Target=₹{_tgt:.1f} ({_tgt_dist:+.1f}pts)"
                if _tgt > 0 else "No fixed target (trailing)"
            )
            _partial_str = " [50% BOOKED]" if _is_part else ""

            logger.info(
                f"Momentum: 📊 MONITOR | LTP=₹{ltp:.1f} | Entry=₹{entry_price:.1f} | "
                f"SL=₹{current_sl:.1f} ({_sl_dist:.1f}pts below) | {_tgt_str} | "
                f"Qty={_qty}{_partial_str} | P&L=₹{_pnl:+,.0f} ({_pnl_pct:+.1f}%) | RR={_rr}"
            )

        # 0. Hard Stop Loss Check
        if current_sl > 0 and ltp <= current_sl:
            logger.info(f"🛑 Stop Hit! Price: {ltp} <= SL: {current_sl}")
            self.close_position("STOPLOSS_HIT")
            self._last_sl_hit_time = time.time()  # Gate re-entry for 5 min
            return True

        profit_points = ltp - entry_price
        trail_atr = max(5.0, atr_at_entry * 0.5)

        # 0b. Target Hit Check (Dynamic RR Target)
        # In TRENDING mode, once 50% is already booked (is_partial=True) we skip
        # the fixed target and let the trailing SL run the remainder indefinitely.
        # This turns a capped 2.5R trade into an open-ended runner.
        if target_price > 0 and ltp >= target_price:
            if is_trending and is_partial:
                # 50% already locked — tighten trail aggressively, don't exit
                new_sl = round(ltp - (trail_atr * 0.4), 1)
                if new_sl > current_sl:
                    logger.info(
                        f"🎯 TRENDING Target Passed ({ltp:.1f} ≥ {target_price:.1f}). "
                        f"50% booked. Tightening trail: SL → {new_sl:.1f} and letting it run."
                    )
                    self.update_sl(new_sl, ltp)
                # fall through — no return, trailing continues
            else:
                logger.info(f"🎯 Target Hit! Price: {ltp} >= Target: {target_price} ({dynamic_rr})")
                self.close_position("TARGET_HIT")
                return True

        if is_trending:
            # TRENDING regime: fast breakeven, let winners run to 5:1
            # SAFETY UPGRADE: If lots >= 4, move to BE even earlier (0.6x instead of 1.0x)
            be_mult = 1.0 if (self.active_position.get('qty', 0) >= 4 * Config.NIFTY_LOT_SIZE) else 1.5
            be_trigger  = be_mult * trail_atr  # Move to breakeven at 1.5:1 (or 1.0:1 for high qty)
            book_trigger = 2.5 * trail_atr  # Book 50% at 2.5:1

            if profit_points > be_trigger and current_sl < entry_price:
                new_sl = entry_price + 1.0
                logger.info("🎯 TRENDING Stage 1 (1:1 ATR). Moving SL to Break-Even (fast pivot).")
                self.update_sl(new_sl, ltp)
                return False

            if profit_points > book_trigger and not is_partial:
                qty_to_close = self.active_position['qty'] // 2
                if qty_to_close >= Config.NIFTY_LOT_SIZE:
                    logger.info(f"💰 TRENDING Stage 2 (2.5x ATR). Booking 50% ({qty_to_close} qty).")
                    self.close_position("PARTIAL_PROFIT", override_qty=qty_to_close)
                    return False
        else:
            # RANGEBOUND regime: tighter stages, early reversal exit
            be_mult = 0.8 if (self.active_position.get('qty', 0) >= 4 * Config.NIFTY_LOT_SIZE) else 1.2
            be_trigger   = be_mult  * trail_atr
            book_trigger = 1.5   * trail_atr

            if profit_points > be_trigger and current_sl < entry_price:
                new_sl = entry_price + 1.0
                logger.info("🎯 RANGEBOUND Stage 1 (0.75 ATR). Moving SL to Break-Even.")
                self.update_sl(new_sl, ltp)
                return False

            if profit_points > book_trigger and not is_partial:
                qty_to_close = self.active_position['qty'] // 2
                if qty_to_close >= Config.NIFTY_LOT_SIZE:
                    logger.info(f"💰 RANGEBOUND Stage 2 (1.5 ATR). Full exit (range target reached).")
                    self.close_position("RANGEBOUND_TARGET")
                    return True

        # Stage 3: Aggressive trailing for TRENDING remainder after partial booking

        if profit_points > (2.0 * trail_atr):
            target_sl = ltp - trail_atr
            if target_sl > current_sl:
                self.update_sl(target_sl, ltp)
                
        return False

    def update_sl(self, new_sl, ltp):
        new_sl = round(new_sl, 1)
        self.active_position['sl_price'] = new_sl
        logger.info(f"📈 SL Moved Up to {new_sl} (LTP: {ltp})")
        
        if 'id' in self.active_position:
            trade_repo.update_sl(self.active_position['id'], new_sl)
            
        sl_oid = self.active_position.get('sl_order_id')
        if sl_oid and not self.dry_run:
            token = self.active_position['token']
            symbol = self.active_position['symbol']
            qty = self.active_position['qty']
            self.order_manager.modify_sl_order(sl_oid, new_sl, symbol, token, qty)

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
        new_api = get_angel_session()
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
