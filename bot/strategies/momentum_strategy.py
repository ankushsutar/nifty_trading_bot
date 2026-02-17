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

# ... imports ...

# ... imports ...

class MomentumStrategy:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(self.api, dry_run=self.dry_run)
        self.data_fetcher = DataFetcher(self.api)
        self.regime_classifier = RegimeClassifier()
        self.oi_analyzer = OIAnalyzer(self.api, self.token_loader)
        
        self.data_failure_count = 0
        self.active_position = None
        self.running = True  # Flag for graceful shutdown
        self.last_sync_time = 0
        self.last_analysis = {} # Stores EMA9, RSI, etc for logging
        self.last_oi_scan = 0
        self.last_trailing_check = 0 # Throttle Trailing Stop Checks
        self.oi_data = {}
        self._ltp_cache = {} # SafeLTP Cache
        
        self.sync_state() # Initial Sync with Broker

    def export_state(self):
        """Exports current strategy state to JSON for UI consumption."""
        try:
            data_dir = "data"
            if not os.path.exists(data_dir):
                os.makedirs(data_dir)
            
            # Serialize datetime objects if any (active_position might have them?)
            # Usually active_position is dict of primitives.
            
            state = {
                "timestamp": datetime.datetime.now().isoformat(),
                "analysis": self.last_analysis,
                "oi_data": self.oi_data,
                "active_position": self.active_position,
                "dry_run": self.dry_run
            }
            
            with open(os.path.join(data_dir, "market_status.json"), "w") as f:
                json.dump(state, f, default=str) # Use default=str for safety
        except Exception as e:
            logger.error(f"State Export Error: {e}")

    def sync_state(self):
        """
        Synchronizes active position from Broker API.
        Current Rule: Looks for the FIRST active NIFTY Intraday position.
        """
        if self.dry_run:
            # Try to recover state from DB for Paper Trading
            if self.active_position is None:
                db_trade = trade_repo.get_active_trade(mode="PAPER", strategy="MOMENTUM")
                if db_trade:
                    # Map DB columns to Strategy State
                    self.active_position = {
                        'id': db_trade['id'],
                        'leg': db_trade['leg'],
                        'symbol': db_trade['symbol'],
                        'token': db_trade['token'],
                        'qty': db_trade['qty'],
                        'entry_price': db_trade['entry_price'],
                        'sl_price': db_trade['sl_price'],
                        # Restore context if possible, or default
                        'atr': 0.0 # Will be updated on next analysis
                    }
                    logger.info(f"♻️ PAPER RECOVERY: Found Active Trade in DB! {db_trade['symbol']}")
            return
        
        try:
             # logger.info("System: 🔄 Syncing State with Broker...")
             pos_resp = self.api.position()
             
             if pos_resp and pos_resp.get('status') and pos_resp.get('data'):
                 found_active = None
                 
                 for pos in pos_resp['data']:
                     if (pos['symbolname'] == 'NIFTY' and 
                         pos['producttype'] == 'INTRADAY' and 
                         int(pos['netqty']) != 0):
                         
                         qty = int(pos['netqty'])
                         
                         found_active = {
                             'leg': "CE" if "CE" in pos['symbolnm'] else "PE", # simplistic
                             'symbol': pos['tradingsymbol'],
                             'token': pos['symboltoken'],
                             'qty': abs(qty),
                             'entry_price': float(pos['avgnetprice']),
                             # If we recover, we default SL to entry - 20 or 20% (whichever is closer)
                             'sl_price': float(pos['avgnetprice']) - min(20, float(pos['avgnetprice']) * 0.2) if self.active_position is None else self.active_position.get('sl_price', 0)
                         }
                         # Log ONLY if we are discovering a new position (Recovery)
                         if self.active_position is None:
                             logger.info(f"♻️ RECOVERY: Found Active Trade on Broker! {found_active['symbol']}")
                         
                         break # Handle one position for now
                 
                 # Logic for Remote Closure
                 if found_active:
                     self.active_position = found_active
                 elif self.active_position is not None:
                     # We thought we had a position, but Broker says NO active Nifty Intraday positions.
                     logger.warning("⚠️ SYNC: Active Position closed externally! Resetting State.")
                     trade_repo.close_trade(symbol=self.active_position['symbol'])
                     self.active_position = None
                 
                 # Attempt to link DB ID if we found a position
                 if self.active_position and 'id' not in self.active_position:
                     db_trade = trade_repo.get_active_trade(mode="LIVE", strategy="MOMENTUM")
                     if db_trade and db_trade['symbol'] == self.active_position['symbol']:
                         self.active_position['id'] = db_trade['id']
                         logger.info(f"Sync: Linked to DB Trade ID {db_trade['id']}")
                     
        except Exception as e:
            logger.error(f"Sync State Error: {e}")

    def stop(self):
        """Signals the loop to stop and closes open positions."""
        self.running = False
        logger.info("[Control] Stop Requested from API.")
        
        if self.active_position:
            logger.warning("[Control] 🛑 Force Closing Open Position due to Stop Signal.")
            self.close_position("USER_STOPPED")

    def check_trailing_stop(self):
        """
        Manages Step-Trailing Stop Loss.
        """
        if not self.active_position: return False
        
        token = self.active_position['token']
        symbol = self.active_position['symbol']
        entry_price = self.active_position.get('entry_price', 0.0)
        current_sl = self.active_position.get('sl_price', 0.0)
        
        if entry_price == 0: return False # Dry run or missing data
        
        # Get Current LTP
        ltp = 0.0
        try:
             q_resp = self.api.ltpData("NFO", symbol, token)
             if q_resp and q_resp.get('status'):
                 ltp = float(q_resp['data']['ltp'])
        except: pass
        
        if ltp == 0: return False
        
        profit_pts = ltp - entry_price
        
        # 1. Check if SL Hit
        if current_sl > 0 and ltp <= current_sl:
            logger.info(f"🛑 Trailing Stop Hit! Price: {ltp} <= SL: {current_sl}")
            self.close_position("TRAILING_STOP")
            return True
            
        # 2. Update SL (Step Ladder)
        new_sl = current_sl
        
        if profit_pts >= 60:
            target_sl = entry_price + 45
            if target_sl > current_sl: new_sl = target_sl
            
        elif profit_pts >= 40:
            target_sl = entry_price + 25
            if target_sl > current_sl: new_sl = target_sl
            
        elif profit_pts >= 20:
            target_sl = entry_price + 5
            if target_sl > current_sl: new_sl = target_sl
            
        if new_sl > current_sl:
            self.active_position['sl_price'] = new_sl
            logger.info(f"📈 SL Moved Up to {new_sl} (Profit: {profit_pts:.2f})")
            
            # Update DB
            if 'id' in self.active_position:
                trade_repo.update_sl(self.active_position['id'], new_sl)
            
        return False

    def execute(self, expiry, action="BUY"):
        """
        Momentum Logic (EMA Crossover + RSI):
        - Timeframe: 5 Minutes.
        - Buy Signal: 9 EMA > 21 EMA AND RSI < 70 -> Buy CE.
        - Sell Signal: 9 EMA < 21 EMA AND RSI > 30 -> Buy PE.
        - Exit: When crossover reverses.
        """
        logger.info(f"--- EMA CROSSOVER + RSI STRATEGY ({expiry}) ---")

        # 0. Risk Checks
        if not self.gatekeeper.check_funds(required_margin_per_lot=5000): return
        if not self.gatekeeper.check_max_daily_loss(0): return
        # Allow managing EXISTING positions during Blackout
        if not self.active_position and self.gatekeeper.is_blackout_period(): return

        # 1. Continuous Monitor Loop
        logger.info("Starting Smart Monitor Loop (Safety: 1s | Trend: 5m Sync)...")
        
        # Initialize Next Candle Check Time (e.g., next 5-min mark + 5s buffer)
        # e.g., if now is 09:12:30 -> next is 09:15:05
        # Sync logic:
        now = datetime.datetime.now()
        minute = now.minute
        remainder = minute % 5
        # Minutes to add to reach next 5-min mark
        minutes_to_add = 5 - remainder
        next_check = now + datetime.timedelta(minutes=minutes_to_add)
        # Reset seconds/micro to 0 and add buffer
        next_check = next_check.replace(second=5, microsecond=0)
        
        if not self.running:
            logger.info("[Control] Stopping Strategy Loop.")
            return

        # --- INITIAL PULSE (Populate UI immediately) ---
        try:
            logger.info("📡 Performing Initial Market Analysis Pulse...")
            trend, ema9, ema21, rsi, adx, atr, regime = self.analyze_market_trend()
            htf_trend = self.calculate_htf_trend()
            self.last_analysis = {
                "ema9": ema9, "ema21": ema21, "rsi": rsi, 
                "htf_trend": htf_trend, "adx": adx,
                "atr": atr, "regime": regime
            }
            self.export_state()
            logger.info(f"✅ Initial Pulse Complete. Regime: {regime}")
        except Exception as e:
            logger.error(f"Initial Pulse Error: {e}")

        while self.running:
            # Check for stop signal
            if not self.running:
                logger.info("[Control] Stopping Strategy Loop.")
                break

            try:
                # --- FAST LOOP (Safety & Management) ---
                # Runs every iteration (~1 second)
                # 1. Active PnL & Sync Check (Synced every 15s)
                 if not self.dry_run and time.time() - self.last_sync_time > 15:
                      self.sync_state()
                      self.last_sync_time = time.time()

                if self.active_position:
                    # Check Trailing Stop & PnL (Throttled to 3s)
                    if time.time() - self.last_trailing_check > 3:
                        if self.check_trailing_stop():
                            pass # Triggered and Closed
                        
                        else:
                            # Calculate Real-Time PnL & Max Loss (Only if still active)
                            try:
                                token = self.active_position['token']
                                symbol = self.active_position['symbol']
                                entry_price = self.active_position['entry_price']
                                qty = self.active_position['qty']
                                
                                # Get LTP (Throttled)
                                curr_ltp = 0
                                now = time.time()
                                if hasattr(self, '_ltp_cache') and token in self._ltp_cache:
                                     last_time, last_val = self._ltp_cache[token]
                                     if now - last_time < 0.9: curr_ltp = last_val
                                
                                if curr_ltp == 0:
                                     ltp_check = self.api.ltpData("NFO", symbol, token)
                                     if ltp_check and ltp_check.get('status'):
                                         curr_ltp = float(ltp_check['data']['ltp'])
                                         self._ltp_cache[token] = (now, curr_ltp)

                                if curr_ltp > 0:
                                    curr_pnl = (curr_ltp - entry_price) * qty
                                    
                                    # Check against Max Daily Loss
                                    if not self.gatekeeper.check_max_daily_loss(curr_pnl):
                                        logger.error(f"🛑 ACTIVE MAX LOSS HIT (PnL: {curr_pnl}). Force Closing!")
                                        self.close_position("MAX_DAILY_LOSS")
                                        break # Stop strategy completely
                            except Exception as e:
                                logger.error(f"Active PnL Check Error: {e}")

                        self.last_trailing_check = time.time()

                # 2. Time Exit
                now_time = datetime.datetime.now().time()
                if not self.dry_run and now_time >= datetime.time(15, 15):
                    logger.info("Market Closed (15:15). Stopping Strategy.")
                    if self.active_position:
                        self.close_position("TIME_EXIT")
                    break

                # --- SLOW LOOP (Trend Analysis) ---
                # Only run if current time >= next_check
                if datetime.datetime.now() >= next_check:
                    logger.info(f"⏰ Candle Closed. Running Trend Analysis...")
                    
                    # 3. Analyze Trend (5-Minute)
                    trend, ema9, ema21, rsi, adx, atr, regime = self.analyze_market_trend()
                    
                    # 3.5 Analyze Higher Timeframe Trend (15-Minute)
                    htf_trend = self.calculate_htf_trend()
                    
                    # Store analysis for logging
                    self.last_analysis = {
                        "ema9": ema9, "ema21": ema21, "rsi": rsi, 
                        "htf_trend": htf_trend, "adx": adx,
                        "atr": atr, "regime": regime
                    }
                    self.export_state() # Export to UI
                    
                    logger.info(f"[Analysis] {trend} | Regime: {regime} | EMA9: {ema9:.2f} | RSI: {rsi:.2f} | ATR: {atr:.2f}")
                    
                    logger.info(f"[Analysis] 5m Trend: {trend} | 15m Trend: {htf_trend} | EMA9: {ema9:.2f} | RSI: {rsi:.2f}")
                    logger.info(f"[Active] {self.active_position['leg'] if self.active_position else 'None'}")
                    
                    # Check for Data Failure
                    if trend == "NEUTRAL" and rsi == 0 and self.active_position:
                        self.data_failure_count += 1
                        logger.warning(f"⚠️ Blind Mode Active ({self.data_failure_count}/3). Keeping Position.")
                        
                        if self.data_failure_count >= 3:
                            logger.error("🛑 Max Data Failures Reached. Force Exiting.")
                            self.close_position("DATA_LOSS_SAFETY")
                            break 
                    else:
                        self.data_failure_count = 0 # Reset on success
                    
                    # 4. Signal Logic
                    # If No Position: Enter based on Trend & RSI
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
                    
                    # If Active Position: Check for Reversal
                    else:
                        current_leg = self.active_position['leg']

                        # Alignment Check (Optional: Exit if trend reverses?)
                        # Exit CE if Bearish Crossover happens
                        if current_leg == "CE" and trend == "BEARISH":
                             logger.info("Signal: Trend Reversed to BEARISH. Exiting CE.")
                             self.close_position("REVERSAL")
                             if rsi > 30:
                                 self.enter_position(expiry, "PE") 
                             else:
                                 logger.info("Reversal Entry Ignored: RSI Oversold.")

                        # Exit PE if Bullish Crossover happens
                        elif current_leg == "PE" and trend == "BULLISH":
                             logger.info("Signal: Trend Reversed to BULLISH. Exiting PE.")
                             self.close_position("REVERSAL")
                             if rsi < 70:
                                 self.enter_position(expiry, "CE")
                             else:
                                 logger.info("Reversal Entry Ignored: RSI Overbought.")

                    # Schedule NEXT check
                    # Recalculate to stay in sync (avoid drift)
                    now = datetime.datetime.now()
                    minute = now.minute
                    remainder = minute % 5
                    minutes_to_add = 5 - remainder
                    next_check = now + datetime.timedelta(minutes=minutes_to_add)
                    next_check = next_check.replace(second=5, microsecond=0)
                    logger.info(f"⏳ Next Trend Check scheduled for: {next_check.strftime('%H:%M:%S')}")

                # Sleep significantly less for safety checks (Speed: 0.5s for fast reaction)
                time.sleep(0.5)
                
            except KeyboardInterrupt:
                logger.info("User Manual Stop.")
                break
            except Exception as e:
                logger.error(f"Loop Error: {e}")
                time.sleep(1)

    def analyze_market_trend(self):
        """
        Calculates trend using MarketService (which shares intelligence across processes)
        or falls back to manual fetch if MarketService is stale.
        """
        from backend.market_service import market_service
        
        # 0. Try using the shared intelligence from MarketService
        market_data = market_service.get_market_data()
        analysis = market_data.get('analysis', {})
        
        # If MarketService has fresh analysis, use it!
        if analysis and analysis.get('regime') != 'UNKNOWN':
            # logger.info(">>> [Strategy] Using Shared Market Analysis 📡")
            return (
                analysis.get('trend', 'NEUTRAL'),
                analysis.get('ema9', 0),
                analysis.get('ema21', 0),
                analysis.get('rsi', 0),
                analysis.get('adx', 0),
                analysis.get('atr', 20.0),
                analysis.get('regime', 'UNKNOWN')
            )

        # 1. Fallback: Fetch 5-min candles via DataFetcher if MarketService is unavailable/stale
        is_mock_api = self.api.__class__.__name__ == 'MockSmartConnect'
        
        if is_mock_api:
            df = self.get_mock_df()
        else:
            # Nifty 50 Index Token: 99926000
            # Note: Child processes (the bot) will hit this if shared state isn't ready.
            df = self.data_fetcher.fetch_latest_candles("99926000")
            
        if df is None or df.empty: 
            return "NEUTRAL", 0, 0, 0, 0, 0, "UNKNOWN"
        
        # 2. Regime Classification
        regime_meta = self.regime_classifier.classify(df)
        
        # 3. Periodic OI Sentiment Scan (Every 5 minutes)
        now = time.time()
        if now - self.last_oi_scan > 300: # 5 Minutes
            try:
                # Use shared OI data if available
                if market_data.get('oi_data'):
                    self.oi_data = market_data['oi_data']
                    self.last_oi_scan = now
                else:
                    # Manual fetch
                    ltp = df.iloc[-1]['close']
                    strike = int(round(ltp / 50) * 50)
                    expiry = get_next_weekly_expiry()
                    self.oi_data = self.oi_analyzer.get_market_sentiment(expiry, strike)
                    self.last_oi_scan = now
            except Exception as e:
                logger.error(f"Periodic OI Scan Error: {e}")

        # Signal Generation logic preserved for strategy
        signal = "NEUTRAL"
        if regime_meta['regime'] == "TRENDING":
            signal = regime_meta['trend']
        
        return (
            signal, 
            regime_meta['ema9'], 
            regime_meta['ema21'], 
            regime_meta['rsi'], 
            regime_meta['adx'], 
            regime_meta['atr'], 
            regime_meta['regime']
        )


    def calculate_htf_trend(self):
        """
        Calculates Trend on Higher Timeframe (15 Minutes).
        Returns: "BULLISH", "BEARISH", "NEUTRAL"
        """
        # Nifty 50 Token: 99926000
        # Check SmartAPI interval key: usually "FIFTEEN_MINUTE"
        if self.dry_run:
            # Randomize HTF trend for dry run
            return random.choice(["BULLISH", "BEARISH", "NEUTRAL"])

        df = self.data_fetcher.fetch_latest_candles("99926000", interval="FIFTEEN_MINUTE")
        
        if df is None or len(df) < 22: # Need enough for EMA21
            return "NEUTRAL"
            
        # Use Closed Candle logic here too? Yes, safer.
        last_closed = df.iloc[-2]
        
        # Calculate recent EMAs locally or on whole DF? 
        # Calculate on whole DF to get correct values
        df['EMA9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['EMA21'] = df['close'].ewm(span=21, adjust=False).mean()
        
        # Check validation of Last Closed Candle
        last_closed = df.iloc[-2]
        ema9 = last_closed['EMA9']
        ema21 = last_closed['EMA21']
        
        # Log Logic for debug
        # logger.info(f"HTF 15m: EMA9={ema9:.2f} EMA21={ema21:.2f}")

        if ema9 > ema21: return "BULLISH"
        if ema9 < ema21: return "BEARISH"
        return "NEUTRAL"

    def calculate_atr(self, df, period=14):
        """
        Calculates Average True Range (ATR).
        """
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
        """
        Calculates Bollinger Bandwidth.
        """
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
        """
        Calculates Average Directional Index (ADX).
        """
        try:
            df = df.copy()
            df['up_move'] = df['high'] - df['high'].shift(1)
            df['down_move'] = df['low'].shift(1) - df['low']
            
            df['pdm'] = 0.0
            df['ndm'] = 0.0
            
            # DM Logic
            df.loc[(df['up_move'] > df['down_move']) & (df['up_move'] > 0), 'pdm'] = df['up_move']
            df.loc[(df['down_move'] > df['up_move']) & (df['down_move'] > 0), 'ndm'] = df['down_move']
            
            # TR (True Range)
            df['tr1'] = df['high'] - df['low']
            df['tr2'] = abs(df['high'] - df['close'].shift(1))
            df['tr3'] = abs(df['low'] - df['close'].shift(1))
            df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
            
            # Smoothing
            # ATR
            df['atr'] = df['tr'].ewm(alpha=1/period, adjust=False).mean()
            
            # Smoothed DM
            df['pdm_s'] = df['pdm'].ewm(alpha=1/period, adjust=False).mean()
            df['ndm_s'] = df['ndm'].ewm(alpha=1/period, adjust=False).mean()
            
            # DI
            df['pdi'] = 100 * (df['pdm_s'] / df['atr'])
            df['ndi'] = 100 * (df['ndm_s'] / df['atr'])
            
            # DX
            df['dx'] = 100 * abs(df['pdi'] - df['ndi']) / (df['pdi'] + df['ndi'])
            
            # ADX
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
        return rsi.fillna(50) # Return 50 if NaN

    def enter_position(self, expiry, leg):
        # 1. RISK CALCULATION (Volatility Based)
        # Fetch Nifty LTP for Strike Logic
        nifty_ltp = self.get_nifty_ltp()
        if not nifty_ltp:
            logger.error("Could not fetch Nifty LTP for Entry.")
            return

        # Context from Analysis
        atr = self.last_analysis.get('atr', 20.0) # Default to 20 if missing
        if atr == 0: atr = 20.0
        
        # SL Distance = 2 * ATR
        sl_points = 2 * atr
        
        # Risk Per Trade (Fixed Dollar Amount)
        RISK_PER_TRADE = 2000.0 # Configurable
        
        # Effective Risk per Qty ? 
        # Option Delta is roughly 0.5 (ATM). So Option moves 0.5 * Index.
        # Option SL Points = Index SL Points * Delta = (2 * ATR) * 0.5 = ATR
        # So Risk per Qty = ATR * LotSize? No.
        # Risk = Qty * Option_SL_Points
        # Qty = Risk / Option_SL_Points
        # Option_SL_Points approx ATR (since 2*ATR index ~ 1*ATR option price? Roughly)
        # Let's be safer: Assume Option moves 1:1 in worst case or just use Index ATR directly for sizing logic?
        # Better: Option Price SL = Option Entry - Option SL.
        # We don't know Option Entry yet.
        # Estimation: Option ATR approx Index ATR * 0.5. 
        # Let's use Index ATR based Stop.
        # Stop Loss in Index = 2 * ATR.
        # Stop Loss in Option Premium ~= 1 * ATR (assuming Delta 0.5).
        
        option_sl_points = atr # Approx
        
        # Quantity Calculation
        # Qty = Risk / Loss_Per_Qty
        if option_sl_points < 5: option_sl_points = 5 # Min Protection
        
        calc_qty = int(RISK_PER_TRADE / option_sl_points)
        
        # Round to Lot Size (25)
        lot_size = Config.NIFTY_LOT_SIZE
        # Lots = calc_qty // lot_size
        lots = max(1, int(calc_qty / lot_size))
        
        qty = lots * lot_size
        
        logger.info(f"⚖️ Sizing: ATR={atr:.2f} | Risk=₹{RISK_PER_TRADE} | Est. Option SL={option_sl_points:.1f} pts | Qty={qty} ({lots} lots)")

        # Sentiment Check
        direction = "LONG" if leg == "CE" else "SHORT"
        if not self.gatekeeper.check_sentiment_risk(direction):
             logger.warning(f"Trade Skipped due to Sentiment Risk.")
             return

        # EXPIRY GUARD
        try:
             today_str = datetime.datetime.now().strftime("%d%b%Y").upper()
             if expiry == today_str:
                 now = datetime.datetime.now().time()
                 if now >= datetime.time(13, 30):
                     logger.warning("⛔ Expiry Day Safety: Blocking new entries after 1:30 PM.")
                     return
        except Exception as e:
             logger.error(f"Expiry Guard Check Error: {e}")

        # Strike Selection
        strike = round(nifty_ltp / 50) * 50
        token, symbol = self.token_loader.get_token("NIFTY", expiry, strike, leg)
        if not token: 
            logger.error(f"Token not found for {strike} {leg}")
            return
        
        # Margin Check (Real Price)
        quote_ltp = 0
        try:
             q_resp = self.api.ltpData("NFO", symbol, token)
             if q_resp and q_resp.get('status'):
                 quote_ltp = float(q_resp['data']['ltp'])
        except Exception as e:
            logger.warning(f"Could not fetch option LTP for margin check: {e}")
            
        estimated_cost = quote_ltp * qty
        if estimated_cost > 0:
             if not self.dry_run and not self.gatekeeper.check_trade_margin(estimated_cost):
                 logger.warning(f"Risk: Insufficient Funds (Cost: {estimated_cost})")
                 return
        
        # PLACE ORDER
        logger.info(f"Trade: Entering {leg} ({symbol}) Qty: {qty} Price: {quote_ltp} Cost: {estimated_cost}")
        
        trade_context = {
            'entry_ema9': self.last_analysis.get('ema9', 0),
            'entry_ema21': self.last_analysis.get('ema21', 0),
            'entry_rsi': self.last_analysis.get('rsi', 0),
            'entry_adx': self.last_analysis.get('adx', 0),
            'entry_atr': atr,
            'regime': self.last_analysis.get('regime', 'UNKNOWN'),
            'htf_trend': self.last_analysis.get('htf_trend', "N/A")
        }

        # Calculate OPTION Stop Loss Price
        # Option SL = Entry - ATR (Dynamic)
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
             orderparams = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "BUY", "exchange": "NFO", "ordertype": "MARKET",
                "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
             }
             oid = self.api.placeOrder(orderparams)
             
             if not oid:
                 logger.error("❌ Order Placement Failed! (API returned None). Check 'logs/YYYY-MM-DD/app.log' for details.")
                 return

             logger.info(f"Success: Order Placed: {oid}")
            
             # 5. Wait for Fill (New Resilience Logic)
             fill_price = self.wait_for_fill(oid)
             if not fill_price:
                  fill_price = quote_ltp # Fallback to estimate if poll fails
                  logger.warning(f"Momentum: Fill price not captured, using estimate: ₹{fill_price}")

             # Recalculate SL based on ACTUAL fill
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
             
             # Place Hard SL
             sl_oid = self.place_stop_loss(token, symbol, actual_sl, qty)
             if sl_oid:
                 self.active_position['sl_order_id'] = sl_oid
        except Exception as e:
             logger.error(f"Enter Order Failure: {e}")

    def wait_for_fill(self, order_id):
        """Polls for order completion to get average fill price."""
        if not order_id: return None
        if self.dry_run: return 100.0
        
        for _ in range(10):
            try:
                time.sleep(1)
                book = self.api.orderBook()
                if book and book.get('data'):
                    for o in book['data']:
                        if o['orderid'] == order_id:
                            if o['status'] == 'complete':
                                return float(o['averageprice'])
                            elif o['status'] in ['rejected', 'cancelled']:
                                return None
            except: pass
        return None

    def place_stop_loss(self, token, symbol, price, qty):
        """Places a Hard Stop Loss Order."""
        if self.dry_run: return "dry_run_sl"
        try:
             # SL Order for BUY is SELL SL
             # Trigger slightly higher than limit price
             trig = round(price + 0.5, 1)
             orderparams = {
                "variety": "STOPLOSS", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "SELL", "exchange": "NFO", "ordertype": "STOPLOSS_LIMIT",
                "producttype": "INTRADAY", "duration": "DAY", "triggerprice": trig, "price": price, "quantity": qty
            }
             oid = self.api.placeOrder(orderparams)
             logger.info(f"🛡️ Hard SL Placed: {symbol} @ {price} | ID: {oid}")
             return oid
        except Exception as e:
             logger.error(f"SL Place Error: {e}")
             return None


    def close_position(self, reason):
        if not self.active_position: return
        
        symbol = self.active_position['symbol']
        token = self.active_position['token']
        qty = self.active_position['qty']
        
        logger.info(f"Exit: Closing {symbol} due to {reason}")
        
        # Determine Exit Price for Logging (Paper Trading uses Real LTP)
        exit_price = 0
        try:
             q_resp = self.api.ltpData("NFO", symbol, token)
             if q_resp and q_resp.get('status'):
                 exit_price = float(q_resp['data']['ltp'])
        except: pass
        
        # Fallback if API fails
        if exit_price == 0: 
            exit_price = self.active_position.get('entry_price', 0)

        # Cancel Pending SL
        sl_oid = self.active_position.get('sl_order_id')
        if sl_oid:
            try:
                self.api.cancelOrder(sl_oid, "STOPLOSS")
                logger.info(f"Cleanup: Cancelled SL Order {sl_oid}")
            except: pass

        # LOGGING
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
        except Exception as e:
             logger.error(f"Journal Error: {e}")

        if not self.dry_run:
            try:
                orderparams = {
                    "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                    "transactiontype": "SELL", "exchange": "NFO", "ordertype": "MARKET",
                    "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
                }
                oid = self.api.placeOrder(orderparams)
                logger.info(f"Success: Exit Order Placed: {oid}")
            except Exception as e:
                logger.error(f"Exit Order Failure: {e}")
        else:
            logger.info(f"Dry Run: Simulated Exit for {symbol}")

        # Close in DB: ALWAYS do this (Dry Run or Real)
        try:
             trade_repo.close_trade(
                 symbol=symbol, 
                 exit_price=exit_price, 
                 pnl=round(pnl, 2), 
                 exit_reason=reason
             )
             self.active_position = None
        except Exception as e:
             logger.error(f"DB Close Error: {e}")

    def check_trailing_stop(self):
        """
        ATR-Based Trailing Stop.
        """
        if not self.active_position: return False
        
        token = self.active_position['token']
        symbol = self.active_position['symbol']
        entry_price = self.active_position.get('entry_price', 0.0)
        current_sl = self.active_position.get('sl_price', 0.0)
        atr_at_entry = self.active_position.get('atr', 20.0)
        
        if entry_price == 0: return False 
        
        ltp = 0.0
        try:
             q_resp = self.api.ltpData("NFO", symbol, token)
             if q_resp and q_resp.get('status'):
                 ltp = float(q_resp['data']['ltp'])
        except Exception as e:
            logger.warning(f"Trailing Stop LTP fetch error: {e}")
            return False
        if ltp == 0: return False
        
        # 1. Check if SL Hit
        if current_sl > 0 and ltp <= current_sl:
            logger.info(f"🛑 Trailing Stop Hit! Price: {ltp} <= SL: {current_sl}")
            self.close_position("TRAILING_STOP")
            return True
            
        # 2. Dynamic Trailing (ATR Step)
        # Rule: If Price > Entry + (N * ATR), Move SL to Price - ATR
        # Actually simpler: Trail at (High - 1.5 ATR) or Step?
        # Let's use Step Ladder but sized by ATR
        # Step Size = 0.5 * ATR
        
        profit_points = ltp - entry_price
        
        # Define Trailing Logic
        # Option ATR approx atr_at_entry (Index ATR) * 0.5 ? Or just use entry ATR as Option ATR proxy?
        # Let's treat atr_at_entry as "Option ATR" (calculated in enter_position effectively)
        # Wait, in enter_position we used Index ATR as 'atr' context but option_sl_points was roughly Index ATR.
        
        # Let's assume Option ATR ~= Index ATR / 2 (Delta 0.5)
        opt_atr = atr_at_entry * 0.5
        if opt_atr < 5: opt_atr = 5
        
        # If Profit > 1 ATR, Move SL to Breakeven
        if profit_points > (1.0 * opt_atr) and current_sl < entry_price:
            new_sl = entry_price + 1.0 # Breakeven + cost
            self.update_sl(new_sl, ltp)
            return False

        # If Profit > 2 ATR, Trail trend
        if profit_points > (2.0 * opt_atr):
            # Target SL = LTP - 1 ATR (Tighten)
            target_sl = ltp - (1.0 * opt_atr)
            if target_sl > current_sl:
                self.update_sl(target_sl, ltp)
                
        return False

    def update_sl(self, new_sl, ltp):
        new_sl = round(new_sl, 1)
        self.active_position['sl_price'] = new_sl
        logger.info(f"📈 SL Moved Up to {new_sl} (LTP: {ltp})")
        if 'id' in self.active_position:
            trade_repo.update_sl(self.active_position['id'], new_sl)

    def get_nifty_ltp(self):
        try:
             from backend.market_service import market_service
             data = market_service.get_market_data()
             return data.get('nifty', 0.0)
        except Exception as e:
            logger.error(f"LTP Fetch Error: {e}")
            return 0.0

    def get_mock_df(self):
         # Toggle trend based on time? Or just random
         close = 22000 + random.randint(-50, 50)
         # Generate enough rows for EMA/RSI
         data = []
         for i in range(100): # More data for Indicators
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
            self.data_fetcher.api = new_api # Update data fetcher too
            logger.info("System: ✅ Re-login Successful! Session refreshed.")
            return True
        else:
            logger.error("System: ❌ Re-login Failed.")
            return False

    def get_current_position(self):
        """Returns the active position details for UI."""
        return self.active_position

    def stop(self):
        """Graceful Square-off on Shutdown"""
        if self.active_position:
            logger.info(f">>> [Strategy] Stop Signal Received. Squaring off {self.active_position['symbol']}...")
            self.close_position("MANUAL_STOP")
        else:
            logger.info(">>> [Strategy] Stop Signal Received. No active position to close.")
