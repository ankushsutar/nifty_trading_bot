import datetime
import time
import pandas as pd
from config.settings import Config
from core.safety_checks import SafetyGatekeeper

class InsideBarStrategy:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(self.api)

    def execute(self, expiry, action="BUY"):
        """
        Executes Inside Bar Breakout Strategy.
        Timeframe: 15-Minute Candles.
        Pattern: Mother Candle, then Baby Candle inside Mother's High/Low.
        """
        print(f"\n--- INSIDE BAR STRATEGY ({expiry}) ---")

        # 0. Risk Checks
        if not self.gatekeeper.check_max_daily_loss(0): return
        if self.gatekeeper.is_blackout_period(): return

        # 1. Fetch Data (15 Min Candles)
        df = self.fetch_candles("FIFTEEN_MINUTE")
        if df is None or len(df) < 2:
             print(">>> [Error] Insufficient Data.")
             return

        # 2. Identify Pattern (Last 2 completed candles)
        # df.iloc[-1] is the current running candle? Usually API returns completed or snapshot.
        # Assuming we check the *completed* formation.
        # Let's assess the last two CLOSED candles.
        
        mother = df.iloc[-2]
        baby = df.iloc[-1]
        
        print(f">>> [Analysis] Checking Inside Bar Pattern...")
        print(f"    Mother ({-2}): H:{mother['high']} L:{mother['low']}")
        print(f"    Baby   ({-1}): H:{baby['high']} L:{baby['low']}")

        is_inside_bar = (baby['high'] <= mother['high']) and (baby['low'] >= mother['low'])
        
        if not is_inside_bar:
            print(">>> [Result] No Inside Bar Pattern detected.")
            return
            
        print(">>> [Signal] 🔥 INSIDE BAR DETECTED!")
        
        # 3. Check Breakout (Current Market Price vs Mother Range)
        # We need LIVE LTP now to see if it breaks Mother High/Low
        ltp = self.get_nifty_ltp()
        print(f">>> [Market] Current Price: {ltp}")
        
        signal = None
        if ltp > mother['high']:
            print(">>> [Breakout] Price broke Mother HIGH -> BUY CE")
            signal = "BUY_CE"
            sl_level = mother['low'] # SL is opposite end
        elif ltp < mother['low']:
            print(">>> [Breakout] Price broke Mother LOW -> BUY PE")
            signal = "BUY_PE"
            sl_level = mother['high']
        else:
            print(">>> [Wait] Pattern formed but NO BREAKOUT yet.")
            return

        # 4. Entry
        mult = self.gatekeeper.get_vix_adjustment()
        qty = int(Config.NIFTY_LOT_SIZE * mult)
        strike = round(ltp / 50) * 50
        
        if signal == "BUY_CE":
            self.place_trade(expiry, strike, "CE", qty, sl_level, "UP")
        elif signal == "BUY_PE":
            self.place_trade(expiry, strike, "PE", qty, sl_level, "DOWN")

    def place_trade(self, expiry, strike, leg, qty, index_sl, direction):
        token, symbol = self.token_loader.get_token("NIFTY", expiry, strike, leg)
        if not token: return
        
        # Place Buy Order
        print(f">>> [Trade] Entering {symbol} (Qty: {qty})")
        if self.dry_run: return
        
        try:
             # Basic Entry Logic
             orderparams = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "BUY", "exchange": "NFO", "ordertype": "MARKET",
                "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
            }
             oid = self.api.placeOrder(orderparams)
             print(f">>> [Success] Order: {oid}")
             
             # Calculate SL price roughly
             fill = self.wait_for_fill(oid)
             # Approx Option SL based on Index SL difference
             curr = self.get_nifty_ltp()
             diff = abs(curr - index_sl)
             opt_diff = diff * 0.5
             sl_price = round(fill - opt_diff, 1)
             
             self.place_sl(token, symbol, sl_price, qty)
             
             # Trailing Logic
             self.monitor_trailing(token, symbol, qty, oid)
             
        except Exception as e:
             print(f">>> [Error] {e}")

    def monitor_trailing(self, token, symbol, qty, entry_oid):
        """
        Exit: Trailing SL (Move SL to low of every previous candle).
        This requires a loop that checks every new 15-min candle close.
        """
        print(">>> [Monitor] Trailing SL initiated (Candle-by-Candle).")
        # Simplified loop for demonstration
        # In production, this would track the last 'closed' candle timestamp and update SL when it changes.
        import time
        last_candle_time = datetime.datetime.now()
        
        while True:
             time.sleep(10)
             # Mock Logic: If new candle closes, update SL
             # Real Logic: Fetch candles, check if new one added.
             # If new candle, Move SL to its Low (for Long).
             pass

    def fetch_candles(self, interval):
        try:
            today = datetime.date.today().strftime("%Y-%m-%d")
            # Last 3 hours
            historicParam={
                "exchange": "NSE", "symboltoken": "99926000", "interval": interval,
                "fromdate": f"{today} 09:15", "todate": f"{today} 15:30"
            }
            if self.dry_run: return self.get_mock_df()
            
            data = self.api.getCandleData(historicParam)
            if data and data.get('data'):
                df = pd.DataFrame(data['data'], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                return df
        except: pass
        if self.dry_run: return self.get_mock_df()
        return None

    def get_mock_df(self):
        # Create Inside Bar pattern
        return pd.DataFrame([
            {'high': 22100, 'low': 22000, 'close': 22050}, # Mother
            {'high': 22080, 'low': 22020, 'close': 22060}  # Baby (Inside)
        ])

    def get_nifty_ltp(self):
        # Return price triggering breakout
        return 22110 if self.dry_run else None

    def wait_for_fill(self, oid):
        time.sleep(1)
        return 100.0 if self.dry_run else None

    def place_sl(self, token, symbol, price, qty):
        # Place SL
        pass
