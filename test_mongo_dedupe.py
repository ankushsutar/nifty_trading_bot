import sys
import os
import time

# Add root dir to path
sys.path.append(os.getcwd())

from core.trade_repo import trade_repo
from core.mongo_repo import mongo_trade_repo
from utils.logger import logger

def verify_mongo_deduplication():
    logger.info("Starting MongoDB De-duplication Verification...")

    # 1. Simulate a Trade
    symbol = "DEDUPE_TEST"
    token = "888888"
    leg = "PE"
    qty = 50
    entry_price = 150.0
    
    trade_id = trade_repo.save_trade(symbol, token, leg, qty, entry_price, mode="PAPER")
    logger.info(f"✅ Trade saved to SQLite (ID: {trade_id}).")
    
    # 2. First Close (Should Create in Mongo)
    exit_price = 160.0
    pnl = 500.0
    reason = "DEDUPE_TEST_PASS_1"
    
    logger.info("Triggering first close (Expected: CREATE in Mongo)...")
    trade_repo.close_trade(trade_id=trade_id, exit_price=exit_price, pnl=pnl, exit_reason=reason)
    
    # 3. Second Close (Should UPDATE/SKIP in Mongo, not create new)
    logger.info("Triggering second close with same ID (Expected: UPDATE in Mongo)...")
    reason_v2 = "DEDUPE_TEST_PASS_2"
    trade_repo.close_trade(trade_id=trade_id, exit_price=exit_price, pnl=pnl, exit_reason=reason_v2)
    
    # 4. Check Count in Mongo
    count = mongo_trade_repo.collection.count_documents({"sqlite_id": trade_id})
    if count == 1:
        logger.info(f"✅ Success! Only 1 record found in MongoDB for SQLite ID: {trade_id}")
        
        # Verify it has the LATEST data (upsert works)
        record = mongo_trade_repo.collection.find_one({"sqlite_id": trade_id})
        if record.get('exit_reason') == reason_v2:
            logger.info("✅ Verified: Record was UPDATED with the new data.")
        else:
            logger.error(f"❌ Verification failed: Expected reason '{reason_v2}', got '{record.get('exit_reason')}'")
    else:
        logger.error(f"❌ Failure! Found {count} records in MongoDB. Duplication occurred!")

if __name__ == "__main__":
    verify_mongo_deduplication()
