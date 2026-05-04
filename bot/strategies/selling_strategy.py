"""
SellingStrategy — Wrapper for NiftySellingEngine to integrate with the main bot.
"""

import time
import datetime
from bot.utils.logger import logger
from bot.selling_engine.engine import NiftySellingEngine
from bot.utils.expiry_calculator import get_next_weekly_expiry
from backend.market_service import market_service

class SellingStrategy:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.engine = NiftySellingEngine(dry_run=self.dry_run)
        self.running = True

    def execute(self, expiry=None, action=None):
        """
        Main execution loop for the selling engine.
        """
        logger.info("\n--- NIFTY SELLING ENGINE ACTIVE ---")
        
        while self.running:
            try:
                # 1. Fetch current market data
                market_data = market_service.get_market_data()
                spot = market_data.get('nifty', 0)
                vix = market_data.get('vix', 0)
                
                # Calculate DTE (Days to Expiry)
                target_expiry_str = get_next_weekly_expiry()
                target_expiry_date = datetime.datetime.strptime(target_expiry_str, "%d%b%Y").date()
                dte = (target_expiry_date - datetime.date.today()).days
                
                cycle_data = {
                    "spot": spot,
                    "vix": vix,
                    "max_pain": spot, # Placeholder, can be calculated from chain
                    "days_to_expiry": dte,
                    "time": datetime.datetime.now()
                }
                
                # 2. Run Entry Cycle
                decision = self.engine.run_selling_cycle(cycle_data)
                
                if decision["action"] == "enter_trade":
                    logger.info(f">>> [Selling] Entry Decision: {decision['strategy']} - {decision['reason']}")
                    # In a real implementation, we would place orders here
                    # For now, we simulate entry in dry_run or log it
                    if not self.dry_run:
                        # TODO: Implement real order placement logic using self.api
                        logger.warning(">>> [Selling] Real order placement not yet implemented in this wrapper.")
                    
                    # Register the position in the engine
                    self.engine.add_position({
                        "strategy": decision["strategy"],
                        **decision["strikes"],
                        "entry_premium": 100.0,  # Placeholder premium
                        "lots": decision["sizing"]["lots"],
                        "entry_time": datetime.datetime.now(),
                        "current_premium": 100.0  # Initial MTM
                    })
                
                # 3. Monitor Open Positions
                # We need to update current_premium for each position before monitoring
                for pos in self.engine.open_positions:
                    # TODO: Fetch real-time premium for the strategy's strikes
                    # pos["current_premium"] = ...
                    pass
                
                monitor_actions = self.engine.monitor_open_positions(cycle_data)
                for action in monitor_actions:
                    if action["type"] == "exit":
                        logger.info(f">>> [Selling] Exit Triggered: {action['position']['strategy']} - {action['exit_info']['reason']}")
                        # TODO: Execute real exit orders
                        self.engine.close_position(
                            action["position"],
                            exit_premium=action["position"]["current_premium"],
                            exit_type=action["exit_info"]["exit_type"]
                        )
                    elif action["type"] == "adjustment":
                        logger.info(f">>> [Selling] Adjustment Needed: {action['adjustment_info']['action']}")
                        # TODO: Execute real adjustment orders
                
                # 4. Sleep until next check
                time.sleep(60) # Check every minute
                
            except Exception as e:
                logger.error(f">>> [Selling] Error in execution loop: {e}")
                time.sleep(10)

    def stop(self):
        logger.info(">>> [Selling] Stopping Selling Engine...")
        self.running = False
