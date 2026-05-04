import sys
import os
import pandas as pd
from datetime import datetime

# Add project root to sys.path
sys.path.append(os.getcwd())

from bot.core.backtest_engine import BacktestEngine
from bot.config.settings import Config
from bot.utils.logger import logger

def export_trades(initial_capital=35000):
    DATA_PATH = os.path.join(os.getcwd(), "data", "historical_nifty_1m.csv")
    
    if not os.path.exists(DATA_PATH):
        logger.error(f"❌ Historical data file not found at {DATA_PATH}")
        return

    # 1. Load Data
    logger.info(f"--- 📊 Loading Data for Trade Export ---")
    df = pd.read_csv(DATA_PATH, parse_dates=['timestamp'])

    # 2. Setup Engine
    engine = BacktestEngine.from_capital(initial_capital=initial_capital)
    engine.load_data(df)
    
    # 3. Run and Collect Trades
    results = engine.run_all_strategies()
    
    all_trades = []
    for strategy_name, metrics in results.items():
        if "trades" in metrics:
            for trade in metrics["trades"]:
                # Add strategy name to each trade record
                trade["strategy"] = strategy_name
                all_trades.append(trade)
    
    if not all_trades:
        logger.error("No trades found to export.")
        return

    # 4. Save to CSV
    trades_df = pd.DataFrame(all_trades)
    
    # Reorder columns for better readability
    cols = ['date', 'timestamp', 'strategy', 'direction', 'entry', 'exit', 'pnl', 'exit_reason', 'qty', 'lots', 'sl', 'target', 'capital_after']
    trades_df = trades_df[cols]
    
    output_path = os.path.join(os.getcwd(), "data", "backtest_trade_history.csv")
    trades_df.to_csv(output_path, index=False)
    
    logger.info(f"✅ Success! Exported {len(all_trades)} trades to {output_path}")
    print(f"\n📁 Trade history saved to: {output_path}")

if __name__ == "__main__":
    cap = 35000
    if len(sys.argv) > 1:
        try:
            cap = float(sys.argv[1])
        except ValueError:
            pass
    export_trades(initial_capital=cap)
