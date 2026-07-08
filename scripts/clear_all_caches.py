import os
import sys
import datetime
import shutil

# Add project root to sys.path
sys.path.append(os.getcwd())

from bot.core.trade_repo import TradeRepository
from bot.utils.logger import logger

def clear_all():
    print("🧹 Starting cache and database cleanup...")
    
    # 1. MongoDB Clean Today's Trades
    repo = TradeRepository()
    if repo.client:
        today_start = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        count_before = repo.collection.count_documents({"created_at": {"$gte": today_start}})
        if count_before > 0:
            print(f"⚠️ Found {count_before} trades from today in MongoDB. Deleting...")
            res = repo.collection.delete_many({"created_at": {"$gte": today_start}})
            print(f"✅ Deleted {res.deleted_count} trades from MongoDB.")
        else:
            print("ℹ️ No today's trades found in MongoDB.")
    else:
        print("❌ Could not connect to MongoDB.")

    # 2. Local Files Cleanup
    files_to_delete = [
        "data/metrics.json",
        "data/cache_candles.json",
        "data/cache_candles.lock",
        "data/market_analysis.json",
        "data/strategy_state.json",
        "data/vix_history.json",
        "data/oi_history_nifty.json",
        "data/oi_snapshot_nifty.json",
        "data/alpha_history.json",
        ".stop_signal",
    ]

    for f in files_to_delete:
        path = os.path.join(os.getcwd(), f)
        if os.path.exists(path):
            try:
                os.remove(path)
                print(f"✅ Removed local file: {f}")
            except Exception as e:
                print(f"❌ Error removing {f}: {e}")
        else:
            print(f"ℹ️ File not found: {f}")

    print("🎉 Cleanup completed successfully. Ready to start fresh!")

if __name__ == "__main__":
    clear_all()
