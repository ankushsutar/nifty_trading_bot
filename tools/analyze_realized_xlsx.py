import pandas as pd
# pyrefly: ignore [missing-import]
import numpy as np
import sys
from datetime import datetime, timedelta

def analyze_realized(file_path):
    # Read sheet 'Trade', skip first 7 rows so row 7 becomes header
    df = pd.read_excel(file_path, sheet_name='Trade', skiprows=7)
    
    # Clean up column names and drop empty rows
    df.columns = [c.strip() for c in df.columns]
    
    # Drop rows where 'Entry Date' is NaN or doesn't look like a date
    df = df.dropna(subset=['Entry Date', 'PnL'])
    
    # Convert PnL to float
    df['PnL'] = df['PnL'].astype(str).str.replace(',', '').astype(float)
    df['Quantity'] = df['Quantity'].astype(int)
    df['Entry Price'] = df['Entry Price'].astype(float)
    df['Exit Price'] = df['Exit Price'].astype(float)
    
    # Convert dates and times
    df['Entry_DateTime'] = pd.to_datetime(df['Entry Date'].astype(str) + ' ' + df['Entry Time'].astype(str))
    df['Exit_DateTime'] = pd.to_datetime(df['Exit Date'].astype(str) + ' ' + df['Exit Time'].astype(str))
    
    # Calculate holding time in minutes
    df['Hold_Duration_Min'] = (df['Exit_DateTime'] - df['Entry_DateTime']).dt.total_seconds() / 60.0
    
    # Basic metrics
    total_trades = len(df)
    winners = df[df['PnL'] > 0]
    losers = df[df['PnL'] <= 0]
    
    win_count = len(winners)
    loss_count = len(losers)
    win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0.0
    
    gross_profit = winners['PnL'].sum()
    gross_loss = losers['PnL'].sum()
    net_pnl = df['PnL'].sum()
    
    profit_factor = gross_profit / abs(gross_loss) if gross_loss != 0 else float('inf')
    
    avg_win = winners['PnL'].mean() if win_count > 0 else 0.0
    avg_loss = losers['PnL'].mean() if loss_count > 0 else 0.0
    
    largest_win = df['PnL'].max()
    largest_loss = df['PnL'].min()
    
    # Print Markdown Summary
    print("# 📊 Realized Trade PnL Report Analysis")
    print(f"*Analyzed Date Range: {df['Entry Date'].min()} to {df['Entry Date'].max()}*")
    print(f"*Total Trades Analyzed: {total_trades}*")
    
    print("\n## 📈 Performance Summary")
    print("| Metric | Value |")
    print("|---|---|")
    print(f"| **Net P&L** | **₹{net_pnl:+.2f}** |")
    print(f"| **Win Rate** | {win_rate:.2f}% ({win_count} Wins / {loss_count} Losses) |")
    print(f"| **Gross Profit** | ₹{gross_profit:+.2f} |")
    print(f"| **Gross Loss** | ₹{gross_loss:+.2f} |")
    print(f"| **Profit Factor** | {profit_factor:.2f} |")
    print(f"| **Avg. Winning Trade** | ₹{avg_win:+.2f} |")
    print(f"| **Avg. Losing Trade** | ₹{avg_loss:+.2f} |")
    print(f"| **Largest Winning Trade** | ₹{largest_win:+.2f} |")
    print(f"| **Largest Losing Trade** | ₹{largest_loss:+.2f} |")
    print(f"| **Average Hold Duration** | {df['Hold_Duration_Min'].mean():.1f} mins |")
    
    # Performance by Day of Week
    df['Day_of_Week'] = df['Entry_DateTime'].dt.day_name()
    day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    day_stats = df.groupby('Day_of_Week')['PnL'].agg(['count', 'sum', 'mean']).reindex(day_order)
    
    print("\n## 📅 Performance by Day of Week")
    print("| Day | Trade Count | Net PnL | Avg PnL per Trade |")
    print("|---|---|---|---|")
    for day, row in day_stats.iterrows():
        if pd.isna(row['count']):
            continue
        print(f"| {day} | {int(row['count'])} | **₹{row['sum']:+.2f}** | ₹{row['mean']:+.2f} |")
        
    # Performance by Hour of Day
    df['Hour_of_Day'] = df['Entry_DateTime'].dt.hour
    hour_stats = df.groupby('Hour_of_Day')['PnL'].agg(['count', 'sum', 'mean'])
    
    print("\n## ⏰ Performance by Entry Hour")
    print("| Entry Hour | Trade Count | Net PnL | Avg PnL per Trade |")
    print("|---|---|---|---|")
    for hour, row in hour_stats.iterrows():
        print(f"| {hour:02d}:00 - {hour+1:02d}:00 | {int(row['count'])} | **₹{row['sum']:+.2f}** | ₹{row['mean']:+.2f} |")
        
    # Quantity / Size Analysis
    qty_stats = df.groupby('Quantity')['PnL'].agg(['count', 'sum', 'mean'])
    print("\n## 📐 Performance by Position Size (Quantity)")
    print("| Quantity | Trade Count | Net PnL | Avg PnL per Trade |")
    print("|---|---|---|---|")
    for qty, row in qty_stats.iterrows():
        print(f"| {qty} | {int(row['count'])} | **₹{row['sum']:+.2f}** | ₹{row['mean']:+.2f} |")
        
    # Top 5 Wins and Losses
    print("\n## 🏆 Top 5 Winning Trades")
    print("| Date | Time | Symbol | Qty | Entry Price | Exit Price | PnL | Hold Time |")
    print("|---|---|---|---|---|---|---|---|")
    top_wins = df.sort_values(by='PnL', ascending=False).head(5)
    for idx, r in top_wins.iterrows():
        print(f"| {r['Entry Date']} | {r['Entry Time']} | {r['Symbol']} | {r['Quantity']} | ₹{r['Entry Price']:.2f} | ₹{r['Exit Price']:.2f} | **₹{r['PnL']:+.2f}** | {r['Hold_Duration_Min']:.1f}m |")

    print("\n## ⚠️ Top 5 Losing Trades")
    print("| Date | Time | Symbol | Qty | Entry Price | Exit Price | PnL | Hold Time |")
    print("|---|---|---|---|---|---|---|---|")
    top_losses = df.sort_values(by='PnL', ascending=True).head(5)
    for idx, r in top_losses.iterrows():
        print(f"| {r['Entry Date']} | {r['Entry Time']} | {r['Symbol']} | {r['Quantity']} | ₹{r['Entry Price']:.2f} | ₹{r['Exit Price']:.2f} | **₹{r['PnL']:+.2f}** | {r['Hold_Duration_Min']:.1f}m |")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python analyze_realized_xlsx.py <file_path>")
    else:
        analyze_realized(sys.argv[1])
