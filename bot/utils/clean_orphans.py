import argparse
from pymongo import MongoClient
from bot.config.settings import Config

def clean_orphans(force=False):
    client = MongoClient(Config.MONGO_URI)
    db = client[Config.MONGO_DB]
    col = db[Config.MONGO_COLLECTION]
    
    query = {"pnl": {"$in": [0.0, None]}}
    count = col.count_documents(query)
    print(f"🔍 Found {count} documents matching zero/None PnL query.")
    
    matching = list(col.find(query, {"id": 1, "symbol": 1, "pnl": 1, "status": 1}))
    print("📋 Trades identified for removal:")
    for doc in matching:
        print(f"  • ID: {doc.get('id')} | Symbol: {doc.get('symbol')} | PnL: {doc.get('pnl')} | Status: {doc.get('status')}")
        
    if not force:
        confirm = input("⚠️ Are you sure you want to delete these records? (yes/no): ")
        if confirm.lower() != 'yes':
            print("❌ Cleanup aborted.")
            return
            
    result = col.delete_many(query)
    print(f"✅ Successfully deleted {result.deleted_count} orphaned trade records.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Purge orphaned trade records from MongoDB")
    parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()
    clean_orphans(force=args.force)
