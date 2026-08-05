import sys
import os
import datetime

sys.path.append(os.getcwd())

from bot.core.trade_repo import trade_repo

print(">>> Fetching all trades from DB...")
all_trades = list(trade_repo.collection.find())

print(f"Total trades in DB: {len(all_trades)}")

print("\n==========================================")
print("📋 RECENT EXECUTED TRADES LOG")
print("==========================================")
for t in all_trades[-10:]:
    entry_time = t.get('entry_time')
    exit_time = t.get('exit_time')
    print(f"ID: {t.get('id')} | Strategy: {t.get('strategy')} | Symbol: {t.get('symbol')} | Qty: {t.get('qty')}")
    print(f"  Entry: {t.get('entry_price')} ({entry_time}) | Exit: {t.get('exit_price')} ({exit_time})")
    print(f"  PnL: ₹{t.get('pnl')} | Status: {t.get('status')} | Reason: {t.get('exit_reason')}")
    print("------------------------------------------")
