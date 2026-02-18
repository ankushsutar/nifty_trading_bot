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
                cls._instance.cache_duration = 115 # seconds (Increased to ~2 mins)
                cls._instance.disk_cache_path = os.path.join(os.getcwd(), "data", "cache_candles.json")
                cls._instance.disk_cache_lock = os.path.join(os.getcwd(), "data", "cache_candles.lock")
                cls._instance.last_session_check = time.time()
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

    def get_ltp(self, token, exchange="NSE"):
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
            if time.time() - last_time < 5: # LTP cache is short (5s)
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

    def fetch_latest_candles(self, symbol_token, interval="FIVE_MINUTE", days=1, exchange="NSE"):
        """
        Fetches historic candle data and returns a DataFrame.
        Uses caching to prevent hitting unnecessary API limits.
        """
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

        max_retries = 3
        now = datetime.datetime.now()
        
        # --- TIMESTAMP ALIGNMENT FIX (AB1004) ---
        # Angel One requires todate to be aligned with the interval boundary.
        # e.g. For 5-min candles, it MUST be 13:00, 13:05, etc.
        interval_map = {"FIVE_MINUTE": 5, "FIFTEEN_MINUTE": 15, "ONE_MINUTE": 1}
        mins = interval_map.get(interval, 5)
        
        aligned_to = self._align_to_interval(now, mins)
        
        # --- ROBUSTNESS FIX ---
        # We rely on retry logic to backoff if AB1004 occurs, instead of hardcoded 10-min buffer.
        # This allows fetching the most recent closed candle.
        # aligned_to = aligned_to - datetime.timedelta(minutes=mins * 0) 
            
        # Optimization: Snap to today's open (09:15) if we only need ~1 day of data
        # This keeps response size small and helps with NFO tokens.
        if days == 1:
            market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
            if now >= market_open:
                aligned_from = market_open
            else:
                # Before market open — walk back to the last valid trading day
                # (avoids requesting Saturday/Sunday data on Monday mornings)
                prev_day = now.date() - datetime.timedelta(days=1)
                while not is_trading_day(prev_day):
                    prev_day -= datetime.timedelta(days=1)
                aligned_from = datetime.datetime.combine(
                    prev_day, datetime.time(9, 15)
                )
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
                    return self._merge_live_candle(df, symbol_token, interval)
                else:
                    err_code = response.get('errorcode')
                    logger.warning(f"Fetch Candles Failed (Attempt {attempt+1}): {response}")
                    
                    if err_code == 'AB1004':
                        if attempt >= 1: # Trigger breaker on 2nd+ fail
                            from bot.utils.rate_limiter import rate_limiter
                            rate_limiter.trigger_circuit_breaker(30)
            
            except Exception as e:
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
             return self._merge_live_candle(stale_data, symbol_token, interval)
             
        return None

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


    def _read_disk_cache(self, cache_key, force_fresh=True):
        """Reads candle data from shared disk cache (with file lock for multi-process safety)."""
        try:
            if not os.path.exists(self.disk_cache_path):
                return None

            max_age = self.cache_duration if force_fresh else 600
            if time.time() - os.path.getmtime(self.disk_cache_path) > max_age and force_fresh:
                return None

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
                if time.time() - entry['timestamp'] < self.cache_duration:
                    df = pd.DataFrame(entry['data'], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                    df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].apply(pd.to_numeric)
                    return df
        except Exception:
            pass
        return None

    def _write_disk_cache(self, cache_key, df):
        """Writes candle data to shared disk cache (with exclusive file lock for multi-process safety)."""
        try:
            os.makedirs(os.path.dirname(self.disk_cache_path), exist_ok=True)
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
                    "data": df.values.tolist()
                }

                with open(self.disk_cache_path, "w") as f:
                    json.dump(full_cache, f)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
        except Exception:
            pass
