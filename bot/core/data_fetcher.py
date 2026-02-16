import time
import datetime
import pandas as pd
import json
import os
from bot.utils.logger import logger

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
        Fetches LTP and caches it for a short duration.
        """
        cache_key = (token, "LTP")
        if cache_key in self.data_cache:
            last_time, cached_ltp = self.data_cache[cache_key]
            if time.time() - last_time < 5: # LTP cache is short (5s)
                return cached_ltp

        try:
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
        cache_key = f"{symbol_token}_{interval}"
        if cache_key in self.data_cache:
            last_time, cached_df = self.data_cache[cache_key]
            if time.time() - last_time < self.cache_duration:
                return cached_df.copy()

        # 2. Check Disk Cache (for sharing across processes)
        disk_data = self._read_disk_cache(cache_key)
        if disk_data is not None:
            logger.info(f"Using Disk-Cached Data for {symbol_token}_{interval}")
            # Update in-memory cache
            self.data_cache[cache_key] = (time.time(), disk_data)
            return disk_data.copy()

        max_retries = 3
        now = datetime.datetime.now()
        
        # --- TIMESTAMP ALIGNMENT FIX (AB1004) ---
        # Angel One requires todate to be aligned with the interval boundary.
        # e.g. For 5-min candles, it MUST be 13:00, 13:05, etc.
        interval_map = {"FIVE_MINUTE": 5, "FIFTEEN_MINUTE": 15, "ONE_MINUTE": 1}
        mins = interval_map.get(interval, 5)
        
        aligned_to = self._align_to_interval(now, mins)
        
        # --- ROBUSTNESS FIX ---
        # Requesting the 'current' candle being formed often triggers AB1004.
        # We always request up to the LAST COMPLETED candle to be safe.
        # If we are at 13:19, aligned_to is 13:15. This is perfect.
        # If we are at 13:15:05, aligned_to is 13:15, but it might be too fresh.
        # So we always subtract 2 intervals to be extremely safe.
        # This ensures the data is fully finalized on Angel One's servers.
        aligned_to = aligned_to - datetime.timedelta(minutes=mins * 2)
            
        # Optimization: Snap to today's open (09:15) if we only need ~1 day of data
        # This keeps response size small and helps with NFO tokens.
        if days == 1:
            market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
            if now >= market_open:
                aligned_from = market_open
            else:
                # If before market open, get from yesterday's 09:15
                aligned_from = market_open - datetime.timedelta(days=1)
            
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
                    return df
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
             return stale_data
             
        return None


    def _read_disk_cache(self, cache_key, force_fresh=True):
        """Reads candle data from shared disk cache."""
        try:
            if not os.path.exists(self.disk_cache_path):
                return None
                
            # Check if file is fresh (for fallback, we might accept older)
            max_age = self.cache_duration if force_fresh else 600 # 10 mins fallback
            if time.time() - os.path.getmtime(self.disk_cache_path) > max_age and force_fresh:
                return None

            with open(self.disk_cache_path, "r") as f:
                full_cache = json.load(f)
                
            if cache_key in full_cache:
                entry = full_cache[cache_key]
                # Check if specific entry is fresh
                if time.time() - entry['timestamp'] < self.cache_duration:
                    df = pd.DataFrame(entry['data'], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                    df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].apply(pd.to_numeric)
                    return df
        except Exception as e:
            # logger.error(f"Disk Cache Read Error: {e}")
            pass
        return None

    def _write_disk_cache(self, cache_key, df):
        """Writes candle data to shared disk cache."""
        try:
            full_cache = {}
            if os.path.exists(self.disk_cache_path):
                try:
                    with open(self.disk_cache_path, "r") as f:
                        full_cache = json.load(f)
                except: pass
            
            # Use list records format for JSON serialization
            full_cache[cache_key] = {
                "timestamp": time.time(),
                "data": df.values.tolist()
            }
            
            if not os.path.exists(os.path.dirname(self.disk_cache_path)):
                os.makedirs(os.path.dirname(self.disk_cache_path))
                
            with open(self.disk_cache_path, "w") as f:
                json.dump(full_cache, f)
        except Exception as e:
            # logger.error(f"Disk Cache Write Error: {e}")
            pass
