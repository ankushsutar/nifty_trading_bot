
import sqlite3
import threading
import datetime
import os
from utils.logger import logger

DB_PATH = "trades.db"

class TradeRepository:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TradeRepository, cls).__new__(cls)
            cls._instance._init_db()
        return cls._instance

    def _get_connection(self):
        # sqlite3 connections are generally not thread-safe if shared across threads without care.
        # Creating a new connection per request is safer for low-throughput apps like this.
        return sqlite3.connect(DB_PATH, check_same_thread=False)

    def _init_db(self):
        with self._lock:
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                
                # Check if trades table exists
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS trades (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        token TEXT NOT NULL,
                        leg TEXT NOT NULL,
                        side TEXT DEFAULT 'BUY',
                        qty INTEGER NOT NULL,
                        entry_price REAL NOT NULL,
                        sl_price REAL,
                        status TEXT DEFAULT 'OPEN',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.commit()
                conn.close()
                logger.info("TradeRepository: Database initialized.")
            except Exception as e:
                logger.error(f"TradeRepository Init Error: {e}")

    def save_trade(self, symbol, token, leg, qty, entry_price, sl_price=0.0, side="BUY"):
        with self._lock:
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                
                cursor.execute("""
                    INSERT INTO trades (symbol, token, leg, qty, entry_price, sl_price, status, side)
                    VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?)
                """, (symbol, token, leg, qty, entry_price, sl_price, side))
                
                conn.commit()
                trade_id = cursor.lastrowid
                conn.close()
                logger.info(f"TradeRepository: Trade Saved (ID: {trade_id})")
                return trade_id
            except Exception as e:
                logger.error(f"TradeRepository Save Error: {e}")
                return None

    def update_sl(self, trade_id, new_sl):
        with self._lock:
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                cursor.execute("UPDATE trades SET sl_price = ? WHERE id = ?", (new_sl, trade_id))
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error(f"TradeRepository Update SL Error: {e}")

    def close_trade(self, trade_id=None, symbol=None):
        """Closes trade by ID or all open trades for a symbol."""
        with self._lock:
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                
                if trade_id:
                    cursor.execute("UPDATE trades SET status = 'CLOSED' WHERE id = ?", (trade_id,))
                elif symbol:
                    cursor.execute("UPDATE trades SET status = 'CLOSED' WHERE symbol = ? AND status = 'OPEN'", (symbol,))
                
                conn.commit()
                conn.close()
                logger.info("TradeRepository: Trade Closed.")
            except Exception as e:
                logger.error(f"TradeRepository Close Error: {e}")

    def get_active_trade(self):
        """Returns the most recent OPEN trade."""
        try:
            conn = self._get_connection()
            conn.row_factory = sqlite3.Row # Access columns by name
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM trades WHERE status = 'OPEN' ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return dict(row)
            return None
        except Exception as e:
            logger.error(f"TradeRepository Fetch Error: {e}")
            return None

    def get_open_trades(self):
        """Returns detailed list of all OPEN trades."""
        try:
            conn = self._get_connection()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM trades WHERE status = 'OPEN'")
            rows = cursor.fetchall()
            conn.close()
            
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"TradeRepository Fetch All Error: {e}")
            return []

trade_repo = TradeRepository()
