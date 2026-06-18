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
from bot.core.trade_repo import trade_repo

def sync_todays_trades_to_mongo():
    logger.info("Starting Sync: Fetching today's trades from Angel One and pushing to MongoDB 'trades' collection...")
    
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
            logger.info(f"Successfully fetched {len(trades)} raw fills from broker.")
        else:
            logger.error(f"Broker returned failure or empty response: {resp}")
            return
            
    except Exception as e:
        logger.error(f"Error occurred during tradeBook fetch: {e}")
        return

    if not trades:
        logger.info("No trades executed today. Exiting.")
        return

    # Sort trades chronologically explicitly by filltime (assuming HH:MM:SS format)
    trades = sorted(trades, key=lambda x: x.get('filltime', '00:00:00'))

    # Reconstruct Trades
    open_positions = {} # keyed by symbol
    inserted = 0

    # Ensure DB is connected
    if not trade_repo.client:
        logger.error("TradeRepository MongoDB not connected!")
        return

    for t in trades:
        sym = t.get('tradingsymbol', '')
        token = t.get('symboltoken', '')
        side = t.get('transactiontype', '').upper()
        try:
            qty_str = t.get('fillsize') or t.get('quantity') or 0
            qty = int(qty_str)
            price_str = t.get('fillprice') or t.get('averageprice') or 0
            price = float(price_str)
        except (TypeError, ValueError):
            continue
            
        if qty == 0 or price == 0:
            continue

        if sym not in open_positions:
            open_positions[sym] = {"qty": 0, "cost": 0.0, "leg": "CE" if "CE" in sym else "PE", "token": token, "side": side}

        pos = open_positions[sym]
        
        # Determine if this is an opening or closing trade
        if pos["qty"] == 0:
            # Open new position
            pos["qty"] = qty
            pos["cost"] = price * qty
            pos["side"] = side
        else:
            # Position already open.
            if side == pos["side"]:
                # Averaging up/down (add to position)
                pos["qty"] += qty
                pos["cost"] += price * qty
            else:
                # Closing position (or partial close)
                close_qty = min(qty, pos["qty"])
                avg_entry = pos["cost"] / pos["qty"]
                exit_price = price
                
                # Calculate P&L
                if pos["side"] == "BUY":
                    pnl = (exit_price - avg_entry) * close_qty
                else:
                    pnl = (avg_entry - exit_price) * close_qty
                
                # Insert closed trade record into MongoDB
                trade_id = trade_repo._get_next_sequence("trade_id")
                trade_doc = {
                    "id": trade_id,
                    "symbol": sym,
                    "token": pos["token"],
                    "leg": pos["leg"],
                    "side": pos["side"],
                    "qty": close_qty,
                    "entry_price": round(avg_entry, 2),
                    "sl_price": 0.0,
                    "exit_price": round(exit_price, 2),
                    "pnl": round(pnl, 2),
                    "exit_reason": "BROKER_SYNC",
                    "mode": "LIVE",
                    "strategy": "MANUAL",
                    "status": "CLOSED",
                    "partially_booked": False,
                    "created_at": datetime.datetime.now(),
                    "updated_at": datetime.datetime.now(),
                    "closed_at": datetime.datetime.now(),
                    "synced_at": datetime.datetime.now()
                }
                
                # Upsert based on combination of symbol, created_at range, and P&L to avoid exact duplicates 
                # (Simple strategy: just insert, but let's check if exact match exists)
                exists = trade_repo.collection.find_one({
                    "symbol": sym, 
                    "qty": close_qty, 
                    "status": "CLOSED",
                    "mode": "LIVE",
                    "exit_reason": "BROKER_SYNC",
                    "pnl": round(pnl, 2)
                })
                
                if not exists:
                    trade_repo.collection.insert_one(trade_doc)
                    inserted += 1
                
                # Adjust remaining open position
                pos["qty"] -= close_qty
                if pos["qty"] > 0:
                    pos["cost"] -= avg_entry * close_qty
                else:
                    pos["cost"] = 0.0

    logger.info(f"Sync Complete! Reconstructed and inserted {inserted} completely closed trades into 'trades' collection.")

if __name__ == "__main__":
    sync_todays_trades_to_mongo()
