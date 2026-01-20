import datetime
import time

class SafetyGatekeeper:
    def __init__(self, api):
        self.api = api

    def is_market_open(self):
        """
        Hard rule: 09:15 to 15:29 IST
        """
        now = datetime.datetime.now().time()
        start = datetime.time(9, 15)
        end = datetime.time(15, 29)
        
        if start <= now <= end:
            return True
        print(f">>> [Gatekeeper] Market Closed. Current Time: {now}")
        return False

    def check_data_freshness(self, tick_timestamp):
        """
        Rule: Data must be < 2 seconds old.
        tick_timestamp: datetime object of the tick
        """
        if not tick_timestamp:
            print(">>> [Gatekeeper] Error: No Timestamp provided.")
            return False
            
        now = datetime.datetime.now()
        # Ensure timezone awareness compatibility if needed. Assuming both are naive or same TZ.
        diff = (now - tick_timestamp).total_seconds()
        
        if diff < 2.0:
            return True
        print(f">>> [Gatekeeper] Data Stale! Delay: {diff:.2f}s")
        return False

    def check_funds(self, required_margin_per_lot=150000):
        """
        Rule: Available Cash > Required Margin * 1.1 (10% Buffer)
        Note: required_margin_per_lot is an estimate.
        """
        try:
            # SmartAPI rmsLimit fetch
            # Note: The actual method might vary by library version, usually rmsLimit() returns user funds.
            limit = self.api.rmsLimit()
            
            if limit and limit.get('status'):
                # 'net' or 'availableCash' depending on response structure
                # Typically data['net'] is the net available margin
                available_cash = float(limit['data']['net'])
                
                required_total = required_margin_per_lot * 1.1 # 10% Buffer
                
                if available_cash >= required_total:
                    print(f">>> [Gatekeeper] Funds OK. Available: ₹{available_cash}")
                    return True
                else:
                    print(f">>> [Gatekeeper] Insufficient Funds! Available: ₹{available_cash}, Required (with buffer): ₹{required_total}")
                    return False
            else:
                print(">>> [Gatekeeper] Could not fetch RMS Data.")
                return False
        except Exception as e:
            print(f">>> [Gatekeeper] Fund Check Error: {e}")
            # Fail safe: If we can't verify funds, we arguably should stop.
            # But during Mock/Test, this might differ.
            return False

    def check_no_open_orders(self, symbol):
        """
        Rule: No PENDING orders for the same symbol to avoid duplicates.
        """
        try:
            book = self.api.orderBook()
            if book and book.get('status'):
                for order in book['data']:
                    if order['tradingsymbol'] == symbol and order['status'] in ['open', 'pending']:
                        print(f">>> [Gatekeeper] Active Order exists for {symbol}. Blocking duplicate.")
                        return False
            return True
        except Exception as e:
            print(f">>> [Gatekeeper] OrderBook Check Error: {e}")
            return False
