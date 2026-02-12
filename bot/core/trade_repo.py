import threading
import datetime
from pymongo import MongoClient, ASCENDING, DESCENDING
from bot.config.settings import Config
from bot.utils.logger import logger

class TradeRepository:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TradeRepository, cls).__new__(cls)
            cls._instance._init_db()
        return cls._instance

    def _init_db(self):
        try:
            self.client = MongoClient(Config.MONGO_URI, serverSelectionTimeoutMS=5000)
            self.db = self.client[Config.MONGO_DB]
            self.collection = self.db[Config.MONGO_COLLECTION]
            self.counters = self.db["counters"]
            
            # Create Indexes
            self.collection.create_index([("id", ASCENDING)], unique=True)
            self.collection.create_index([("symbol", ASCENDING)])
            self.collection.create_index([("status", ASCENDING)])
            self.collection.create_index([("created_at", DESCENDING)])
            
            # Initialize Counter if not exists
            if not self.counters.find_one({"_id": "trade_id"}):
                self.counters.insert_one({"_id": "trade_id", "seq": 0})
                
            logger.info("TradeRepository: Connected to MongoDB (Direct Mode).")
        except Exception as e:
            logger.error(f"TradeRepository Init Error: {e}")
            self.client = None

    def _get_next_sequence(self, name):
        """Get next integer ID for backward compatibility"""
        ret = self.counters.find_one_and_update(
            {"_id": name},
            {"$inc": {"seq": 1}},
            return_document=True
        )
        return ret['seq']

    def save_trade(self, symbol, token, leg, qty, entry_price, sl_price=0.0, side="BUY", mode="PAPER", strategy=None):
        if not self.client:
            logger.error("TradeRepository: MongoDB not connected.")
            return None

        with self._lock:
            try:
                trade_id = self._get_next_sequence("trade_id")
                
                trade_doc = {
                    "id": trade_id,
                    "symbol": symbol,
                    "token": token,
                    "leg": leg,
                    "side": side,
                    "qty": qty,
                    "entry_price": entry_price,
                    "sl_price": sl_price,
                    "exit_price": None,
                    "pnl": 0.0,
                    "exit_reason": None,
                    "mode": mode,
                    "strategy": strategy,
                    "status": "OPEN",
                    "created_at": datetime.datetime.now(),
                    "updated_at": datetime.datetime.now()
                }
                
                # Retry Loop for MongoDB Insertion
                for attempt in range(3):
                    try:
                        self.collection.insert_one(trade_doc)
                        logger.info(f"TradeRepository: Trade Saved (ID: {trade_id}, Mode: {mode}, Strategy: {strategy})")
                        return trade_id
                    except Exception as e:
                        if attempt == 2: raise e
                        logger.warning(f"TradeRepository Save Attempt {attempt+1} failed: {e}. Retrying...")
                        time.sleep(1)
            except Exception as e:
                logger.error(f"TradeRepository Save Error after retries: {e}")
                return None

    def update_sl(self, trade_id, new_sl):
        if not self.client: return
        try:
            self.collection.update_one(
                {"id": trade_id},
                {"$set": {"sl_price": new_sl, "updated_at": datetime.datetime.now()}}
            )
        except Exception as e:
            logger.error(f"TradeRepository Update SL Error: {e}")

    def close_trade(self, trade_id=None, symbol=None, exit_price=0.0, pnl=0.0, exit_reason="UNKNOWN"):
        """Closes trade by ID or all open trades for a symbol."""
        if not self.client: return

        update_fields = {
            "status": "CLOSED",
            "exit_price": exit_price,
            "pnl": pnl,
            "exit_reason": exit_reason,
            "closed_at": datetime.datetime.now(),
            "updated_at": datetime.datetime.now()
        }

        try:
            if trade_id:
                self.collection.update_one(
                    {"id": trade_id},
                    {"$set": update_fields}
                )
            elif symbol:
                self.collection.update_many(
                    {"symbol": symbol, "status": "OPEN"},
                    {"$set": update_fields}
                )
            
            logger.info(f"TradeRepository: Trade Closed (PnL: {pnl}).")
        except Exception as e:
            logger.error(f"TradeRepository Close Error: {e}")

    def get_active_trade(self, mode=None, strategy=None):
        """Returns the most recent OPEN trade. Optionally filter by mode/strategy."""
        if not self.client: return None
        try:
            query = {"status": "OPEN"}
            if mode:
                query["mode"] = mode
            if strategy:
                query["strategy"] = strategy
                
            return self.collection.find_one(query, sort=[("id", DESCENDING)])
        except Exception as e:
            logger.error(f"TradeRepository Fetch Error: {e}")
            return None

    def get_open_trades(self, mode=None, strategy=None):
        """Returns detailed list of all OPEN trades."""
        if not self.client: return []
        try:
            query = {"status": "OPEN"}
            if mode:
                query["mode"] = mode
            if strategy:
                query["strategy"] = strategy
                
            cursor = self.collection.find(query).sort("id", DESCENDING)
            return list(cursor)
        except Exception as e:
            logger.error(f"TradeRepository Fetch All Error: {e}")
            return []

    def get_today_trades(self, mode=None):
        """Returns all trades (OPEN and CLOSED) created today."""
        if not self.client: return []
        try:
            today_start = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            
            query = {"created_at": {"$gte": today_start}}
            if mode:
                query["mode"] = mode
                
            cursor = self.collection.find(query).sort("id", DESCENDING)
            return list(cursor)
        except Exception as e:
            logger.error(f"TradeRepository Fetch Today Error: {e}")
            return []

    def cleanup_stale_trades(self):
        """
        Closes any trades currently marked as 'OPEN' that were not created today.
        """
        if not self.client: return 0
        try:
            today_start = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            
            result = self.collection.update_many(
                {
                    "status": "OPEN",
                    "created_at": {"$lt": today_start}
                },
                {
                    "$set": {
                        "status": "CLOSED",
                        "exit_reason": "STALE_OVERNIGHT",
                        "pnl": 0.0,
                        "closed_at": datetime.datetime.now(),
                        "updated_at": datetime.datetime.now()
                    }
                }
            )
            
            count = result.modified_count
            if count > 0:
                logger.info(f"TradeRepository: Cleaned up {count} stale trades from previous sessions.")
            return count
        except Exception as e:
            logger.error(f"TradeRepository Cleanup Error: {e}")
            return 0

trade_repo = TradeRepository()
