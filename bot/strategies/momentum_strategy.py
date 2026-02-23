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
             
             if pos_resp and pos_resp.get('status') and pos_resp.get('data'):
                 found_active = None
                 
                 for pos in pos_resp['data']:
                     if (pos['symbolname'] == 'NIFTY' and 
                         pos['producttype'] == 'INTRADAY' and 
                         int(pos['netqty']) != 0):
                         
                         qty = int(pos['netqty'])
                         
                         found_active = {
                             'leg': "CE" if "CE" in pos['symbolname'] else "PE", 
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
                         logger.info(f"Sync: Linked to DB Trade ID {db_trade['id']} (Partial: {self.active_position['partially_booked']})")
                     
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

        if not self.gatekeeper.check_funds(required_margin_per_lot=5000): return
        if not self.gatekeeper.check_max_daily_loss(0): return
        if not self.active_position and self.gatekeeper.is_blackout_period(): return

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
                if not self.dry_run and now_time >= datetime.time(15, 15):
                    logger.info("Market Closed (15:15). Stopping Strategy.")
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
                        if trend == "BULLISH":
                            if htf_trend == "BEARISH":
                                logger.info("Signal Ignored: 5m Bullish but 15m is BEARISH (Trend Misalignment).")
                            elif rsi < 70:
                                self.enter_position(expiry, "CE")
                            else:
                                logger.info("Signal Ignored: Bullish but RSI Overbought (>70).")
                                
                        elif trend == "BEARISH":
                            if htf_trend == "BULLISH":
                                logger.info("Signal Ignored: 5m Bearish but 15m is BULLISH (Trend Misalignment).")
                            elif rsi > 30:
                                self.enter_position(expiry, "PE")
                            else:
                                logger.info("Signal Ignored: Bearish but RSI Oversold (<30).")
                    
                    else:
                        current_leg = self.active_position['leg']
                        if current_leg == "CE" and trend == "BEARISH":
                             logger.info("Signal: Trend Reversed to BEARISH. Exiting CE.")
                             self.close_position("REVERSAL")
                             # FIX Obs #2: 1-candle cooldown before reversal re-entry to avoid whipsaws
                             logger.info("⏳ Reversal Cooldown: Waiting 1 candle (5 min) before re-entry.")
                             time.sleep(300)  # 5 minutes = 1 candle
                             if rsi > 30:
                                 self.enter_position(expiry, "PE") 
                             else:
                                 logger.info("Reversal Entry Ignored: RSI Oversold.")

                        elif current_leg == "PE" and trend == "BULLISH":
                             logger.info("Signal: Trend Reversed to BULLISH. Exiting PE.")
                             self.close_position("REVERSAL")
                             # FIX Obs #2: 1-candle cooldown before reversal re-entry to avoid whipsaws
                             logger.info("⏳ Reversal Cooldown: Waiting 1 candle (5 min) before re-entry.")
                             time.sleep(300)  # 5 minutes = 1 candle
                             if rsi < 70:
                                 self.enter_position(expiry, "CE")
                             else:
                                 logger.info("Reversal Entry Ignored: RSI Overbought.")

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
        # 1. RISK CALCULATION
        nifty_ltp = self.get_nifty_ltp()
        if not nifty_ltp:
            logger.error("Could not fetch Nifty LTP for Entry.")
            return

        atr = self.last_analysis.get('atr', 20.0) 
        if atr == 0: atr = 20.0
        
        sl_points = 2 * atr
        
        # Position Sizing based on Capital
        capital = self.gatekeeper.get_current_capital()
        if capital <= 0: capital = Config.SIMULATION_CAPITAL # Fallback
        
        risk_per_trade = capital * Config.RISK_PER_TRADE_PERCENT
        if risk_per_trade < 500: risk_per_trade = 500 # Min floor
        
        option_sl_points = atr
        if option_sl_points < 5: option_sl_points = 5 
        
        # Apply Compounding (Exponential Scaling)
        lots = self.gatekeeper.get_compounded_lots(margin_per_lot=5000)
        qty = lots * Config.NIFTY_LOT_SIZE  # FIX: was NameError - lot_size was never defined
        
        logger.info(f"⚖️ Sizing: ATR={atr:.2f} | Method=Exponential Compounding | Multiplier={self.risk_multiplier}x | Qty={qty} ({lots} lots)")

        direction = "LONG" if leg == "CE" else "SHORT"
        if not self.gatekeeper.check_sentiment_risk(direction):
             logger.warning(f"Trade Skipped due to Sentiment Risk.")
             return

        try:
             today_str = datetime.datetime.now().strftime("%d%b%Y").upper()
             if expiry == today_str:
                 now = datetime.datetime.now().time()
                 if now >= datetime.time(13, 30):
                     logger.warning("⛔ Expiry Day Safety: Blocking new entries after 1:30 PM.")
                     return
        except Exception as e:
             logger.error(f"Expiry Guard Check Error: {e}")

        strike = round(nifty_ltp / 50) * 50
        token, symbol = self.token_loader.get_token("NIFTY", expiry, strike, leg)
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

        actual_sl_points = option_sl_points
        sl_price = max(0.1, quote_ltp - actual_sl_points)
        
        if self.dry_run:
            self.active_position = {
                'leg': leg, 'symbol': symbol, 'qty': qty, 'token': token, 
                'entry_price': quote_ltp, 
                'sl_price': sl_price, 
                'context': trade_context
            }
            mode = "PAPER" if self.dry_run else "LIVE"
            tid = trade_repo.save_trade(symbol, token, leg, qty, quote_ltp, sl_price, mode=mode, strategy="MOMENTUM")
            if tid: self.active_position['id'] = tid
            return

        try:
             # For BUY orders, we are willing to pay slightly ABOVE current LTP to ensure fill.
             limit_price = quote_ltp * (1.0 + buffer)
                 
             oid = self.order_manager.place_limit_order(symbol, token, qty, limit_price)
             
             if not oid:
                 logger.error("❌ Limit Order Placement Failed! (API returned None).")
                 return

             logger.info(f"Success: Order Placed: {oid}")
            
             fill_result = self.wait_for_fill(oid)
             
             if fill_result['status'] in ['REJECTED', 'CANCELLED']:
                 logger.error(f"❌ Order {oid} was {fill_result['status']}. Reason: {fill_result.get('message', 'Unknown')}")
                 return 

             fill_price = fill_result['price']
             if not fill_price:
                 fill_price = quote_ltp
                 logger.warning(f"Momentum: Fill price not captured, using estimate: ₹{fill_price}")

             actual_sl = max(0.1, fill_price - actual_sl_points)

             self.active_position = {
                'leg': leg, 'symbol': symbol, 'qty': qty, 'token': token, 
                'entry_price': fill_price, 
                'sl_price': actual_sl,
                'atr': atr,
                'context': trade_context
            }
             
             mode = "PAPER" if self.dry_run else "LIVE"
             tid = trade_repo.save_trade(symbol, token, leg, qty, fill_price, actual_sl, mode=mode)
             if tid: self.active_position['id'] = tid
             
             # Place Hard SL (Broker-Side)
             sl_oid = self.order_manager.place_sl_order(symbol, token, qty, actual_sl, leg)
             if sl_oid:
                 self.active_position['sl_order_id'] = sl_oid
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
        except Exception as e:
            logger.error(f"DB Update Error: {e}")

    def check_trailing_stop(self):
        """
        ATR-Based Multi-Stage Trailing Stop.
        Stage 1: @ 1.0 ATR -> Move SL to Break-Even.
        Stage 2: @ 1.5 ATR -> Close 50% Position.
        Stage 3: Trail remainder with 0.5 ATR buffer.
        """
        if not self.active_position: return False
        
        token = self.active_position['token']
        symbol = self.active_position['symbol']
        entry_price = self.active_position.get('entry_price', 0.0)
        current_sl = self.active_position.get('sl_price', 0.0)
        atr_at_entry = self.active_position.get('atr', 20.0)
        is_partial = self.active_position.get('partially_booked', False)
        
        if entry_price == 0: return False 
        
        ltp = market_feed.get_ltp(token)
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
        
        # 0. Basic Stop Loss Check
        if current_sl > 0 and ltp <= current_sl:
            logger.info(f"🛑 Stop Hit! Price: {ltp} <= SL: {current_sl}")
            self.close_position("STOPLOSS_HIT")
            return True
            
        profit_points = ltp - entry_price
        
        # We use half the ATR for trailing to be reactive in options
        trail_atr = atr_at_entry * 0.5
        if trail_atr < 5: trail_atr = 5
        
        # Stage 1: Break-Even (Move SL to Entry +  ₹1 buffer)
        if profit_points > (1.0 * trail_atr) and current_sl < entry_price:
            new_sl = entry_price + 1.0 
            logger.info("🎯 Stage 1 Hit (1.0 ATR). Moving SL to Break-Even.")
            self.update_sl(new_sl, ltp)
            return False

        # Stage 2: Partial Profit Booking (Close 50%)
        if profit_points > (1.5 * trail_atr) and not is_partial:
            qty_to_close = self.active_position['qty'] // 2
            if qty_to_close >= Config.NIFTY_LOT_SIZE: # Only if we have >1 lot
                logger.info(f"💰 Stage 2 Hit (1.5 ATR). Booking 50% Profit ({qty_to_close} qty).")
                self.close_position("PARTIAL_PROFIT", override_qty=qty_to_close)
                return False

        # Stage 3: Aggressive Trailing for remainder
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
