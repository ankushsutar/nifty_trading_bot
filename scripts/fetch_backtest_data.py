import sys
import os
import datetime
import pandas as pd

# Add project root to sys.path
sys.path.append(os.getcwd())

from bot.core.angel_connect import get_angel_session
from bot.core.data_fetcher import DataFetcher
from bot.utils.logger import logger

def fetch_backtest_data(days=31):
    """
    Fetches historical 1-minute data for NIFTY 50 index (token: 99926000)
    and saves it to data/historical_nifty_1m.csv.
    Supports multi-chunk fetching for >30 days.
    """
    logger.info(f"--- 📥 Starting Historical Data Fetch (Last {days} Days) ---")
    
    # 1. Authenticate
    api = get_angel_session()
    if not api:
        logger.error("Failed to authenticate with Angel One.")
        return
    
    fetcher = DataFetcher(api)
    
    # NIFTY Index Token
    NIFTY_TOKEN = "99926000"
    EXCHANGE = "NSE"
    os.environ["PROCESS_TYPE"] = "BACKEND"
    
    all_dfs = []
    
    # Fetch in 30-day chunks
    # Note: for backtesting we just need the dataframe, not the DataFetcher's internal caching for BOT use.
    # DataFetcher.fetch_latest_candles is built for BOT usage (recent data).
    # For older data, we might need a direct call or multiple fetch_latest_candles calls with to_date offsets.
    # Actually, fetch_latest_candles uses current time as to_date.
    
    import datetime
    end_date = datetime.datetime.now()
    
    remaining_days = days
    while remaining_days > 0:
        chunk_days = min(30, remaining_days)
        start_date = end_date - datetime.timedelta(days=chunk_days)
        
        logger.info(f"Fetching chunk: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}...")
        
        # We'll use a lower-level or direct approach if fetch_latest_candles is too BOT-centric.
        # But let's see if we can trick fetch_latest_candles by monkeypatching datetime if needed, 
        # or just implement the call here.
        
        historicParam = {
            "exchange": EXCHANGE,
            "symboltoken": NIFTY_TOKEN,
            "interval": "ONE_MINUTE",
            "fromdate": start_date.strftime("%Y-%m-%d %H:%M"),
            "todate": end_date.strftime("%Y-%m-%d %H:%M")
        }
        
        try:
            from bot.utils.rate_limiter import rate_limiter
            rate_limiter.wait()
            response = api.getCandleData(historicParam)
            if response and response.get('status') and response.get('data'):
                columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
                chunk_df = pd.DataFrame(response['data'], columns=columns)
                all_dfs.append(chunk_df)
                logger.info(f"  Captured {len(chunk_df)} candles.")
            else:
                logger.error(f"  Failed for chunk: {response.get('message') if response else 'No response'}")
        except Exception as e:
            logger.error(f"  Error fetching chunk: {e}")
            
        remaining_days -= chunk_days
        end_date = start_date
        
    if all_dfs:
        df = pd.concat(all_dfs, ignore_index=True)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp').drop_duplicates(subset=['timestamp'])
        
        # Save to CSV
        output_dir = os.path.join(os.getcwd(), "data")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "historical_nifty_1m.csv")
        
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
        df.to_csv(output_path, index=False)
        
        logger.info(f"✅ Success! Saved {len(df)} total candles to {output_path}")
        logger.info(f"Data Date Range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    else:
        logger.error("❌ Failed to fetch any historical data.")

if __name__ == "__main__":
    days_to_fetch = 31
    if len(sys.argv) > 1:
        try:
            days_to_fetch = int(sys.argv[1])
        except ValueError:
            pass
    fetch_backtest_data(days=days_to_fetch)
