import os
import sys
import datetime

# Add the project root to the python path so imports work
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

from pymongo import MongoClient
from bot.core.angel_connect import get_angel_session
from bot.config.settings import Config
from bot.utils.logger import logger
from bot.utils.rate_limiter import rate_limiter

def sync_todays_trades_to_mongo():
    logger.info("Starting Sync: Fetching today's trades from Angel One and pushing to MongoDB...")
    
    # 1. Connect to MongoDB
    try:
        client = MongoClient(
            Config.MONGO_URI,
            serverSelectionTimeoutMS=5000
        )
        db = client[Config.MONGO_DB]
        # We store these in a separate collection from internal bot logs 'trades'
        # to preserve raw broker truth without conflicting internal IDs
        collection = db["broker_tradebook"]
        
        # Create an index on the broker's unique order id to prevent duplicates
        collection.create_index("uniqueorderid", unique=True)
        logger.info(f"Connected to MongoDB DB: {Config.MONGO_DB}, Collection: broker_tradebook")
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        return

    # 2. Authenticate with Angel One
    api = get_angel_session()
    if not api:
        logger.error("Failed to establish session with Angel One API. Cannot sync trades.")
        return

    # 3. Fetch TradeBook from Broker
    trades = []
    try:
        logger.info("Fetching TradeBook from broker...")
        rate_limiter.wait()
        resp = api.tradeBook()
        
        if getattr(resp, 'get', None) and resp.get('status'):
            trades = resp.get('data') or []
            logger.info(f"Successfully fetched {len(trades)} trades from broker.")
        else:
            logger.error(f"Broker returned failure or empty response: {resp}")
            return
            
    except Exception as e:
        logger.error(f"Error occurred during tradeBook fetch: {e}")
        return

    if not trades:
        logger.info("No trades executed today. Exiting.")
        return

    # 4. Push to MongoDB (Upsert to handle duplicate runs on the same day safely)
    inserted = 0
    updated = 0
    
    for trade_data in trades:
        # TradeBook entries typically have 'uniqueorderid' from Angel One
        order_id = trade_data.get('uniqueorderid')
        
        # Fallback if the field is missing somehow, we hash the dict to avoid dupes
        if not order_id:
            order_id = str(hash(frozenset(trade_data.items())))
            
        # Add a sync timestamp
        trade_data["synced_at"] = datetime.datetime.now()

        try:
            result = collection.update_one(
                {"uniqueorderid": order_id},
                {"$set": trade_data},
                upsert=True
            )
            
            if result.upserted_id:
                inserted += 1
            else:
                updated += 1
        except Exception as e:
            logger.error(f"Failed to sync trade {order_id}: {e}")

    logger.info(f"Sync Complete! New Trades Inserted: {inserted} | Existing Trades Updated: {updated}")

if __name__ == "__main__":
    sync_todays_trades_to_mongo()
