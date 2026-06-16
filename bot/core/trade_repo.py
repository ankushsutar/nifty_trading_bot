import threading
import datetime
import time
# pyrefly: ignore [missing-import]
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

    def save_trade(self, symbol, token, leg, qty, entry_price, sl_price=0.0, side="BUY", mode="PAPER", strategy=None, status=None):
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
                
                entry_p = float(trade.get('entry_price') or 0.0)
                exit_p = float(exit_price or 0.0)
                qty_val = int(reduction_qty)
                pnl_val = float(pnl_segment)
                pnl_pct = round(((exit_p - entry_p) / entry_p * 100), 2) if entry_p > 0 else 0.0
                
                trade_record = {
                    "strategy": trade.get('strategy', 'MOMENTUM'),
                    "symbol": trade.get('symbol'),
                    "action": "SELL",
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
                cursor = self.collection.find({"symbol": symbol, "status": "OPEN"})
                trades_to_close = list(cursor)

            if not trades_to_close:
                # No open trade to close
                return

            for trade in trades_to_close:
                tid = trade['id']
                if pnl is None:
                    # Auto-calculate the final-close segment and accumulate
                    entry_price = float(trade.get('entry_price') or 0)
                    qty = int(trade.get('qty') or 0)
                    pnl_segment = round((exit_price - entry_price) * qty, 2) if entry_price > 0 and qty > 0 else 0.0
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
                        pnl_pct = round(((exit_p - entry_p) / entry_p * 100), 2) if entry_p > 0 else 0.0
                        
                        trade_record = {
                            "strategy": updated_trade.get('strategy', 'MOMENTUM'),
                            "symbol": updated_trade.get('symbol'),
                            "action": "SELL",
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
                query["symbol"] = symbol
                
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

    def reconcile_with_broker(self, api):
        """
        Fetches today's Angel One tradeBook and:
        1. Updates any PLACED trades to OPEN if a BUY fill is found.
        2. Closes any OPEN trades if a SELL fill is found.
        
        Called on strategy startup and via /api/reconcile-positions.
        """
        if not self.client:
            return

        # Fetch both OPEN and PLACED trades
        active_trades = list(self.collection.find({"status": {"$in": ["OPEN", "PLACED"]}}))
        if not active_trades:
            logger.info("[Reconcile] No open/placed DB trades. Nothing to sync.")
            return

        # --- Fetch tradeBook (real executed fills) ---
        broker_trades = []
        try:
            from bot.utils.rate_limiter import rate_limiter
            rate_limiter.wait()
            resp = api.tradeBook()
            if resp and resp.get('status'):
                broker_trades = resp.get('data') or []
        except Exception as e:
            logger.error(f"[Reconcile] tradeBook() failed: {e}")

        # --- Build fill maps ---
        # symbol -> {transaction_type -> {total_value: float, total_qty: int}}
        fill_map = {}
        for t in broker_trades:
            sym   = t.get('tradingsymbol', '')
            side  = t.get('transactiontype', '').upper()
            try:
                qty   = int(t.get('fillsize') or t.get('quantity') or 0)
                price = float(t.get('fillprice') or t.get('averageprice') or 0)
            except (TypeError, ValueError):
                continue

            if qty > 0 and price > 0:
                if sym not in fill_map:
                    fill_map[sym] = {}
                if side not in fill_map[sym]:
                    fill_map[sym][side] = {'total_value': 0.0, 'total_qty': 0}
                
                fill_map[sym][side]['total_value'] += price * qty
                fill_map[sym][side]['total_qty']   += qty

        # Calculate weighted averages
        avg_prices = {}
        for sym, sides in fill_map.items():
            avg_prices[sym] = {}
            for side, data in sides.items():
                if data['total_qty'] > 0:
                    avg_prices[sym][side] = round(data['total_value'] / data['total_qty'], 2)

        # --- Match DB trades against broker fills ---
        reconciled = 0
        for trade in active_trades:
            symbol      = trade.get('symbol', '')
            trade_id    = trade.get('id')
            status      = trade.get('status')
            entry_price = float(trade.get('entry_price', 0))
            
            # 1. Handle PLACED trades -> Match with BUY fill
            if status == "PLACED":
                buy_price = avg_prices.get(symbol, {}).get('BUY')
                if buy_price:
                    self.update_entry_price(trade_id, buy_price)
                    logger.info(f"[Reconcile] ♻️ Trade #{trade_id} ({symbol}): PLACED -> OPEN (Fill: ₹{buy_price})")
                    reconciled += 1
                    status = "OPEN"
                    entry_price = buy_price

            # 2. Handle OPEN trades -> Match with SELL fill
            if status == "OPEN":
                qty         = int(trade.get('qty', 0))
                sell_price  = avg_prices.get(symbol, {}).get('SELL')
                
                if sell_price:
                    pnl    = round((sell_price - entry_price) * qty, 2)
                    reason = "SYNC_FROM_BROKER"
                    self.close_trade(trade_id=trade_id, exit_price=sell_price, pnl=pnl, exit_reason=reason)
                    logger.info(f"[Reconcile] ✅ Trade #{trade_id} ({symbol}): OPEN -> CLOSED (Exit: ₹{sell_price} | PnL: {pnl:+.2f})")
                    reconciled += 1
                else:
                    # Note: We used to close it with entry as fallback if no SELL was found.
                    # This is dangerous if the bot just restarted and the position is still open.
                    # Only close if we are SURE it was manually exited (which we can't be sure of without more info)
                    # For now, it's safer to leave it OPEN if no SELL fill is found.
                    pass

        if reconciled:
            logger.info(f"[Reconcile] {reconciled} trade(s) reconciled from broker tradeBook.")
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
            pnl = round((exit_price - entry_price) * qty, 2) if exit_price > 0 else 0.0

            self.close_trade(trade_id=trade_id, exit_price=exit_price, pnl=pnl, exit_reason=reason)
            logger.info(f"[ForceClose] Trade #{trade_id} ({trade.get('symbol')}) force-closed. Exit: {exit_price} | PnL: {pnl}")
            return True
        except Exception as e:
            logger.error(f"[ForceClose] Error: {e}")
            return False

trade_repo = TradeRepository()
