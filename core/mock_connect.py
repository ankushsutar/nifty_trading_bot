import random
import uuid

class MockSmartConnect:
    def __init__(self, api_key=None):
        self.api_key = api_key
        self.orders = [] # Mock Order Book
        print(f">>> [Mock] Initialized MockSmartConnect with API Key: {api_key}")

    def generateSession(self, clientCode, password, totp):
        print(f">>> [Mock] generateSession called for Client: {clientCode}")
        # Return a fake success response structure similar to the real API
        return {
            "status": True,
            "message": "SUCCESS",
            "data": {
                "jwtToken": "mock_jwt_token",
                "refreshToken": "mock_refresh_token",
                "feedToken": "mock_feed_token"
            }
        }

    def ltpData(self, exchange, tradingsymbol, symboltoken):
        print(f">>> [Mock] ltpData called for {tradingsymbol} ({symboltoken})")
        # Return a hardcoded/random Nifty spot price so the strategy believes the market is open.
        # Nifty is typically around 23000 these days (as per user request example).
        mock_ltp = 23000.0 + random.uniform(-50, 50)
        return {
            "status": True,
            "data": {
                "ltp": mock_ltp,
                "exchange": exchange,
                "tradingsymbol": tradingsymbol,
                "symboltoken": symboltoken
            }
        }

    def placeOrder(self, orderparams):
        print(f">>> [Mock] placeOrder called")
        print(f"    Symbol: {orderparams.get('tradingsymbol')}")
        print(f"    Action: {orderparams.get('transactiontype')}")
        print(f"    Product: {orderparams.get('producttype')}")
        
        mock_order_id = str(uuid.uuid4())
        
        # Simulate a filled order and add to internal book
        mock_order = {
            "orderid": mock_order_id,
            "status": "complete", # Simulate immediate fill
            "tradingsymbol": orderparams.get('tradingsymbol'),
            "symboltoken": orderparams.get('symboltoken'),
            "transactiontype": orderparams.get('transactiontype'),
            "quantity": orderparams.get('quantity'),
            "price": orderparams.get('price', 0), # Limit price (if any)
            # Simulate a filled price (usually close to LTP or limit)
            "averageprice": 100.0  # Dummy filled price for options
        }
        self.orders.append(mock_order)
        
        return mock_order_id
        
    def orderBook(self):
        """Mock Order Book"""
        # print(">>> [Mock] fetching orderBook...")
        return {
            "status": True,
            "data": self.orders
        }

class MockTokenLookup:
    def load_scrip_master(self):
        print(">>> [Mock] Skipping Scrip Master download.")
    
    def get_token(self, symbol_name, expiry_date, strike, option_type):
        # Return dummy values
        # Construct a dummy symbol, e.g., NIFTY29JAN202623000CE
        fake_symbol = f"{symbol_name}{expiry_date}{strike}{option_type}"
        fake_token = "99999" # Dummy token ID
        print(f">>> [Mock] Generated dummy token '{fake_token}' for {fake_symbol}")
        return fake_token, fake_symbol

