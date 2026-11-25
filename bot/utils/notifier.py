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

    def notify_gamma_blast_entry(self, symbol, leg, qty, price, sl=None, sl_points=None, adx=None, atr=None):
        lot_size = getattr(Config, "NIFTY_LOT_SIZE", 65)
        lots = max(1, qty // lot_size) if lot_size else 1
        risk_per_unit = (price - sl) if sl is not None else (sl_points or 0.0)
        risk_rupees = risk_per_unit * qty
        risk_pct = (risk_per_unit / price * 100) if price > 0 else 0.0
        total_outlay = price * qty

        adx_str = f" | ADX: <b>{adx:.1f}</b>" if adx else ""
        atr_str = f" | ATR: <b>₹{atr:.1f}</b>" if atr else ""
        sl_val_str = f"₹{sl:.2f}" if sl is not None else "Dynamic"

        msg = (
            f"🚀 <b>GAMMA BLAST INITIATED</b> ⚡\n"
            f"Contract: <b>BUY {leg}</b> | <code>{symbol}</code>\n"
            f"Entry: <b>₹{price:.2f}</b> | Qty: <b>{qty} units</b> ({lots} lot{'s' if lots > 1 else ''})\n"
            f"Capital Outlay: <b>₹{total_outlay:,.2f}</b>\n"
            f"🛡️ Initial SL: <b>{sl_val_str}</b> (Risk: -₹{risk_rupees:,.0f} | -{risk_pct:.1f}%){adx_str}{atr_str}\n"
            f"🎯 <b>Progression Targets:</b>\n"
            f"  • Stage 0.5: Breakeven Lock (+1.0 ATR)\n"
            f"  • Stage 1.0: Profit Floor (+1.5 ATR)\n"
            f"  • Stage 2.0: 50% Partial Booking (+2.5 ATR)\n"
            f"  • Stage 3.0+: 1m EMA Runner Mode 🏃"
        )
        self.send_message(msg)

    def notify_gamma_blast_exit(self, symbol, pnl, exit_price=0.0, entry_price=0.0, reason="EXIT", qty=0):
        is_profit = pnl > 0
        icon = "🎯" if is_profit else "🛑"
        status = "PROFIT" if is_profit else "LOSS"
        pnl_sign = "+" if is_profit else ""
        pnl_str = f"₹{pnl_sign}{pnl:,.2f}"

        roi_str = ""
        points_str = ""
        if entry_price > 0 and exit_price > 0:
            roi = ((exit_price - entry_price) / entry_price) * 100
            roi_str = f" ({roi:+.1f}%)"
            pts = exit_price - entry_price
            points_str = f"\nExecution: ₹{entry_price:.2f} ➔ <b>₹{exit_price:.2f}</b> ({pts:+.1f}pts)"

        qty_str = f" | Qty: {qty} units" if qty > 0 else ""

        msg = (
            f"{icon} <b>GAMMA BLAST CLOSED</b>\n"
            f"Symbol: <code>{symbol}</code>{qty_str}\n"
            f"Result: <b>{status}</b> | Net P&L: <b>{pnl_str}</b>{roi_str}{points_str}\n"
            f"Exit Reason: <i>{reason}</i>"
        )
        self.send_message(msg)

    def notify_zero_to_hero_entry(self, symbol, leg, qty, prem, sl_init=None, target_strike=None, expiry=None):
        lot_size = getattr(Config, "NIFTY_LOT_SIZE", 65)
        lots = max(1, qty // lot_size) if lot_size else 1
        total_cost = prem * qty
        sl_str = f"₹{sl_init:.2f}" if sl_init is not None else "Hard Floor"
        expiry_str = f" | Expiry: <b>{expiry}</b>" if expiry else ""
        strike_str = f" | Target Strike: <b>{target_strike}</b>" if target_strike else ""

        msg = (
            f"🛸 <b>ZERO-TO-HERO WILDCARD ACTIVATED</b> 🚀\n"
            f"Action: <b>BUY {leg}</b> | <code>{symbol}</code>{expiry_str}\n"
            f"Entry Premium: <b>₹{prem:.2f}</b> | Qty: <b>{qty} units</b> ({lots} lot{'s' if lots > 1 else ''})\n"
            f"💰 Capital Outlay: <b>₹{total_cost:,.2f}</b> (Max Risk Capped){strike_str}\n"
            f"🛡️ Emergency SL: <b>{sl_str}</b>\n"
            f"🎯 <b>Jackpot Strategy:</b>\n"
            f"  • Scale 50% at 3x (+200% ROI) to recover 100% capital + profit\n"
            f"  • Remaining runner holds to EOD with ZERO risk!"
        )
        self.send_message(msg)

    def notify_zero_to_hero_milestone(self, symbol, ltp, entry_price, milestone_desc):
        roi = ((ltp - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
        msg = (
            f"🚀 <b>ZERO-TO-HERO: {milestone_desc}</b> ⚡\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"Current LTP: <b>₹{ltp:.2f}</b> (from ₹{entry_price:.2f} | <b>{roi:+.1f}%</b>)\n"
            f"Holding position for 3x Jackpot Target scale-out! 🎯"
        )
        self.send_message(msg)

    def notify_zero_to_hero_scale_out(self, symbol, sell_qty, exit_price, entry_price, booked_pnl, remaining_qty):
        roi = ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 200.0
        msg = (
            f"🔥 <b>JACKPOT SCALE-OUT: ZERO-TO-HERO</b> 💰\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"Secured: <b>{sell_qty} units</b> @ <b>₹{exit_price:.2f}</b> (<b>+{roi:.1f}% ROI</b>)\n"
            f"Booked P&L: <b>+₹{booked_pnl:,.2f}</b> 💵\n"
            f"🚀 <b>Capital Fully Recovered + 100% Profit!</b>\n"
            f"Remaining <b>{remaining_qty} units</b> running risk-free to the moon!"
        )
        self.send_message(msg)

    def notify_zero_to_hero_exit(self, symbol, pnl, exit_price=0.0, entry_price=0.0, reason="EXIT", qty=0):
        is_profit = pnl > 0
        icon = "🏆" if is_profit else "💀"
        is_jackpot = exit_price >= (entry_price * 2.5) if (entry_price > 0 and exit_price > 0) else False
        status = "JACKPOT" if is_jackpot else ("PROFIT" if is_profit else "LOSS")
        pnl_sign = "+" if is_profit else ""
        pnl_str = f"₹{pnl_sign}{pnl:,.2f}"

        roi_str = ""
        points_str = ""
        if entry_price > 0 and exit_price > 0:
            roi = ((exit_price - entry_price) / entry_price) * 100
            mult = exit_price / entry_price
            roi_str = f" ({roi:+.1f}% | {mult:.1f}x)"
            pts = exit_price - entry_price
            points_str = f"\nExecution: ₹{entry_price:.2f} ➔ <b>₹{exit_price:.2f}</b> ({pts:+.1f}pts)"

        qty_str = f" | Qty: {qty} units" if qty > 0 else ""

        msg = (
            f"{icon} <b>ZERO-TO-HERO CLOSED ({status})</b>\n"
            f"Symbol: <code>{symbol}</code>{qty_str}\n"
            f"Result: <b>{status}</b> | Final P&L: <b>{pnl_str}</b>{roi_str}{points_str}\n"
            f"Exit Reason: <i>{reason}</i>"
        )
        self.send_message(msg)

    def notify_trailing_sl_modify(self, strategy, symbol, old_sl, new_sl, ltp, stage_name, stage=None, entry_price=0.0, qty=0, extra_note=None):
        sl_change = new_sl - old_sl
        change_sign = "+" if sl_change >= 0 else ""
        
        stage_desc = f"<b>{stage_name}</b>"
        if stage is not None:
            stage_desc += f" [Stage {stage}]"
            
        floor_text = ""
        if entry_price > 0 and qty > 0:
            if new_sl >= entry_price:
                pts_locked = new_sl - entry_price
                rupees_locked = pts_locked * qty
                floor_text = f"\n🔒 <b>Guaranteed Floor: +₹{rupees_locked:,.2f}</b> (+{pts_locked:.1f}pts from entry)"
            else:
                pts_risk = entry_price - new_sl
                rupees_risk = pts_risk * qty
                floor_text = f"\n🛡️ <b>Risk Reduced: Max risk cut to -₹{rupees_risk:,.2f}</b> (-{pts_risk:.1f}pts from entry)"
                
        note_text = f"\n{extra_note}" if extra_note else ""

        msg = (
            f"📈 <b>TRAILING SL ADVANCED ({strategy})</b> 🛡️\n"
            f"Symbol: <code>{symbol}</code> | LTP: <b>₹{ltp:.2f}</b>\n"
            f"Phase: {stage_desc}\n"
            f"SL Adjusted: ₹{old_sl:.2f} ➔ <b>₹{new_sl:.2f}</b> ({change_sign}{sl_change:.1f}pts ⬆️){floor_text}{note_text}"
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
