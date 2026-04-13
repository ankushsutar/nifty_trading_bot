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
                # 300s cache = 5 minutes.
                # Technical indicators need a moving window; 1 hour was too long for live strategies.
                cls._instance.cache_duration = 300 # 5 Minutes cache phase
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

    def get_ltp(self, token, exchange=None, symbol=None):
        """
        Fetches LTP using WebSocket (Hot Path) or API (Cold Path).

        Args:
            token:    Angel One symbol token (primary lookup key).
            exchange: Exchange string, e.g. "NSE", "MCX" (required for cold path).
            symbol:   Trading symbol name, e.g. "CRUDEOIL20APR26FUT".
                      Used in the ltpData cold-path call — Angel One accepts the
                      token as the authoritative key, but the symbol should still
                      be the real instrument name, not the literal "SYMBOL".
                      Defaults to the token string if not provided.
        """
        # 1. Hot Path: Check Real-Time Market Feed (sub-millisecond)
        from bot.core.market_feed import market_feed
        hot_ltp = market_feed.get_ltp(token)
        if hot_ltp:
            return hot_ltp

        # 2. Cold Path: API Polling (Legacy)
        cache_key = (token, "LTP")
        if cache_key in self.data_cache:
            last_time, cached_ltp = self.data_cache[cache_key]
            if time.time() - last_time < 15:  # LTP cache: 15s to avoid AB1004
                return cached_ltp

        try:
            from bot.utils.rate_limiter import rate_limiter
            rate_limiter.wait()
            # Use the provided symbol name; fall back to the token string so we
            # never send the literal "SYMBOL" to the broker API.
            tradingsymbol = symbol if symbol else str(token)
            resp = self.api.ltpData(exchange, tradingsymbol, token)
            if resp and resp.get('status'):
                ltp = float(resp['data']['ltp'])
                self.data_cache[cache_key] = (time.time(), ltp)
                return ltp
        except Exception as e:
            logger.error(f"DataFetcher LTP Error: {e}")

        return 0.0

    def fetch_latest_candles(self, symbol_token, interval="FIVE_MINUTE", days=1, exchange="NSE"):
        """
        Public entry point for master analysis.
        Implements multi-interval fallback: if FIVE_MINUTE fails, tries FIFTEEN_MINUTE then ONE_HOUR.
        """
        intervals_to_try = [interval]
        if interval == "FIVE_MINUTE":
            intervals_to_try.extend(["FIFTEEN_MINUTE", "ONE_HOUR"])
            
        for target_interval in intervals_to_try:
            df = self._fetch_targeted_data(symbol_token, target_interval, days, exchange)
            if df is not None and len(df) >= (10 if target_interval == "ONE_HOUR" else 20):
                if target_interval != interval:
                    logger.info(f"DataFetcher: Successfully backfilled using {target_interval}")
                return df
                
        return None

    def _fetch_targeted_data(self, symbol_token, interval="FIVE_MINUTE", days=1, exchange="NSE"):
        """Internal worker for fetching specific interval data."""
        now = datetime.datetime.now()
        aligned_to = now.replace(second=0, microsecond=0)
        
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
        
        interval_map = {"FIVE_MINUTE": 5, "FIFTEEN_MINUTE": 15, "ONE_MINUTE": 1}
        mins = interval_map.get(interval, 5)
        
        aligned_to = self._align_to_interval(now, mins)
        aligned_to = aligned_to - datetime.timedelta(minutes=1)
        
        # --- ROBUST SESSION-SPLITTING LOGIC ---
        requests = []
        if days == 1:
            market_start_str = "09:00" if exchange == "MCX" else "09:15"
            h, m = map(int, market_start_str.split(':'))
            # NUDGE: Start at 09:01 instead of 09:00 to avoid AB boundary issues
            today_start = datetime.datetime.combine(now.date(), datetime.time(h, m+1 if m==0 else m))
            
            prev_day = now.date() - datetime.timedelta(days=1)
            while not is_trading_day(prev_day, exchange=exchange):
                prev_day -= datetime.timedelta(days=1)
            
            prev_session_start = datetime.datetime.combine(prev_day, datetime.time(h, m+1 if m==0 else m))
            prev_session_end = datetime.datetime.combine(prev_day, datetime.time(23, 30 if exchange == "MCX" else 15, 30))
            
            requests.append((prev_session_start, prev_session_end, "PREV_SESSION"))
            requests.append((today_start, aligned_to, "TODAY_SESSION"))
            logger.info(f"DataFetcher: Targeted Session Fetch for {symbol_token} ({exchange})")
        else:
            aligned_from = aligned_to - datetime.timedelta(days=days)
            requests.append((aligned_from, aligned_to, "SPAN"))

        all_dfs = []
        for start_dt, end_dt, label in requests:
            from_str = start_dt.strftime("%Y-%m-%d %H:%M")
            to_str = end_dt.strftime("%Y-%m-%d %H:%M")
            
            historicParam = {
                "exchange": exchange, "symboltoken": symbol_token,
                "interval": interval, "fromdate": from_str, "todate": to_str
            }
            
            logger.info(f"DataFetcher: Requesting {label} | {from_str} to {to_str} | Token: {symbol_token}")
            
            session_df = None
            for attempt in range(max_retries):
                try:
                    from bot.utils.rate_limiter import rate_limiter
                    rate_limiter.wait()
                    
                    response = self.api.getCandleData(historicParam)
                    if response and response.get('status') and response.get('data') is not None:
                        if not response['data']:
                            logger.warning(
                                f"DataFetcher: Broker returned EMPTY list for {symbol_token} "
                                f"[{from_str} to {to_str}]. This usually means no trades occurred "
                                f"in this interval or the broker has a data gap for this contract."
                            )
                        cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
                        df_tmp = pd.DataFrame(response['data'], columns=cols)
                        if not df_tmp.empty:
                            df_tmp['timestamp'] = pd.to_datetime(df_tmp['timestamp'])
                            df_tmp[['open', 'high', 'low', 'close', 'volume']] = df_tmp[['open', 'high', 'low', 'close', 'volume']].apply(pd.to_numeric)
                            session_df = df_tmp
                            logger.info(f"DataFetcher: Received {len(df_tmp)} candles for {label}")
                            break
                        else:
                            logger.warning(f"DataFetcher: Empty data in SUCCESS response for {label}")
                    else:
                        logger.warning(f"DataFetcher: API Failed for {label}: {response}")
                    
                    time.sleep(1 * (attempt + 1))
                except Exception as e:
                    logger.error(f"DataFetcher Error: {e}")
                    time.sleep(1)

            if session_df is not None:
                all_dfs.append(session_df)

        # Merge and Validate
        if all_dfs:
            df = pd.concat(all_dfs).drop_duplicates('timestamp').sort_values('timestamp')
        else:
            df = pd.DataFrame()

        # --- HYBRID FALLBACK: If targeted fetch failed or returned tiny data (< 20 candles) ---
        if len(df) < 20:
            logger.warning(f"DataFetcher: Low data count ({len(df)}). Triggering Hybrid Span Fallback...")
            # Fallback to a single broad request from 48h ago
            fallback_from = (aligned_to - datetime.timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
            fallbackParam = {
                "exchange": exchange, "symboltoken": symbol_token,
                "interval": interval, "fromdate": fallback_from, "todate": to_str
            }
            try:
                response = self.api.getCandleData(fallbackParam)
                if response and response.get('status') and response.get('data'):
                    cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
                    df_fallback = pd.DataFrame(response['data'], columns=cols)
                    if not df_fallback.empty:
                        df_fallback['timestamp'] = pd.to_datetime(df_fallback['timestamp'])
                        df_fallback[['open', 'high', 'low', 'close', 'volume']] = df_fallback[['open', 'high', 'low', 'close', 'volume']].apply(pd.to_numeric)
                        df = df_fallback
                        logger.info(f"DataFetcher: Hybrid Fallback Success: {len(df)} candles.")
            except Exception as e:
                logger.error(f"DataFetcher: Hybrid Fallback Error: {e}")

        if df.empty:
            logger.error(f"DataFetcher: FINAL FAILURE for {symbol_token}. No data available.")
            with self._inflight_lock_guard:
                ev = self._inflight_locks.pop(cache_key, None)
            if ev: ev.set()
            return None

        # Finalize and cache
        self.data_cache[cache_key] = (time.time(), df)
        self._write_disk_cache(cache_key, df)
        
        with self._inflight_lock_guard:
            ev = self._inflight_locks.pop(cache_key, None)
        if ev: ev.set()
        
        return self._merge_live_candle(df, symbol_token, interval)

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
