from bot.core.trade_repo import trade_repo
from pprint import pprint

print("\n--- TODAY'S LIVE TRADES ---")
live_trades = trade_repo.get_today_trades(mode="LIVE")
if not live_trades:
    print("No LIVE trades found today.")
for t in live_trades:
    print(f"ID: {t.get('id')} | Symbol: {t.get('symbol')} | Status: {t.get('status')} | Entry: {t.get('entry_price')} | Exit: {t.get('exit_price')} | PnL: {t.get('pnl')}")

print("\n--- TODAY'S PAPER TRADES ---")
paper_trades = trade_repo.get_today_trades(mode="PAPER")
if not paper_trades:
    print("No PAPER trades found today.")
for t in paper_trades:
    print(f"ID: {t.get('id')} | Symbol: {t.get('symbol')} | Status: {t.get('status')} | Entry: {t.get('entry_price')} | Exit: {t.get('exit_price')} | PnL: {t.get('pnl')}")

