"""
VIX Gate — validates whether current VIX allows premium selling.
Call this BEFORE any strategy entry. If it returns False, abort entry.
"""
from bot.utils.logger import logger

class VIXGate:
    def __init__(self, config: dict):
        self.config = config

    def check(self, current_vix: float, adx: float = 0.0) -> dict:
        """
        Returns:
            {
                "allowed": bool,
                "size_multiplier": float,  # 1.0 = normal, 0.5 = half size, 0.0 = no trade
                "reason": str,
                "recommended_strategy": str or None
            }
        """
        vix = current_vix
        vmin = self.config["vix_min"]
        vmax = self.config["vix_max"]
        vhigh = self.config["vix_high_threshold"]

        logger.info(f">>> [VIX Gate] Checking VIX: {vix}, ADX: {adx} (Min: {vmin}, Max: {vmax}, High: {vhigh})")

        if vix < vmin:
            return {
                "allowed": False,
                "size_multiplier": 0.0,
                "reason": f"VIX {vix} below minimum {vmin}. Premiums too thin. Consider calendar spread.",
                "recommended_strategy": "calendar_spread"
            }
        elif vix > vmax:
            return {
                "allowed": False,
                "size_multiplier": 0.0,
                "reason": f"VIX {vix} above maximum {vmax}. Extreme tail risk. Sit out.",
                "recommended_strategy": None
            }
        
        # 2. ADX Check (Trend Danger)
        adx_limit = self.config.get("adx_max_for_selling", 25.0)
        if adx > adx_limit:
            return {
                "allowed": False,
                "size_multiplier": 0.0,
                "reason": f"ADX {adx:.1f} exceeds limit {adx_limit}. Trend detected, selling neutral spreads is unsafe.",
                "recommended_strategy": None
            }

        # 3. Sizing and Recommendation
        if vix > vhigh:
            return {
                "allowed": True,
                "size_multiplier": 0.5,
                "reason": f"VIX {vix} elevated. Using 50% position size. Iron condor recommended.",
                "recommended_strategy": "iron_condor"
            }
        else:
            return {
                "allowed": True,
                "size_multiplier": 1.0,
                "reason": f"VIX {vix} in optimal range. Full position size allowed.",
                "recommended_strategy": "iron_condor"
            }
