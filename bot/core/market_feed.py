
import time
import threading
import json
import datetime
from SmartApi.smartWebSocketV2 import SmartWebSocketV2
from bot.config.settings import Config
from bot.utils.logger import logger
from bot.core.angel_connect import get_angel_session
from bot.utils.token_lookup import TokenLookup
from bot.utils.expiry_calculator import get_next_weekly_expiry

class MarketFeedService:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(MarketFeedService, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized: return
        self._initialized = True
        
        self.sws = None
        self.is_connected = False
        self.latest_data = {} # Key: Token, Value: {ltp, timestamp, ...}
        self.running = False
        self.thread = None
        
        # Dynamic Management
        self.token_lookup = TokenLookup()
        self.subscribed_tokens = set() # Set of tokens currently subscribed
        self.last_subscription_time = 0
        self.current_atm = 0
        
        # Real-Time Candle Construction
        self.candle_cache = {} # Key: Token, Value: 1-min candle
        self.candle_cache_5m = {} # Key: Token, Value: 5-min candle
        self.last_vol_cache = {} # Key: Token, Value: Total Day Volume
        
    def start(self):
        """Starts the WebSocket connection in a background thread."""
        if self.running: 
            return

        logger.info(">>> [MarketFeed] Starting Real-Time Feed Service... 🚀")
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def stop(self):
        """Stops the service."""
        logger.info(">>> [MarketFeed] Stopping Service...")
        self.running = False
        if self.sws:
            try:
                self.sws.close_connection()
            except: pass

    def _run_loop(self):
        """Main loop to maintain connection."""
        while self.running:
            try:
                # 1. Get Valid Session
                api = get_angel_session()
                if not api:
                    logger.error(">>> [MarketFeed] No Valid API Session. Retrying in 5s...")
                    time.sleep(5)
                    continue

                # 2. Extract Tokens
                auth_token = api.access_token 
                feed_token = getattr(api, 'feed_token', None)
                if not feed_token:
                    try:
                        with open("data/session.json", "r") as f:
                            data = json.load(f)
                            feed_token = data.get('feedToken')
                            auth_token = data.get('jwtToken')
                    except: pass
                
                if not auth_token or not feed_token:
                     logger.error(">>> [MarketFeed] Missing Auth/Feed Tokens.")
                     time.sleep(10)
                     continue

                # 3. Init WebSocket
                self.sws = SmartWebSocketV2(
                    auth_token, 
                    Config.API_KEY, 
                    Config.CLIENT_ID, 
                    feed_token
                )

                # 4. Bind Callbacks
                self.sws.on_open = self._on_open
                self.sws.on_data = self._on_data
                self.sws.on_error = self._on_error
                self.sws.on_close = self._on_close

                # 5. Connect
                logger.info(">>> [MarketFeed] Connecting to Angel One WebSocket...")
                self.sws.connect()
                
                logger.warning(">>> [MarketFeed] Connection Closed. Reconnecting in 5s...")
                time.sleep(5)

            except Exception as e:
                logger.error(f">>> [MarketFeed] Crash: {e}. Rebooting in 5s...")
                time.sleep(5)

    def _on_open(self, ws):
        logger.info(">>> [MarketFeed] Connected! ✅")
        self.is_connected = True
        self.subscribed_tokens.clear()
        
        # 1. Subscribe to Nifty 50 Spot (Token 99926000)
        try:
            token_list = [{"exchangeType": 1, "tokens": ["99926000"]}]
            self.sws.subscribe("cor_id_nifty_spot", 3, token_list)
            logger.info(">>> [MarketFeed] Subscribed to Nifty 50 Spot")
            
            # 2. Trigger Dynamic Subscription (in separate thread to not block on_open)
            threading.Thread(target=self._manage_dynamic_subscriptions, daemon=True).start()
            
        except Exception as e:
            logger.error(f"Subscription Error: {e}")

    def _manage_dynamic_subscriptions(self):
        """
        Periodically checks Nifty Spot Price and configures Option Subscriptions.
        """
        while self.is_connected and self.running:
            try:
                # 1. Get Nifty Spot Price
                spot_price = self.get_ltp("99926000")
                if not spot_price:
                    # Wait for data
                    time.sleep(2)
                    continue
                    
                # 2. Calculate ATM
                strike_diff = 50
                atm = round(spot_price / strike_diff) * strike_diff
                
                # 3. Check if ATM changed or forced refresh (every 60s)
                if atm != self.current_atm or time.time() - self.last_subscription_time > 60:
                    logger.info(f">>> [MarketFeed] Updating Subscriptions. Nifty: {spot_price}, ATM: {atm}")
                    self._update_subscriptions(atm)
                    self.current_atm = atm
                    self.last_subscription_time = time.time()
                    
            except Exception as e:
                logger.error(f"Dynamic Sub Error: {e}")
            
            time.sleep(5) # Check every 5s

    def _update_subscriptions(self, atm_strike):
        expiry = get_next_weekly_expiry()
        # logger.info(f"Fetching Options for Expiry: {expiry}")
        
        # Get Bucket: ATM +/- 5 strikes (250 points)
        bucket = self.token_lookup.get_option_bucket(expiry, atm_strike, range_points=250)
        
        if not bucket:
            logger.warning("No Options found for subscription!")
            return

        new_tokens = set()
        for key, info in bucket.items():
            new_tokens.add(info['token'])
            
        # Add Nifty Spot
        new_tokens.add("99926000")
        
        # Identify changes
        to_subscribe = new_tokens - self.subscribed_tokens
        # to_unsubscribe = self.subscribed_tokens - new_tokens # Optional: Unsub old to save bandwidth
        
        if to_subscribe:
            token_list = [{"exchangeType": 2, "tokens": list(to_subscribe)}] # Exchange 2 = NFO
            self.sws.subscribe("cor_id_options", 3, token_list)
            logger.info(f">>> [MarketFeed] Subscribed to {len(to_subscribe)} new Options.")
            self.subscribed_tokens.update(to_subscribe)

    def _on_data(self, ws, message):
        """
        Parses binary/json message.
        """
        try:
            # logger.info(f"Stream Data: {message}") # Enable for low-level debugging
            if isinstance(message, list):
                for tick in message:
                    self._process_tick(tick)
            elif isinstance(message, dict):
                self._process_tick(message)
                
        except Exception as e:
            logger.error(f"Data Parse Error: {e}")

    def _process_tick(self, tick):
        if 'token' in tick:
            token = tick['token']
            ltp = tick.get('last_traded_price')
            
            if ltp:
                # Normalization: Angel One sends LTP in Paise (Int) -> Convert to Rupees (Float)
                ltp_val = float(ltp) / 100.0
                
                # Update Snapshot Cache
                self.latest_data[token] = {
                    'ltp': ltp_val, 
                    'timestamp': time.time(),
                    'best_ask': float(tick.get('best_5_sell_data', [{}])[0].get('price', 0)) / 100.0 if tick.get('best_5_sell_data') else 0,
                    'best_bid': float(tick.get('best_5_buy_data', [{}])[0].get('price', 0)) / 100.0 if tick.get('best_5_buy_data') else 0
                }
                
                # --- Candle Construction (1-Minute) ---
                try:
                    ts = float(tick.get('exchange_timestamp', time.time())) # Prefer Exchange TS
                    dt_obj = datetime.datetime.fromtimestamp(ts)
                    timestamp_str = dt_obj.strftime("%Y-%m-%dT%H:%M:%S+05:30")
                    
                    # Volume Delta Logic
                    day_vol = float(tick.get('volume_trade_for_the_day', 0))
                    last_vol = self.last_vol_cache.get(token, day_vol) # First tick delta=0
                    vol_delta = max(0, day_vol - last_vol)
                    self.last_vol_cache[token] = day_vol
                    
                    # --- 1-Minute Candle ---
                    minute_ts = int(ts // 60) * 60
                    current = self.candle_cache.get(token)
                    
                    if not current or current['minute_ts'] != minute_ts:
                        self.candle_cache[token] = {
                            'minute_ts': minute_ts,
                            'timestamp': timestamp_str,
                            'open': ltp_val, 'high': ltp_val, 'low': ltp_val, 'close': ltp_val,
                            'volume': vol_delta
                        }
                    else:
                        current['high'] = max(current['high'], ltp_val)
                        current['low'] = min(current['low'], ltp_val)
                        current['close'] = ltp_val
                        current['volume'] += vol_delta
                        
                    # --- 5-Minute Candle ---
                    five_min_ts = int(ts // 300) * 300
                    current_5m = self.candle_cache_5m.get(token)
                    
                    if not current_5m or current_5m['minute_ts'] != five_min_ts:
                        self.candle_cache_5m[token] = {
                            'minute_ts': five_min_ts,
                            'timestamp': timestamp_str,
                            'open': ltp_val, 'high': ltp_val, 'low': ltp_val, 'close': ltp_val,
                            'volume': vol_delta
                        }
                    else:
                        current_5m['high'] = max(current_5m['high'], ltp_val)
                        current_5m['low'] = min(current_5m['low'], ltp_val)
                        current_5m['close'] = ltp_val
                        current_5m['volume'] += vol_delta
                        
                except Exception as e:
                    logger.error(f"Candle Build Error: {e}")
                # logger.debug(f"Tick: {token} -> {ltp_val}")

    def _on_error(self, ws, error):
        logger.error(f">>> [MarketFeed] Error: {error}")

    def _on_close(self, ws):
        logger.warning(">>> [MarketFeed] Disconnected ❌")
        self.is_connected = False
        self.subscribed_tokens.clear()
        
    def get_ltp(self, token):
        data = self.latest_data.get(token)
        if data and time.time() - data.get('timestamp', 0) < 10: # 10s freshness
            return data.get('ltp')
        return None

    def get_quote(self, token):
        """Returns full quote including Bid/Ask"""
        data = self.latest_data.get(token)
        if data and time.time() - data.get('timestamp', 0) < 10:
            return data
        return None

    def get_current_candle(self, token, interval_min=1):
        """Returns the current forming candle. Default 1-min."""
        if interval_min == 5:
            return self.candle_cache_5m.get(token)
        return self.candle_cache.get(token)

market_feed = MarketFeedService()
