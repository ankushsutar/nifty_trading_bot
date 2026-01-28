import datetime
import time
import pandas as pd
from config.settings import Config
from core.safety_checks import SafetyGatekeeper
from core.trade_repo import trade_repo

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
        if not self.gatekeeper.check_funds(required_margin_per_lot=5000): return
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
        adjusted_lots = max(1, int(mult))
        qty = int(Config.NIFTY_LOT_SIZE * adjusted_lots)
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
             
             
             # Save
             tid = trade_repo.save_trade(symbol, token, leg, qty, fill, sl_price)

             # Trailing Logic
             self.monitor_trailing(token, symbol, qty, oid, tid)
             
        except Exception as e:
             print(f">>> [Error] {e}")

    def place_sl(self, token, symbol, price, qty):
        try:
             # Buy SL for Sell Entry? No, Inside Bar is direction based.
             # If Entry was BUY, SL is SELL STOP.
             # Trigger slightly below price (for Sell SL).
             trig = round(price + 0.5, 1) # Assuming Sell SL Trigger > Price? No.
             # Sell SL: Trigger = 100, Price = 99.
             # But SmartAPI might want Trigger=99.5, Price=99.
             
             trig = round(price + 0.5, 1) 

             orderparams = {
                "variety": "STOPLOSS", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "SELL", "exchange": "NFO", "ordertype": "STOPLOSS_LIMIT",
                "producttype": "INTRADAY", "duration": "DAY", "triggerprice": trig, "price": price, "quantity": qty
            }
             oid = self.api.placeOrder(orderparams)
             print(f">>> [Risk] SL Placed {symbol} | Price: {price} | ID: {oid}")
        except Exception as e:
             print(f">>> [Error] SL Place: {e}")

    def monitor_trailing(self, token, symbol, qty, entry_oid, trade_id=None):
        """
        Monitor for Exit.
        """
        print(">>> [Monitor] Trade Active. Waiting for SL or Time Exit...")
        
        while True:
            try:
                time.sleep(5)
                # 1. Time Check
                if datetime.datetime.now().time() >= datetime.time(15, 15):
                     print(">>> [Exit] Time 15:15. Closing.")
                     # Exit Market
                     orderparams = {
                        "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                        "transactiontype": "SELL", "exchange": "NFO", "ordertype": "MARKET",
                        "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
                    }
                     self.api.placeOrder(orderparams)
                     if trade_id: trade_repo.close_trade(trade_id=trade_id)
                     break
                
            except KeyboardInterrupt:
                 print("Stopped.")
                 break
            except Exception as e:
                 pass
