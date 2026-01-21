import time
import datetime
from config.settings import Config

from core.safety_checks import SafetyGatekeeper

class NiftyStrategy:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(self.api)

    def get_atm_strike(self):
        """
        Fetches NIFTY 50 Spot Price and rounds to nearest 50.
        """
        try:
            # Token for Nifty 50 Index is usually '99926000' (Angel One) or '26000' (NSE).
            # We need to be careful with the token ID for the Index.
            # Using NSE Nifty 50 symbol: "NIFTY" and exchange "NSE"
            # It's safer to fetch the token from the loader if possible, or use the known consistent one.
            # For simplicity in this demo, we'll try to fetch by symbol if possible or use a known one.
            # Let's assume we can query 'Nifty 50' on 'NSE'. 
            # In Angel SmartAPI, "Nifty 50" token is "99926000".
            
            # NOTE: For Mock mode, we need to ensure this works too.
            
            response = self.api.ltpData("NSE", "Nifty 50", "99926000")
            
            if response and response.get('status'):
                ltp = response['data']['ltp']
                print(f">>> [Market] Nifty Spot Price: {ltp}")
                
                # Round to nearest 50
                atm_strike = round(ltp / 50) * 50
                return int(atm_strike)
            else:
                print(">>> [Error] Failed to fetch Nifty LTP.")
                return None
        except Exception as e:
            print(f">>> [Error] get_atm_strike failed: {e}")
            return None

    def execute(self, expiry, action="BUY"):
        """
        Main logic to execute a Straddle (Buy/Sell Both CE & PE at ATM)
        """
        # 0. Safety Checks (Funds & Active Orders)
        # Long Straddle (2 Legs) Cost approx: 100 * 65 * 2 = 13000. 
        # Checking for 13000.
        if not self.gatekeeper.check_funds(required_margin_per_lot=13000):
             print(">>> [Strategy] Insufficient Funds for Long Straddle (Need ~13k). Aborting.")
             return

        # 1. Determine ATM Strike
        strike = self.get_atm_strike()
        if not strike:
            print(">>> [Error] Could not determine ATM Strike. Aborting.")
            return

        print(f">>> [Strategy] ATM Strike Calculated: {strike}")

        # 2. Get Tokens for Both Legs
        ce_token, ce_symbol = self.token_loader.get_token("NIFTY", expiry, strike, "CE")
        pe_token, pe_symbol = self.token_loader.get_token("NIFTY", expiry, strike, "PE")
        
        if not ce_token or not pe_token:
            print(f">>> [Error] Could not find tokens for Strike {strike}")
            return

        # 3. Place Orders (Dual Leg)
        print(f">>> [Trade] Executing Straddle at {strike}...")
        
        # Leg 1: CE
        ce_order_id = self.place_order(ce_token, ce_symbol, action)
        
        # Leg 2: PE
        pe_order_id = self.place_order(pe_token, pe_symbol, action)

        if self.dry_run:
            print(">>> [Dry Run] Skipping Order verification and Stop Loss placement.")
            return

        # 4. Wait for fills and Place Stop Loss
        print(">>> [Strategy] Waiting for fills to place Stop Loss...")
        
        # Handle CE SL
        if ce_order_id:
            ce_fill_price = self.wait_for_fill(ce_order_id)
            if ce_fill_price:
                self.place_stop_loss(ce_token, ce_symbol, ce_fill_price, Config.NIFTY_LOT_SIZE, entry_action=action)
        
        # Handle PE SL
        if pe_order_id:
            pe_fill_price = self.wait_for_fill(pe_order_id)
            if pe_fill_price:
                self.place_stop_loss(pe_token, pe_symbol, pe_fill_price, Config.NIFTY_LOT_SIZE, entry_action=action)

        # 5. Monitor for Profit Target (Simple Loop)
        if not self.dry_run:
            print(">>> [Strategy] Monitoring positions for Target (20% Profit)...")
            self.monitor_positions({
                ce_symbol: ce_token,
                pe_symbol: pe_token
            })

    def monitor_positions(self, active_symbols):
        """
        Simple loop to check if we should exit for profit.
        Target: 20% gain or Time Exit (15:15).
        NOTE: Real P&L tracking requires fetching average price again or tracking it.
        For MVP, we just loop and wait for manual interrupt or time exit.
        """
        try:
            while True:
                time.sleep(10)
                now = datetime.datetime.now().time()
                if now > datetime.time(15, 15):
                    print(">>> [Exit] Time is 15:15. Auto-Squaring off positions.")
                    break
                # TODO: Implement P&L calculation API call to check unrealized MTOM
        except KeyboardInterrupt:
            print(">>> [User] Manual Stop Signal.")

    def place_order(self, token, symbol, action):
        if self.dry_run:
            print(f">>> [Dry Run] Would place {action} MARKET Order for {symbol} (Token: {token})")
            return "dry_run_id"

        try:
            orderparams = {
                "variety": "NORMAL",
                "tradingsymbol": symbol,
                "symboltoken": token,
                "transactiontype": action,
                "exchange": "NFO",
                "ordertype": "MARKET",
                "producttype": "INTRADAY",
                "duration": "DAY",
                "quantity": Config.NIFTY_LOT_SIZE
            }
            order_id = self.api.placeOrder(orderparams)
            print(f">>> [Success] Placed {action} on {symbol} | Order ID: {order_id}")
            return order_id
        except Exception as e:
            print(f">>> [Error] Order Failed for {symbol}: {e}")
            return None

    def wait_for_fill(self, order_id):
        """
        Polls the order book until the order is 'complete' (filled).
        Returns the average filled price.
        """
        attempts = 0
        max_attempts = 5 # Retry 5 times
        while attempts < max_attempts:
            try:
                book = self.api.orderBook()
                if book and book.get('status'):
                    for order in book['data']:
                        if order['orderid'] == order_id:
                            if order['status'] == 'complete':
                                avg_price = float(order['averageprice'])
                                print(f">>> [Fill] Order {order_id} filled at ₹{avg_price}")
                                return avg_price
                            else:
                                print(f">>> [Wait] Order {order_id} status: {order['status']}")
            except Exception as e:
                print(f">>> [Wait] Error fetching order book: {e}")
            
            time.sleep(1)
            attempts += 1
        
        print(f">>> [Error] Order {order_id} failed to fill after waiting.")
        return None

    def place_stop_loss(self, token, symbol, field_price, quantity, sl_percent=0.10, entry_action="BUY"):
        """
        Places a Stop Loss Order. 
        If Entry was BUY -> Place SELL SL at (Price - 10%).
        If Entry was SELL -> Place BUY SL at (Price + 10%).
        """
        try:
            if entry_action == "BUY":
                # Long Position: Protect Downside
                sl_price = round(field_price * (1 - sl_percent), 1)
                trigger_price = round(sl_price + 0.5, 1) # Trigger slightly higher (Sell Stop Limit)
                transaction_type = "SELL"
            else:
                # Short Position: Protect Upside
                sl_price = round(field_price * (1 + sl_percent), 1)
                trigger_price = round(sl_price - 0.5, 1) # Trigger slightly lower (Buy Stop Limit)
                transaction_type = "BUY"

            print(f">>> [Risk] Placing SL for {symbol} | Entry: {entry_action} @ {field_price} | SL @ {sl_price} ({int(sl_percent*100)}%)")
            
            orderparams = {
                "variety": "STOPLOSS",
                "tradingsymbol": symbol,
                "symboltoken": token,
                "transactiontype": transaction_type,
                "exchange": "NFO",
                "ordertype": "STOPLOSS_LIMIT",
                "producttype": "INTRADAY",
                "duration": "DAY",
                "price": sl_price,
                "triggerprice": trigger_price,
                "quantity": quantity
            }
            order_id = self.api.placeOrder(orderparams)
            print(f">>> [Success] SL Placed | Order ID: {order_id}")
            return order_id
        except Exception as e:
            print(f">>> [Error] SL Placement Failed: {e}")
            return None
