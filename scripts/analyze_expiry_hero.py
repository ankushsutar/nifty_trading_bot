import sys
import os
import pandas as pd
from datetime import datetime

# Add project root to sys.path
sys.path.append(os.getcwd())

from bot.core.backtest_engine import BacktestEngine
from bot.config.settings import Config
from bot.utils.logger import logger

def analyze_expiries(initial_capital=None):
    """
    Runs backtest and specifically analyzes performance on Expiry Days (Thursdays).
    """
    DATA_PATH = os.path.join(os.getcwd(), "data", "historical_nifty_1m.csv")
    
    if not os.path.exists(DATA_PATH):
        logger.error(f"❌ Historical data file not found at {DATA_PATH}")
        return

    # 1. Load Data
    df = pd.read_csv(DATA_PATH, parse_dates=['timestamp'])
    
    # 2. Setup Engine
    if initial_capital is None:
        initial_capital = Config.SIMULATION_CAPITAL
        
    engine = BacktestEngine.from_capital(initial_capital=initial_capital)
    engine.load_data(df)
    
    # 3. Run Strategies
    results = engine.run_all_strategies()
    
    # 4. Group Results by Date
    daily_stats = []
    
    for name, m in results.items():
        if "trades" not in m: continue
        
        for t in m["trades"]:
            # Ensure trade record has 'date' or we parse from timestamp
            t_date = datetime.strptime(t["date"], "%Y-%m-%d").date()
            is_expiry = (t_date.weekday() == 3) # Thursday
            
            roi = (t["exit"] - t["entry"]) / t["entry"] * 100
            
            daily_stats.append({
                "Date": t_date,
                "Weekday": ["Mon", "Tue", "Wed", "Thu", "Fri"][t_date.weekday()],
                "Strategy": name,
                "Trades": 1,
                "PnL": t["pnl"],
                "ROI": roi,
                "Is_Expiry": is_expiry
            })

    if not daily_stats:
        print("No trades found to analyze.")
        return

    stats_df = pd.DataFrame(daily_stats)
    
    # 5. Summary of Expiry vs Non-Expiry
    expiry_summary = stats_df.groupby("Is_Expiry").agg({
        "PnL": "sum",
        "ROI": "mean",
        "Trades": "sum"
    })
    
    print("\n" + "📅 " + "="*60)
    print("      EXPIRY DAY (THURSDAY) PERFORMANCE ANALYSIS")
    print("="*62)
    
    # Daily breakdown for Thursdays
    thursdays = stats_df[stats_df["Is_Expiry"] == True].copy()
    if not thursdays.empty:
        thurs_grouped = thursdays.groupby(["Date", "Strategy"]).agg({
            "PnL": "sum",
            "ROI": "max",
            "Trades": "sum"
        }).sort_values("PnL", ascending=False)
        
        print("\nTOP EXPIRY PERFORMANCES:")
        print(thurs_grouped.head(15))
    else:
        print("\nNo Thursday trades detected in this period.")

    # Show Hero candidates
    print("\n🚀 POTENTIAL 'ZERO-TO-HERO' WINDOWS (ROI > 50%):")
    heroes = stats_df[stats_df["ROI"] >= 50].sort_values("ROI", ascending=False)
    if not heroes.empty:
        print(heroes[["Date", "Weekday", "Strategy", "ROI", "PnL"]].head(10))
    else:
        print("No Hero-level ROIs detected.")

    print("="*62 + "\n")

if __name__ == "__main__":
    cap = None
    if len(sys.argv) > 1:
        try:
            cap = float(sys.argv[1])
        except ValueError:
            pass
            
    analyze_expiries(initial_capital=cap)
