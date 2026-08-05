import sys
import os
import pprint

sys.path.append(os.getcwd())

from bot.core.trade_repo import trade_repo

for t in list(trade_repo.collection.find({"symbol": "NIFTY26JUL23550PE"})):
    print(f"\n--- TRADE {t.get('id')} ---")
    pprint.pprint(t)
