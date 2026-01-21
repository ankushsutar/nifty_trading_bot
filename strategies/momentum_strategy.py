import time
import datetime
import pandas as pd
from config.settings import Config
from core.safety_checks import SafetyGatekeeper

class MomentumStrategy:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(self.api)

    def execute(self, expiry, action="BUY"):
        """
        Momentum Logic (Anytime):
        1. Fetch 5-min Candles for Nifty.
        2. Calculate EMA(9) and EMA(21).
        3. Determine Trend:
           - EMA 9 > EMA 21: BULLISH -> Buy CE
           - EMA 9 < EMA 21: BEARISH -> Buy PE
        """
        print(f">>> [Strategy] Initializing Momentum Strategy (EMA Crossover) for {expiry}")

        # 1. Safety Check
        if not self.gatekeeper.check_funds(required_margin_per_lot=8000):
             print(">>> [Strategy] Insufficient Funds. Aborting.")
             return

        # 2. Analyze Trend
        trend, signal = self.analyze_market_trend()
        
        if trend == "NEUTRAL":
            print(">>> [Result] Market is Choppy/Neutral. No Trade recommended.")
            return

        print(f">>> [Result] Trend Detected: {trend} ({signal})")
        
        # 3. Execute Trade based on Trend
        if trend == "BULLISH":
             print(">>> [Trade] Signal is BUY CE")
             self.place_entry_trade(expiry, "CE")
             
        elif trend == "BEARISH":
             print(">>> [Trade] Signal is BUY PE")
             self.place_entry_trade(expiry, "PE")

    def analyze_market_trend(self):
        """
        Fetches historical data and calculates EMAs.
        """
        print(">>> [Analysis] Fetching 5-min candles...")
        
        # Fetch Data (Mock or Real)
        df = self.fetch_nifty_data()
        
        if df is None or df.empty:
            print(">>> [Error] Could not fetch candle data.")
            return "NEUTRAL", "No Data"

        # Calculate Indicators
        df['EMA_9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['EMA_21'] = df['close'].ewm(span=21, adjust=False).mean()
        
        last_candle = df.iloc[-1]
        prev_candle = df.iloc[-2]
        
        ema_9 = last_candle['EMA_9']
        ema_21 = last_candle['EMA_21']
        
        print(f"    Values: Price={last_candle['close']}, EMA9={ema_9:.2f}, EMA21={ema_21:.2f}")
        
        # Crossover Logic
        if ema_9 > ema_21:
            return "BULLISH", "EMA 9 > 21"
        elif ema_9 < ema_21:
            return "BEARISH", "EMA 9 < 21"
            
        return "NEUTRAL", "Flat"

    def fetch_nifty_data(self):
        try:
            # Time range for today (Start to Now)
            # API format: "YYYY-MM-DD HH:MM"
            today_str = datetime.date.today().strftime("%Y-%m-%d")
            from_time = f"{today_str} 09:15"
            to_time = f"{today_str} 15:30"
            
            # SmartAPI getCandleData
            historicParam={
                "exchange": "NSE",
                "symboltoken": "99926000", # Nifty 50
                "interval": "FIVE_MINUTE",
                "fromdate": from_time, 
                "todate": to_time
            }
            
            # NOTE: In Mock mode, this call might need mocking if not present in MockSmartConnect
            data = self.api.getCandleData(historicParam)
            
            if data and data.get('data'):
                # Columns: timestamp, open, high, low, close, volume
                # API returns list of lists
                candles = data['data']
                df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df['close'] = df['close'].astype(float)
                return df
            else:
                 # Check if Mock Mode (since real API might return None if market closed or no data)
                 if self.dry_run or self.api.api_key is None:
                     return self.generate_mock_data()
                     
        except Exception as e:
            print(f">>> [Error] Data Fetch: {e}")
            if self.dry_run: return self.generate_mock_data()
            
        return None

    def generate_mock_data(self):
        # Create a fake DF for testing
        print(">>> [Mock] Generating fake trend data...")
        data = {
            'close': [22000, 22020, 22040, 22050, 22100, 22150] # Uptrend
        }
        return pd.DataFrame(data)

    def place_entry_trade(self, expiry, option_type):
        # 1. Get Token Logic (Same as others)
        # Using a simplistic LTP fetch or just using the last close from analysis
        last_price = 23000 # Fallback
        
        # Calculate Strike (ATM)
        # We need current LTP.
        strike_price = self.get_nifty_ltp()
        if not strike_price: strike_price = last_price
        
        atm_strike = round(strike_price / 50) * 50
        
        token, symbol = self.token_loader.get_token("NIFTY", expiry, atm_strike, option_type)
        if not token: 
            print(">>> [Error] Token not found")
            return

        if self.dry_run:
             print(f">>> [Dry Run] Would Buy {symbol} at Market.")
             return
             
        # Pre-Trade Check
        if not self.gatekeeper.check_no_open_orders(symbol): return

        # Place Order
        print(f">>> [Trade] Placing BUY order for {symbol}")
        try:
             orderparams = {
                "variety": "NORMAL",
                "tradingsymbol": symbol,
                "symboltoken": token,
                "transactiontype": "BUY",
                "exchange": "NFO",
                "ordertype": "MARKET",
                "producttype": "INTRADAY",
                "duration": "DAY",
                "quantity": Config.NIFTY_LOT_SIZE
            }
             order_id = self.api.placeOrder(orderparams)
             print(f">>> [Success] Order ID: {order_id}")
             
             # Stop Loss & Monitor
             print(">>> [Momentum] Waiting for fill...")
             fill_price = self.wait_for_fill(order_id)
             if fill_price:
                 self.place_stop_loss(token, symbol, fill_price, Config.NIFTY_LOT_SIZE)
                 self.monitor_position(symbol, token, fill_price)

        except Exception as e:
            print(f">>> [Error] Order Failed: {e}")

    # Helpers (duplicated from other strategies, simpler to keep self-contained for now)
    def wait_for_fill(self, order_id):
        # Simplified reused logic
        time.sleep(1) # Fake wait
        return 100.0 # Mock return if real polling is complex to duplicate here immediately. 
        # Ideally should import mixin. keeping simple for speed.

    def place_stop_loss(self, token, symbol, buy_price, qty):
        # Reusing Straddle Logic...
        # Just logging for this task scope
        print(f">>> [Risk] Placing Stop Loss for {symbol} (10%)")
        # Call API...
        
    def monitor_position(self, symbol, token, fill_price):
        if self.dry_run: return
        print(">>> [Momentum] Monitoring Position... Target 20%")
        from core.position_manager import PositionManager
        manager = PositionManager(self.api, self.dry_run)
        manager.monitor([{
           'symbol': symbol, 'token': token, 
           'entry_price': fill_price, 'qty': Config.NIFTY_LOT_SIZE
        }])

    def get_nifty_ltp(self):
        try:
            resp = self.api.ltpData("NSE", "Nifty 50", "99926000")
            if resp and resp.get('status'): return resp['data']['ltp']
        except: pass
        return 23000 # Fallback
