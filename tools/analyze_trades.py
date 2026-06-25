import sys
import os
import datetime
from collections import defaultdict

# Add project root to sys.path
sys.path.append(os.getcwd())

from bot.core.trade_repo import trade_repo

def run_analysis():
    # Fetch all LIVE trades (we purged PAPER trades but let's filter to be sure)
    cursor = trade_repo.collection.find({"mode": "LIVE"}).sort("created_at", 1)
    trades = list(cursor)
    
    if not trades:
        print("# Trade Analysis Report")
        print("\nNo LIVE trades found in the database.")
        return
        
    total_trades = len(trades)
    closed_trades = [t for t in trades if t.get('status') == 'CLOSED']
    open_trades = [t for t in trades if t.get('status') == 'OPEN']
    placed_trades = [t for t in trades if t.get('status') == 'PLACED']
    
    # Financial metrics on closed trades
    total_closed = len(closed_trades)
    winners = [t for t in closed_trades if (t.get('pnl') or 0.0) > 0.0]
    losers = [t for t in closed_trades if (t.get('pnl') or 0.0) <= 0.0]
    
    win_count = len(winners)
    loss_count = len(losers)
    win_rate = (win_count / total_closed * 100) if total_closed > 0 else 0.0
    
    gross_profit = sum(t.get('pnl', 0.0) for t in winners)
    gross_loss = sum(t.get('pnl', 0.0) for t in losers)
    net_pnl = gross_profit + gross_loss
    
    profit_factor = (gross_profit / abs(gross_loss)) if gross_loss < 0 else (float('inf') if gross_profit > 0 else 1.0)
    
    avg_win = (gross_profit / win_count) if win_count > 0 else 0.0
    avg_loss = (gross_loss / loss_count) if loss_count > 0 else 0.0
    
    # Strategy performance
    strategy_stats = defaultdict(lambda: {"count": 0, "wins": 0, "losses": 0, "pnl": 0.0})
    for t in closed_trades:
        strat = t.get('strategy', 'UNKNOWN')
        pnl = t.get('pnl', 0.0)
        stats = strategy_stats[strat]
        stats["count"] += 1
        stats["pnl"] += pnl
        if pnl > 0:
            stats["wins"] += 1
        else:
            stats["losses"] += 1
            
    # Daily performance
    daily_stats = defaultdict(lambda: {"count": 0, "pnl": 0.0})
    for t in closed_trades:
        created_at = t.get('created_at')
        if not created_at:
            continue
        date_str = created_at.strftime('%Y-%m-%d')
        pnl = t.get('pnl', 0.0)
        daily_stats[date_str]["count"] += 1
        daily_stats[date_str]["pnl"] += pnl

    # Generate Markdown Output
    print("# 📊 Trade Performance & Analysis Report")
    print(f"*Generated on: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    print("\n## 📈 Key Metrics Summary")
    print("| Metric | Value |")
    print("|---|---|")
    print(f"| **Total Trade Records** | {total_trades} |")
    print(f"| **Closed Trades** | {total_closed} |")
    print(f"| **Open Trades** | {len(open_trades)} |")
    print(f"| **Placed Trades (Pending)** | {len(placed_trades)} |")
    print(f"| **Winning Trades** | {win_count} |")
    print(f"| **Losing Trades** | {loss_count} |")
    print(f"| **Win Rate** | {win_rate:.2f}% |")
    print(f"| **Net Profit / Loss** | **₹{net_pnl:+.2f}** |")
    print(f"| **Gross Profit** | ₹{gross_profit:+.2f} |")
    print(f"| **Gross Loss** | ₹{gross_loss:+.2f} |")
    if profit_factor == float('inf'):
        print(f"| **Profit Factor** | ∞ |")
    else:
        print(f"| **Profit Factor** | {profit_factor:.2f} |")
    print(f"| **Average Win** | ₹{avg_win:+.2f} |")
    print(f"| **Average Loss** | ₹{avg_loss:+.2f} |")
    
    print("\n## 🎯 Performance by Strategy")
    print("| Strategy | Closed Trades | Wins / Losses | Win Rate | Net PnL |")
    print("|---|---|---|---|---|")
    for strat, stats in sorted(strategy_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
        s_count = stats["count"]
        s_win_rate = (stats["wins"] / s_count * 100) if s_count > 0 else 0.0
        pnl_val = stats["pnl"]
        print(f"| `{strat}` | {s_count} | {stats['wins']} W / {stats['losses']} L | {s_win_rate:.1f}% | **₹{pnl_val:+.2f}** |")

    print("\n## 📅 Daily PnL Trend")
    print("| Date | Trade Count | Daily PnL |")
    print("|---|---|---|")
    for d_str, stats in sorted(daily_stats.items()):
        pnl_val = stats["pnl"]
        print(f"| {d_str} | {stats['count']} | **₹{pnl_val:+.2f}** |")

    print("\n## 📜 Full Trade Ledger")
    print("| ID | Date | Symbol | Side | Qty | Strategy | Status | Entry | Exit | Net PnL |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for t in reversed(trades):
        date_str = t.get('created_at').strftime('%Y-%m-%d %H:%M') if t.get('created_at') else "N/A"
        pnl = t.get('pnl')
        pnl_str = f"**₹{pnl:+.2f}**" if pnl is not None else "-"
        
        row = [
            str(t.get('id')),
            date_str,
            t.get('symbol', 'N/A'),
            t.get('side', 'BUY'),
            str(t.get('qty', 0)),
            t.get('strategy', 'N/A'),
            f"`{t.get('status', 'N/A')}`",
            f"₹{t.get('entry_price', 0.0):.2f}" if t.get('entry_price') else "-",
            f"₹{t.get('exit_price', 0.0):.2f}" if t.get('exit_price') else "-",
            pnl_str
        ]
        print("| " + " | ".join(row) + " |")

if __name__ == "__main__":
    run_analysis()
