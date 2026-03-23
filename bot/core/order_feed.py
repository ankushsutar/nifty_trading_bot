
import time
import threading
import json
from SmartApi.smartWebSocketOrderUpdate import SmartWebSocketOrderUpdate
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
        self.last_cleanup = time.time()

    def _cleanup_registry(self):
        """Clears old order statuses every hour to keep memory footprint lean."""
        now = time.time()
        if now - self.last_cleanup < 3600: return
        
        with self.registry_lock:
            # FIX: Only clear orders that ARE NOT pending or active.
            # This prevents wiping events for orders currently being walked/monitored.
            active_ids = list(self.order_events.keys())
            initial_count = len(self.order_status_registry)
            
            # Keep only active events and recent status
            new_registry = {}
            for oid, data in self.order_status_registry.items():
                if oid in active_ids or data.get('status') == 'PENDING':
                    new_registry[oid] = data
            
            self.order_status_registry = new_registry
            self.last_cleanup = now
            logger.info(f">>> [OrderFeed] Registry Cleanup: Flushed {initial_count - len(new_registry)} stale orders. Keeping {len(active_ids)} active events.")

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
        """Pre-registers an order to be tracked, handling pre-arrived updates."""
        if not order_id: return
        with self.registry_lock:
            # Convert to string to ensure consistent lookup (Angel One IDs are strings)
            order_id = str(order_id)
            if order_id not in self.order_status_registry:
                self.order_status_registry[order_id] = {'status': 'PENDING'}
            
            event = threading.Event()
            self.order_events[order_id] = event
            
            # Handle Race: If update arrived before registration
            current_status = self.order_status_registry[order_id]['status']
            if current_status != 'PENDING':
                event.set()
                logger.debug(f">>> [OrderFeed] Registered {order_id} (Already {current_status})")
            else:
                logger.debug(f">>> [OrderFeed] Registered {order_id} for tracking.")

    def get_order_status(self, order_id):
        with self.registry_lock:
            return self.order_status_registry.get(order_id)

    def wait_for_fill(self, order_id, timeout=10):
        """Waits for an order to be filled using WebSocket events, with REST fallback."""
        order_id = str(order_id)
        event = None
        with self.registry_lock:
            event = self.order_events.get(order_id)
        
        if not event:
            # Maybe already filled or not registered
            status = self.get_order_status(order_id)
            if status and status['status'] != 'PENDING':
                return status
            logger.error(f">>> [OrderFeed] Order {order_id} NOT FOUND in events registry. Registered IDs: {list(self.order_events.keys())}")
            return {'status': 'ERROR', 'message': f'Order {order_id} not registered in feed'}

        # Wait for the WebSocket callback to signal the event
        signaled = event.wait(timeout=timeout)
        
        if not signaled:
            logger.warning(f"⚠️ Order {order_id} fill TIMEOUT via WebSocket. Falling back to REST API...")
            try:
                from bot.utils.rate_limiter import rate_limiter
                api = get_angel_session()
                if api:
                    rate_limiter.wait()
                    ob_res = api.orderBook()
                    if ob_res and isinstance(ob_res, dict) and ob_res.get('status') == True:
                        orders = ob_res.get('data', [])
                        if orders:
                            for ord_info in orders:
                                if ord_info.get('orderid') == order_id:
                                    ws_status = ord_info.get('status', '').lower()
                                    if ws_status == 'complete':
                                        return {'status': 'FILLED', 'price': float(ord_info.get('averageprice', 0))}
                                    elif ws_status in ['rejected', 'cancelled']:
                                        return {'status': ws_status.upper(), 'price': 0}
            except Exception as e:
                logger.error(f">>> [OrderFeed] REST Fallback Error: {e}")
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

                auth_token = api.access_token
                if not auth_token:
                    logger.warning(">>> [OrderFeed] Session token is None — session may have expired. Retrying in 10s.")
                    time.sleep(10)
                    continue
                if not auth_token.startswith("Bearer "):
                    auth_token = f"Bearer {auth_token}"

                self.sws = SmartWebSocketOrderUpdate(
                    auth_token, Config.API_KEY, Config.CLIENT_ID, feed_token
                )

                self.sws.on_open = self._on_open
                # SmartWebSocketOrderUpdate uses on_message instead of directly passing generic dicts like V2
                self.sws.on_message = self._on_message
                self.sws.on_error = self._on_error
                self.sws.on_close = self._on_close

                self.sws.connect()
                
                # Periodically clean up registry in background
                self._cleanup_registry()
                
                time.sleep(5)
            except Exception as e:
                logger.error(f">>> [OrderFeed] Crash: {e}. Retrying...")
                time.sleep(5)

    def _on_open(self, ws):
        logger.info(">>> [OrderFeed] Connected! ✅")
        self.is_connected = True
        # SmartWebSocketOrderUpdate subscribes automatically via headers. No need to send 'order_update_query'.

    def _on_message(self, ws, message):
        """Processes real-time order status updates unconditionally."""
        try:
            if isinstance(message, bytes):
                message = message.decode('utf-8')
            
            if message is None:
                return

            if isinstance(message, str):
                if message.strip() == "":
                    return
                try:
                    message = json.loads(message)
                except json.JSONDecodeError:
                    # Ignore harmless heartbeats or acknowledgment packets
                    if message.strip() not in ["pong", "ping", "ack", ""]:
                        # Only log if it's substantial non-JSON data
                        if len(message) > 1:
                            logger.debug(f">>> [OrderFeed] Non-JSON message: {repr(message)}")
                    return
                
            if isinstance(message, dict) and 'orderid' in message:
                oid = message['orderid']
                status = message.get('status', '').lower()
                fill_price = float(message.get('averageprice', 0))
                
                with self.registry_lock:
                    # Map internal status
                    internal_status = 'PENDING'
                    if status == 'complete': internal_status = 'FILLED'
                    elif status in ['rejected', 'cancelled']: internal_status = status.upper()
                    
                    # Store update UNCONDITIONALLY (even if not pre-registered yet)
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
            logger.error(f">>> [OrderFeed] Parse Error: {e} | Raw Message Type: {type(message)} | Content: {repr(message)}")

    def _on_error(self, ws, error):
        logger.error(f">>> [OrderFeed] Error: {error}")

    def _on_close(self, ws, status_code=None, close_msg=None):
        logger.warning(f">>> [OrderFeed] Disconnected ❌ (Status: {status_code})")
        self.is_connected = False

order_feed = OrderFeedService()
