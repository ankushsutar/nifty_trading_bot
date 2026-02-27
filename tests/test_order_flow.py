import time
import threading
from bot.core.order_feed import order_feed
from bot.core.order_manager import OrderManager
from bot.core.mock_connect import MockSmartConnect

def simulate_order_update(oid, status, delay=2):
    time.sleep(delay)
    print(f"--- Simulating WebSocket update for {oid}: {status}")
    order_feed._on_message(None, {
        'orderid': oid,
        'status': status,
        'averageprice': '86.50',
        'text': 'Filled successfully'
    })

def test_registration_and_wait():
    print("\n--- Testing OrderFeed Registration and Wait ---")
    oid = "TEST_OID_123"
    order_feed.register_order(oid)
    
    # Start simulation in background
    threading.Thread(target=simulate_order_update, args=(oid, 'complete', 1)).start()
    
    print(f"Waiting for fill for {oid}...")
    start_time = time.time()
    result = order_feed.wait_for_fill(oid, timeout=5)
    end_time = time.time()
    
    print(f"Result: {result}")
    print(f"Wait took: {end_time - start_time:.2f}s")
    
    if result['status'] == 'FILLED':
        print("✅ PASS: Order filled correctly via WebSocket simulation.")
    else:
        print("❌ FAIL: Order did not fill correctly.")

def test_smart_limit_timeout():
    print("\n--- Testing Smart-Limit Timeout and Cancellation ---")
    api = MockSmartConnect()
    manager = OrderManager(api, dry_run=False) # dry_run=False but api is mock
    
    # We want placeOrder to succeed but _on_message NEVER to be called to force timeout
    # MockSmartConnect.placeOrder returns {'status': True, 'data': {'orderid': 'REAL_ID_MOCK'}}
    
    symbol = "NIFTY_TEST"
    token = "12345"
    qty = 50
    price = 85.0
    
    print("Executing Smart-Limit (expected to timeout and return None)...")
    start_time = time.time()
    oid = manager.place_smart_limit(symbol, token, qty, price, max_walk_ticks=2)
    end_time = time.time()
    
    print(f"Smart-Limit Result OID: {oid}")
    print(f"Took: {end_time - start_time:.2f}s")
    
    if oid is None:
        print("✅ PASS: Smart-Limit timed out and returned None as expected.")
    else:
        print("❌ FAIL: Smart-Limit returned an OID after timeout.")

if __name__ == "__main__":
    # Ensure OrderFeed is running for registry cleanup loop (optional for this test)
    # order_feed.start() 
    
    test_registration_and_wait()
    test_smart_limit_timeout()
    
    # Give background threads time to finish or just exit
    time.sleep(1)
    print("\nTest complete.")
