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

    def notify_trade_entry(self, strategy, symbol, side, qty, price, sl=None, target=None, **kwargs):
        # Route to specialized notification handlers if available
        if strategy == "GAMMA_BLAST":
            return self.notify_gamma_blast_entry(
                symbol=symbol,
                leg=kwargs.get('leg', 'CE' if 'CE' in symbol else 'PE'),
                qty=qty,
                price=price,
                sl=sl,
                sl_points=kwargs.get('sl_points'),
                adx=kwargs.get('adx'),
                atr=kwargs.get('atr')
            )
        elif strategy == "ZERO_TO_HERO":
            return self.notify_zero_to_hero_entry(
                symbol=symbol,
                leg=kwargs.get('leg', 'CE' if 'CE' in symbol else 'PE'),
                qty=qty,
                prem=price,
                sl_init=sl,
                target_strike=kwargs.get('target_strike'),
                expiry=kwargs.get('expiry')
            )

        icon = "📢"
        action = "BUY" if side == "BUY" else "SELL"
        
        # Target representation logic
        target_str = "Trailing"
        if target is not None:
            if isinstance(target, (int, float)):
                target_str = f"₹{target:.2f}"
            else:
                target_str = str(target)
            
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

    def notify_trade_exit(self, strategy, symbol, pnl, reason, exit_price=0.0, entry_price=0.0, qty=0, **kwargs):
        if strategy == "GAMMA_BLAST":
            return self.notify_gamma_blast_exit(
                symbol=symbol,
                pnl=pnl,
                exit_price=exit_price,
                entry_price=entry_price,
                reason=reason,
                qty=qty
            )
        elif strategy == "ZERO_TO_HERO":
            return self.notify_zero_to_hero_exit(
                symbol=symbol,
                pnl=pnl,
                exit_price=exit_price,
                entry_price=entry_price,
                reason=reason,
                qty=qty
            )

        icon = "🎯" if pnl > 0 else "🛑"
        status = "PROFIT" if pnl > 0 else "LOSS"
        
        # Format PnL with sign
        pnl_sign = "+" if pnl > 0 else ""
        pnl_formatted = f"₹{pnl_sign}{pnl:,.2f}"
        
        details = ""
        if entry_price > 0 and exit_price > 0:
            roi = ((exit_price - entry_price) / entry_price) * 100
            details = f"\nPrice: ₹{entry_price:.2f} ➔ <b>₹{exit_price:.2f}</b> ({roi:+.1f}%)"
            
        qty_str = f" | Qty: {qty}" if qty > 0 else ""

        msg = (
            f"{icon} <b>CLOSED: {strategy}</b>\n"
            f"Symbol: <code>{symbol}</code>{qty_str}\n"
            f"Result: <b>{status}</b> | P&L: <b>{pnl_formatted}</b>{details}\n"
            f"Reason: <i>{reason}</i>"
        )
        self.send_message(msg)

    @staticmethod
    def _friendly_reason(reason: str) -> str:
        mapping = {
            "SL_HIT": "Stop-Loss Hit",
            "LADDERED_SL_MARKET": "Trailing Stop-Loss Hit",
            "LADDERED_SL_LIMIT": "Trailing Stop-Loss Hit",
            "TIME_KILLSWITCH": "End-of-Day Exit (3:10 PM)",
            "TIME": "End-of-Day Exit",
            "EOD_LIQUIDATION": "End-of-Day Exit (3:10 PM)",
            "HARD_FLOOR": "Stop-Loss Hit",
            "TARGET": "Target Hit",
        }
        return mapping.get(str(reason), str(reason).replace("_", " ").title())

    def notify_gamma_blast_entry(self, symbol, leg, qty, price, sl=None, sl_points=None, adx=None, atr=None):
        lot_size = getattr(Config, "NIFTY_LOT_SIZE", 65)
        lots = max(1, qty // lot_size) if lot_size else 1
        risk_per_unit = (price - sl) if sl is not None else (sl_points or 0.0)
        risk_rupees = risk_per_unit * qty
        sl_val_str = f"₹{sl:.2f}" if sl is not None else "Auto"

        msg = (
            f"🚀 <b>GAMMA BLAST — NEW TRADE</b>\n"
            f"<b>BUY {qty}</b> <code>{symbol}</code> ({lots} lot{'s' if lots > 1 else ''}) @ <b>₹{price:.2f}</b>\n"
            f"🛡️ Stop-Loss: <b>{sl_val_str}</b> (Max Risk: ₹{risk_rupees:,.0f})\n"
            f"📈 Plan: Stop-Loss will automatically trail upward as price rises"
        )
        self.send_message(msg)

    def notify_gamma_blast_exit(self, symbol, pnl, exit_price=0.0, entry_price=0.0, reason="EXIT", qty=0):
        is_profit = pnl >= 0
        icon = "🎯" if is_profit else "🛑"
        status = "PROFIT" if is_profit else "LOSS"
        pnl_sign = "+" if pnl > 0 else ""
        pnl_str = f"{pnl_sign}₹{pnl:,.2f}"

        roi_str = ""
        price_line = ""
        if entry_price > 0 and exit_price > 0:
            roi = ((exit_price - entry_price) / entry_price) * 100
            roi_str = f" ({roi:+.1f}%)"
            price_line = f"\nBought @ ₹{entry_price:.2f} ➔ Sold @ <b>₹{exit_price:.2f}</b>"

        qty_str = f"{qty} " if qty > 0 else ""
        friendly_reason = self._friendly_reason(reason)

        msg = (
            f"{icon} <b>GAMMA BLAST — {status} ({pnl_str}{roi_str})</b>\n"
            f"SOLD {qty_str}<code>{symbol}</code>{price_line}\n"
            f"Reason: <i>{friendly_reason}</i>"
        )
        self.send_message(msg)

    def notify_zero_to_hero_entry(self, symbol, leg, qty, prem, sl_init=None, target_strike=None, expiry=None):
        lot_size = getattr(Config, "NIFTY_LOT_SIZE", 65)
        lots = max(1, qty // lot_size) if lot_size else 1
        total_cost = prem * qty
        sl_str = f"₹{sl_init:.2f}" if sl_init is not None else "Auto"
        target_3x = prem * 3

        msg = (
            f"🛸 <b>ZERO-TO-HERO — NEW TRADE</b>\n"
            f"<b>BUY {qty}</b> <code>{symbol}</code> ({lots} lot{'s' if lots > 1 else ''}) @ <b>₹{prem:.2f}</b>\n"
            f"💰 Invested: <b>₹{total_cost:,.0f}</b> | 🛡️ Stop-Loss: <b>{sl_str}</b>\n"
            f"🎯 Goal: Book 50% at 3x (<b>₹{target_3x:.2f}</b>) &amp; ride rest risk-free"
        )
        self.send_message(msg)

    def notify_zero_to_hero_milestone(self, symbol, ltp, entry_price, milestone_desc):
        roi = ((ltp - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
        target_3x = entry_price * 3
        msg = (
            f"🚀 <b>ZERO-TO-HERO — PRICE DOUBLED (2x)</b>\n"
            f"<code>{symbol}</code> is now <b>₹{ltp:.2f}</b> (<b>+{roi:.0f}%</b>)\n"
            f"Bought @ ₹{entry_price:.2f} | Holding for 3x target (₹{target_3x:.2f})"
        )
        self.send_message(msg)

    def notify_zero_to_hero_scale_out(self, symbol, sell_qty, exit_price, entry_price, booked_pnl, remaining_qty):
        roi = ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 200.0
        msg = (
            f"🔥 <b>ZERO-TO-HERO — HALF PROFIT BOOKED (3x)</b>\n"
            f"SOLD <b>{sell_qty}</b> <code>{symbol}</code> @ <b>₹{exit_price:.2f}</b> (+{roi:.0f}%)\n"
            f"💰 Profit Booked: <b>+₹{booked_pnl:,.2f}</b>\n"
            f"✅ Investment recovered! Remaining <b>{remaining_qty} units</b> are now risk-free."
        )
        self.send_message(msg)

    def notify_zero_to_hero_exit(self, symbol, pnl, exit_price=0.0, entry_price=0.0, reason="EXIT", qty=0):
        is_profit = pnl >= 0
        icon = "🏆" if is_profit else "🛑"
        status = "PROFIT" if is_profit else "LOSS"
        pnl_sign = "+" if pnl > 0 else ""
        pnl_str = f"{pnl_sign}₹{pnl:,.2f}"

        roi_str = ""
        price_line = ""
        if entry_price > 0 and exit_price > 0:
            roi = ((exit_price - entry_price) / entry_price) * 100
            roi_str = f" ({roi:+.0f}%)"
            price_line = f"\nBought @ ₹{entry_price:.2f} ➔ Sold @ <b>₹{exit_price:.2f}</b>"

        qty_str = f"{qty} " if qty > 0 else ""
        friendly_reason = self._friendly_reason(reason)

        msg = (
            f"{icon} <b>ZERO-TO-HERO — {status} ({pnl_str}{roi_str})</b>\n"
            f"SOLD {qty_str}<code>{symbol}</code>{price_line}\n"
            f"Reason: <i>{friendly_reason}</i>"
        )
        self.send_message(msg)

    def notify_trailing_sl_modify(self, strategy, symbol, old_sl, new_sl, ltp, stage_name, stage=None, entry_price=0.0, qty=0, extra_note=None):
        sl_change = new_sl - old_sl
        change_str = f" (+{sl_change:.1f} pts)" if sl_change > 0 else ""
        strat_label = strategy.replace("_", " ")

        if entry_price > 0 and new_sl >= entry_price:
            header = f"🔒 <b>PROFIT LOCKED — SL MOVED UP ({strat_label})</b>"
            if qty > 0:
                rupees_locked = (new_sl - entry_price) * qty
                status_line = f"\n✅ Min Guaranteed Profit: <b>+₹{rupees_locked:,.2f}</b>"
            else:
                status_line = "\n✅ Trade is now in guaranteed profit"
        else:
            header = f"🛡️ <b>STOP-LOSS MOVED UP ({strat_label})</b>"
            status_line = f"\n📌 Phase: {stage_name}"

        note_text = f"\n💰 {extra_note}" if extra_note else ""

        msg = (
            f"{header}\n"
            f"<code>{symbol}</code> | Current Price: <b>₹{ltp:.2f}</b>\n"
            f"Stop-Loss: ₹{old_sl:.2f} ➔ <b>₹{new_sl:.2f}</b>{change_str}{status_line}{note_text}"
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
