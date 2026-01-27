import time
import datetime
import pandas as pd
import json
import os
import random

from config.settings import Config
from core.angel_connect import get_angel_session
from core.safety_checks import SafetyGatekeeper
from core.data_fetcher import DataFetcher
from utils.logger import logger

from utils.file_ops import write_json_atomic

# ... imports ...

class MomentumStrategy:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(self.api)
        self.data_fetcher = DataFetcher(self.api)
        self.data_failure_count = 0
        self.active_position = None 
        self.stop_requested = False  # Control flag for API
        self.last_sync_time = 0
        self.sync_state() # Initial Sync with Broker

    def sync_state(self):
        """
        Synchronizes active position from Broker API.
        Current Rule: Looks for the FIRST active NIFTY Intraday position.
        """
        if self.dry_run: return # No sync in dry run
        
        try:
             # logger.info("System: 🔄 Syncing State with Broker...")
             pos_resp = self.api.position()
             
             if pos_resp and pos_resp.get('status') and pos_resp.get('data'):
                 found_active = None
                 
                 for pos in pos_resp['data']:
                     # Filter for NIFTY Options, Intraday, and Open (NetQty != 0)
                     # Note: SmartAPI 'symbolname' handles 'NIFTY', 'BANKNIFTY' etc.
                     # 'netqty' is the open quantity.
                     if (pos['symbolname'] == 'NIFTY' and 
                         pos['producttype'] == 'INTRADAY' and 
                         int(pos['netqty']) != 0):
                         
                         qty = int(pos['netqty'])
                         # If qty > 0 (LONG), < 0 (SHORT). We usually Buy options so Qty > 0.
                         # If we sold (Short Strategy), Qty < 0.
                         
                         found_active = {
                             'leg': "CE" if "CE" in pos['symbolnm'] else "PE", # simplistic
                             'symbol': pos['tradingsymbol'],
                             'token': pos['symboltoken'],
                             'qty': abs(qty),
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
                     self.active_position = None
                     
        except Exception as e:
            logger.error(f"Sync State Error: {e}")

    def stop(self):
        """Signals the loop to stop and closes open positions."""
        self.stop_requested = True
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
            logger.info(f"📈 SL Moved Up to {new_sl} (Profit: {profit_pts:.2f})")
            # self.save_state() # No more file save
            
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
        if not self.dry_run:
            if not self.gatekeeper.check_funds(required_margin_per_lot=5000): return
        if not self.gatekeeper.check_max_daily_loss(0): return
        if self.gatekeeper.is_blackout_period(): return

        # 1. Continuous Monitor Loop
        logger.info("Starting Continuous Monitor for Crossover...")
        
        while True:
            if self.stop_requested:
                logger.info("[Control] Stopping Strategy Loop.")
                break

            try:
                # Time Check
                now = datetime.datetime.now().time()
                # Only enforce time exit if NOT in dry_run or if explicitly desired
                if not self.dry_run and now >= datetime.time(15, 15):
                    logger.info("Market Closed (15:15). Stopping Strategy.")
                    self.close_position("TIME_EXIT")
                    break

                # 1.5 ACTIVE PNL & SYNC CHECK
                # Sync every 15 seconds to detect external closures
                if not self.dry_run and time.time() - self.last_sync_time > 15:
                     self.sync_state()
                     self.last_sync_time = time.time()

                if self.active_position:
                    # Calculate Real-Time PnL
                    try:
                        token = self.active_position['token']
                        symbol = self.active_position['symbol']
                        entry_price = self.active_position['entry_price']
                        qty = self.active_position['qty']
                        
                        # Get LTP
                        ltp_check = self.api.ltpData("NFO", symbol, token)
                        if ltp_check and ltp_check.get('status'):
                            curr_ltp = float(ltp_check['data']['ltp'])
                            curr_pnl = (curr_ltp - entry_price) * qty
                            
                            # Check against Max Daily Loss
                            if not self.gatekeeper.check_max_daily_loss(curr_pnl):
                                logger.error(f"🛑 ACTIVE MAX LOSS HIT (PnL: {curr_pnl}). Force Closing!")
                                self.close_position("MAX_DAILY_LOSS")
                                break # Stop strategy completely
                    except Exception as e:
                        logger.error(f"Active PnL Check Error: {e}")

                # 2. Analyze Trend
                trend, ema9, ema21, rsi = self.analyze_market_trend()
                logger.info(f"[Analysis] Trend: {trend} | EMA9: {ema9:.2f} | EMA21: {ema21:.2f} | RSI: {rsi:.2f} | Active: {self.active_position['leg'] if self.active_position else 'None'}")
                
                # Check for Data Failure
                if trend == "NEUTRAL" and rsi == 0 and self.active_position:
                    self.data_failure_count += 1
                    logger.warning(f"⚠️ Blind Mode Active ({self.data_failure_count}/3). Keeping Position.")
                    
                    if self.data_failure_count >= 3:
                        logger.error("🛑 Max Data Failures Reached. Force Exiting.")
                        self.close_position("DATA_LOSS_SAFETY")
                        break 
                    
                    time.sleep(60 if self.dry_run else 60)
                    continue
                else:
                    self.data_failure_count = 0 # Reset on success
                
                # 3. Logic
                # If No Position: Enter based on Trend & RSI
                if not self.active_position:
                    if trend == "BULLISH":
                        if rsi < 70:
                            self.enter_position(expiry, "CE")
                        else:
                            logger.info("Signal Ignored: Bullish but RSI Overbought (>70).")
                    elif trend == "BEARISH":
                        if rsi > 30:
                            self.enter_position(expiry, "PE")
                        else:
                            logger.info("Signal Ignored: Bearish but RSI Oversold (<30).")
                
                # If Active Position: Check for Reversal
                else:
                    current_leg = self.active_position['leg']

                    # A. Check Trailing Stop
                    if self.check_trailing_stop():
                        pass
                    
                    # B. Exit CE if Bearish Crossover happens
                    elif current_leg == "CE" and trend == "BEARISH":
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

                time.sleep(60 if self.dry_run else 60)
                
            except KeyboardInterrupt:
                logger.info("User Manual Stop.")
                break
            except Exception as e:
                logger.error(f"Loop Error: {e}")
                time.sleep(10)

    def analyze_market_trend(self):
        # Fetch 5-min candles via DataFetcher
        # Using Index Token or Mock
        if self.dry_run:
            df = self.get_mock_df()
        else:
             # Nifty 50 Index Token: 99926000
            df = self.data_fetcher.fetch_latest_candles("99926000")
            
        if df is None or df.empty: return "NEUTRAL", 0, 0, 0
        
        # Calc EMA
        df['EMA9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['EMA21'] = df['close'].ewm(span=21, adjust=False).mean()
        
        # Calc RSI
        df['RSI'] = self.calculate_rsi(df)

        # Calc ADX (Trend Strength)
        df['ADX'] = self.calculate_adx(df)
        
        # PRO TRADER FIX: Use Closed Candle (iloc[-2]) to avoid Repainting
        # iloc[-1] is the currently forming candle, which changes every second.
        if len(df) < 2: return "NEUTRAL", 0, 0, 0
        
        last_closed = df.iloc[-2] 
        current_forming = df.iloc[-1]
        
        ema9 = last_closed['EMA9']
        ema21 = last_closed['EMA21']
        rsi = last_closed['RSI']
        adx = last_closed['ADX']
        
        # Trend Filter: ADX > 20 (Strong Trend)
        trend_strength = "WEAK" if adx < 20 else "STRONG"
        
        if adx < 20:
             # logger.info(f"[Filter] Market Choppy (ADX: {adx:.2f} < 20). Staying Neutral.")
             return "NEUTRAL", ema9, ema21, rsi
        
        if ema9 > ema21: return "BULLISH", ema9, ema21, rsi
        if ema9 < ema21: return "BEARISH", ema9, ema21, rsi
        return "NEUTRAL", ema9, ema21, rsi

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
        # Sentiment Check
        direction = "LONG" if leg == "CE" else "SHORT"
        if not self.gatekeeper.check_sentiment_risk(direction):
             logger.warning(f"Trade Skipped due to Sentiment Risk.")
             return

        # EXPIRY GUARD: No new trades after 1:30 PM on Expiry Day
        # Expiry Format: 27JAN2026
        try:
             today_str = datetime.datetime.now().strftime("%d%b%Y").upper()
             if expiry == today_str:
                 now = datetime.datetime.now().time()
                 if now >= datetime.time(13, 30):
                     logger.warning("⛔ Expiry Day Safety: Blocking new entries after 1:30 PM.")
                     return
        except Exception as e:
             logger.error(f"Expiry Guard Check Error: {e}")

        # VIX Sizing
        mult = self.gatekeeper.get_vix_adjustment()
        adjusted_lots = max(1, int(mult))
        qty = int(Config.NIFTY_LOT_SIZE * adjusted_lots)
        
        # Strike
        ltp = self.get_nifty_ltp()
        if not ltp: 
            logger.error("Could not fetch Nifty LTP for Strike Selection.")
            return

        strike = round(ltp / 50) * 50
        
        token, symbol = self.token_loader.get_token("NIFTY", expiry, strike, leg)
        if not token: 
            logger.error(f"Could not find token for {strike} {leg}")
            return
        
        # Determine actual cost
        quote_ltp = 0
        try:
             # Fetch LTP for the specific option to check margin
             q_resp = self.api.ltpData("NFO", symbol, token)
             if q_resp and q_resp.get('status'):
                 quote_ltp = float(q_resp['data']['ltp'])
        except Exception as e:
            logger.warning(f"Could not fetch option LTP for margin check: {e}")
            
        estimated_cost = quote_ltp * qty
        if estimated_cost > 0:
             # Only check margin if NOT in dry run
             if not self.dry_run and not self.gatekeeper.check_trade_margin(estimated_cost):
                 logger.warning(f"Risk: Trade Skipped due to Insufficient Funds (Cost: {estimated_cost})")
                 return
        
        logger.info(f"Trade: Entering {leg} ({symbol}) Qty: {qty} Price: {quote_ltp} Cost: {estimated_cost}")
        
        if self.dry_run:
            sl_offset = min(20, quote_ltp * 0.2)
            self.active_position = {
                'leg': leg, 'symbol': symbol, 'qty': qty, 'token': token, 
                'entry_price': quote_ltp, 'sl_price': quote_ltp - sl_offset  # Smart SL
            }
            return

        try:
             orderparams = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "BUY", "exchange": "NFO", "ordertype": "MARKET",
                "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
            }
             self.api.placeOrder(orderparams)
             logger.info(f"Success: Order Placed: {oid}")
            
             sl_offset = min(20, quote_ltp * 0.2)
             self.active_position = {
                'leg': leg, 'symbol': symbol, 'qty': qty, 'token': token, 
                'entry_price': quote_ltp, 'sl_price': quote_ltp - sl_offset # Smart SL 
            }
             # self.save_state()
        except Exception as e:
             logger.error(f"Enter Order Failure: {e}")

    def close_position(self, reason):
        if not self.active_position: return
        
        symbol = self.active_position['symbol']
        token = self.active_position['token']
        qty = self.active_position['qty']
        
        logger.info(f"Exit: Closing {symbol} due to {reason}")
        if self.dry_run:
            self.active_position = None
            return

        try:
             orderparams = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "SELL", "exchange": "NFO", "ordertype": "MARKET",
                "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
            }
             oid = self.api.placeOrder(orderparams)
             logger.info(f"Success: Exit Order Placed: {oid}")
             logger.info(f"Success: Exit Order Placed: {oid}")
             self.active_position = None
             # self.save_state()
        except Exception as e:
             logger.error(f"Exit Order Failure: {e}")

    def get_nifty_ltp(self):
        try:
            resp = self.api.ltpData("NSE", "Nifty 50", "99926000")
            if resp: return resp['data']['ltp']
        except: pass
        return None

    def get_mock_df(self):
         # Toggle trend based on time? Or just random
         close = 22000 + random.randint(-50, 50)
         # Generate enough rows for EMA/RSI
         data = []
         for i in range(50):
             data.append({'close': 22000 + (i * 10) + random.randint(-5, 5)})
         
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
