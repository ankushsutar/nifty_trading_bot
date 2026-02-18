import requests
import os
import datetime
from bot.config.settings import Config
from bot.utils.logger import logger

class TelegramNotifier:
    def __init__(self):
        self.token = Config.TELEGRAM_BOT_TOKEN
        self.chat_id = Config.TELEGRAM_CHAT_ID
        self.enabled = bool(self.token and self.chat_id)
        
        if self.enabled:
            logger.info(">>> [System] Telegram Notifications ENABLED. 🔔")
        else:
            logger.warning(">>> [System] Telegram Notifications DISABLED (Token/ChatID missing).")

    def send_message(self, message):
        """Sends a text message to the configured Telegram chat."""
        if not self.enabled: return
        
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {
                "chat_id": self.chat_id, 
                "text": message, 
                "parse_mode": "Markdown"
            }
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code != 200:
                logger.error(f"Telegram API Error: {response.text}")
        except Exception as e:
            logger.error(f"Telegram Notification Crash: {e}")

    def notify_trade_entry(self, strategy, symbol, side, qty, price):
        icon = "🟢" if side == "BUY" else "🔴"
        msg = (
            f"{icon} *Trade Entry: {strategy}*\n"
            f"--------------------------\n"
            f"*Symbol:* `{symbol}`\n"
            f"*Side:* `{side}`\n"
            f"*Qty:* `{qty}`\n"
            f"*Price:* `₹{price:.2f}`\n"
            f"*Time:* `{datetime.datetime.now().strftime('%H:%M:%S')}`"
        )
        self.send_message(msg)

    def notify_trade_exit(self, strategy, symbol, pnl, reason):
        icon = "💰" if pnl > 0 else "❌"
        status = "PROFIT" if pnl > 0 else "LOSS"
        msg = (
            f"{icon} *Trade Exit: {strategy}*\n"
            f"--------------------------\n"
            f"*Symbol:* `{symbol}`\n"
            f"*Result:* `{status}`\n"
            f"*PnL:* `₹{pnl:.2f}`\n"
            f"*Reason:* `{reason}`"
        )
        self.send_message(msg)

notifier = TelegramNotifier()
