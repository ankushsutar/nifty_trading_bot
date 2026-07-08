import os
import sys

# Ensure project root is in path
sys.path.insert(0, os.getcwd())

from bot.config.settings import Config
from bot.core.session import get_session
from bot.core.data_fetcher import DataFetcher
from bot.core.regime_classifier import RegimeClassifier

def main():
    print(">>> Initializing session...")
    api = get_session()
    if not api:
        print(">>> Failed to initialize session.")
        return
        
    print(">>> Initializing DataFetcher...")
    data_fetcher = DataFetcher(api)
    
    # Check NIFTY 50
    symbol_token = "99926000" # NIFTY spot index
    print(f">>> Fetching latest candles for token={symbol_token}...")
    df = data_fetcher.fetch_latest_candles(symbol_token, interval="FIVE_MINUTE", days=1)
    
    if df is None:
        print(">>> df is None!")
        return
        
    print(f">>> Fetched DataFrame: len={len(df)}")
    print("First 3 rows:")
    print(df.head(3))
    print("Last 3 rows:")
    print(df.tail(3))
    
    print(">>> Classifying regime...")
    classifier = RegimeClassifier()
    res = classifier.classify(df)
    print(">>> Classification Result:")
    for k, v in res.items():
        print(f"  {k}: {v}")

if __name__ == "__main__":
    main()
