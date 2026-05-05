import pandas as pd
from bot.core.backtest_engine import BacktestEngine
from bot.utils.logger import logger
import os

def run_pro_backtest():
    # 1. Load Data
    data_path = os.path.join(os.getcwd(), "data", "historical_nifty_1m.csv")
    if not os.path.exists(data_path):
        print(f"Error: Data file not found at {data_path}")
        return

    print("📖 Loading 2 months of Nifty data...")
    df = pd.read_csv(data_path)
    
    # 2. Setup Engine (Mirrors SMALL account logic)
    # We use 35,000 as the base capital to test your exact tier.
    engine = BacktestEngine.from_capital(initial_capital=35000)
    engine.load_data(df)
    
    # 3. Run Strategies
    print("\n🚀 Running X-Factor Backtest (with Volume filters & Moonshot Trail)...")
    results = engine.run_all_strategies()
    
    # 4. Print Summary
    print("\n" + "="*50)
    print("      PRO-TRADER 30-DAY BACKTEST RESULTS")
    print("="*50)
    
    for name, m in results.items():
        if "error" in m:
            print(f"{name:15} | ERROR: {m['error']}")
            continue
            
        print(f"{name:15} | PnL: ₹{m['total_pnl']:+9.0f} | Win: {m['win_rate_pct']}% | Trades: {m['total_trades']}")
    
    print("="*50)
    
    # Detail on Momentum (your primary strategy)
    mom = results.get("MOMENTUM", {})
    if mom and "trades" in mom:
        print("\n🔥 MOMENTUM STRATEGY DEEP DIVE:")
        print(f"   Final Capital: ₹{mom['final_capital']:,}")
        print(f"   Max Drawdown:  {mom['max_drawdown_pct']}%")
        print(f"   Avg Profit/Trade: ₹{mom['avg_pnl']:.0f}")
        
        # Show top 5 winners (Moonshot efficacy)
        trades = pd.DataFrame(mom['trades'])
        if not trades.empty:
            winners = trades.sort_values("pnl", ascending=False).head(5)
            print("\n🚀 TOP 5 MOONSHOT TRADES:")
            for _, t in winners.iterrows():
                print(f"   {t['date']} | {t['direction']} | PnL: ₹{t['pnl']:+6.0f} | Reason: {t['exit_reason']}")

if __name__ == "__main__":
    run_pro_backtest()
