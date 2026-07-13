import sys
import os
import pandas as pd
# pyrefly: ignore [missing-import]
import numpy as np
from datetime import time as dtime

# Add root
sys.path.append(os.getcwd())

from bot.core.backtest_engine import BacktestEngine
from bot.config.settings import Config

def classify_day(day_df):
    # Simplified regime classification
    # Compute daily ATR/PctRange and Trendness
    day_open = day_df["open"].iloc[0]
    day_close = day_df["close"].iloc[-1]
    day_high = day_df["high"].max()
    day_low = day_df["low"].min()
    
    range_pct = (day_high - day_low) / day_open * 100
    body_pct = abs(day_close - day_open) / day_open * 100
    
    if range_pct > 1.5:
        return "Volatile"
    elif body_pct > 0.6:
        return "Trending"
    else:
        return "Sideways"

def analyze_run(results, engine, slippage_label):
    report = []
    
    for name, metrics in results.items():
        if not metrics or "trades" not in metrics or not metrics["trades"]:
            continue
            
        trades = pd.DataFrame(metrics["trades"])
        trades['pnl'] = pd.to_numeric(trades['pnl'])
        trades['timestamp'] = pd.to_datetime(trades['timestamp'])
        
        # 1. Consecutive Losses
        pnl_sign = trades['pnl'].apply(lambda x: 1 if x > 0 else 0)
        consec_losses = 0
        current_streak = 0
        for s in pnl_sign:
            if s == 0:
                current_streak += 1
                consec_losses = max(consec_losses, current_streak)
            else:
                current_streak = 0
                
        # 2. Time Segments
        trades['hour'] = trades['timestamp'].dt.hour
        def get_seg(h):
            if h < 11: return "Morning"
            elif h < 13.5: return "Midday"
            else: return "Afternoon"
        trades['segment'] = trades['hour'].apply(get_seg)
        
        seg_perf = trades.groupby('segment')['pnl'].sum().to_dict()
        
        # 3. Regime analysis (fetch day's classification)
        # (We will correlate this in the master run loop)
        
        report.append({
            "Strategy": name,
            "Slippage": slippage_label,
            "NetPnL": metrics["total_pnl"],
            "WinRate": metrics["win_rate_pct"],
            "MaxDD": metrics["max_drawdown_pct"],
            "Sharpe": metrics["sharpe_ratio"],
            "MaxConsecLoss": consec_losses,
            "ProfitFactor": metrics.get("profit_factor", 0),
            "MorningPnL": seg_perf.get("Morning", 0),
            "MiddayPnL": seg_perf.get("Midday", 0),
            "AfternoonPnL": seg_perf.get("Afternoon", 0),
            "AvgWin": metrics.get("avg_win", 0),
            "AvgLoss": metrics.get("avg_loss", 0),
            "TotalTrades": len(trades)
        })
    return report

