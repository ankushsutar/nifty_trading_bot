"""
Adjustment Manager — handles mid-week strike breaches for short strangle.
When Nifty breaks toward a short strike, this rolls that side further OTM.
"""
from bot.utils.logger import logger

class AdjustmentManager:
    def __init__(self, config: dict):
        self.config = config

    def check_adjustment_needed(self, position: dict, spot: float) -> dict:
        """
        Returns adjustment instructions if Nifty is approaching a short strike.
        Only applies to short strangle (iron condor has defined risk via wings).
        """
        if position["strategy"] != "short_strangle":
            return {"adjustment_needed": False}

        trigger = self.config["sc_adjustment_trigger_pts"]
        roll = self.config["sc_adjustment_roll_pts"]

        short_call = position["short_call_strike"]
        short_put = position["short_put_strike"]

        call_distance = short_call - spot
        put_distance = spot - short_put

        if call_distance <= trigger:
            new_call = short_call + roll
            return {
                "adjustment_needed": True,
                "side": "call",
                "action": f"Roll short call from {short_call} to {new_call}",
                "buy_back": short_call,
                "sell_new": new_call,
                "reason": f"Nifty {spot} is only {call_distance:.0f} pts from short call {short_call}. Trigger: {trigger} pts.",
                "urgency": "immediate" if call_distance < 100 else "next_candle"
            }

        if put_distance <= trigger:
            new_put = short_put - roll
            return {
                "adjustment_needed": True,
                "side": "put",
                "action": f"Roll short put from {short_put} to {new_put}",
                "buy_back": short_put,
                "sell_new": new_put,
                "reason": f"Nifty {spot} is only {put_distance:.0f} pts from short put {short_put}. Trigger: {trigger} pts.",
                "urgency": "immediate" if put_distance < 100 else "next_candle"
            }

        return {
            "adjustment_needed": False,
            "call_distance": round(call_distance),
            "put_distance": round(put_distance)
        }
