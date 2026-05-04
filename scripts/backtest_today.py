import sys
import os
import datetime
import pandas as pd
from bot.core.angel_connect import get_angel_session
from bot.core.data_fetcher import DataFetcher
from bot.core.backtest_engine import BacktestEngine
from bot.config.settings import Config
from bot.utils.logger import logger

def backtest_today():
    logger.info(">>> [Backtest] 🚀 Starting Intraday Backtest for TODAY...")
    
    # 1. Connect to Angel One
    api = get_angel_session()
    if not api:
        logger.error("❌ Failed to connect to Angel One. Cannot fetch today's data.")
        return

    fetcher = DataFetcher(api)
    
    # 2. Fetch Today's 1m Data (Nifty 50 Index)
    # Angel One uses token 99926000 for Nifty 50 Spot
    token = "99926000"
    logger.info(f">>> [Backtest] Fetching 1m candles for token {token}...")
    
    # fetch_latest_candles handles the REST API call
    df = fetcher.fetch_latest_candles(token, interval="ONE_MINUTE", days=1, exchange="NSE")
    
    if df is None or df.empty:
        logger.error("❌ No data fetched for today. Ensure market was open.")
        return

    logger.info(f">>> [Backtest] Fetched {len(df)} candles. Initializing Engine...")

    # 3. Initialize Engine with User's Capital (₹35,000)
    initial_capital = 35000.0
    engine = BacktestEngine.from_capital(initial_capital=initial_capital, lot_size=Config.NIFTY_LOT_SIZE)
    
    # 4. Run Backtest
    engine.load_data(df)
    results = engine.run_all_strategies()
    
    # 5. Display Results
    print("\n" + "="*60)
    print(f"      INTRADAY BACKTEST RESULTS: {datetime.date.today()}")
    print(f"      Capital: ₹{initial_capital:,} | Lots: Dynamic")
    print("="*60)
    
    for name, m in results.items():
        if not m or 'total_trades' not in m or m['total_trades'] == 0:
            continue
            
        print(f"\n📈 Strategy: {name}")
        print(f"   Trades: {m['total_trades']}")
        print(f"   Win Rate: {m['win_rate_pct']:.1f}%")
        print(f"   Net PnL: ₹{m['total_pnl']:,.2f}")
        print(f"   Daily ROI: {(m['total_pnl'] / initial_capital * 100):.2f}%")
        print(f"   Max Drawdown: {m['max_drawdown_pct']:.2f}%")
        
        # Detail trades
        for t in m.get('trades', []):
            icon = "✅" if t['pnl'] > 0 else "❌"
            entry_raw = t['timestamp']
            exit_raw = t['exit_time']
            # Format times (handling both string and datetime objects)
            entry_t = entry_raw.split(' ')[1][:5] if ' ' in entry_raw else entry_raw[:5]
            exit_t = exit_raw.split(' ')[1][:5] if ' ' in exit_raw else exit_raw[:5]
            print(f"      {icon} {entry_t} -> {exit_t} | PnL: ₹{t['pnl']:,.0f} ({t['exit_reason']})")

    print("\n" + "="*60)
    print("   Note: This backtest uses index-based option simulation.")
    print("="*60 + "\n")

if __name__ == "__main__":
    backtest_today()
