
import sys
import os
import json

# Add project root to sys.path
sys.path.append(os.getcwd())

from bot.core.trade_repo import TradeRepository
from bot.core.angel_connect import get_angel_session
from bot.utils.logger import logger

def debug_positions_sync():
    repo = TradeRepository()
    
    try:
        api = get_angel_session()
        api_ready = True if api else False
    except Exception as e:
        print(f"Error establishing session: {e}")
        api_ready = False
        api = None
    
    if not api_ready:
        print("❌ Error: Broker API not ready.")
        return

    print("--- Broker Positions ---")
    pos_res = api.position()
    broker_positions = []
    if pos_res and pos_res.get('status'):
        broker_positions = pos_res.get('data') or []
        for p in broker_positions:
            if int(p.get('netqty', 0)) != 0:
                print(f"Symbol: {p['tradingsymbol']} | Qty: {p['netqty']} | PnL: {p['pnl']}")
    else:
        print("No active positions found on broker.")

    print("\n--- Local DB Active Trades (Ready for Monitoring) ---")
    active_db = list(repo.collection.find({"status": {"$in": ["OPEN", "PLACED"]}}))
    if active_db:
        for t in active_db:
            print(f"ID: {t['id']} | Symbol: {t['symbol']} | Status: {t['status']} | Qty: {t['qty']}")
    else:
        print("No active trades found in local database.")

    print("\n--- Reconciliation Scan ---")
    # This will show what the bot *would* do if it ran reconciliation now
    trade_book_res = api.tradeBook()
    if trade_book_res and trade_book_res.get('status'):
        fills = trade_book_res.get('data') or []
        for f in fills:
            qty = f.get('fillsize') or f.get('quantity') or f.get('fillshares', 0)
            price = f.get('fillprice') or f.get('averageprice') or f.get('price', 0)
            print(f"Fill: {f.get('tradingsymbol')} | Side: {f.get('transactiontype')} | Qty: {qty} | Price: {price}")

if __name__ == "__main__":
    debug_positions_sync()
