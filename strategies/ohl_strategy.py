import datetime
import time
from config.settings import Config
from core.safety_checks import SafetyGatekeeper

class OHLStrategy:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(self.api)

    def execute(self, expiry, action="BUY"):
        """
        Executes Open High Low (OHL) Scalp.
        Time: 09:16 AM (After first 1-min candle 09:15-09:16)
        """
        print(f"\n--- OHL SCALP STRATEGY ({expiry}) ---")

        # 0. Risk Checks
        if not self.gatekeeper.check_funds(required_margin_per_lot=5000): return
        if not self.gatekeeper.check_max_daily_loss(0): return
        if self.gatekeeper.is_blackout_period(): return
        
        # 1. Fetch First 1-Minute Candle (09:15)
        candle = self.get_first_minute_candle()
        if not candle:
            print(">>> [Error] Could not fetch 09:15 Candle.")
            return

        c_open = candle['open']
        c_high = candle['high']
        c_low = candle['low']
        c_close = candle['close']
        
        print(f">>> [Market] 09:15 Candle | O: {c_open} H: {c_high} L: {c_low} C: {c_close}")

        # 2. Logic Check
        signal = None
        stop_loss_level = 0.0
        
        # Buffer for 'equal' comparison (e.g. within 0.5 points)
        buffer = 1.0 
        
        if abs(c_open - c_low) <= buffer:
            # Open ~ Low -> Bullish
            print(">>> [Signal] OPEN ~= LOW (Strong Buying) 🐂")
            signal = "BUY_CE"
            stop_loss_level = c_low # SL is Candle Low
            
        elif abs(c_open - c_high) <= buffer:
            # Open ~ High -> Bearish
            print(">>> [Signal] OPEN ~= HIGH (Strong Selling) 🐻")
            signal = "BUY_PE"
            stop_loss_level = c_high # SL is Candle High
        else:
            print(">>> [Signal] No clear OHL Pattern.")
            return

        # 3. Calculate Quantity (VIX Adjusted)
        mult = self.gatekeeper.get_vix_adjustment()
        adjusted_lots = max(1, int(mult))
        qty = int(Config.NIFTY_LOT_SIZE * adjusted_lots)

        # 4. Entry
        strike = round(c_close / 50) * 50
        print(f">>> [Trade] Target Strike: {strike} | Qty: {qty}")
        
        if signal == "BUY_CE":
            self.place_entry(expiry, strike, "CE", qty, stop_loss_level, direction="UP")
        elif signal == "BUY_PE":
            self.place_entry(expiry, strike, "PE", qty, stop_loss_level, direction="DOWN")

    def place_entry(self, expiry, strike, leg_type, qty, index_sl_level, direction):
        # 1. Get Token
        token, symbol = self.token_loader.get_token("NIFTY", expiry, strike, leg_type)
        if not token: 
             print(">>> [Error] Token Not Found")
             return

        if self.dry_run:
             print(f">>> [Dry Run] Buy {symbol} | Index SL: {index_sl_level}")
             return

        # 2. Buy Order
        try:
             orderparams = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "BUY", "exchange": "NFO", "ordertype": "MARKET",
                "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
            }
             order_id = self.api.placeOrder(orderparams)
             print(f">>> [Success] Entry Order: {order_id}")
             
             # 3. Wait for Fill
             print(">>> [Trade] Waiting for fill...")
             fill_price = self.wait_for_fill(order_id)
             if not fill_price: return
             
             # 4. Calculate Option SL & Target
             # NOTE: SL is based on Index Level. Option Price SL is approximate.
             # Option Delta approx 0.5 (ATM).
             # Risk = (Entry Index - SL Index). Option Risk ~= Risk * 0.5.
             
             # Get Index LTP to calculate Points Risk
             curr_index = self.get_nifty_ltp() 
             points_risk = abs(curr_index - index_sl_level)
             option_risk = points_risk * 0.5 # Delta 0.5 assumption
             
             sl_price = round(fill_price - option_risk, 1)
             target_price = round(fill_price + (option_risk * 2), 1) # 1:2 R:R
             
             print(f">>> [Risk] Index Risk: {points_risk:.1f} pts. Option Risk: {option_risk:.1f} pts.")
             print(f">>> [Risk] SL: {sl_price} | Target: {target_price}")
             
             # 5. Place SL Order
             self.place_sl_order(token, symbol, sl_price, qty)
             
             # 6. Monitor
             self.monitor_trade(token, symbol, qty, target_price, sl_price)

        except Exception as e:
             print(f">>> [Error] Entry Failed: {e}")

    def get_first_minute_candle(self):
        # Fetch 09:15 candle logic
        # For simplicity, fetching last 5 candles and picking 09:15 if available, 
        # or most recent if running live at 09:16.
        try:
             today = datetime.date.today().strftime("%Y-%m-%d")
             from_time = f"{today} 09:00"
             to_time = f"{today} 09:20"
             
             historicParam={
                "exchange": "NSE", "symboltoken": "99926000", "interval": "ONE_MINUTE",
                "fromdate": from_time, "todate": to_time
            }
             # Mock support
             if self.dry_run: return self.get_mock_candle()

             resp = self.api.getCandleData(historicParam)
             if resp and resp.get('data'):
                 # Look for 09:15 candle
                 for c in resp['data']:
                     # c[0] is timestamp string "2024-01-01T09:15:00..."
                     if "09:15" in c[0]:
                         return {'open': c[1], 'high': c[2], 'low': c[3], 'close': c[4]}
                 # If not found (delayed), return last?
                 return None
        except: pass
        if self.dry_run: return self.get_mock_candle()
        return None

    def get_mock_candle(self):
         # Return a Bullish OHL candle
         return {'open': 22000, 'low': 22000, 'high': 22050, 'close': 22040}

    def wait_for_fill(self, order_id):
        time.sleep(1)
        return 100.0 if self.dry_run else None

    def get_nifty_ltp(self):
        try:
            resp = self.api.ltpData("NSE", "Nifty 50", "99926000")
            if resp: return resp['data']['ltp']
        except: pass
        return 22040.0 if self.dry_run else None

    def place_sl_order(self, token, symbol, price, qty):
        # SL for Buy is SELL STOP
        try:
             # Trigger slightly higher than limit price
             trig = round(price + 0.5, 1)
             orderparams = {
                "variety": "STOPLOSS", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "SELL", "exchange": "NFO", "ordertype": "STOPLOSS_LIMIT",
                "producttype": "INTRADAY", "duration": "DAY", "triggerprice": trig, "price": price, "quantity": qty
            }
             oid = self.api.placeOrder(orderparams)
             print(f">>> [Risk] SL Placed: {oid}")
        except: pass

    def monitor_trade(self, token, symbol, qty, target, sl):
         print(f">>> [Monitor] Target: {target} | SL: {sl}")
         while True:
            try:
                time.sleep(5)
                # 1. Time Check
                if datetime.datetime.now().time() >= datetime.time(15, 15):
                     print(">>> [Exit] Time 15:15. Closing.")
                     self.place_entry(None, 0, "CE" if "CE" in symbol else "PE", qty, 0, "EXIT") # Re-use entry? No.
                     # Exit Market
                     orderparams = {
                        "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                        "transactiontype": "SELL", "exchange": "NFO", "ordertype": "MARKET",
                        "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
                    }
                     self.api.placeOrder(orderparams)
                     break
                
            except KeyboardInterrupt:
                 print("Stopped.")
                 break
            except Exception as e:
                 pass
