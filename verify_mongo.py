import sys
import os
import datetime
import time

# Add root dir to path
sys.path.append(os.getcwd())

from core.trade_repo import trade_repo
from core.mongo_repo import mongo_trade_repo
from utils.logger import logger

def verify_mongodb_integration():
    logger.info("Starting MongoDB Integration Verification...")

    # 1. Test MongoTradeRepository Connection
    if mongo_trade_repo.client:
        logger.info("✅ MongoDB Client initialized.")
    else:
        logger.error("❌ MongoDB Client NOT initialized. Is MongoDB running?")
        return

    # 2. Simulate a Trade Lifecycle
    symbol = "NIFTY_VERIFY_TEST"
    token = "999999"
    leg = "CE"
    qty = 25
    entry_price = 100.0
    
    logger.info(f"Simulating trade save for {symbol}...")
    trade_id = trade_repo.save_trade(symbol, token, leg, qty, entry_price, mode="PAPER")
    
    if not trade_id:
        logger.error("❌ Failed to save trade to SQLite.")
        return
    
    logger.info(f"✅ Trade saved to SQLite (ID: {trade_id}). Now closing trade to trigger MongoDB sync...")
    
    # 3. Close Trade (should trigger mongo sync)
    exit_price = 110.0
    pnl = 250.0
    reason = "VERIFICATION_TEST"
    
    trade_repo.close_trade(trade_id=trade_id, exit_price=exit_price, pnl=pnl, exit_reason=reason)
    
    # 4. Verification Check in MongoDB
    logger.info("Checking MongoDB for the record...")
    time.sleep(1) # Small delay for sync
    
    try:
        record = mongo_trade_repo.collection.find_one({"sqlite_id": trade_id})
        if record:
            logger.info(f"✅ FOUND record in MongoDB: {record}")
            logger.info("Verification SUCCESSFUL! 🚀")
        else:
            logger.error("❌ Record NOT found in MongoDB.")
    except Exception as e:
        logger.error(f"❌ Error querying MongoDB: {e}")

if __name__ == "__main__":
    verify_mongodb_integration()
