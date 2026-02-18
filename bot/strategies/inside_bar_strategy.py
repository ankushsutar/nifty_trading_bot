import datetime
import time
import pandas as pd
from bot.config.settings import Config
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.trade_repo import trade_repo

class InsideBarStrategy:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(self.api, dry_run=self.dry_run)
        self.active_trade = None
        from bot.core.data_fetcher import DataFetcher
        self.data_fetcher = DataFetcher(self.api)
        self.running = True

    def fetch_candles(self, interval="FIFTEEN_MINUTE"):
        # Nifty 50 Token
        token = "99926000"
        return self.data_fetcher.fetch_latest_candles(token, interval=interval)

    def get_nifty_ltp(self):
        try:
            resp = self.api.ltpData("NSE", "Nifty 50", "99926000")
            if resp and resp.get('status'):
                return float(resp['data']['ltp'])
        except: pass
        return 0.0

    def wait_for_fill(self, order_id):
        """Polls for order completion. Returns dict with status and price."""
        if not order_id: return {'status': 'ERROR', 'price': None}
        if self.dry_run: return {'status': 'FILLED', 'price': 100.0}
        
        for _ in range(10):
            try:
                time.sleep(1)
                book = self.api.orderBook()
                if book and book.get('data'):
                    for o in book['data']:
                        if o['orderid'] == order_id:
                            if o['status'] == 'complete':
                                return {'status': 'FILLED', 'price': float(o['averageprice'])}
                            elif o['status'] == 'rejected':
                                return {'status': 'REJECTED', 'message': o.get('text', 'No Reason')}
                            elif o['status'] == 'cancelled':
                                return {'status': 'CANCELLED', 'message': o.get('text', 'No Reason')}
            except: pass
        return {'status': 'TIMEOUT', 'price': None}

    def execute(self, expiry, action="BUY"):
        """
        Executes Inside Bar Breakout Strategy.
        Timeframe: 15-Minute Candles.
        Pattern: Mother Candle, then Baby Candle inside Mother's High/Low.
        """
        print(f"\n--- INSIDE BAR STRATEGY ({expiry}) ---")

        # Check for Resumption
        mode = "PAPER" if self.dry_run else "LIVE"
        self.active_trade = trade_repo.get_active_trade(mode=mode, strategy="INSIDE_BAR")
        
        if self.active_trade:
            print(f">>> [Resumption] Found Open Trade: {self.active_trade['symbol']} (ID: {self.active_trade['id']})")
            print(">>> [Resumption] Resuming Monitoring...")
            self.monitor_trailing(
                self.active_trade['token'], 
                self.active_trade['symbol'], 
                self.active_trade['qty'], 
                "RECOVERED", 
                self.active_trade['id']
            )

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
        mode = "PAPER" if self.dry_run else "LIVE"
        
        if self.dry_run:
             # Save Dry Run
             fill = self.get_nifty_ltp()
             sl_price = fill * 0.9
             tid = trade_repo.save_trade(symbol, token, leg, qty, fill, sl_price, mode=mode, strategy="INSIDE_BAR")
             self.monitor_trailing(token, symbol, qty, "dry_run_oid", tid)
             return
        
        try:
             # Basic Entry Logic
             orderparams = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "BUY", "exchange": "NFO", "ordertype": "MARKET",
                "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
            }
             oid = self.api.placeOrder(orderparams)
             print(f">>> [Success] Order: {oid}")
             
             # 4. Wait for Fill
             fill_result = self.wait_for_fill(oid)
             
             if fill_result['status'] in ['REJECTED', 'CANCELLED']:
                 logger.error(f"❌ Order {oid} was {fill_result['status']}. Reason: {fill_result.get('message', 'Unknown')}")
                 return # Abort

             fill = fill_result['price']
             if not fill:
                 # Fallback to current LTP if fill price isn't available (e.g., timeout)
                 fill = self.get_nifty_ltp() 
                 logger.warning(f"InsideBar: Fill not caught (Status: {fill_result['status']}), using current LTP: {fill}")

             # Approx Option SL based on Index SL difference
             curr = self.get_nifty_ltp()
             diff = abs(curr - index_sl)
             opt_diff = diff * 0.5
             sl_price = round(fill - opt_diff, 1)
             
             sl_oid = self.place_sl(token, symbol, sl_price, qty)
             
             # Save
             tid = trade_repo.save_trade(symbol, token, leg, qty, fill, sl_price, mode=mode, strategy="INSIDE_BAR")

             # Trailing Logic
             self.monitor_trailing(token, symbol, qty, oid, tid, sl_oid)
             
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

    def monitor_trailing(self, token, symbol, qty, entry_oid, trade_id=None, sl_oid=None):
        """
        Monitor using shared PositionManager (TSL + Target).
        """
        print(">>> [Monitor] Trade Active. Handing over to PositionManager.")
        from bot.core.position_manager import PositionManager
        manager = PositionManager(self.api, self.dry_run)
        
        # Determine Entry Price
        entry_price = 0.0
        if self.active_trade: entry_price = self.active_trade.get('entry_price', 0.0)
        if entry_price == 0:
             # Try fetching from Repo or use a fallback if just entered
             pass 

        # We need entry price for PositionManager. 
        # It's passed in calling context usually, but here we might need to look it up if resuming.
        # But wait, execute() calls this with trade_id.
        
        # Retrieve trade details if needed
        if trade_id and entry_price == 0:
             trade = trade_repo.get_active_trade(mode="PAPER" if self.dry_run else "LIVE", strategy="INSIDE_BAR")
             if trade: entry_price = trade['entry_price']
        
        manager.monitor([{
           'symbol': symbol, 'token': token, 
           'entry_price': entry_price, 'qty': qty,
           'id': trade_id,
           'sl_order_id': sl_oid
        }])

    def stop(self):
        """Signal strategy to stop monitoring and exit."""
        print(">>> [System] Stopping Strategy...")
        self.running = False
