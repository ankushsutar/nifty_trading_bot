"""
Position Sizer — calculates how many lots to sell based on capital and VIX.
"""
from bot.utils.logger import logger

class PositionSizer:
    def __init__(self, config: dict):
        self.config = config

    def calculate_lots(
        self,
        strategy: str,
        vix_multiplier: float,
        current_capital: float = None
    ) -> dict:
        # Use live capital for compounding
        capital = current_capital or self.config["total_capital"]
        deploy_pct = self.config["max_deploy_pct"] / 100
        lot_size = self.config["lot_size"]

        # Institutional Margin Buffer (conservative)
        margin_map = {
            "iron_condor": 55000,    # Increased for extra safety
            "short_strangle": 110000,
            "iron_fly": 70000
        }

        margin_per_lot = margin_map.get(strategy, 60000)
        deployable = capital * deploy_pct * vix_multiplier
        
        # Compounding Logic: 1 Lot per every ₹Margin Required
        # This ensures we don't over-leverage early on
        lots = int(deployable / margin_per_lot)
        if lots < 1:
            lots = 1 if capital >= margin_per_lot else 0 # Prevent trading if account is too small

        logger.info(f">>> [PositionSizer] COMPOUNDING: Capital ₹{capital:,.0f} -> Strategy: {strategy} -> Lots: {lots}")

        return {
            "lots": lots,
            "units": lots * lot_size,
            "margin_used": lots * margin_per_lot,
            "margin_pct": round(lots * margin_per_lot / capital * 100, 1)
        }
