import csv
import os
import datetime
from utils.logger import logger

class TradeJournal:
    FILE_PATH = "logs/trade_journal.csv"
    HEADERS = [
        "timestamp", "strategy", "symbol", "action", "qty", 
        "entry_price", "exit_price", "pnl", "pnl_percent", "result", "exit_reason",
        "entry_ema9", "entry_ema21", "entry_rsi", "entry_adx", "htf_trend"
    ]

    @staticmethod
    def log_trade(trade_data):
        """
        Appends a completed trade to the CSV journal.
        trade_data: dict containing keys matching HEADERS.
        """
        file_exists = os.path.isfile(TradeJournal.FILE_PATH)
        
        try:
            with open(TradeJournal.FILE_PATH, mode='a', newline='') as file:
                writer = csv.DictWriter(file, fieldnames=TradeJournal.HEADERS)
                
                if not file_exists:
                    writer.writeheader()
                    
                # Ensure timestamp is string
                if 'timestamp' not in trade_data:
                    trade_data['timestamp'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                # Fill missing keys with None
                row = {key: trade_data.get(key, "") for key in TradeJournal.HEADERS}
                
                writer.writerow(row)
                logger.info(f"📓 Journal Updated: Trade Logged ({trade_data.get('result')})")
                
        except Exception as e:
            logger.error(f"Failed to log trade to journal: {e}")
