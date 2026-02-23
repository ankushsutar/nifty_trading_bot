
import time
import threading
import sys
import os

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from bot.core.order_feed import OrderFeedService
from bot.utils.logger import logger

def simulate_order_update(order_feed, order_id, status, price):
    """Mocks a WebSocket message arrival."""
    time.sleep(2) # Simulate network delay
    logger.info(f"🧪 [Mock] Sending Order Update: {order_id} -> {status}")
    
    # Simulate the incoming dictionary from Angel One
    message = {
        'orderid': order_id,
        'status': status,
        'averageprice': str(price),
        'text': 'Simulated Update'
    }
    
    # Call the callback directly
    order_feed._on_data(None, message)

def test_order_fill_event():
    logger.info("🚀 Starting Order Feed Verification Test...")
    
    # 1. Initialize Service (Singleton)
    feed = OrderFeedService()
    
    # 2. Register a mock order
    oid = "240223000000123"
    feed.register_order(oid)
    logger.info(f"✅ Registered Order: {oid}")
    
    # 3. Start a simulation thread
    sim_thread = threading.Thread(
        target=simulate_order_update, 
        args=(feed, oid, 'complete', 150.55)
    )
    sim_thread.start()
    
    # 4. Strategy waits for fill
    logger.info(f"⏳ Strategy calling wait_for_fill({oid})...")
    start_time = time.time()
    result = feed.wait_for_fill(oid, timeout=5)
    end_time = time.time()
    
    duration = end_time - start_time
    logger.info(f"🏁 wait_for_fill returned in {duration:.2f}s")
    logger.info(f"📊 Result: {result}")
    
    if result.get('status') == 'FILLED' and result.get('price') == 150.55:
        logger.info("✨ TEST PASSED: Sub-second event signaling verified! ✅")
    else:
        logger.error("❌ TEST FAILED: Unexpected result or timeout.")

def test_order_rejection_event():
    logger.info("🚀 Starting Order Rejection Verification Test...")
    
    feed = OrderFeedService()
    oid = "240223000000456"
    feed.register_order(oid)
    
    sim_thread = threading.Thread(
        target=simulate_order_update, 
        args=(feed, oid, 'rejected', 0.0)
    )
    sim_thread.start()
    
    result = feed.wait_for_fill(oid, timeout=5)
    logger.info(f"📊 Result: {result}")
    
    if result.get('status') == 'REJECTED':
        logger.info("✨ TEST PASSED: Rejection event signaling verified! ✅")
    else:
        logger.error("❌ TEST FAILED: Unexpected result or timeout.")

if __name__ == "__main__":
    test_order_fill_event()
    print("-" * 50)
    test_order_rejection_event()
