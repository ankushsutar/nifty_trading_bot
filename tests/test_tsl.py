import sys
import os
import time
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.position_manager import PositionManager

# Mock API
class MockAPI:
    def placeOrder(self, params):
        return "TEST_ORDER_ID"

# Subclass to inject price sequence
class TestPositionManager(PositionManager):
    def __init__(self, prices):
        super().__init__(MockAPI(), dry_run=True)
        self.prices = prices # List of prices to feed
        self.idx = 0
        
    def get_ltp(self, token):
        if self.idx < len(self.prices):
            price = self.prices[self.idx]
            self.idx += 1
            return price
        return self.prices[-1] # Stay at last price

def test_tsl_scenario():
    print(">>> [Test] Simulating TSL Scenario: Rally then Crash")
    
    # Buy @ 100
    # Sequence: 
    # 1. 100 (0%)
    # 2. 105 (+5%)
    # 3. 110 (+10%) -> Expect TSL to Breakeven (0% / 100)
    # 4. 115 (+15%) -> Expect TSL to +5% (105)
    # 5. 122 (+22%) -> Expect TSL to +12% (112) [Round(22-10, 2) = 0.12]
    # 6. 115 (+15%) -> Price drops. TSL is 112. Safe.
    # 7. 110 (+10%) -> Price drops below 112. EXIT!
    
    prices = [100, 105, 110, 115, 122, 115, 110]
    
    manager = TestPositionManager(prices)
    
    # Fake Position
    pos = {
        'symbol': 'NIFTY26FEB22000CE',
        'token': '12345',
        'entry_price': 100.0,
        'qty': 50
    }
    
    # Patch datetime in the module
    import core.position_manager as pm
    from unittest.mock import MagicMock
    import datetime

    # Mock Current Time to 10:00 AM
    mock_now = datetime.datetime.now().replace(hour=10, minute=0, second=0)
    
    # Create a mock class that behaves like datetime.datetime
    class MockDatetime(datetime.datetime):
        @classmethod
        def now(cls):
            return mock_now

    # Patch it
    original_datetime = pm.datetime.datetime
    pm.datetime.datetime = MockDatetime
    
    # Faster Sleep for test
    original_sleep = pm.time.sleep
    pm.time.sleep = lambda x: None # No Delay
    
    try:
        manager.monitor([pos])
    finally:
        # Restore
        pm.datetime.datetime = original_datetime
        pm.time.sleep = original_sleep

if __name__ == "__main__":
    test_tsl_scenario()
