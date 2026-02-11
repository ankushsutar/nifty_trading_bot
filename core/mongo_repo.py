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
            # Test connection
            self.client.admin.command('ping')
            logger.info("MongoTradeRepository: Connected to MongoDB.")
        except Exception as e:
            logger.error(f"MongoTradeRepository: Failed to connect to MongoDB: {e}")
            self.client = None

    def save_historical_trade(self, trade_data):
        """
        Saves a completed trade record to MongoDB.
        """
        if self.client is None:
            logger.warning("MongoTradeRepository: MongoDB is not connected. Skipping historical save.")
            return False

        try:
            # Add metadata if not present
            if 'synced_at' not in trade_data:
                trade_data['synced_at'] = datetime.datetime.now()
            
            # Remove sqlite-specific ID to let MongoDB generate its own _id
            if 'id' in trade_data:
                trade_data['sqlite_id'] = trade_data.pop('id')

            result = self.collection.insert_one(trade_data)
            logger.info(f"MongoTradeRepository: Historical trade saved (MongoID: {result.inserted_id})")
            return True
        except Exception as e:
            logger.error(f"MongoTradeRepository: Failed to save historical trade: {e}")
            return False

mongo_trade_repo = MongoTradeRepository()
