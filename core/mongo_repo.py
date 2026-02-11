import datetime
from pymongo import MongoClient
from config.settings import Config
from utils.logger import logger

class MongoTradeRepository:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MongoTradeRepository, cls).__new__(cls)
            cls._instance._init_client()
        return cls._instance

    def _init_client(self):
        try:
            self.client = MongoClient(Config.MONGO_URI, serverSelectionTimeoutMS=5000)
            self.db = self.client[Config.MONGO_DB]
            self.collection = self.db[Config.MONGO_COLLECTION]
            # Ensure unique index on sqlite_id to prevent duplication
            self.collection.create_index("sqlite_id", unique=True)
            # Test connection
            self.client.admin.command('ping')
            logger.info("MongoTradeRepository: Connected to MongoDB with Duplicate Prevention.")
        except Exception as e:
            logger.error(f"MongoTradeRepository: Failed to connect to MongoDB: {e}")
            self.client = None

    def save_historical_trade(self, trade_data):
        """
        Saves or updates a completed trade record in MongoDB to prevent duplication.
        Uses sqlite_id as the unique key.
        """
        if self.client is None:
            logger.warning("MongoTradeRepository: MongoDB is not connected. Skipping historical save.")
            return False

        try:
            # 1. Prepare Data
            sqlite_id = trade_data.get('id')
            if not sqlite_id:
                logger.error("MongoTradeRepository: Cannot save trade without sqlite_id.")
                return False

            # Transfer ID to avoid confusion with Mongo's _id
            data_to_save = trade_data.copy()
            data_to_save['sqlite_id'] = data_to_save.pop('id')
            
            if 'synced_at' not in data_to_save:
                data_to_save['synced_at'] = datetime.datetime.now()

            # 2. Upsert using update_one (Prevents duplication)
            result = self.collection.update_one(
                {"sqlite_id": sqlite_id},
                {"$set": data_to_save},
                upsert=True
            )

            if result.upserted_id:
                logger.info(f"MongoTradeRepository: Historical trade CREATED (MongoID: {result.upserted_id})")
            elif result.modified_count > 0:
                logger.info(f"MongoTradeRepository: Historical trade UPDATED (SQLiteID: {sqlite_id})")
            else:
                logger.debug(f"MongoTradeRepository: Historical trade already exists and is identical (SQLiteID: {sqlite_id})")
                
            return True
        except Exception as e:
            logger.error(f"MongoTradeRepository: Failed to save historical trade: {e}")
            return False


mongo_trade_repo = MongoTradeRepository()
