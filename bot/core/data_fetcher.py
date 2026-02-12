import time
import datetime
import pandas as pd
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
                cls._instance.cache_duration = 55 # seconds
            elif api is not None:
                # Update API if a new one is provided (e.g. session refreshed)
                cls._instance.api = api
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
        # 1. Check Cache
        cache_key = (symbol_token, interval)
        if cache_key in self.data_cache:
            last_time, cached_df = self.data_cache[cache_key]
            if time.time() - last_time < self.cache_duration:
                # logger.info(f"Using Cached Data for {symbol_token} ({time.time() - last_time:.0f}s old)")
                return cached_df.copy() # Return copy to avoid mutation issues

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
        # So we always subtract 1 interval to be 100% safe.
        aligned_to = aligned_to - datetime.timedelta(minutes=mins)
            
        aligned_from = self._align_to_interval(now - datetime.timedelta(days=days), mins)


        # Optimization: If it's after 11:30 AM, today's data (09:15) is enough for EMA21
        if days == 1 and now.time() > datetime.time(11, 30):
            aligned_from = aligned_to.replace(hour=9, minute=15)
            
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
            try:
                # Rate limit protection + slight jitter
                import random
                time.sleep(0.5 + random.uniform(0.1, 0.3)) 
                
                response = self.api.getCandleData(historicParam)
                
                if response and response.get('status') and response.get('data'):
                    columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
                    df = pd.DataFrame(response['data'], columns=columns)
                    
                    # Convert columns to proper types
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                    df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].apply(pd.to_numeric)
                    
                    # Update Cache
                    self.data_cache[cache_key] = (time.time(), df)
                    
                    return df
                else:
                    logger.warning(f"Fetch Candles Failed (Attempt {attempt+1}): {response}")
                    logger.warning(f"Params: {historicParam}")
            
            except Exception as e:
                logger.error(f"Fetch Candles Error (Attempt {attempt+1}): {e}")
                logger.error(f"Params: {historicParam}")
                
            if attempt < max_retries - 1:
                # Exponential-ish backoff with jitter
                sleep_time = (attempt + 1) * 2 + random.uniform(0.5, 1.5)
                logger.info(f"Retrying in {sleep_time:.2f}s...")
                time.sleep(sleep_time)

        return None

