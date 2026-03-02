
import datetime
import sys
import os

# Add project root to sys.path so we can import bot modules
sys.path.append(os.getcwd())

from bot.core.trade_repo import TradeRepository
from bot.utils.logger import logger

def clear_today():
    repo = TradeRepository()
    if not repo.client:
        print("❌ Error: Could not connect to MongoDB.")
        return

    today_start = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Count before deletion
    count_before = repo.collection.count_documents({"created_at": {"$gte": today_start}})
    
    if count_before == 0:
        print("ℹ️ No trades found for today. Nothing to clear.")
        return

    print(f"⚠️ Found {count_before} trades from today. Clearing...")
    
    # Delete trades
    result = repo.collection.delete_many({"created_at": {"$gte": today_start}})
    
    print(f"✅ Successfully deleted {result.deleted_count} trades from today.")
    logger.info(f"TradeRepository: Manually cleared {result.deleted_count} trades for a clean start.")

if __name__ == "__main__":
    clear_today()
