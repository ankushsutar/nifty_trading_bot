
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
                        exit_price REAL,
                        pnl REAL,
                        exit_reason TEXT,
                        mode TEXT DEFAULT 'PAPER',
                        status TEXT DEFAULT 'OPEN',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Schema Migration for existing tables
                try:
                    cursor.execute("ALTER TABLE trades ADD COLUMN exit_price REAL")
                except: pass
                try:
                    cursor.execute("ALTER TABLE trades ADD COLUMN pnl REAL")
                except: pass
                try:
                    cursor.execute("ALTER TABLE trades ADD COLUMN exit_reason TEXT")
                except: pass
                try:
                    cursor.execute("ALTER TABLE trades ADD COLUMN mode TEXT DEFAULT 'PAPER'")
                except: pass
                
                conn.commit()
                conn.close()
                logger.info("TradeRepository: Database initialized.")
            except Exception as e:
                logger.error(f"TradeRepository Init Error: {e}")

    def save_trade(self, symbol, token, leg, qty, entry_price, sl_price=0.0, side="BUY", mode="PAPER"):
        with self._lock:
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                
                cursor.execute("""
                    INSERT INTO trades (symbol, token, leg, qty, entry_price, sl_price, status, side, mode)
                    VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
                """, (symbol, token, leg, qty, entry_price, sl_price, side, mode))
                
                conn.commit()
                trade_id = cursor.lastrowid
                conn.close()
                logger.info(f"TradeRepository: Trade Saved (ID: {trade_id}, Mode: {mode})")
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

    def close_trade(self, trade_id=None, symbol=None, exit_price=0.0, pnl=0.0, exit_reason="UNKNOWN"):
        """Closes trade by ID or all open trades for a symbol."""
        with self._lock:
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                
                if trade_id:
                    cursor.execute("""
                        UPDATE trades 
                        SET status = 'CLOSED', exit_price = ?, pnl = ?, exit_reason = ? 
                        WHERE id = ?
                    """, (exit_price, pnl, exit_reason, trade_id))
                elif symbol:
                    cursor.execute("""
                        UPDATE trades 
                        SET status = 'CLOSED', exit_price = ?, pnl = ?, exit_reason = ? 
                        WHERE symbol = ? AND status = 'OPEN'
                    """, (exit_price, pnl, exit_reason, symbol))
                
                conn.commit()
                conn.close()
                logger.info(f"TradeRepository: Trade Closed (PnL: {pnl}).")
            except Exception as e:
                logger.error(f"TradeRepository Close Error: {e}")

    def get_active_trade(self, mode=None):
        """Returns the most recent OPEN trade. Optionally filter by mode."""
        try:
            conn = self._get_connection()
            conn.row_factory = sqlite3.Row # Access columns by name
            cursor = conn.cursor()
            
            if mode:
                cursor.execute("SELECT * FROM trades WHERE status = 'OPEN' AND mode = ? ORDER BY id DESC LIMIT 1", (mode,))
            else:
                cursor.execute("SELECT * FROM trades WHERE status = 'OPEN' ORDER BY id DESC LIMIT 1")
                
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return dict(row)
            return None
        except Exception as e:
            logger.error(f"TradeRepository Fetch Error: {e}")
            return None

    def get_open_trades(self, mode=None):
        """Returns detailed list of all OPEN trades."""
        try:
            conn = self._get_connection()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            if mode:
                cursor.execute("SELECT * FROM trades WHERE status = 'OPEN' AND mode = ?", (mode,))
            else:
                cursor.execute("SELECT * FROM trades WHERE status = 'OPEN'")
                
            rows = cursor.fetchall()
            conn.close()
            
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"TradeRepository Fetch All Error: {e}")
            return []

    def get_today_trades(self, mode=None):
        """Returns all trades (OPEN and CLOSED) created today."""
        try:
            conn = self._get_connection()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # SQLite 'date' function returns YYYY-MM-DD
            if mode:
                cursor.execute("SELECT * FROM trades WHERE date(created_at) = date('now', 'localtime') AND mode = ? ORDER BY id DESC", (mode,))
            else:
                cursor.execute("SELECT * FROM trades WHERE date(created_at) = date('now', 'localtime') ORDER BY id DESC")
                
            rows = cursor.fetchall()
            conn.close()
            
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"TradeRepository Fetch Today Error: {e}")
            return []

trade_repo = TradeRepository()
