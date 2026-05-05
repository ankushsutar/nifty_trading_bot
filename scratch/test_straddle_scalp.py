
import pandas as pd
from bot.core.backtest_engine import BacktestEngine
from bot.utils.logger import logger

def run_straddle_backtest():
    hist_path = "data/historical_nifty_1m.csv"
    df = pd.read_csv(hist_path, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    
    engine = BacktestEngine(initial_capital=200000)
    engine.load_data(df)
    
    # Run only STRADDLE_SCALP
    results = engine.run_strategy("STRADDLE_SCALP")
    
    print("\n--- STRADDLE SCALP RESULTS ---")
    print(f"Final Capital: ₹{results['final_capital']:,.0f}")
    print(f"Total P&L:     ₹{results['total_pnl']:,.0f}")
    print(f"Win Rate:      {results['win_rate_pct']}%")
    print(f"Total Trades:  {results['total_trades']}")

if __name__ == "__main__":
    run_straddle_backtest()
