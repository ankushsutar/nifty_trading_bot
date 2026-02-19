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
            self.client = MongoClient(
                Config.MONGO_URI,
                serverSelectionTimeoutMS=10000,
                connectTimeoutMS=10000,
                socketTimeoutMS=30000,
                retryWrites=True
            )
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

    def reconcile_with_broker(self, api):
        """
        Cross-checks all OPEN DB trades against real broker positions.
        If the broker shows zero / no position for a symbol, it means
        the trade was exited manually — auto-close it in the DB.

        Called on strategy startup and periodically every 5 minutes.
        """
        if not self.client:
            return

        open_trades = self.get_open_trades()
        if not open_trades:
            return

        try:
            from bot.utils.rate_limiter import rate_limiter
            rate_limiter.wait()
            resp = api.position()
        except Exception as e:
            logger.error(f"[Reconcile] Could not fetch broker positions: {e}")
            return

        if not resp or not resp.get('status'):
            logger.warning("[Reconcile] Broker position() returned no data.")
            return

        broker_positions = resp.get('data', []) or []

        # Build a lookup: symbol -> netQty from broker
        broker_qty = {}
        for pos in broker_positions:
            sym = pos.get('tradingsymbol', '')
            try:
                net = int(pos.get('netqty', 0))
            except (TypeError, ValueError):
                net = 0
            broker_qty[sym] = net

        reconciled = 0
        for trade in open_trades:
            symbol = trade.get('symbol', '')
            trade_id = trade.get('id')
            entry_price = trade.get('entry_price', 0.0)

            net_qty = broker_qty.get(symbol, None)

            # If symbol is fully absent from broker OR net qty is 0 → position was closed externally
            if net_qty is None or net_qty == 0:
                # Try to get exit price from broker (best effort)
                exit_price = 0.0
                try:
                    from bot.core.data_fetcher import DataFetcher
                    df_inst = DataFetcher.__new__(DataFetcher)
                    ltp = df_inst.get_ltp(trade.get('token', ''), exchange='NFO') if hasattr(df_inst, 'api') else 0.0
                    exit_price = ltp if ltp else 0.0
                except Exception:
                    exit_price = 0.0

                pnl = round((exit_price - entry_price) * trade.get('qty', 0), 2) if exit_price > 0 else 0.0

                self.close_trade(
                    trade_id=trade_id,
                    exit_price=exit_price,
                    pnl=pnl,
                    exit_reason="MANUAL_EXIT"
                )
                logger.info(
                    f"[Reconcile] ✅ Trade #{trade_id} ({symbol}) closed in DB "
                    f"(broker shows flat). Exit: {exit_price} | PnL: {pnl}"
                )
                reconciled += 1

        if reconciled:
            logger.info(f"[Reconcile] {reconciled} trade(s) synced with broker.")
        else:
            logger.info("[Reconcile] All open DB trades match broker positions. ✅")

    def force_close_trade(self, trade_id, exit_price=0.0, reason="MANUAL_EXIT"):
        """
        Forcefully closes a specific trade by ID — used when user manually
        exits on broker and wants to sync the DB immediately via the API.
        """
        if not self.client:
            return False
        try:
            trade = self.collection.find_one({"id": trade_id})
            if not trade:
                logger.warning(f"[ForceClose] Trade #{trade_id} not found.")
                return False

            entry_price = trade.get('entry_price', 0.0)
            qty = trade.get('qty', 0)
            pnl = round((exit_price - entry_price) * qty, 2) if exit_price > 0 else 0.0

            self.close_trade(trade_id=trade_id, exit_price=exit_price, pnl=pnl, exit_reason=reason)
            logger.info(f"[ForceClose] Trade #{trade_id} ({trade.get('symbol')}) force-closed. Exit: {exit_price} | PnL: {pnl}")
            return True
        except Exception as e:
            logger.error(f"[ForceClose] Error: {e}")
            return False

trade_repo = TradeRepository()
