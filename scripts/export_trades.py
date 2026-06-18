import sys
import os
import pandas as pd
from datetime import datetime

# Add project root to sys.path
sys.path.append(os.getcwd())

from bot.core.backtest_engine import BacktestEngine
from bot.config.settings import Config
from bot.utils.logger import logger

def export_trades(initial_capital=None):
    DATA_PATH = os.path.join(os.getcwd(), "data", "historical_nifty_1m.csv")
    
    if not os.path.exists(DATA_PATH):
        print("Historical data not found.")
        return

    df = pd.read_csv(DATA_PATH, parse_dates=['timestamp'])
    
    if initial_capital is None:
        initial_capital = Config.SIMULATION_CAPITAL
        
    engine = BacktestEngine.from_capital(initial_capital=initial_capital)
    engine.load_data(df)
    results = engine.run_all_strategies()
    
    all_trades = []
    for name, m in results.items():
        if "trades" not in m: continue
        for t in m["trades"]:
            t["strategy"] = name
            all_trades.append(t)

    if not all_trades:
        print("No trades found.")
        return

    trades_df = pd.DataFrame(all_trades)
    output_path = os.path.join(os.getcwd(), "data", "backtest_trades_detailed.csv")
    trades_df.to_csv(output_path, index=False)
    print(f"✅ Exported {len(all_trades)} trades to {output_path}")

if __name__ == "__main__":
    cap = 38000
    if len(sys.argv) > 1:
        try:
            cap = float(sys.argv[1])
        except ValueError:
            pass
    export_trades(cap)
