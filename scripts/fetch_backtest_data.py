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
    
    # Set PROCESS_TYPE to BACKEND to ensure DataFetcher actually calls the API
    os.environ["PROCESS_TYPE"] = "BACKEND"
    
    logger.info(f"Fetching {days} days of ONE_MINUTE data for NIFTY Index...")
    
    # We fetch in one go as 30 days of 1-min data is ~11,000 candles (6.25 hours * 60 * 30),
    # which fits within Angel One's typical response limits.
    df = fetcher.fetch_latest_candles(
        symbol_token=NIFTY_TOKEN,
        interval="ONE_MINUTE",
        days=days,
        exchange=EXCHANGE
    )
    
    if df is not None and not df.empty:
        # Save to CSV
        output_dir = os.path.join(os.getcwd(), "data")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "historical_nifty_1m.csv")
        
        # Ensure correct column order and format
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
        df.to_csv(output_path, index=False)
        
        logger.info(f"✅ Success! Saved {len(df)} candles to {output_path}")
        logger.info(f"Data Date Range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    else:
        logger.error("❌ Failed to fetch historical data. Check logs for API errors.")

if __name__ == "__main__":
    fetch_backtest_data(days=31)
