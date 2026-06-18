import time
import threading
from bot.core.order_feed import order_feed
from bot.utils.logger import logger

# Simulate an order
TEST_OID = "260225000413200"

def simulate_fast_websocket():
    # 1. Simulate the WebSocket receiving the FILLED event FAST (before register_order)
    logger.info(">>> [Test] Simulating WebSocket FILLED event arriving...")
    order_feed._on_message(None, {
        "orderid": TEST_OID,
        "status": "complete",
        "averageprice": "105.5",
        "text": "Simulated fill"
    })
    
    # Let slightly more time pass to guarantee it arrived first
    time.sleep(0.1)

    # 2. Simulate the REST API finally returning the order ID to order_manager
    logger.info(">>> [Test] REST API returned. Calling register_order()...")
    order_feed.register_order(TEST_OID)
    
    # 3. Strategy calls wait_for_fill
    logger.info(f">>> [Test] Strategy calling wait_for_fill({TEST_OID})...")
    start = time.time()
    
    # If the race condition fix works, this will return instantly instead of waiting timeout
    result = order_feed.wait_for_fill(TEST_OID, timeout=5)
    
    duration = time.time() - start
    logger.info(f">>> [Test] wait_for_fill returned in {duration:.3f}s with status: {result['status']}")
    
    if duration < 1.0 and result['status'] == 'FILLED':
        logger.info("✅ SUCCESS: Race condition fixed! Event processed instantly.")
    else:
        logger.error("❌ FAILED: Still timed out or incorrect status.")

if __name__ == "__main__":
    simulate_fast_websocket()
