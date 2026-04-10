import time
import datetime
import pandas as pd
import json
import os
import fcntl
from bot.utils.logger import logger
from bot.utils.expiry_calculator import is_trading_day

import threading

class DataFetcher:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, api=None):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(DataFetcher, cls).__new__(cls)
                cls._instance.api = api
                cls._instance.data_cache = {} # Key: (token, interval), Value: (timestamp, df)
                # 600s cache = 10 minutes.
                # WebSocket keeps prices live, so we only need REST for historical context.
                cls._instance.cache_duration = 3600 # 1 Hour cache (Hybrid merge keeps candles live)
                cls._instance._ab1004_cooldowns = {} # Key: token, Value: timestamp of last AB1004
                cls._instance.disk_cache_path = os.path.join(os.getcwd(), "data", "cache_candles.json")
                cls._instance.disk_cache_lock = os.path.join(os.getcwd(), "data", "cache_candles.lock")
                cls._instance.last_session_check = time.time()
                # Per-key in-flight lock: prevents multiple strategies from firing
                # simultaneous REST calls for the same token+interval.
                cls._instance._inflight_locks = {}
                cls._instance._inflight_lock_guard = threading.Lock()
            elif api is not None:
                # Update API if a new one is provided (e.g. session refreshed)
                cls._instance.api = api
                cls._instance.last_session_check = time.time()
            return cls._instance

    def __init__(self, api=None):
        # Init logic moved to __new__ for singleton consistency
        pass

    def _align_to_interval(self, dt, interval_mins=5):
        """Rounds down a datetime to the nearest interval boundary."""
        remainder = dt.minute % interval_mins
        return dt.replace(minute=dt.minute - remainder, second=0, microsecond=0)

    def get_ltp(self, token, exchange=None):
        """
        Fetches LTP using WebSocket (Hot Path) or API (Cold Path).
        """
        # 1. Hot Path: Check Real-Time Market Feed
        # This is sub-millisecond if data is available
        from bot.core.market_feed import market_feed
        hot_ltp = market_feed.get_ltp(token)
        if hot_ltp:
            # found in websocket cache
            return hot_ltp

        # 2. Cold Path: API Polling (Legacy)
        cache_key = (token, "LTP")
        if cache_key in self.data_cache:
            last_time, cached_ltp = self.data_cache[cache_key]
            if time.time() - last_time < 15: # LTP cache is longer (15s) to avoid AB1004
                return cached_ltp

        try:
            from bot.utils.rate_limiter import rate_limiter
            rate_limiter.wait()
            resp = self.api.ltpData(exchange, "SYMBOL", token)
            if resp and resp.get('status'):
                ltp = float(resp['data']['ltp'])
                self.data_cache[cache_key] = (time.time(), ltp)
                return ltp
        except Exception as e:
            logger.error(f"DataFetcher LTP Error: {e}")
        
        return 0.0

    def fetch_latest_candles(self, symbol_token, interval="FIVE_MINUTE", days=1, exchange=None):
        """
        Fetches historic candle data and returns a DataFrame.
        Priority: WebSocket ring-buffer (ONE_MINUTE) → In-Memory Cache → Disk Cache → REST.
        Uses in-flight deduplication so only ONE REST call fires per token+interval.
        """
        # 0. WebSocket Fast Path for ONE_MINUTE (ORB/OHL opening range)
        # market_feed builds 1-min candles from ticks in real-time — no REST needed.
        if interval in ["ONE_MINUTE", "FIVE_MINUTE"]:
            try:
                from bot.core.market_feed import market_feed
                if interval == "ONE_MINUTE":
                    ws_candles = market_feed.get_1min_candles(symbol_token)
                else:
                    ws_candles = market_feed.get_5min_candles(symbol_token)
                
                if ws_candles is not None and len(ws_candles) >= 1:
                    logger.debug(f"DataFetcher: {interval} from WebSocket ring-buffer ({len(ws_candles)} candles)")
                    return ws_candles
            except Exception as e:
                logger.debug(f"DataFetcher: WebSocket {interval} path unavailable: {e}")
            # Fall through to REST if WebSocket buffer is empty

        # 1. Check In-Memory Cache first
        cache_key = f"{symbol_token}_{interval}_{days}"
        if cache_key in self.data_cache:
            last_time, cached_df = self.data_cache[cache_key]
            if time.time() - last_time < self.cache_duration:
                return self._merge_live_candle(cached_df.copy(), symbol_token, interval)

        # 2. Check Disk Cache (for sharing across processes)
        disk_data = self._read_disk_cache(cache_key)
        if disk_data is not None:
            logger.info(f"Using Disk-Cached Data for {symbol_token}_{interval}")
            # Update in-memory cache
            self.data_cache[cache_key] = (time.time(), disk_data)
            return self._merge_live_candle(disk_data.copy(), symbol_token, interval)

        # 3. AB1004 Cool-down: If we recently hit a rate limit for this token, 
        # return stale data immediately instead of hammering the API again.
        cooldown_ts = self._ab1004_cooldowns.get(symbol_token, 0)
        if time.time() - cooldown_ts < 900: # 15 min cool-down
            logger.warning(f"DataFetcher: 🛑 AB1004 Cool-down active for {symbol_token}. Using STALE data (up to 4h fallback).")
            # Emergency: Use any available cache for up to 4 hours if broker is blocking us
            stale_data = self._read_disk_cache(cache_key, force_fresh=False, max_age=14400)
            if stale_data is not None:
                return self._merge_live_candle(stale_data, symbol_token, interval)
            return None

        # 4. In-Flight Deduplication: only ONE REST call per unique cache_key at a time.
        # If another strategy is already fetching this token+interval, wait for it
        # and return the result from the shared cache — no second REST call fired.
        with self._inflight_lock_guard:
            if cache_key not in self._inflight_locks:
                self._inflight_locks[cache_key] = threading.Event()
                is_leader = True
            else:
                is_leader = False
                wait_event = self._inflight_locks[cache_key]

        if not is_leader:
            logger.info(f"DataFetcher: Waiting for in-flight fetch of {cache_key}...")
            wait_event.wait(timeout=30)
            # After the leader finishes, try the cache again
            if cache_key in self.data_cache:
                _, cached_df = self.data_cache[cache_key]
                return self._merge_live_candle(cached_df.copy(), symbol_token, interval)
            return None  # leader failed — return None gracefully

        # --- ARCHITECTURAL ENFORCEMENT: Master Fetcher Only ---
        process_type = os.getenv("PROCESS_TYPE", "BOT")
        if process_type != "BACKEND":
            # CHILD/BOT PROCESS: STRICTLY PROHIBITED from calling getCandleData.
            # It must poll the disk cache for up to 30s, assuming the BACKEND is fetching it.
            logger.warning(f"DataFetcher [CHILD]: Blocked REST fetch for {cache_key}. Polling shared disk cache for up to 30s...")
            start_poll = time.time()
            while time.time() - start_poll < 30:  # FIX: lowered from 120s to 30s
                disk_data = self._read_disk_cache(cache_key, force_fresh=True)
                if disk_data is not None:
                    logger.info(f"DataFetcher [CHILD]: Found Shared Data for {cache_key} after polling.")
                    self.data_cache[cache_key] = (time.time(), disk_data)
                    # Release in-flight lock for any other local threads
                    with self._inflight_lock_guard:
                        ev = self._inflight_locks.pop(cache_key, None)
                    if ev: ev.set()
                    return self._merge_live_candle(disk_data, symbol_token, interval)
                time.sleep(2)
            
            logger.error(f"DataFetcher [CHILD]: Timeout (30s) waiting for BACKEND to populate {cache_key}. Falling back to stale data.")
            with self._inflight_lock_guard:
                ev = self._inflight_locks.pop(cache_key, None)
            if ev: ev.set()
            
            # Emergency fallback: use any available cache for up to 4 hours if backend timed out
            stale_data = self._read_disk_cache(cache_key, force_fresh=False, max_age=14400)
            if stale_data is not None:
                logger.warning(f"DataFetcher [CHILD]: Using STALE data for {cache_key} (up to 4h fallback).")
                return self._merge_live_candle(stale_data, symbol_token, interval)
                
            return None

        # BACKEND PROCESS (MASTER): Proceed with REST Fetch
        max_retries = 3
        now = datetime.datetime.now()
        
        # --- TIMESTAMP ALIGNMENT FIX (AB1004) ---
        # Angel One requires todate to be aligned with the interval boundary.
        # e.g. For 5-min candles, it MUST be 13:00, 13:05, etc.
        interval_map = {"FIVE_MINUTE": 5, "FIFTEEN_MINUTE": 15, "ONE_MINUTE": 1}
        mins = interval_map.get(interval, 5)
        
        aligned_to = self._align_to_interval(now, mins)
        
        # --- FIX: Shift back by 1 minute to avoid requesting unfinalized candles ---
        # Requesting a candle exactly at its boundary can trigger AB1004/TooManyRequests
        # if the broker's historical DB hasn't finalized it yet.
        aligned_to = aligned_to - datetime.timedelta(minutes=1)
        
        # Default start time: 24 hours ago (ensures enough candles for indicators)
        aligned_from = aligned_to - datetime.timedelta(days=days)
            
        # Optimization: For intraday (days=1), ensure we have at least ~50 candles 
        # to prime indicators (EMA, ADX, RSI) properly even at 09:15 AM.
        if days == 1:
            # For intraday analysis, we always want at least 24 hours of data 
            # to include yesterday's session for indicator priming (EMA, ADX).
            # We fetch from 09:15 AM of the PREVIOUS trading day.
            prev_day = now.date() - datetime.timedelta(days=1)
            while not is_trading_day(prev_day):
                prev_day -= datetime.timedelta(days=1)
            
            aligned_from = datetime.datetime.combine(prev_day, datetime.time(9, 15))
        else:
            # For larger requests, use the standard timedelta
            aligned_from = aligned_to - datetime.timedelta(days=days)
            
        from_date = aligned_from.strftime("%Y-%m-%d %H:%M")
        to_date = aligned_to.strftime("%Y-%m-%d %H:%M")

        historicParam = {
            "exchange": exchange,
            "symboltoken": symbol_token,
            "interval": interval,
            "fromdate": from_date,
            "todate": to_date
        }


        for attempt in range(max_retries):
            # Aggressive Time Alignment on subsequent attempts
            current_aligned_to = aligned_to
            if attempt > 0:
                current_aligned_to = aligned_to - datetime.timedelta(minutes=mins * attempt)
                historicParam["todate"] = current_aligned_to.strftime("%Y-%m-%d %H:%M")

            try:
                from bot.utils.rate_limiter import rate_limiter
                rate_limiter.wait()
                
                response = self.api.getCandleData(historicParam)
                
                if response and response.get('status') and response.get('data'):
                    columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
                    df = pd.DataFrame(response['data'], columns=columns)
                    
                    if df.empty:
                        logger.warning(f"Fetch Candles Success but EMPTY data for {symbol_token}")
                        return None

                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                    df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].apply(pd.to_numeric)
                    
                    self.data_cache[cache_key] = (time.time(), df)
                    self._write_disk_cache(cache_key, df)
                    # Release in-flight lock so waiting threads get the cached result
                    with self._inflight_lock_guard:
                        ev = self._inflight_locks.pop(cache_key, None)
                    if ev:
                        ev.set()
                    return self._merge_live_candle(df, symbol_token, interval)
                if not response.get('status'):
                    err_msg = str(response.get('message', ''))
                    err_code = str(response.get('errorcode', ''))
                    
                    if err_code == 'AB1004' or "TooManyRequests" in err_msg:
                        logger.critical(f"🛑 [CRITICAL] AB1004 Rate Limit Hit for {symbol_token}. Triggering 60s Circuit Breaker.")
                        self._ab1004_cooldowns[symbol_token] = time.time()
                        from bot.utils.rate_limiter import rate_limiter
                        # Trigger 60s penalty to prevent global starvation while the token cools down
                        rate_limiter.trigger_circuit_breaker(60)
                        return None # STOP RETRYING immediately for AB1004

                    if attempt < max_retries - 1:
                        import random
                        sleep_time = (attempt + 1) * 3 + random.uniform(1.0, 5.0)
                        logger.warning(f"Fetch Candles Failed (Attempt {attempt+1}): {response}. Retrying in {sleep_time:.2f}s...")
                        time.sleep(sleep_time)
                        continue
                    else:
                        logger.error(f"Fetch Candles Final Failure: {response}")
                        return None
            
            except Exception as e:
                err_str = str(e)
                if "AB1004" in err_str or "TooManyRequests" in err_str:
                    logger.critical(f"🛑 [CRITICAL] AB1004 Exception for {symbol_token}. Triggering 60s Circuit Breaker.")
                    from bot.utils.rate_limiter import rate_limiter
                    rate_limiter.trigger_circuit_breaker(60)
                    return None # Critical: Do not continue loop
                
                logger.error(f"Fetch Candles Error (Attempt {attempt+1}): {e}")
                
                # --- SESSION RELOAD CHECK ---
                # If we encounter an error, check if the session file has been updated (by MarketService)
                try:
                    session_file = os.path.join(os.getcwd(), "data", "session.json")
                    if os.path.exists(session_file):
                        file_mtime = os.path.getmtime(session_file)
                        if file_mtime > self.last_session_check:
                            logger.info(">>> [DataFetcher] Deteced New Session File! Reloading API... 🔄")
                            from bot.core.angel_connect import get_angel_session
                            new_api = get_angel_session()
                            if new_api:
                                self.api = new_api
                                self.last_session_check = time.time()
                                logger.info(">>> [DataFetcher] API Instance Reloaded Successfully.")
                except Exception as ex:
                    logger.warning(f"Session Reload Check Failed: {ex}")
                # -----------------------------

            if attempt < max_retries - 1:
                import random
                sleep_time = (attempt + 1) * 2 + random.uniform(0.5, 1.5)
                logger.info(f"Retrying Candle Fetch in {sleep_time:.2f}s...")
                time.sleep(sleep_time)

        # --- OPTIMISTIC FALLBACK ---
        # If all retries fail, check if we have ANY data in disk/memory cache 
        # that is not TOO old (e.g. < 10 mins)
        stale_data = self._read_disk_cache(cache_key, force_fresh=False)
        if stale_data is not None:
             logger.warning(f"!!! [System] All retries failed. Returning STALE cached data for {symbol_token} as fallback.")
             result = self._merge_live_candle(stale_data, symbol_token, interval)
        else:
             result = None

        # Always release the in-flight lock so waiting threads unblock
        with self._inflight_lock_guard:
            ev = self._inflight_locks.pop(cache_key, None)
        if ev:
            ev.set()

        return result

    def _merge_live_candle(self, df, token, interval):
        """Appends real-time forming candle from MarketFeed if available."""
        try:
            from bot.core.market_feed import market_feed
            interval_map = {"FIVE_MINUTE": 5, "ONE_MINUTE": 1}
            mins = interval_map.get(interval)
            
            if not mins: return df
            
            live_candle = market_feed.get_current_candle(token, interval_min=mins)
            if not live_candle: return df
            
            # Create a localized timestamp for comparison
            # Live candle timestamp string is already formatted
            live_ts = pd.to_datetime(live_candle['timestamp'])
            
            if df.empty:
                # Create single row df
                new_row = {
                    'timestamp': live_ts,
                    'open': live_candle['open'],
                    'high': live_candle['high'],
                    'low': live_candle['low'],
                    'close': live_candle['close'],
                    'volume': live_candle['volume']
                }
                return pd.DataFrame([new_row])
                
            last_ts = df.iloc[-1]['timestamp']
            
            if live_ts > last_ts:
                # Append
                new_row = pd.DataFrame([{
                    'timestamp': live_ts,
                    'open': live_candle['open'],
                    'high': live_candle['high'],
                    'low': live_candle['low'],
                    'close': live_candle['close'],
                    'volume': live_candle['volume']
                }])
                return pd.concat([df, new_row], ignore_index=True)
            elif live_ts == last_ts:
                # Update Last Row (Refinement)
                idx = df.index[-1]
                df.at[idx, 'high'] = max(df.at[idx, 'high'], live_candle['high'])
                df.at[idx, 'low'] = min(df.at[idx, 'low'], live_candle['low'])
                df.at[idx, 'close'] = live_candle['close']
                # Volume might be tricky if API volume is different, but let's trust API > WebSocket for closed/forming match?
                # Actually if timestamps match, it means API returned an OPEN/Incomplete candle?
                # Angel One usually returns completed.
                # If they match, maybe we just leave it or take the 'fresher' one?
                # Let's update close/high/low to be sure.
                pass
                
        except Exception as e:
            logger.error(f"Hybrid Merge Error: {e}")
            
        return df


    def _read_disk_cache(self, cache_key, force_fresh=True, max_age=None):
        """Reads candle data from shared disk cache (with file lock for multi-process safety)."""
        duration = max_age if max_age is not None else self.cache_duration
        try:
            if not os.path.exists(self.disk_cache_path):
                return None

            # Removed: os.path.getmtime check. This is unreliable in multi-process 
            # where a child might have a stale view of the file metadata.
            # We rely on the per-entry 'timestamp' within the JSON instead.

            lock_fd = os.open(self.disk_cache_lock, os.O_RDWR | os.O_CREAT)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_SH)  # Shared lock for reads
                with open(self.disk_cache_path, "r") as f:
                    full_cache = json.load(f)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)

            if cache_key in full_cache:
                entry = full_cache[cache_key]
                if time.time() - entry['timestamp'] < duration:
                    df = pd.DataFrame(entry['data'], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                    df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].apply(pd.to_numeric)
                    return df
        except Exception:
            pass
        return None

    def _write_disk_cache(self, cache_key, df):
        """Writes candle data to shared disk cache with atomic-style write and JSON-safe timestamps."""
        try:
            os.makedirs(os.path.dirname(self.disk_cache_path), exist_ok=True)
            
            # 1. Prepare Serialized Data (JSON safe)
            # Convert Timestamp objects to ISO strings
            serializable_df = df.copy()
            serializable_df['timestamp'] = serializable_df['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')
            
            lock_fd = os.open(self.disk_cache_lock, os.O_RDWR | os.O_CREAT)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)  # Exclusive lock for write
                
                full_cache = {}
                if os.path.exists(self.disk_cache_path):
                    try:
                        with open(self.disk_cache_path, "r") as f:
                            full_cache = json.load(f)
                    except Exception:
                        pass

                full_cache[cache_key] = {
                    "timestamp": time.time(),
                    "data": serializable_df.values.tolist()
                }

                # 2. Atomic Write: Write to temp file then rename (prevents corruption on crash)
                temp_path = self.disk_cache_path + ".tmp"
                with open(temp_path, "w") as f:
                    json.dump(full_cache, f)
                
                os.replace(temp_path, self.disk_cache_path)
                
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
        except Exception as e:
            logger.error(f"Disk Cache Write Error: {e}")