def run_quant_assessment(data_path, output_file):
    print(f"🧪 Starting Rigorous Quant Assessment Loading {data_path}...")
    df = pd.read_csv(data_path, parse_dates=['timestamp'])
    
    # Setup Baseline Capital
    cap = 100000
    
    # Robustness Test Scenarios (Slippage Perturbation)
    scenarios = [
        {"label": "Standard (0.5%)", "slippage": 0.005},
        {"label": "High Friction (1.0%)", "slippage": 0.01},
        {"label": "Chaos/Illiquid (1.5%)", "slippage": 0.015}
    ]
    
    all_perf_records = []
    
    for sc in scenarios:
        print(f" -> Running {sc['label']} scenario...")
        engine = BacktestEngine.from_capital(cap)
        # Elevate limit to 8 trades/day so morning sessions don't completely exhaust afternoon budget
        engine.max_trades_per_day = 8 
        engine.slippage_pct = sc["slippage"]
        engine.load_data(df)
        
        results = engine.run_all_strategies()
        recs = analyze_run(results, engine, sc["label"])
        all_perf_records.extend(recs)

    df_rep = pd.DataFrame(all_perf_records)
    
    # Generate the markdown artifact report string
    md = "# 📊 Institutional Quant Backtest Audit Report\n\n"
    md += "Generated via Automated Quant Framework (Simulating Realistic Friction & Theta Decay)\n\n"
    
    md += "## 🛡️ 1. Baseline Risk Metrics\n"
    base = df_rep[df_rep["Slippage"] == "Standard (0.5%)"].sort_values("NetPnL", ascending=False)
    md += "| Strategy | Net P&L | Win Rate | Max DD | Sharpe | Profit Factor | Max Consec Loss |\n"
    md += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
    for _, row in base.iterrows():
        md += f"| {row['Strategy']} | ₹{row['NetPnL']:,.0f} | {row['WinRate']:.1f}% | {row['MaxDD']:.1f}% | {row['Sharpe']:.2f} | {row['ProfitFactor']:.2f} | {row['MaxConsecLoss']} |\n"
    
    md += "\n## 📉 2. Robustness Test (Slippage Sensitivities)\n"
    md += "How does strategy performance degrade as friction increases?\n\n"
    md += "| Strategy | Standard (0.5%) | High (1.0%) | Chaos (1.5%) | Degradation Status |\n"
    md += "| :--- | :--- | :--- | :--- | :--- |\n"
    
    for strat in base["Strategy"].unique():
        strat_rows = df_rep[df_rep["Strategy"] == strat]
        s05 = strat_rows[strat_rows["Slippage"] == "Standard (0.5%)"]["NetPnL"].sum()
        s10 = strat_rows[strat_rows["Slippage"] == "High Friction (1.0%)"]["NetPnL"].sum()
        s15 = strat_rows[strat_rows["Slippage"] == "Chaos/Illiquid (1.5%)"]["NetPnL"].sum()
        
        status = "🔥 Collapsed" if s15 <= 0 and s05 > 0 else "🛡️ Robust" if s15 > 0.5 * s05 else "⚠️ Fragile"
        md += f"| {strat} | ₹{s05:,.0f} | ₹{s10:,.0f} | ₹{s15:,.0f} | **{status}** |\n"

    md += "\n## ⏰ 3. Time Segment Analysis\n"
    md += "Profit Distribution across Morning, Midday, and Afternoon sessions.\n\n"
    md += "| Strategy | Morning | Midday | Afternoon |\n"
    md += "| :--- | :--- | :--- | :--- |\n"
    for _, row in base.iterrows():
         md += f"| {row['Strategy']} | ₹{row['MorningPnL']:,.0f} | ₹{row['MiddayPnL']:,.0f} | ₹{row['AfternoonPnL']:,.0f} |\n"

    md += "\n## 🔍 4. Forensic Strategy Verdicts\n\n"
    
    for strat in base["Strategy"].unique():
        row = base[base["Strategy"] == strat].iloc[0]
        s15_row = df_rep[(df_rep["Strategy"] == strat) & (df_rep["Slippage"] == "Chaos/Illiquid (1.5%)")].iloc[0]
        
        md += f"### Strategy: `{strat}`\n"
        
        if row["WinRate"] > 80 and row["Sharpe"] > 10:
            md += "- **Quant Warning**: Extremely high win rate/Sharpe detected. Potentially overfitted or highly reliant on low-volatility entry windows.\n"
        
        if row["MaxConsecLoss"] > 4:
            md += f"- **Risk**: Has experienced {row['MaxConsecLoss']} losses in a row. Ensure mental tolerance can handle it.\n"
            
        if s15_row["NetPnL"] < 0:
             md += "- **Friction Trap**: Strategy becomes unprofitable at 1.5% slippage. DO NOT trade with slow feeds or market orders during high volatility.\n"
        else:
             md += "- **Friction Armor**: Strategy survives chaos slippage well. High alpha cushion.\n"
        
        ratio = abs(row["AvgWin"] / row["AvgLoss"]) if row["AvgLoss"] != 0 else float('inf')
        md += f"- **Avg Reward/Risk**: {ratio:.2f} ratio (AvgWin: ₹{row['AvgWin']:,.0f}, AvgLoss: ₹{row['AvgLoss']:,.0f})\n"
        md += "\n"

    with open(output_file, "w") as f:
        f.write(md)
    print(f"✅ Quant Report generated at {output_file}")

if __name__ == "__main__":
    DATA = "data/historical_nifty_1m.csv"
    if not os.path.exists(DATA):
        print("No data found.")
        sys.exit(1)
    
    # Default to current active conversation ID path
    default_out = "/home/cwd/.gemini/antigravity/brain/6c0986b8-bf06-476c-b9d5-0b7ccd20b9dc/quant_analysis_report.md"
    out = sys.argv[1] if len(sys.argv) > 1 else default_out
    
    # Ensure directory exists
    os.makedirs(os.path.dirname(out), exist_ok=True)
    
    run_quant_assessment(DATA, out)
