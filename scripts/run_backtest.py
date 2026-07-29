import sys
import os
import pandas as pd

# Add project root to sys.path
sys.path.append(os.getcwd())

from bot.core.backtest_engine import BacktestEngine
from bot.config.settings import Config
from bot.utils.logger import logger

def run_ranked_backtest(initial_capital=None):
    """
    Loads historical data, runs all strategies via BacktestEngine,
    and prints a ranked comparison.
    """
    DATA_PATH = os.path.join(os.getcwd(), "data", "historical_nifty_1m.csv")
    
    if not os.path.exists(DATA_PATH):
        logger.error(f"❌ Historical data file not found at {DATA_PATH}")
        logger.error("Please run 'python3 scripts/fetch_backtest_data.py' first.")
        return

    # 1. Load Data
    logger.info(f"--- 📊 Loading Historical Data: {DATA_PATH} ---")
    try:
        df = pd.read_csv(DATA_PATH, parse_dates=['timestamp'])
    except Exception as e:
        logger.error(f"Failed to read CSV: {e}")
        return

    # 2. Setup Engine
    # If initial_capital not provided, use simulation capital from settings
    if initial_capital is None:
        initial_capital = Config.SIMULATION_CAPITAL
    
    logger.info(f"Initializing BacktestEngine with ₹{initial_capital:,.2f} capital...")
    engine = BacktestEngine.from_capital(initial_capital=initial_capital)
    
    # Check if strict mode is requested (forces exact live lot limits)
    is_strict = any(arg.lower() == "strict" for arg in sys.argv)
    if is_strict:
        tier = Config.get_tier(initial_capital)
        engine.max_lots = tier.max_lots
        logger.info(f"🔒 STRICT MODE ENABLED: Capping backtest max_lots to exact live limit of {engine.max_lots} lots.")
    
    # 3. Load & Run
    engine.load_data(df)
    results = engine.run_all_strategies()

    # 4. Process Results for Ranking
    comparison_data = []
    
    for name, m in results.items():
        if "error" in m:
            continue
            
        comparison_data.append({
            "Strategy": name,
            "Net P&L (₹)": f"{m.get('total_pnl', 0):,.0f}",
            "CAGR (%)": f"{m.get('cagr_pct', 0):.1f}%",
            "Max DD (%)": f"{m.get('max_drawdown_pct', 0):.1f}%",
            "Sharpe": round(m.get('sharpe_ratio', 0), 2),
            "Win Rate (%)": f"{m.get('win_rate_pct', 0):.1f}%",
            "Trades": m.get('total_trades', 0),
            "Profit Factor": round(m.get('profit_factor', 0), 2)
        })

    if not comparison_data:
        logger.error("No valid backtest results generated.")
        return

    # Sort by Sharpe Ratio (Descending)
    comparison_data.sort(key=lambda x: x["Sharpe"], reverse=True)

    # 5. Print Summary Table
    print("\n" + "="*95)
    print(f"      🏆 STRATEGY RANKING (Last 30 Days) | Capital: ₹{initial_capital:,.0f}")
    print("="*95)
    
    header = f"{'Strategy':<12} | {'Net P&L (₹)':>12} | {'CAGR (%)':>10} | {'Max DD (%)':>10} | {'Sharpe':>8} | {'Win%':>8} | {'Trades':>6}"
    print(header)
    print("-" * len(header))
    
    for row in comparison_data:
        print(f"{row['Strategy']:<12} | {row['Net P&L (₹)']:>12} | {row['CAGR (%)']:>10} | {row['Max DD (%)']:>10} | {row['Sharpe']:>8} | {row['Win Rate (%)']:>8} | {row['Trades']:>6}")
    
    print("="*95)

    print("\n" + "="*95)
    print("      📋 DETAILED TRADE LOGS")
    print("="*95)
    for name, m in results.items():
        if "trades" in m and m["trades"]:
            print(f"\nStrategy: {name}")
            t_header = f"  {'Timestamp':<25} | {'Dir':<3} | {'Lots':<4} | {'Entry':>8} | {'Exit':>8} | {'P&L (₹)':>10} | {'Exit Reason':<15}"
            print(t_header)
            print("  " + "-" * (len(t_header) - 2))
            for t in m["trades"]:
                print(f"  {t['timestamp']:<25} | {t['direction']:<3} | {t['lots']:<4} | {t['entry']:>8.2f} | {t['exit']:>8.2f} | {t['pnl']:>10.2f} | {t['exit_reason']:<15}")
    print("="*95)

    # 6. Hero Trade Callouts (>100% P&L)
    hero_trades = []
    for name, m in results.items():
        if "trades" in m:
            for t in m["trades"]:
                roi = (t["exit"] - t["entry"]) / t["entry"] * 100
                if roi >= 100:
                    hero_trades.append({
                        "Strategy": name,
                        "Date": t["date"],
                        "Entry": t["entry"],
                        "Exit": t["exit"],
                        "ROI (%)": f"{roi:,.1f}%",
                        "Reason": t["exit_reason"]
                    })
    
    if hero_trades:
        print("\n" + "🚀 " + "!"*20 + " HERO TRADES (>100% ROI) detected! " + "!"*20)
        hero_trades.sort(key=lambda x: float(x["ROI (%)"].replace("%", "").replace(",", "")), reverse=True)
        h_header = f"{'Strategy':<12} | {'Date':<12} | {'Entry':>8} | {'Exit':>8} | {'ROI (%)':>10} | {'Reason':<15}"
        print(h_header)
        print("-" * len(h_header))
        for h in hero_trades[:5]: # Show top 5
            print(f"{h['Strategy']:<12} | {h['Date']:<12} | {h['Entry']:>8.1f} | {h['Exit']:>8.1f} | {h['ROI (%)']:>10} | {h['Reason']:<15}")
        print("!"*75 + "\n")

    # Final recommendation
    top_strategy = comparison_data[0]["Strategy"]
    logger.info(f"💡 Recommendation: [{top_strategy}] performed best on a risk-adjusted basis (Sharpe).")

if __name__ == "__main__":
    # Use capital from command line or default from settings
    cap = None
    if len(sys.argv) > 1:
        try:
            cap = float(sys.argv[1])
        except ValueError:
            pass
            
    run_ranked_backtest(initial_capital=cap)
