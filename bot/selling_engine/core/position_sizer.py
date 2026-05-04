"""
Position Sizer — calculates how many lots to sell based on capital and VIX.
"""
from bot.utils.logger import logger

class PositionSizer:
    def __init__(self, config: dict):
        self.config = config

    def calculate_lots(
        self,
        strategy: str,        # "iron_condor", "short_strangle", "iron_fly"
        vix_multiplier: float # From VIXGate: 1.0 or 0.5
    ) -> dict:
        capital = self.config["total_capital"]
        deploy_pct = self.config["max_deploy_pct"] / 100
        lot_size = self.config["lot_size"]

        # Approximate margin requirements per lot (INR)
        # These are conservative estimates. Broker API should be used for exact numbers.
        margin_map = {
            "iron_condor": 45000,    # Defined risk = lower margin
            "short_strangle": 95000, # Naked = higher margin
            "iron_fly": 60000        # Defined risk, expiry day
        }

        margin_per_lot = margin_map.get(strategy, 50000)
        deployable = capital * deploy_pct * vix_multiplier
        lots = max(1, int(deployable / margin_per_lot))

        logger.info(f">>> [PositionSizer] Strategy: {strategy}, Capital: {capital}, Deployable: {deployable}, Lots: {lots}")

        return {
            "lots": lots,
            "units": lots * lot_size,
            "margin_used": lots * margin_per_lot,
            "margin_pct": round(lots * margin_per_lot / capital * 100, 1)
        }
