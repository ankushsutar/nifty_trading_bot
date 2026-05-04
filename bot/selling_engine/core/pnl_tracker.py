"""
P&L Tracker — logs every trade and generates weekly/monthly reports.
Saves to JSON file. Add database integration as needed.
"""

import json
import os
from datetime import datetime, date
from bot.utils.logger import logger

class PnLTracker:
    def __init__(self, log_file: str = "data/selling_pnl.json"):
        # Ensure data directory exists
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        self.log_file = log_file
        self.data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f">>> [PnLTracker] Error loading log file: {e}")
        return {"trades": [], "weekly_summary": {}}

    def _save(self):
        try:
            with open(self.log_file, "w") as f:
                json.dump(self.data, f, indent=2, default=str)
        except Exception as e:
            logger.error(f">>> [PnLTracker] Error saving log file: {e}")

    def log_trade(
        self,
        strategy: str,
        entry_premium: float,
        exit_premium: float,
        lots: int,
        lot_size: int,
        exit_type: str,  # "take_profit" | "stop_loss" | "time_exit"
        notes: str = ""
    ):
        units = lots * lot_size
        pnl = (entry_premium - exit_premium) * units
        week_key = date.today().strftime("%Y-W%W")

        trade = {
            "date": str(date.today()),
            "week": week_key,
            "strategy": strategy,
            "entry_premium": entry_premium,
            "exit_premium": exit_premium,
            "lots": lots,
            "units": units,
            "pnl_inr": round(pnl),
            "exit_type": exit_type,
            "notes": notes,
            "timestamp": str(datetime.now())
        }
        self.data["trades"].append(trade)

        # Update weekly summary
        if week_key not in self.data["weekly_summary"]:
            self.data["weekly_summary"][week_key] = {
                "total_pnl": 0, "wins": 0, "losses": 0, "trades": 0
            }
        ws = self.data["weekly_summary"][week_key]
        ws["total_pnl"] += round(pnl)
        ws["trades"] += 1
        if pnl >= 0:
            ws["wins"] += 1
        else:
            ws["losses"] += 1

        self._save()
        logger.info(f">>> [PnLTracker] Trade logged: {strategy}, P&L: ₹{round(pnl)}")
        return trade

    def get_weekly_summary(self, week_key: str = None) -> dict:
        if not week_key:
            week_key = date.today().strftime("%Y-W%W")
        return self.data["weekly_summary"].get(week_key, {
            "total_pnl": 0, "wins": 0, "losses": 0, "trades": 0
        })

    def check_weekly_loss_limit(self, capital: float, max_loss_pct: float) -> bool:
        """Returns True if weekly loss limit breached — halt all trading."""
        summary = self.get_weekly_summary()
        max_loss = capital * max_loss_pct / 100
        is_breached = summary["total_pnl"] < -max_loss
        if is_breached:
            logger.warning(f">>> [PnLTracker] Weekly loss limit breached: {summary['total_pnl']} < -{max_loss}")
        return is_breached

    def get_monthly_report(self) -> dict:
        month_key = date.today().strftime("%Y-%m")
        month_trades = [t for t in self.data["trades"] if t["date"].startswith(month_key)]
        total_pnl = sum(t["pnl_inr"] for t in month_trades)
        wins = sum(1 for t in month_trades if t["pnl_inr"] >= 0)
        return {
            "month": month_key,
            "total_pnl": total_pnl,
            "trades": len(month_trades),
            "wins": wins,
            "losses": len(month_trades) - wins,
            "win_rate_pct": round(wins / len(month_trades) * 100) if month_trades else 0
        }
