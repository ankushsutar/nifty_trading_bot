import requests
import os
import datetime
import threading
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

    def send_message(self, message, parse_mode="HTML"):
        """Asynchronously sends a text message to the configured Telegram chat."""
        if not self.enabled: return
        
        # Dispatch the HTTP POST request to a background thread to keep trading execution non-blocking
        threading.Thread(target=self._send_message_sync, args=(message, parse_mode), daemon=True).start()

    def _send_message_sync(self, message, parse_mode="HTML"):
        """Synchronously sends the HTTP POST request to Telegram API."""
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {
                "chat_id": self.chat_id, 
                "text": message, 
                "parse_mode": parse_mode
            }
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code != 200:
                logger.error(f"Telegram API Error: {response.text}")
        except Exception as e:
            logger.error(f"Telegram Notification Crash: {e}")

    def send(self, message, parse_mode="HTML"):
        """Alias for send_message"""
        self.send_message(message, parse_mode)

    def send_notification(self, message, parse_mode="HTML"):
        """Alias for send_message"""
        self.send_message(message, parse_mode)

    def notify_trade_entry(self, strategy, symbol, side, qty, price, sl=None, target=None):
        icon = "📢"
        action = "BUY" if side == "BUY" else "SELL"
        
        # Target representation logic
        target_str = "Trailing"
        if target is not None:
            if isinstance(target, (int, float)):
                target_str = f"₹{target:.2f}"
            else:
                target_str = str(target)
        elif strategy == "GAMMA_BLAST":
            target_str = "Trail (3-Stages)"
            
        sl_str = "N/A"
        if sl is not None:
            sl_str = f"₹{sl:.2f}"
            
        msg = (
            f"{icon} <b>NEW SIGNAL ({strategy})</b>\n"
            f"Action: <b>{action}</b> | <code>{symbol}</code>\n"
            f"Entry: <b>₹{price:.2f}</b> | SL: <b>{sl_str}</b>\n"
            f"Target: <b>{target_str}</b> | Qty: {qty} units"
        )
        self.send_message(msg)

    def notify_trade_exit(self, strategy, symbol, pnl, reason):
        icon = "🎯" if pnl > 0 else "🛑"
        status = "PROFIT" if pnl > 0 else "LOSS"
        
        # Format PnL with sign
        pnl_sign = "+" if pnl > 0 else ""
        pnl_formatted = f"₹{pnl_sign}{pnl:,.2f}"
        
        msg = (
            f"{icon} <b>CLOSED: {strategy}</b>\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"Result: <b>{status}</b> | P&L: <b>{pnl_formatted}</b>\n"
            f"Reason: <i>{reason}</i>"
        )
        self.send_message(msg)

    def notify_condor_entry(self, sc_sym, sp_sym, lc_sym, lp_sym, sc_entry, sp_entry, lc_entry, lp_entry, qty_units):
        net_credit = (sc_entry + sp_entry) - (lc_entry + lp_entry)
        max_profit = net_credit * qty_units
        msg = (
            f"📢 <b>MARKET SETUP (STRADDLE SCALP)</b> ⚖️\n"
            f"Setup: <b>Iron Condor Basket</b> | Qty: {qty_units} units\n"
            f"Short CE: <code>{sc_sym}</code> @ ₹{sc_entry:.1f} | Hedge: <code>{lc_sym}</code> @ ₹{lc_entry:.1f}\n"
            f"Short PE: <code>{sp_sym}</code> @ ₹{sp_entry:.1f} | Hedge: <code>{lp_sym}</code> @ ₹{lp_entry:.1f}\n"
            f"Credit collected: <b>~₹{net_credit:.1f}/lot</b> | Max Profit: <b>₹{max_profit:,.0f}</b>"
        )
        self.send_message(msg)

    def notify_condor_exit(self, reason, short_pnl, long_pnl, total_pnl):
        icon = "🎯" if total_pnl > 0 else "🛑"
        status = "PROFIT" if total_pnl > 0 else "LOSS"
        pnl_sign = "+" if total_pnl > 0 else ""
        pnl_formatted = f"₹{pnl_sign}{total_pnl:,.2f}"
        
        msg = (
            f"{icon} <b>CONDOR CLOSED (STRADDLE SCALP)</b>\n"
            f"Reason: <b>{reason}</b>\n"
            f"Net P&L: <b>{pnl_formatted}</b>\n"
            f"Shorts P&L: <code>₹{short_pnl:+.1f}</code> | Hedges P&L: <code>₹{long_pnl:+.1f}</code>"
        )
        self.send_message(msg)

notifier = TelegramNotifier()
