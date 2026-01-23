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

class MomentumStrategy:
    STATE_FILE = "trade_state.json"

    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(self.api)
        self.data_fetcher = DataFetcher(self.api)
        self.data_failure_count = 0
        self.active_position = None 
        self.stop_requested = False  # Control flag for API
        self.load_state() # Restore state on startup

    def save_state(self):
        try:
            with open(self.STATE_FILE, 'w') as f:
                json.dump(self.active_position, f)
        except Exception as e:
            logger.error(f"Save State Error: {e}")

    def load_state(self):
        if not os.path.exists(self.STATE_FILE): return
        try:
            with open(self.STATE_FILE, 'r') as f:
                data = json.load(f)
                if data:
                    self.active_position = data
                    logger.info(f"♻️ Restored Active Position from State: {data['symbol']}")
        except Exception as e:
            logger.error(f"Load State Error: {e}")

    def stop(self):
        """Signals the loop to stop."""
        self.stop_requested = True
        logger.info("[Control] Stop Requested from API.")

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
            self.save_state()
            
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
        
        last = df.iloc[-1]
        ema9 = last['EMA9']
        ema21 = last['EMA21']
        rsi = last['RSI']
        
        if ema9 > ema21: return "BULLISH", ema9, ema21, rsi
        if ema9 < ema21: return "BEARISH", ema9, ema21, rsi
        return "NEUTRAL", ema9, ema21, rsi

    def calculate_rsi(self, df, period=14):
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/period, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/period, adjust=False).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50) # Return 50 if NaN

    def enter_position(self, expiry, leg):
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
            self.active_position = {
                'leg': leg, 'symbol': symbol, 'qty': qty, 'token': token, 
                'entry_price': quote_ltp, 'sl_price': 0
            }
            return

        try:
             orderparams = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "BUY", "exchange": "NFO", "ordertype": "MARKET",
                "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
            }
             oid = self.api.placeOrder(orderparams)
             logger.info(f"Success: Order Placed: {oid}")
             self.active_position = {
                'leg': leg, 'symbol': symbol, 'qty': qty, 'token': token, 
                'entry_price': quote_ltp, 'sl_price': 0 
            }
             self.save_state()
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
             self.active_position = None
             self.save_state()
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
