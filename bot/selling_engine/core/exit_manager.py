"""
Exit Manager — monitors all open selling positions and triggers exits.
Call check_exits() in your main bot loop every 1–5 minutes during market hours.
"""

from datetime import datetime, time
from bot.utils.logger import logger

class ExitManager:
    def __init__(self, config: dict):
        self.config = config

    def check_exits(self, position: dict, current_data: dict) -> dict:
        """
        Args:
            position: {
                "strategy": str,
                "entry_premium": float,       # Total premium collected per unit
                "current_premium": float,     # Current mark-to-market premium
                "entry_time": datetime,
                "short_call_strike": float,
                "short_put_strike": float,
                "atm_strike": float (optional, for Iron Fly)
            }
            current_data: {
                "spot": float,
                "time": datetime,
                "vix": float
            }

        Returns:
            {
                "should_exit": bool,
                "exit_type": str,   # "take_profit" | "stop_loss" | "time_exit" | "hold"
                "reason": str,
                "urgency": str      # "immediate" | "next_candle" | "eod"
            }
        """
        strategy = position["strategy"]
        entry_prem = position["entry_premium"]
        curr_prem = position["current_premium"]
        spot = current_data["spot"]
        now = current_data["time"]

        # --- Take profit checks ---
        tp_pct = {
            "iron_condor": self.config["ic_take_profit_pct"],
            "short_strangle": self.config["sc_take_profit_pct"],
            "iron_fly": self.config["if_take_profit_pct"]
        }.get(strategy, 50)

        # For selling, profit is (Entry - Current)
        profit_pct = (entry_prem - curr_prem) / entry_prem * 100
        if profit_pct >= tp_pct:
            return {
                "should_exit": True,
                "exit_type": "take_profit",
                "reason": f"Target hit: {profit_pct:.1f}% of premium collected (target {tp_pct}%)",
                "urgency": "next_candle"
            }

        # --- Stop loss checks ---
        sl_mult = {
            "iron_condor": self.config["ic_stop_loss_multiplier"],
            "short_strangle": self.config["sc_stop_loss_multiplier"],
            "iron_fly": None  # Iron fly uses point-based SL
        }.get(strategy)

        if sl_mult and curr_prem >= entry_prem * sl_mult:
            loss_pct = (curr_prem - entry_prem) / entry_prem * 100
            return {
                "should_exit": True,
                "exit_type": "stop_loss",
                "reason": f"Stop loss: position premium {curr_prem:.1f} hit {sl_mult}x multiplier (loss {loss_pct:.1f}%)",
                "urgency": "immediate"
            }

        # Iron fly point-based stop
        if strategy == "iron_fly":
            atm = position.get("atm_strike", spot)
            sl_pts = self.config["if_stop_loss_pts"]
            if abs(spot - atm) >= sl_pts:
                return {
                    "should_exit": True,
                    "exit_type": "stop_loss",
                    "reason": f"Iron fly SL: Nifty moved {abs(spot-atm):.0f} pts from ATM (limit {sl_pts})",
                    "urgency": "immediate"
                }

        # --- Time-based exits ---
        current_time = now.time()

        if strategy == "iron_fly":
            # Check for expiry day exit
            deadline_tuple = self.config["if_exit_deadline"]
            deadline = time(int(deadline_tuple[0].split(':')[0]), int(deadline_tuple[0].split(':')[1])) if ':' in deadline_tuple[0] else time(14, 0)
            if current_time >= deadline:
                return {
                    "should_exit": True,
                    "exit_type": "time_exit",
                    "reason": f"Iron fly {deadline} hard exit. Never hold past this on expiry day.",
                    "urgency": "immediate"
                }

        if strategy in ("iron_condor", "short_strangle"):
            # Check for Wednesday exit
            exit_day, exit_time_str = self.config["ic_exit_deadline"] # "Wednesday", "14:00"
            exit_h, exit_m = map(int, exit_time_str.split(':'))
            if now.strftime("%A") == exit_day and current_time >= time(exit_h, exit_m):
                return {
                    "should_exit": True,
                    "exit_type": "time_exit",
                    "reason": f"{exit_day} {exit_time_str} deadline. Exit to avoid expiry day gamma risk.",
                    "urgency": "immediate"
                }

        return {
            "should_exit": False,
            "exit_type": "hold",
            "reason": f"No exit condition met. P&L: {profit_pct:.1f}% collected.",
            "urgency": None
        }
