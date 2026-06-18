import sys
import os
import datetime
from bot.core.trade_repo import trade_repo

def cleanup():
    print("--- 🧹 MongoDB Cleanup: Removing Dummy Trades (Qty 75) ---")
    
    if not trade_repo.client:
        print("❌ Error: Could not connect to MongoDB.")
        sys.exit(1)
        
    collection = trade_repo.collection
    
    # 1. Identify Trades to Remove
    query = {"qty": 75}
    count = collection.count_documents(query)
    
    if count == 0:
        print("✅ No dummy trades (Qty 75) found. Database is clean.")
        return

    print(f"⚠️ Found {count} dummy trades with Qty 75.")
    
    # 2. Delete Trades
    result = collection.delete_many(query)
    print(f"🗑️ Deleted {result.deleted_count} trades.")
    
    # 3. Verify
    remaining = collection.count_documents(query)
    if remaining == 0:
        print("✅ Verification Passed: All dummy trades removed.")
    else:
        print(f"❌ Verification Failed: {remaining} trades still exist.")

if __name__ == "__main__":
    try:
        cleanup()
    except Exception as e:
        print(f"❌ Cleanup Script Error: {e}")
        sys.exit(1)
