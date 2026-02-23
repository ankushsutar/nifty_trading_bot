
import time
import threading
import json
from SmartApi.smartWebSocketV2 import SmartWebSocketV2
from bot.config.settings import Config
from bot.utils.logger import logger
from bot.core.angel_connect import get_angel_session

class OrderFeedService:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(OrderFeedService, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized: return
        self._initialized = True
        
        self.sws = None
        self.is_connected = False
        self.running = False
        self.thread = None
        
        # Registry: Key = OrderID, Value = {status: 'complete'|'rejected', price: float, ...}
        self.order_status_registry = {}
        self.registry_lock = threading.Lock()
        
        # Event Registry for wait_for_fill
        self.order_events = {} # Key = OrderID, Value = threading.Event

    def start(self):
        """Starts the Order WebSocket connection."""
        if self.running: return
        logger.info(">>> [OrderFeed] Starting Real-Time Order Feed Service... 🎧")
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.sws:
            try: self.sws.close_connection()
            except: pass

    def register_order(self, order_id):
        """Pre-registers an order to be tracked."""
        with self.registry_lock:
            self.order_status_registry[order_id] = {'status': 'PENDING'}
            self.order_events[order_id] = threading.Event()

    def get_order_status(self, order_id):
        with self.registry_lock:
            return self.order_status_registry.get(order_id)

    def wait_for_fill(self, order_id, timeout=10):
        """Waits for an order to be filled using WebSocket events."""
        event = None
        with self.registry_lock:
            event = self.order_events.get(order_id)
        
        if not event:
            # Maybe already filled or not registered
            status = self.get_order_status(order_id)
            if status and status['status'] != 'PENDING':
                return status
            return {'status': 'ERROR', 'message': 'Order not registered in feed'}

        # Wait for the WebSocket callback to signal the event
        signaled = event.wait(timeout=timeout)
        
        if not signaled:
            return {'status': 'TIMEOUT'}
            
        return self.get_order_status(order_id)

    def _run_loop(self):
        while self.running:
            try:
                api = get_angel_session()
                if not api:
                    time.sleep(5)
                    continue

                feed_token = getattr(api, 'feed_token', None)
                if not feed_token:
                    try:
                        with open("data/session.json", "r") as f:
                            data = json.load(f)
                            feed_token = data.get('feedToken')
                    except: pass
                
                if not feed_token:
                    time.sleep(10)
                    continue

                self.sws = SmartWebSocketV2(
                    api.access_token, Config.API_KEY, Config.CLIENT_ID, feed_token
                )

                self.sws.on_open = self._on_open
                self.sws.on_data = self._on_data
                self.sws.on_error = self._on_error
                self.sws.on_close = self._on_close

                self.sws.connect()
                time.sleep(5)
            except Exception as e:
                logger.error(f">>> [OrderFeed] Crash: {e}. Retrying...")
                time.sleep(5)

    def _on_open(self, ws):
        logger.info(">>> [OrderFeed] Connected! ✅")
        self.is_connected = True
        # Subscribe to Order Updates (Action 3, Mode 3 for Order Feed)
        # For Order Feed, we don't need token list in some versions, 
        # but standard way is to tell it we want order updates.
        try:
            # Angel protocol for order feed: subscribe to special correlation ID
            # Usually it's automatic on connection for the order feed type if configured,
            # but we explicitly call subscribe for robust logic.
            # Correlation ID: 'order_update', Action: 1 (Subscribe), Mode: 3 (Order Feed)
            self.sws.subscribe("order_update_query", 1, [{"exchangeType": 1, "tokens": [""]}])
        except Exception as e:
            logger.error(f"Order Feed Sub Error: {e}")

    def _on_data(self, ws, message):
        """Processes real-time order status updates."""
        try:
            # logger.debug(f"[OrderFeed] Received: {message}")
            # Angel One Order Feed format is usually JSON
            if isinstance(message, dict) and 'orderid' in message:
                oid = message['orderid']
                status = message.get('status', '').lower()
                fill_price = float(message.get('averageprice', 0))
                
                with self.registry_lock:
                    if oid in self.order_status_registry:
                        # Map internal status
                        internal_status = 'PENDING'
                        if status == 'complete': internal_status = 'FILLED'
                        elif status in ['rejected', 'cancelled']: internal_status = status.upper()
                        
                        self.order_status_registry[oid] = {
                            'status': internal_status,
                            'price': fill_price,
                            'message': message.get('text', '')
                        }
                        
                        # Signal those waiting
                        if internal_status != 'PENDING' and oid in self.order_events:
                            self.order_events[oid].set()
                            logger.info(f">>> [OrderFeed] Order {oid} updated to {internal_status}")

        except Exception as e:
            logger.error(f"OrderFeed Parse Error: {e}")

    def _on_error(self, ws, error):
        logger.error(f">>> [OrderFeed] Error: {error}")

    def _on_close(self, ws):
        logger.warning(">>> [OrderFeed] Disconnected ❌")
        self.is_connected = False

order_feed = OrderFeedService()
