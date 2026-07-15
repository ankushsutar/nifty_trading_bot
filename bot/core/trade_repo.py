import threading
import datetime
import time
import os
import sys
# pyrefly: ignore [missing-import]
from pymongo import MongoClient, ASCENDING, DESCENDING
from bot.config.settings import Config, is_test_env
from bot.utils.logger import logger

class InMemoryCollection:
    def __init__(self):
        self._docs = []

    def create_index(self, *args, **kwargs):
        pass

    def insert_one(self, doc):
        self._docs.append(doc)
        from unittest.mock import MagicMock
        res = MagicMock()
        res.inserted_id = doc.get("_id")
        return res

    def _match(self, doc, filter):
        for k, v in filter.items():
            if isinstance(v, dict):
                # operator like $in, $gte, etc.
                for op, op_val in v.items():
                    if op == "$in":
                        if doc.get(k) not in op_val:
                            return False
                    elif op == "$gte":
                        if doc.get(k) is None or doc.get(k) < op_val:
                            return False
                    elif op == "$lte":
                        if doc.get(k) is None or doc.get(k) > op_val:
                            return False
                    elif op == "$gt":
                        if doc.get(k) is None or doc.get(k) <= op_val:
                            return False
                    elif op == "$lt":
                        if doc.get(k) is None or doc.get(k) >= op_val:
                            return False
            else:
                if doc.get(k) != v:
                    return False
        return True

    def find_one(self, filter, sort=None, *args, **kwargs):
        matches = [d for d in self._docs if self._match(d, filter)]
        if not matches:
            return None
        if sort:
            key, order = sort[0]
            matches.sort(key=lambda x: x.get(key) if x.get(key) is not None else 0, reverse=(order == -1))
        return matches[0]

    def find(self, filter, *args, **kwargs):
        matches = [d for d in self._docs if self._match(d, filter)]
        class Cursor:
            def __init__(self, data):
                self.data = data
            def sort(self, key, order=-1):
                self.data.sort(key=lambda x: x.get(key) if x.get(key) is not None else 0, reverse=(order == -1))
                return self
            def __iter__(self):
                return iter(self.data)
            def __next__(self):
                return next(self.data)
        return Cursor(matches)

    def update_one(self, filter, update, *args, **kwargs):
        doc = self.find_one(filter)
        if doc and "$set" in update:
            for k, v in update["$set"].items():
                doc[k] = v
        from unittest.mock import MagicMock
        res = MagicMock()
        res.modified_count = 1 if doc else 0
        res.matched_count = 1 if doc else 0
        return res

    def update_many(self, filter, update, *args, **kwargs):
        matches = [d for d in self._docs if self._match(d, filter)]
        if "$set" in update:
            for doc in matches:
                for k, v in update["$set"].items():
                    doc[k] = v
        from unittest.mock import MagicMock
        res = MagicMock()
        res.modified_count = len(matches)
        res.matched_count = len(matches)
        return res

    def find_one_and_update(self, filter, update, return_document=True, *args, **kwargs):
        doc = self.find_one(filter)
        if not doc:
            doc = {"_id": filter.get("_id"), "seq": 0}
            self._docs.append(doc)
        if "$inc" in update:
            for k, v in update["$inc"].items():
                doc[k] = doc.get(k, 0) + v
        return doc


