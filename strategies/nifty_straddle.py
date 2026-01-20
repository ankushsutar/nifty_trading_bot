import time
from config.settings import Config

class NiftyStrategy:
    def __init__(self, api, token_loader):
        self.api = api
        self.token_loader = token_loader

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
        self.place_order(ce_token, ce_symbol, action)
        
        # Leg 2: PE
        self.place_order(pe_token, pe_symbol, action)

    def place_order(self, token, symbol, action):
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
        except Exception as e:
            print(f">>> [Error] Order Failed for {symbol}: {e}")
