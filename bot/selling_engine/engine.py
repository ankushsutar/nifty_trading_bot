"""
NiftySellingEngine — main entry point.
"""

from datetime import datetime, time
from bot.utils.logger import logger
from .config import SELLING_CONFIG
from .core.vix_gate import VIXGate
from .core.strike_selector import StrikeSelector
from .core.position_sizer import PositionSizer
from .core.exit_manager import ExitManager
from .core.adjustment import AdjustmentManager
from .core.pnl_tracker import PnLTracker
from .strategies.iron_condor import IronCondorStrategy
from .strategies.short_strangle import ShortStrangleStrategy
from .strategies.iron_fly import IronFlyStrategy

class NiftySellingEngine:
    def __init__(self, config: dict = None, dry_run: bool = False):
        self.config = config or SELLING_CONFIG
        self.dry_run = dry_run
        
        # Core Components
        self.vix_gate = VIXGate(self.config)
        self.strike_selector = StrikeSelector(self.config)
        self.position_sizer = PositionSizer(self.config)
        self.exit_manager = ExitManager(self.config)
        self.adjustment_manager = AdjustmentManager(self.config)
        self.pnl_tracker = PnLTracker()
        
        # Strategies
        self.ic_strat = IronCondorStrategy(self.config)
        self.ss_strat = ShortStrangleStrategy(self.config)
        self.if_strat = IronFlyStrategy(self.config)
        
        self.open_positions = []  # List of active selling positions
        logger.info(f">>> [NiftySellingEngine] Initialized (Dry Run: {self.dry_run})")

    def run_selling_cycle(self, market_data: dict) -> dict:
        """
        Check for new trade entries based on current market data.
        """
        spot = market_data["spot"]
        vix = market_data["vix"]
        now = market_data["time"]
        day_name = now.strftime("%A")
        max_pain = market_data.get("max_pain", spot)
        dte = market_data.get("days_to_expiry", 5)
        current_time = now.time()

        # Step 1: Check weekly loss limit
        if self.pnl_tracker.check_weekly_loss_limit(
            self.config["total_capital"],
            self.config["max_weekly_loss_pct"]
        ):
            return {
                "action": "skip",
                "strategy": None,
                "reason": "Weekly loss limit breached. Halting all selling.",
                "vix_status": None
            }

        # Step 2: VIX gate
        vix_result = self.vix_gate.check(vix)
        if not vix_result["allowed"]:
            return {
                "action": "skip",
                "strategy": None,
                "vix_status": vix_result,
                "reason": vix_result["reason"]
            }

        # Step 3: Strategy selection by day and time
        
        # Iron Fly Check
        if self.if_strat.is_entry_window(current_time, day_name, dte):
            strikes = self.strike_selector.select_iron_fly_strikes(spot)
            sizing = self.position_sizer.calculate_lots("iron_fly", vix_result["size_multiplier"])
            return {
                "action": "enter_trade",
                "strategy": "iron_fly",
                "strikes": strikes,
                "sizing": sizing,
                "vix_status": vix_result,
                "reason": "Expiry day iron fly conditions met."
            }

        # Iron Condor Check
        if self.ic_strat.is_entry_window(current_time, day_name, dte):
            # TREND-AWARE SKEW (Institutional Logic)
            # Fetch Trend (e.g. Price vs EMA20)
            trend = market_data.get("trend", "SIDEWAYS")
            
            strikes = self.strike_selector.select_iron_condor_strikes(spot, vix, dte, max_pain)
            
            if trend == "BEARISH":
                # Market is dropping. Don't sell Puts! Sell only Call Spread.
                logger.info(">>> [Selling] Regime: BEARISH. Switching to Bear Call Spread.")
                strikes["short_put"] = 0 # Disable Put side
                strikes["long_put"] = 0
            elif trend == "BULLISH":
                # Market is rising. Don't sell Calls! Sell only Put Spread.
                logger.info(">>> [Selling] Regime: BULLISH. Switching to Bull Put Spread.")
                strikes["short_call"] = 0 # Disable Call side
                strikes["long_call"] = 0
            
            sizing = self.position_sizer.calculate_lots("iron_condor", vix_result["size_multiplier"])
            return {
                "action": "enter_trade",
                "strategy": "iron_condor",
                "strikes": strikes,
                "sizing": sizing,
                "vix_status": vix_result,
                "reason": f"Adaptive {trend} entry conditions met."
            }

        # Short Strangle Check
        if self.ss_strat.is_entry_window(current_time, day_name, dte, vix):
            strikes = self.strike_selector.select_strangle_strikes(spot, vix, dte)
            sizing = self.position_sizer.calculate_lots("short_strangle", vix_result["size_multiplier"])
            return {
                "action": "enter_trade",
                "strategy": "short_strangle",
                "strikes": strikes,
                "sizing": sizing,
                "vix_status": vix_result,
                "reason": "Short strangle entry conditions met."
            }

        return {
            "action": "monitor_only",
            "strategy": None,
            "vix_status": vix_result,
            "reason": f"No entry conditions met for {day_name} at {current_time}."
        }

    def monitor_open_positions(self, current_data: dict) -> list:
        """
        Monitor active positions for exits or adjustments.
        """
        actions = []
        for position in self.open_positions:
            # Update current premium (In production, this would fetch real-time LTPs)
            # For now, we assume current_data contains the necessary premium info or we'd fetch it here.
            
            # Check exits
            exit_result = self.exit_manager.check_exits(position, current_data)
            if exit_result["should_exit"]:
                actions.append({
                    "type": "exit",
                    "position": position,
                    "exit_info": exit_result
                })
                continue

            # Check adjustments (short strangle only)
            if position["strategy"] == "short_strangle":
                adj_result = self.adjustment_manager.check_adjustment_needed(
                    position, current_data["spot"]
                )
                if adj_result["adjustment_needed"]:
                    actions.append({
                        "type": "adjustment",
                        "position": position,
                        "adjustment_info": adj_result
                    })

        return actions

    def add_position(self, position_details: dict):
        """Register a new open position."""
        self.open_positions.append(position_details)
        logger.info(f">>> [NiftySellingEngine] Position Added: {position_details['strategy']}")

    def close_position(self, position: dict, exit_premium: float, exit_type: str):
        """Remove a position and log the outcome."""
        self.open_positions = [p for p in self.open_positions if p != position]
        self.pnl_tracker.log_trade(
            strategy=position["strategy"],
            entry_premium=position["entry_premium"],
            exit_premium=exit_premium,
            lots=position["lots"],
            lot_size=self.config["lot_size"],
            exit_type=exit_type
        )
        logger.info(f">>> [NiftySellingEngine] Position Closed: {position['strategy']} ({exit_type})")