class TradeRepository:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TradeRepository, cls).__new__(cls)
            cls._instance._init_db()
        return cls._instance

    def _init_db(self):
        if is_test_env():
            logger.info("TradeRepository: Test environment detected. Using InMemoryCollection.")
            from unittest.mock import MagicMock
            self.client = MagicMock()
            self.db = MagicMock()
            self.collection = InMemoryCollection()
            self.counters = InMemoryCollection()
            self.counters.insert_one({"_id": "trade_id", "seq": 0})
            return

        try:
            self.client = MongoClient(
                Config.MONGO_URI,
                serverSelectionTimeoutMS=10000,
                connectTimeoutMS=10000,
                socketTimeoutMS=30000,
                retryWrites=True
            )
            if Config.MONGO_DB == "nifty_bot_test":
                logger.info("Test environment detected. Dropping test database to start fresh.")
                self.client.drop_database("nifty_bot_test")
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

    def save_trade(self, symbol, token, leg, qty, entry_price, sl_price=0.0, side="BUY", mode="PAPER", strategy=None, status=None):
        if not self.client:
            logger.error("TradeRepository: MongoDB not connected.")
            return None

        with self._lock:
            try:
                # Uniqueness Check: Look for an existing open or placed trade for the same symbol, strategy, and mode
                # to prevent duplicate entries (e.g. from early record in order placement and subsequent strategy save)
                existing = None
                if strategy:
                    query = {
                        "symbol": symbol,
                        "strategy": strategy,
                        "status": {"$in": ["OPEN", "PLACED"]},
                        "mode": mode
                    }
                    existing = self.collection.find_one(query, sort=[("id", -1)])

                if existing:
                    trade_id = existing["id"]
                    update_fields = {
                        "updated_at": datetime.datetime.now()
                    }
                    if entry_price > 0.0:
                        update_fields["entry_price"] = entry_price
                    if leg:
                        update_fields["leg"] = leg
                    if side:
                        update_fields["side"] = side
                    if qty:
                        update_fields["qty"] = qty
                        update_fields["remaining_qty"] = qty
                    if sl_price > 0.0:
                        update_fields["sl_price"] = sl_price
                    if status:
                        update_fields["status"] = status
                    elif entry_price > 0.0:
                        update_fields["status"] = "OPEN"

                    self.collection.update_one({"id": trade_id}, {"$set": update_fields})
                    logger.info(f"TradeRepository: Updated existing active trade (ID: {trade_id}, Symbol: {symbol}, Strategy: {strategy})")
                    return trade_id

                # If no existing active trade found, proceed with new insertion
                trade_id = self._get_next_sequence("trade_id")
                
                trade_doc = {
                    "id": trade_id,
                    "symbol": symbol,
                    "token": token,
                    "leg": leg,
                    "side": side,
                    "qty": qty,
                    "remaining_qty": qty,
                    "entry_price": entry_price,
                    "sl_price": sl_price,
                    "sl_order_id": None,
                    "monitoring_stage": 0,
                    "exit_price": None,
                    "pnl": 0.0,
                    "exit_reason": None,
                    "mode": mode,
                    "strategy": strategy,
                    "status": status if status else ("OPEN" if entry_price > 0 else "PLACED"),
                    "partially_booked": False,
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
            logger.info(f"TradeRepository: Updated Trade #{trade_id} SL to ₹{new_sl}")
        except Exception as e:
            logger.error(f"TradeRepository Update SL Error: {e}")

    def update_sl_order_id(self, trade_id, sl_oid):
        """Persists the Broker-Side SL Order ID for recovery after restarts."""
        if not self.client or not sl_oid: return
        try:
            self.collection.update_one(
                {"id": trade_id},
                {"$set": {"sl_order_id": str(sl_oid), "updated_at": datetime.datetime.now()}}
            )
            logger.info(f"TradeRepository: Updated Trade #{trade_id} SL Order ID to {sl_oid}")
        except Exception as e:
            logger.error(f"TradeRepository Update SL OID Error: {e}")

    def update_trade_context(self, trade_id, context):
        """Persists the strategy indicator context for the trade."""
        if not self.client or not context: return
        try:
            self.collection.update_one(
                {"id": trade_id},
                {"$set": context}
            )
            logger.info(f"TradeRepository: Updated Trade #{trade_id} context indicator values.")
        except Exception as e:
            logger.error(f"TradeRepository Update Context Error: {e}")

    def update_monitoring_state(self, trade_id, stage, remaining_qty=None):
        """Persists the current strategy stage and remaining quantity."""
        if not self.client: return
        try:
            update_data = {
                "monitoring_stage": stage,
                "updated_at": datetime.datetime.now()
            }
            if remaining_qty is not None:
                update_data["remaining_qty"] = remaining_qty

            self.collection.update_one(
                {"id": trade_id},
                {"$set": update_data}
            )
            # Throttled log or debug log for state updates to avoid log bloat
            logger.debug(f"TradeRepository: Updated Trade #{trade_id} Stage: {stage}, Qty: {remaining_qty}")
        except Exception as e:
            logger.error(f"TradeRepository Update Monitoring State Error: {e}")

    def update_entry_price(self, trade_id, actual_price, expected_price=None):
        """Updates a trade with the actual fill details and calculates slippage."""
        if not self.client: return
        
        update_data = {
            "entry_price": actual_price,
            "status": "OPEN",
            "updated_at": datetime.datetime.now()
        }
        
        if expected_price and expected_price > 0:
            slippage = actual_price - expected_price
            slippage_pct = (slippage / expected_price) * 100
            update_data["slippage_points"] = round(slippage, 2)
            update_data["slippage_percent"] = round(slippage_pct, 2)
            logger.info(f"📊 [Slippage] Trade #{trade_id}: Estimate={expected_price} | Fill={actual_price} | Slippage={slippage:.2f} ({slippage_pct:.2f}%)")
        
        try:
            self.collection.update_one({"id": trade_id}, {"$set": update_data})
            logger.info(f"TradeRepository: Updated Trade #{trade_id} with fill price ₹{actual_price}")
        except Exception as e:
            logger.error(f"TradeRepository Update Entry Price Error: {e}")

    def scale_in_position(self, trade_id, added_qty, added_price):
        """Adds to an existing position and recalculates weighted average entry price."""
        if not self.client: return
        try:
            trade = self.collection.find_one({"id": trade_id})
            if not trade: return
            
            old_qty = trade.get('qty', 0)
            old_price = trade.get('entry_price', 0.0)
            
            new_qty = old_qty + added_qty
            # Weighted Average Price Calculation
            new_avg_price = ((old_price * old_qty) + (added_price * added_qty)) / new_qty
            new_avg_price = round(new_avg_price, 2)
            
            self.collection.update_one(
                {"id": trade_id},
                {
                    "$set": {
                        "qty": new_qty,
                        "remaining_qty": new_qty,
                        "entry_price": new_avg_price,
                        "updated_at": datetime.datetime.now(),
                        "scaled_in": True
                    },
                    "$push": {
                        "scale_ins": {
                            "qty": added_qty,
                            "price": added_price,
                            "time": datetime.datetime.now()
                        }
                    }
                }
            )
            logger.info(f"TradeRepository: Scaled-In Trade #{trade_id}. New Qty: {new_qty}, New Avg Price: {new_avg_price}")
        except Exception as e:
            logger.error(f"TradeRepository Scale-In Error: {e}")

    def reduce_position(self, trade_id, reduction_qty, exit_price, pnl_segment, reason):
        """Reduces the quantity of an open trade (Partial Booking). Status remains OPEN."""
        if not self.client: return
        try:
            trade = self.collection.find_one({"id": trade_id})
            if not trade:
                return
            
            self.collection.update_one(
                {"id": trade_id},
                {
                    "$inc": {"qty": -reduction_qty, "pnl": pnl_segment},
                    "$set": {
                        "partially_booked": True,
                        "last_partial_exit_price": exit_price,
                        "updated_at": datetime.datetime.now()
                    },
                    "$push": {
                        "partial_exits": {
                            "qty": reduction_qty,
                            "price": exit_price,
                            "pnl": pnl_segment,
                            "reason": reason,
                            "time": datetime.datetime.now()
                        }
                    }
                }
            )
            logger.info(f"TradeRepository: Trade #{trade_id} Position Reduced by {reduction_qty}.")

            # Log partial exit to journal!
            try:
                from bot.utils.trade_journal import TradeJournal
                
                trade_side = trade.get('side', 'BUY').upper()
                entry_p = float(trade.get('entry_price') or 0.0)
                exit_p = float(exit_price or 0.0)
                qty_val = int(reduction_qty)
                pnl_val = float(pnl_segment)
                
                if trade_side == "BUY":
                    pnl_pct = round(((exit_p - entry_p) / entry_p * 100), 2) if entry_p > 0 else 0.0
                else:
                    pnl_pct = round(((entry_p - exit_p) / entry_p * 100), 2) if entry_p > 0 else 0.0
                
                trade_record = {
                    "strategy": trade.get('strategy', 'MOMENTUM'),
                    "symbol": trade.get('symbol'),
                    "action": "SELL" if trade_side == "BUY" else "BUY",
                    "qty": qty_val,
                    "entry_price": entry_p,
                    "exit_price": exit_p,
                    "pnl": round(pnl_val, 2),
                    "pnl_percent": pnl_pct,
                    "result": "WIN" if pnl_val > 0 else "LOSS",
                    "exit_reason": f"PARTIAL_{reason}"
                }
                
                # Copy context indicator fields
                for field in ["entry_ema9", "entry_ema21", "entry_rsi", "entry_adx", "htf_trend",
                              "entry_atr", "entry_bbw", "oi_pcr", "oi_sentiment"]:
                    if field in trade:
                        trade_record[field] = trade[field]
                        
                TradeJournal.log_trade(trade_record)
            except Exception as j_err:
                logger.error(f"TradeRepository central journaling error for Trade reduction #{trade_id}: {j_err}")
                
        except Exception as e:
            logger.error(f"TradeRepository Reduce Position Error: {e}")

    def close_trade(self, trade_id=None, symbol=None, exit_price=0.0, pnl=None, exit_reason="UNKNOWN"):
        """Closes trade by ID or all open trades for a symbol.

        If pnl is not supplied (None), it is auto-calculated from the stored
        entry_price and current qty, then added to any partial-booking pnl
        already accumulated via reduce_position (uses $inc so partial profits
        are preserved).  When pnl is passed explicitly it is written directly
        with $set — used by reconcile and force-close paths.
        """
        if not self.client: return

        try:
            # We want to keep track of which trades are being closed, so we can journal them
            trades_to_close = []
            if trade_id:
                trade = self.collection.find_one({"id": trade_id})
                if trade and trade.get('status') != 'CLOSED':
                    trades_to_close.append(trade)
            elif symbol:
                cursor = self.collection.find({"symbol": symbol, "status": {"$in": ["OPEN", "PLACED"]}})
                trades_to_close = list(cursor)

            if not trades_to_close:
                # No open trade to close
                return

            for trade in trades_to_close:
                tid = trade['id']
                side = trade.get('side', 'BUY').upper()
                if pnl is None:
                    # Auto-calculate the final-close segment and accumulate
                    entry_price = float(trade.get('entry_price') or 0)
                    qty = int(trade.get('qty') or 0)
                    if side == "BUY":
                        pnl_segment = round((exit_price - entry_price) * qty, 2) if entry_price > 0 and qty > 0 else 0.0
                    else:
                        pnl_segment = round((entry_price - exit_price) * qty, 2) if entry_price > 0 and qty > 0 else 0.0
                    self.collection.update_one(
                        {"id": tid},
                        {
                            "$set": {
                                "status": "CLOSED",
                                "exit_price": exit_price,
                                "exit_reason": exit_reason,
                                "closed_at": datetime.datetime.now(),
                                "updated_at": datetime.datetime.now()
                            },
                            "$inc": {"pnl": pnl_segment}
                        }
                    )
                    final_pnl = round((trade.get('pnl') or 0) + pnl_segment, 2)
                else:
                    # Explicit PnL path
                    self.collection.update_one(
                        {"id": tid},
                        {
                            "$set": {
                                "status": "CLOSED",
                                "exit_price": exit_price,
                                "pnl": pnl,
                                "exit_reason": exit_reason,
                                "closed_at": datetime.datetime.now(),
                                "updated_at": datetime.datetime.now()
                            }
                        }
                    )
                    final_pnl = pnl

                # Retrieve the updated document to log to the CSV trade journal
                updated_trade = self.collection.find_one({"id": tid})
                if updated_trade:
                    logger.info(f"TradeRepository: Trade #{tid} Closed (PnL: {final_pnl:+.2f}).")
                    
                    try:
                        from bot.utils.trade_journal import TradeJournal
                        
                        entry_p = float(updated_trade.get('entry_price') or 0.0)
                        exit_p = float(updated_trade.get('exit_price') or exit_price or 0.0)
                        qty_val = int(updated_trade.get('qty') or 0)
                        pnl_val = float(updated_trade.get('pnl') or final_pnl or 0.0)
                        
                        if side == "BUY":
                            pnl_pct = round(((exit_p - entry_p) / entry_p * 100), 2) if entry_p > 0 else 0.0
                        else:
                            pnl_pct = round(((entry_p - exit_p) / entry_p * 100), 2) if entry_p > 0 else 0.0
                            
                        trade_record = {
                            "strategy": updated_trade.get('strategy', 'MOMENTUM'),
                            "symbol": updated_trade.get('symbol'),
                            "action": "SELL" if side == "BUY" else "BUY",
                            "qty": qty_val,
                            "entry_price": entry_p,
                            "exit_price": exit_p,
                            "pnl": round(pnl_val, 2),
                            "pnl_percent": pnl_pct,
                            "result": "WIN" if pnl_val > 0 else "LOSS",
                            "exit_reason": updated_trade.get('exit_reason', exit_reason)
                        }
                        
                        # Copy context indicator fields if they exist in updated_trade
                        for field in ["entry_ema9", "entry_ema21", "entry_rsi", "entry_adx", "htf_trend",
                                      "entry_atr", "entry_bbw", "oi_pcr", "oi_sentiment"]:
                            if field in updated_trade:
                                trade_record[field] = updated_trade[field]
                                
                        TradeJournal.log_trade(trade_record)
                    except Exception as j_err:
                        logger.error(f"TradeRepository central journaling error for Trade #{tid}: {j_err}")

        except Exception as e:
            logger.error(f"TradeRepository Close Error: {e}")

    def get_active_trade(self, mode=None, strategy=None, symbol=None):
        """Returns the most recent OPEN/PLACED trade. Optionally filter by mode/strategy/symbol."""
        if not self.client: return None
        try:
            query = {"status": {"$in": ["OPEN", "PLACED"]}}
            if mode:
                query["mode"] = mode
            if strategy:
                query["strategy"] = strategy
            if symbol:
                query["symbol"] = {"$regex": f"^{symbol}"}
                
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
            # Timezone-robust calculation of Indian Standard Time (IST) start of day
            ist_tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
            now_ist = datetime.datetime.now(ist_tz)
            today_start_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
            today_start = today_start_ist.astimezone().replace(tzinfo=None)
            
            query = {"created_at": {"$gte": today_start}}
            if mode:
                query["mode"] = mode
            else:
                # IMPORTANT: Default to LIVE trades if mode is not specified to prevent PAPER trades from affecting LIVE!
                query["mode"] = "LIVE"
                
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
            # Timezone-robust calculation of Indian Standard Time (IST) start of day
            ist_tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
            now_ist = datetime.datetime.now(ist_tz)
            today_start_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
            today_start = today_start_ist.astimezone().replace(tzinfo=None)
            
            result = self.collection.update_many(
                {
                    "status": {"$in": ["OPEN", "PLACED"]},
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

    @staticmethod
    def _is_symbol_expired(symbol: str) -> bool:
        import re
        import datetime
        try:
            today = datetime.date.today()
            
            # Layer 1: Scrip Master lookup (primary source of truth)
            try:
                from bot.utils.token_lookup import TokenLookup
                tl = TokenLookup()
                if tl.df is None:
                    tl.load_scrip_master()
                if tl.df is not None and len(tl.df) > 1000:
                    matching_rows = tl.df[tl.df['symbol'] == symbol]
                    if not matching_rows.empty:
                        expiry_val = matching_rows.iloc[0].get('expiry')
                        if expiry_val:
                            if isinstance(expiry_val, str):
                                if '-' in expiry_val:
                                    expiry_date = datetime.datetime.strptime(expiry_val[:10], '%Y-%m-%d').date()
                                else:
                                    _MONTHS_MAP = {
                                        'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4,
                                        'MAY': 5, 'JUN': 6, 'JUL': 7, 'AUG': 8,
                                        'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12
                                    }
                                    match_exp = re.match(r'^(\d{1,2})([A-Z]{3})(\d{4})', expiry_val.upper())
                                    if match_exp:
                                        d_val = int(match_exp.group(1))
                                        m_val = _MONTHS_MAP.get(match_exp.group(2))
                                        y_val = int(match_exp.group(3))
                                        if m_val:
                                            expiry_date = datetime.date(y_val, m_val, d_val)
                                        else:
                                            expiry_date = None
                                    else:
                                        expiry_date = None
                            elif isinstance(expiry_val, (datetime.date, datetime.datetime)):
                                expiry_date = expiry_val if isinstance(expiry_val, datetime.date) else expiry_val.date()
                            else:
                                expiry_date = None
                            
                            if expiry_date:
                                return expiry_date < today
                    else:
                        is_opt_or_fut = re.match(r'^[A-Z]+(\d{2})([A-Z]{3}|\d+)\d*(CE|PE|FUT)?$', symbol.upper())
                        if is_opt_or_fut:
                            logger.info(f"Symbol {symbol} not found in active scrip master. Assuming expired.")
                            return True
            except Exception as e:
                logger.error(f"Scrip Master check failed for symbol {symbol}: {e}")

            # Check Weekly Format: INDEX + YY + M/O/N/D + DD + STRIKE + CE/PE
            w_match = re.match(r'^[A-Z]+(\d{2})([1-9ONDond])(\d{2})\d+(CE|PE)$', symbol.upper())
            if w_match:
                yy = int(w_match.group(1)) + 2000
                m_str = w_match.group(2).upper()
                month_map = {'O': 10, 'N': 11, 'D': 12}
                mon = month_map.get(m_str) or int(m_str)
                day = int(w_match.group(3))
                try:
                    expiry = datetime.date(yy, mon, day)
                    return expiry < today
                except ValueError:
                    return False
            
            # Check Monthly Format: INDEX + YY + MMM + STRIKE + CE/PE
            m_match = re.match(r'^[A-Z]+(\d{2})([A-Z]{3})\d+(CE|PE)$', symbol.upper())
            if m_match:
                _MONTHS = {
                    'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4,
                    'MAY': 5, 'JUN': 6, 'JUL': 7, 'AUG': 8,
                    'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12
                }
                yy = int(m_match.group(1)) + 2000
                mon = _MONTHS.get(m_match.group(2).upper())
                if not mon:
                    return False
                
                import calendar
                try:
                    last_day = calendar.monthrange(yy, mon)[1]
                    expiry_date = datetime.date(yy, mon, last_day)
                    
                    if yy > 2025 or (yy == 2025 and mon >= 9):
                        target_weekday = 1  # Tuesday
                    else:
                        target_weekday = 3  # Thursday
                        
                    while expiry_date.weekday() != target_weekday:
                        expiry_date -= datetime.timedelta(days=1)
                    
                    try:
                        from bot.utils.expiry_calculator import is_trading_day
                        while not is_trading_day(expiry_date):
                            expiry_date -= datetime.timedelta(days=1)
                    except Exception:
                        while expiry_date.weekday() >= 5:
                            expiry_date -= datetime.timedelta(days=1)
                            
                    return expiry_date < today
                except Exception:
                    return False

            return False
        except Exception:
            return False

    def reconcile_with_broker(self, api):
        """
        Fetches today's Angel One tradeBook, active positions, and orderBook:
        1. Updates any PLACED trades to OPEN if a BUY fill is found.
        2. Closes any OPEN trades if a SELL fill is found.
        3. Closes any OPEN trades that are no longer active at the broker (no position or net quantity is 0).
        4. Closes any PLACED trades that have no corresponding open order at the broker (cancelled/rejected/filled).
        5. Synchronizes orphaned active positions from broker to MongoDB.
        
        Called on strategy startup and via /api/reconcile-positions.
        """
        if not self.client:
            return

        # Fetch both OPEN and PLACED trades
        active_trades = list(self.collection.find({"status": {"$in": ["OPEN", "PLACED"]}}))

        # --- Fetch tradeBook (real executed fills) ---
        broker_trades = []
        try:
            from bot.utils.rate_limiter import rate_limiter
            rate_limiter.wait()
            resp = api.tradeBook()
            if isinstance(resp, dict) and resp.get('status'):
                broker_trades = resp.get('data') or []
        except Exception as e:
            logger.error(f"[Reconcile] tradeBook() failed: {e}")

        # --- Fetch active positions to audit open trades ---
        broker_positions = {}
        has_positions_info = False
        pos_data = []
        try:
            from bot.utils.rate_limiter import rate_limiter
            rate_limiter.wait()
            pos_resp = api.position()
            if isinstance(pos_resp, dict) and pos_resp.get('status'):
                pos_data = pos_resp.get('data')
                if isinstance(pos_data, list):
                    has_positions_info = True
                    for pos in pos_data:
                        sym = pos.get('tradingsymbol')
                        if sym:
                            try:
                                net_qty = int(pos.get('netqty', 0) or 0)
                                broker_positions[sym] = net_qty
                            except (TypeError, ValueError):
                                pass
        except Exception as e:
            logger.error(f"[Reconcile] position() failed: {e}")

        # --- Fetch order book to audit placed trades ---
        broker_orders = {}
        has_orders_info = False
        raw_orders_list = []
        try:
            from bot.utils.rate_limiter import rate_limiter
            rate_limiter.wait()
            ord_resp = api.orderBook()
            if isinstance(ord_resp, dict) and ord_resp.get('status'):
                ord_data = ord_resp.get('data')
                if isinstance(ord_data, list):
                    has_orders_info = True
                    raw_orders_list = ord_data
                    for ord_item in ord_data:
                        sym = ord_item.get('tradingsymbol')
                        status = ord_item.get('status', '').upper()
                        if sym:
                            if sym not in broker_orders:
                                broker_orders[sym] = []
                            broker_orders[sym].append(status)
        except Exception as e:
            logger.error(f"[Reconcile] orderBook() failed: {e}")

        # --- Build fill maps ---
        fill_map = {}
        for t in broker_trades:
            sym   = t.get('tradingsymbol', '')
            side  = t.get('transactiontype', '').upper()
            try:
                qty   = int(t.get('quantity', 0) or 0)
                price = float(t.get('averageprice', 0) or 0)
            except (TypeError, ValueError):
                continue

            if qty > 0 and price > 0:
                if sym not in fill_map:
                    fill_map[sym] = {}
                if side not in fill_map[sym]:
                    fill_map[sym][side] = {'total_value': 0.0, 'total_qty': 0}
                
                fill_map[sym][side]['total_value'] += price * qty
                fill_map[sym][side]['total_qty']   += qty

        avg_prices = {}
        for sym, sides in fill_map.items():
            avg_prices[sym] = {}
            for side, data in sides.items():
                if data['total_qty'] > 0:
                    avg_prices[sym][side] = round(data['total_value'] / data['total_qty'], 2)

        # --- Match DB trades against broker state ---
        reconciled = 0
        for trade in active_trades:
            symbol      = trade.get('symbol', '')
            trade_id    = trade.get('id')
            status      = trade.get('status')
            side        = trade.get('side', 'BUY').upper()
            
            # 1. Handle PLACED trades
            if status == "PLACED":
                entry_fill_side = side
                entry_price = avg_prices.get(symbol, {}).get(entry_fill_side)
                if entry_price:
                    self.update_entry_price(trade_id, entry_price)
                    logger.info(f"[Reconcile] ♻️ Trade #{trade_id} ({symbol}): PLACED -> OPEN (Fill: ₹{entry_price})")
                    reconciled += 1
                elif has_orders_info:
                    orders = broker_orders.get(symbol, [])
                    has_open_order = any(o in ["SUBMITTED", "PENDING", "MODIFY PENDING", "OPEN"] for o in orders)
                    if not has_open_order:
                        self.close_trade(trade_id=trade_id, exit_price=0.0, pnl=0.0, exit_reason="SYNC_CANCELLED_OR_REJECTED")
                        logger.info(f"[Reconcile] ♻️ Trade #{trade_id} ({symbol}): PLACED -> CLOSED (No active broker order found)")
                        reconciled += 1
                continue

            # 2. Handle OPEN trades
            if status == "OPEN":
                entry_price = float(trade.get('entry_price', 0))
                qty         = int(trade.get('qty', 0))
                
                exit_fill_side = "SELL" if side == "BUY" else "BUY"
                exit_price = avg_prices.get(symbol, {}).get(exit_fill_side)
                
                if exit_price:
                    if side == "BUY":
                        pnl = round((exit_price - entry_price) * qty, 2)
                    else:
                        pnl = round((entry_price - exit_price) * qty, 2)
                    reason = "SYNC_FROM_BROKER"
                    self.close_trade(trade_id=trade_id, exit_price=exit_price, pnl=pnl, exit_reason=reason)
                    logger.info(f"[Reconcile] ✅ Trade #{trade_id} ({symbol}): OPEN -> CLOSED (Exit: ₹{exit_price} | PnL: {pnl:+.2f})")
                    reconciled += 1
                elif has_positions_info:
                    net_qty = broker_positions.get(symbol, 0)
                    if net_qty == 0:
                        is_expired = self._is_symbol_expired(symbol)
                        if is_expired:
                            self.close_trade(trade_id=trade_id, exit_price=0.0, pnl=-(entry_price * qty), exit_reason="CONTRACT_EXPIRED_WORTHLESS")
                            logger.warning(f"[Reconcile] 📅 Trade #{trade_id} ({symbol}): OPEN -> CLOSED (Contract past expiry date — expired worthless)")
                        else:
                            self.close_trade(trade_id=trade_id, exit_price=entry_price, pnl=0.0, exit_reason="SYNC_CLOSED_FROM_BROKER_POSITIONS")
                            logger.info(f"[Reconcile] ♻️ Trade #{trade_id} ({symbol}): OPEN -> CLOSED (No active broker position found)")
                        reconciled += 1

        # 3. Synchronize orphaned active positions from broker to MongoDB
        if has_positions_info:
            unrepresented_positions = []
            for symbol, net_qty in broker_positions.items():
                if net_qty != 0 and (symbol.upper().startswith("NIFTY") or "NIFTY" in symbol.upper()):
                    existing_active = self.collection.find_one({
                        "symbol": symbol,
                        "status": {"$in": ["OPEN", "PLACED"]}
                    })
                    if not existing_active:
                        unrepresented_positions.append((symbol, net_qty))

            if unrepresented_positions:
                logger.info(f"[Reconcile] Found {len(unrepresented_positions)} active broker positions not present in DB. Syncing...")
                calls = []
                puts = []
                import re
                for symbol, net_qty in unrepresented_positions:
                    opt_type = 'CE' if symbol.upper().endswith('CE') else 'PE'
                    match_w = re.match(r'^[A-Z]+(?:\d{2})(?:[1-9ONDond])(?:\d{2})(\d+)(CE|PE)$', symbol.upper())
                    match_m = re.match(r'^[A-Z]+(?:\d{2})(?:[A-Z]{3})(\d+)(CE|PE)$', symbol.upper())
                    strike = None
                    if match_w:
                        strike = int(match_w.group(1))
                    elif match_m:
                        strike = int(match_m.group(1))
                    
                    if strike:
                        item = {"symbol": symbol, "net_qty": net_qty, "strike": strike}
                        if opt_type == 'CE':
                            calls.append(item)
                        else:
                            puts.append(item)

                calls.sort(key=lambda x: x["strike"])
                for idx, item in enumerate(calls):
                    leg = "SC" if idx == 0 else "LC"
                    item["leg"] = leg

                puts.sort(key=lambda x: x["strike"], reverse=True)
                for idx, item in enumerate(puts):
                    leg = "SP" if idx == 0 else "LP"
                    item["leg"] = leg

                for item in (calls + puts):
                    try:
                        symbol = item["symbol"]
                        qty = abs(item["net_qty"])
                        side = "SELL" if item["net_qty"] < 0 else "BUY"
                        
                        avg_price = 0.0
                        if isinstance(pos_data, list):
                            for pos in pos_data:
                                if pos.get('tradingsymbol') == symbol:
                                    try:
                                        avg_price = float(pos.get('avgnetprice', 0) or 0)
                                    except ValueError:
                                        pass
                                    break
                        
                        if avg_price <= 0:
                            from bot.core.data_fetcher import DataFetcher
                            try:
                                fetcher = DataFetcher(api)
                                from bot.utils.token_lookup import TokenLookup
                                tl = TokenLookup()
                                token = tl.get_token_by_symbol(symbol)
                                if token:
                                    avg_price = fetcher.get_ltp(token, exchange="NFO")
                            except Exception:
                                avg_price = 10.0

                        from bot.utils.token_lookup import TokenLookup
                        tl = TokenLookup()
                        token = tl.get_token_by_symbol(symbol) or "UNKNOWN"

                        # Try to inherit actual strategy and leg from the most recent DB record of this symbol
                        reconstructed_strategy = "MOMENTUM"
                        reconstructed_leg = item["leg"]
                        
                        try:
                            recent_db_trade = self.collection.find_one(
                                {"symbol": symbol, "mode": "PAPER" if getattr(api, "dry_run", False) else "LIVE"},
                                sort=[("id", -1)]
                            )
                            if recent_db_trade:
                                reconstructed_strategy = recent_db_trade.get("strategy", "MOMENTUM")
                                if recent_db_trade.get("leg"):
                                    reconstructed_leg = recent_db_trade["leg"]
                        except Exception as inherit_err:
                            logger.warning(f"[Reconcile] Could not inherit strategy for {symbol}: {inherit_err}")

                        trade_id = self.save_trade(
                            symbol=symbol,
                            token=token,
                            qty=qty,
                            side=side,
                            entry_price=avg_price,
                            sl_price=avg_price * 1.5 if side == "SELL" else avg_price * 0.5,
                            strategy=reconstructed_strategy,
                            leg=reconstructed_leg,
                            mode="PAPER" if getattr(api, "dry_run", False) else "LIVE",
                            status="OPEN"
                        )
                        
                        # Find and link stop-loss order ID if active on the broker
                        sl_oid = None
                        if isinstance(raw_orders_list, list):
                            for o in raw_orders_list:
                                o_sym = o.get('tradingsymbol')
                                o_status = str(o.get('status', '')).upper()
                                o_type = str(o.get('ordertype', o.get('order_type', ''))).upper()
                                o_variety = str(o.get('variety', '')).upper()
                                
                                if (o_sym == symbol and 
                                        o_status in ["TRIGGER PENDING", "PENDING", "OPEN", "ACTIVE"] and 
                                        ("STOPLOSS" in o_type or o_type in ["SL", "SL-M"] or o_variety == "STOPLOSS")):
                                    sl_oid = o.get('orderid', o.get('order_id'))
                                    break
                                    
                        if sl_oid:
                            self.update_sl_order_id(trade_id, sl_oid)
                            logger.info(f"[Reconcile] Linked active broker SL order {sl_oid} to reconstructed trade #{trade_id}")
                            
                        logger.info(f"[Reconcile] ♻️ Reconstructed and synced {item['leg']} Leg: {symbol} (Qty: {qty}, Entry: ₹{avg_price})")
                        reconciled += 1
                    except Exception as e:
                        logger.error(f"[Reconcile] Failed to sync orphaned position {item.get('symbol')}: {e}")

        if reconciled:
            logger.info(f"[Reconcile] {reconciled} trade(s) reconciled from broker state.")
        else:
            logger.info("[Reconcile] All DB trades match broker state.")


    def get_recent_closed_trades(self, mode=None, strategy=None, since=None):
        """
        Returns CLOSED trades optionally filtered by mode, strategy, and start date.
        Used by DecisionEngine confidence scoring to evaluate recent performance.
        """
        if not self.client:
            return []
        try:
            query = {"status": "CLOSED"}
            if mode:
                query["mode"] = mode
            if strategy:
                query["strategy"] = strategy
            if since:
                query["closed_at"] = {"$gte": since}
            cursor = self.collection.find(query).sort("closed_at", -1)
            return list(cursor)
        except Exception as e:
            logger.error(f"TradeRepository get_recent_closed_trades Error: {e}")
            return []

    def get_slippage_stats(self, mode=None, strategy=None, days=5):
        """
        Aggregates slippage data across recent trades for auto-adjusting
        Smart-Limit walk speed in OrderManager.

        Returns:
            dict with avg_slippage_pct, avg_slippage_points, sample_size
        """
        if not self.client:
            return {"avg_slippage_pct": 0.0, "avg_slippage_points": 0.0, "sample_size": 0}
        try:
            since = datetime.datetime.now() - datetime.timedelta(days=days)
            query = {
                "created_at": {"$gte": since},
                "slippage_points": {"$exists": True},
            }
            if mode:
                query["mode"] = mode
            if strategy:
                query["strategy"] = strategy

            trades = list(self.collection.find(query, {"slippage_points": 1, "slippage_percent": 1}))
            if not trades:
                return {"avg_slippage_pct": 0.0, "avg_slippage_points": 0.0, "sample_size": 0}

            points_list = [abs(float(t.get("slippage_points", 0))) for t in trades]
            pct_list    = [abs(float(t.get("slippage_percent", 0))) for t in trades]

            return {
                "avg_slippage_pct":    round(sum(pct_list)    / len(pct_list), 3),
                "avg_slippage_points": round(sum(points_list) / len(points_list), 3),
                "sample_size":         len(trades),
            }
        except Exception as e:
            logger.error(f"TradeRepository get_slippage_stats Error: {e}")
            return {"avg_slippage_pct": 0.0, "avg_slippage_points": 0.0, "sample_size": 0}

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
            side = trade.get('side', 'BUY').upper()
            
            if exit_price > 0:
                if side == "BUY":
                    pnl = round((exit_price - entry_price) * qty, 2)
                else:
                    pnl = round((entry_price - exit_price) * qty, 2)
            else:
                pnl = 0.0

            self.close_trade(trade_id=trade_id, exit_price=exit_price, pnl=pnl, exit_reason=reason)
            logger.info(f"[ForceClose] Trade #{trade_id} ({trade.get('symbol')}) force-closed. Exit: {exit_price} | PnL: {pnl}")
            return True
        except Exception as e:
            logger.error(f"[ForceClose] Error: {e}")
            return False

trade_repo = TradeRepository()
