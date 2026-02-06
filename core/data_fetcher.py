import time
import datetime
import pandas as pd
from utils.logger import logger

class DataFetcher:
    def __init__(self, api):
        self.api = api
        self.data_cache = {} # Key: (token, interval), Value: (timestamp, df)
        self.cache_duration = 55 # seconds (Just under 1 minute)

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
        # Look back 'days' to ensure we have data, but usually strictly for today/intraday
        from_date = (now - datetime.timedelta(days=days)).strftime("%Y-%m-%d 09:15")
        to_date = now.strftime("%Y-%m-%d %H:%M")

        historicParam = {
            "exchange": exchange,
            "symboltoken": symbol_token,
            "interval": interval,
            "fromdate": from_date,
            "todate": to_date
        }

        for attempt in range(max_retries):
            try:
                # Rate limit protection
                time.sleep(0.5) 
                
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
            
            except Exception as e:
                logger.error(f"Fetch Candles Error (Attempt {attempt+1}): {e}")
                
            if attempt < max_retries - 1:
                time.sleep(1)

        return None
