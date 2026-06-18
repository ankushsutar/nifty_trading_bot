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
        Works for both Short Strangle and Iron Condor.
        """
        strategy = position["strategy"]
        if strategy not in ["short_strangle", "iron_condor"]:
            return {"adjustment_needed": False}

        # Trigger settings
        trigger = self.config.get("ic_adjustment_trigger_pts", 75) if strategy == "iron_condor" else self.config["sc_adjustment_trigger_pts"]
        
        short_call = position["short_call"]
        short_put = position["short_put"]

        call_distance = short_call - spot
        put_distance = spot - short_put

        # --- CALL SIDE TESTED: Roll Put Side UP ---
        if call_distance <= trigger:
            # For Iron Condor, we roll the UNTESTED side (Put) closer to collect more credit
            new_put = round((spot - 150) / 50) * 50 # Roll to ~150 pts away
            if new_put > short_put + 50: # Ensure we are actually moving it
                return {
                    "adjustment_needed": True,
                    "side": "put",
                    "action": f"Roll Put side UP from {short_put} to {new_put}",
                    "reason": f"Call side tested ({call_distance:.0f} pts). Rolling untested Put side UP.",
                    "old_short": short_put,
                    "new_short": new_put,
                    "urgency": "next_candle"
                }

        # --- PUT SIDE TESTED: Roll Call Side DOWN ---
        if put_distance <= trigger:
            new_call = round((spot + 150) / 50) * 50 # Roll to ~150 pts away
            if new_call < short_call - 50:
                return {
                    "adjustment_needed": True,
                    "side": "call",
                    "action": f"Roll Call side DOWN from {short_call} to {new_call}",
                    "reason": f"Put side tested ({put_distance:.0f} pts). Rolling untested Call side DOWN.",
                    "old_short": short_call,
                    "new_short": new_call,
                    "urgency": "next_candle"
                }

        return {
            "adjustment_needed": False,
            "call_distance": round(call_distance),
            "put_distance": round(put_distance)
        }
