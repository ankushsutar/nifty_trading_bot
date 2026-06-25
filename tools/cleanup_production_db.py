import datetime
from pymongo import MongoClient
from bot.config.settings import Config

def clean_database():
    print("Connecting to MongoDB...")
    client = MongoClient(Config.MONGO_URI)
    db = client[Config.MONGO_DB]
    collection = db[Config.MONGO_COLLECTION]
    
    # 1. Create a backup
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_col_name = f"{Config.MONGO_COLLECTION}_backup_{timestamp}"
    backup_collection = db[backup_col_name]
    
    all_docs = list(collection.find({}))
    if all_docs:
        backup_collection.insert_many(all_docs)
        print(f"Backup created successfully: {backup_col_name} ({len(all_docs)} documents)")
    else:
        print("No documents found to backup.")
        return

    # 2. Delete all PAPER trades
    paper_res = collection.delete_many({"mode": "PAPER"})
    print(f"Deleted {paper_res.deleted_count} PAPER trades.")

    # 3. Find and clean duplicates for LIVE trades
    live_trades = list(collection.find({"mode": "LIVE"}))
    print(f"Found {len(live_trades)} LIVE trades. Identifying duplicates...")

    # Group by key: (symbol, qty, entry_price, exit_price, date)
    groups = {}
    for trade in live_trades:
        created_at = trade.get('created_at')
        if not created_at:
            continue
        if isinstance(created_at, str):
            try:
                dt = datetime.datetime.fromisoformat(created_at)
            except:
                dt = datetime.date.min
        else:
            dt = created_at
        
        trade_date = dt.date() if hasattr(dt, 'date') else dt
        
        key = (
            trade.get('symbol'),
            trade.get('qty'),
            trade.get('entry_price'),
            trade.get('exit_price'),
            trade_date
        )
        groups.setdefault(key, []).append(trade)

    to_delete_ids = []
    for key, group in groups.items():
        if len(group) > 1:
            print(f"\nDuplicate group found for key {key}:")
            # Sort the group: keep closed trades with non-UNKNOWN exit reason if possible.
            # Best candidate has:
            # - status == "CLOSED"
            # - exit_reason not in [None, "UNKNOWN"]
            # - higher ID (so newer/corrected) or lower ID
            def sort_key(t):
                status = t.get('status', '')
                reason = t.get('exit_reason') or ''
                rank_closed = 1 if status == "CLOSED" else 0
                rank_reason = 1 if (reason and reason != "UNKNOWN") else 0
                return (-rank_closed, -rank_reason, t.get('id', 0))

            sorted_group = sorted(group, key=sort_key)
            keep = sorted_group[0]
            dups = sorted_group[1:]
            
            print(f"  KEEPING: ID {keep.get('id')} | Symbol: {keep.get('symbol')} | Status: {keep.get('status')} | Reason: {keep.get('exit_reason')} | PnL: {keep.get('pnl')}")
            for d in dups:
                print(f"  DELETING: ID {d.get('id')} | Symbol: {d.get('symbol')} | Status: {d.get('status')} | Reason: {d.get('exit_reason')} | PnL: {d.get('pnl')}")
                to_delete_ids.append(d['id'])

    if to_delete_ids:
        del_res = collection.delete_many({"id": {"$in": to_delete_ids}})
        print(f"\nDeleted {del_res.deleted_count} duplicate trade records.")
    else:
        print("\nNo duplicates found to delete.")

if __name__ == "__main__":
    clean_database()
