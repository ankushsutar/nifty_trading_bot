import sys
import os
sys.path.append(os.getcwd())

from bot.core.angel_connect import get_angel_session
from bot.core.data_fetcher import DataFetcher
import pandas as pd

def main():
    api = get_angel_session()
    fetcher = DataFetcher(api)
    df = fetcher.fetch_latest_candles("99926000", interval="FIVE_MINUTE", days=1, exchange="NSE")
    if df is not None:
        print("\n=== Last 5 NIFTY 5m Candles ===")
        print(df.tail(5)[['timestamp', 'open', 'high', 'low', 'close']])
        
        _l3 = df.tail(3)
        _bull = (_l3['close'] > _l3['open']).sum()
        _bear = (_l3['close'] < _l3['open']).sum()
        print(f"\nBullish count: {_bull}/3")
        print(f"Bearish count: {_bear}/3")

if __name__ == "__main__":
    main()
