import sys
import os
from datetime import datetime

# Add project root to sys.path
sys.path.append(os.getcwd())

from bot.core.trade_repo import trade_repo
from bot.utils.logger import logger

def analyze_losses():
    # Get last 10 trades
    try:
        trades = trade_repo.get_all_trades(limit=10)
    except Exception as e:
        print(f"Error fetching trades: {e}")
        return

    print("\n" + "="*80)
    print(f"{'ID':<4} | {'Strategy':<12} | {'Symbol':<15} | {'Entry':<8} | {'Exit':<8} | {'PnL':<8} | {'Reason'}")
    print("-" * 80)
    
    for t in trades:
        pnl = t.get('pnl', 0)
        reason = t.get('exit_reason', 'OPEN')
        entry = t.get('entry_price', 0)
        exit_p = t.get('exit_price', 0)
        print(f"{t.get('id', '??'):<4} | {t.get('strategy', '??'):<12} | {t.get('symbol', '??'):<15} | {entry:<8.1f} | {exit_p:<8.1f} | {pnl:<8.0f} | {reason}")
    print("="*80 + "\n")

if __name__ == "__main__":
    analyze_losses()
