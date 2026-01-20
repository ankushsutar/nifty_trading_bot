import time
import datetime
from config.settings import Config
from core.safety_checks import SafetyGatekeeper

class ORBStrategy:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(self.api)
        
        # State
        self.range_high = -1
        self.range_low = 999999
        self.range_set = False

    def execute(self, expiry, action="BUY"):
        """
        ORB Logic:
        1. 09:15 - 09:30: Monitor High/Low
        2. 09:30+: Wait for Breakout
        """
        print(f">>> [Strategy] Initializing ORB Strategy for {expiry}")
        
        # 1. Establish Range (Simulated or Real)
        self.establish_opening_range()
        
        if not self.range_set:
            print(">>> [Error] Failed to establish Opening Range.")
            return

        print(f">>> [ORB] Range Set: High={self.range_high}, Low={self.range_low}")
        
        # 2. Monitor for Breakout
        self.monitor_breakout(expiry)

    def establish_opening_range(self):
        """
        In a real scenario, this would loop from 09:15 to 09:30 updating high/low.
        For this implementation, let's assume we are running 'Post-9:30' and we fetch the 15m candle.
        OR we loop if current time < 09:30.
        """
        print(">>> [ORB] Establishing Opening Range (09:15 - 09:30)...")
        # For simplicity/demo: We will fetch the current LTP and assume a small range around it
        # In production: Fetch OHLC data for the first 15min candle using `getCandleData`
        
        # Simulating fetch
        ltp = self.get_nifty_ltp() 
        if ltp:
            # Fake range for demo/mock if real data not fully available historically
            self.range_high = round(ltp + 20, 2)
            self.range_low = round(ltp - 20, 2)
            self.range_set = True
        else:
            print(">>> [ORB] Could not fetch LTP to set range.")

    def monitor_breakout(self, expiry):
        print(">>> [ORB] Monitoring for Breakout...")
        
        while True:
            # 1. Safety Check: Gatekeepers
            # We construct a fake 'tick_timestamp' for now as we are polling
            if not self.gatekeeper.check_funds():
                break
                
            ltp = self.get_nifty_ltp()
            if not ltp:
                time.sleep(1)
                continue
                
            print(f"    LTP: {ltp} | Range: {self.range_low} - {self.range_high}")
            
            # 2. Check Breakout / Signal
            
            # Case A: Upside Breakout -> Buy CE
            if ltp > self.range_high:
                print(">>> [ORB] Upside Breakout! Buying CE.")
                self.place_entry_order(expiry, "CE")
                break # Exit loop after trade (or continue to manage)
                
            # Case B: Downside Breakout -> Buy PE
            elif ltp < self.range_low:
                print(">>> [ORB] Downside Breakout! Buying PE.")
                self.place_entry_order(expiry, "PE")
                break
            
            # Demo Break: Don't loop forever in mock/dry-run if no breakout
            if self.dry_run or self.api.api_key is None: # Mock check
                print(">>> [Only for Demo] Breaking loop to avoid infinite wait.")
                break
                
            time.sleep(2)

    def place_entry_order(self, expiry, option_type):
        # Calculate Strike (ATM or slightly ITM based on breakout)
        # If Upside Breakout at 23050, we usually buy 23050 CE or 23000 CE.
        
        current_ltp = self.get_nifty_ltp()
        strike = round(current_ltp / 50) * 50
        
        token, symbol = self.token_loader.get_token("NIFTY", expiry, strike, option_type)
        
        if not token:
            print(">>> [Error] Token not found.")
            return

        if self.dry_run:
             print(f">>> [Dry Run] Would Buy {symbol} at Market.")
             return

        # Pre-Trade Check: Open Orders
        if not self.gatekeeper.check_no_open_orders(symbol):
            return

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
             
             # Stop Loss Logic (Low of Range for CE, High of Range for PE)
             # Adding simple 1:2 Risk Reward SL for now based on Premium
             # ... (Copy logic from Straddle or implementing specific ORB SL)
             
        except Exception as e:
            print(f">>> [Error] Order Failed: {e}")

    def get_nifty_ltp(self):
        # Helper to get Nifty Index LTP
        try:
            # Nifty 50 Token: 99926000
            resp = self.api.ltpData("NSE", "Nifty 50", "99926000")
            if resp and resp.get('status'):
                return resp['data']['ltp']
        except:
            pass
        return None
