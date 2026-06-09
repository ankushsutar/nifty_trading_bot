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
        if not expiry:
            expiry = get_next_weekly_expiry()
            
        from bot.core.safety_checks import SafetyGatekeeper
        gatekeeper = SafetyGatekeeper(self.api, dry_run=self.dry_run)
        
        while self.running:
            try:
                # 1. Fetch dynamic broker capital to prevent multi-process collisions
                live_capital = gatekeeper.get_current_capital()
                if live_capital > 0:
                    # Dynamically inject REAL current available cash into sizing engine config
                    # This forces compounding calculations to adapt to actual funds right now.
                    self.engine.config["total_capital"] = live_capital
                else:
                    logger.warning(">>> [Selling] Dynamic capital read was 0. Falling back to static config.")
                
                # 2. Fetch current market data (Spot & VIX)
                market_data = market_service.get_market_data()
                spot = market_data.get('nifty', 0)
                vix = market_data.get('vix', 0)
                
                if spot <= 0:
                    logger.warning(">>> [Selling] Waiting for valid market data (Spot is 0)...")
                    time.sleep(10)
                    continue

                # Calculate DTE (Days to Expiry)
                target_expiry_date = datetime.datetime.strptime(expiry, "%d%b%Y").date()
                dte = (target_expiry_date - datetime.date.today()).days
                
                cycle_data = {
                    "spot": spot,
                    "vix": vix,
                    "max_pain": market_data.get('levels', {}).get('pdh', spot), # Using PDH as max_pain fallback
                    "days_to_expiry": dte,
                    "time": datetime.datetime.now()
                }
                
                # 2. Run Entry Cycle
                decision = self.engine.run_selling_cycle(cycle_data)
                
                if decision["action"] == "enter_trade":
                    logger.info(f">>> [Selling] 🎯 ENTRY OPPORTUNITY: {decision['strategy']}")
                    
                    # Resolve Tokens & Symbols
                    legs = self._resolve_leg_details(decision["strategy"], decision["strikes"], expiry)
                    if not legs:
                        logger.error(">>> [Selling] ❌ Could not resolve leg details. Skipping.")
                        time.sleep(10)
                        continue

                    # Fetch real LTPs for 'Smart Limit' initialization
                    leg_tokens_list = [l['token'] for l in legs.values()]
                    premiums = self._fetch_leg_premiums_dict(legs)
                    total_credit = sum(p for k, p in premiums.items() if k.startswith('s')) - \
                                   sum(p for k, p in premiums.items() if k.startswith('l'))
                    
                    logger.info(f">>> [Selling] Initial Basket Credit: ₹{total_credit:.2f}")

                    # --- EXECUTION ---
                    from bot.core.basket_manager import BasketManager
                    basket = BasketManager(self.api, dry_run=self.dry_run)
                    
                    # Prepare legs with initial prices for BasketManager
                    for key in legs:
                        legs[key]['initial_price'] = premiums[key]

                    mode = "PAPER" if self.dry_run else "LIVE"
                    execution_result = basket.execute_basket(
                        strategy_name=decision["strategy"],
                        legs=legs,
                        lots=decision["sizing"]["lots"],
                        lot_size=self.engine.config["lot_size"],
                        mode=mode
                    )

                    if execution_result["status"] == "SUCCESS":
                        # Register the position in the engine with execution data
                        self.engine.add_position({
                            "strategy": decision["strategy"],
                            **decision["strikes"],
                            "legs": legs,
                            "entry_premium": total_credit,
                            "lots": decision["sizing"]["lots"],
                            "entry_time": datetime.datetime.now(),
                            "current_premium": total_credit,
                            "order_ids": execution_result["legs_filled"]
                        })
                        logger.info(f">>> [Selling] ✅ {decision['strategy']} Position Established.")
                    else:
                        logger.error(f">>> [Selling] ❌ Basket Execution FAILED for {decision['strategy']}.")

                # 3. Monitor Open Positions
                for pos in self.engine.open_positions:
                    # Update current_premium by fetching real-time LTPs
                    leg_premiums = self._fetch_leg_premiums_dict(pos["legs"])
                    # current_premium for selling is (Total Debt to close)
                    # We want to track MTM: (Entry - Current)
                    current_debt = sum(p for k, p in leg_premiums.items() if k.startswith('s')) - \
                                   sum(p for k, p in leg_premiums.items() if k.startswith('l'))
                    pos["current_premium"] = current_debt
                
                monitor_actions = self.engine.monitor_open_positions(cycle_data)
                for action in monitor_actions:
                    if action["type"] == "exit":
                        logger.info(f">>> [Selling] 🛑 EXIT TRIGGERED: {action['exit_info']['reason']}")
                        
                        # Sequential Exit: Sell Longs last? Actually, for exit, sell Shorts first to release margin?
                        # No, usually exiting all at once is fine, or Buy back shorts first.
                        if not self.dry_run:
                            logger.info(f">>> [Selling] [LIVE] Closing all legs for {action['position']['strategy']}...")
                            from bot.core.basket_manager import BasketManager
                            basket = BasketManager(self.api, dry_run=self.dry_run)
                            
                            # Update legs with current prices for exit
                            for key in action['position']['legs']:
                                action['position']['legs'][key]['current_price'] = leg_premiums.get(key, 0)

                            basket.close_basket(
                                strategy_name=action["position"]["strategy"],
                                legs=action["position"]["legs"],
                                lots=action["position"]["lots"],
                                lot_size=self.engine.config["lot_size"],
                                mode=mode
                            )
                            
                        self.engine.close_position(
                            action["position"],
                            exit_premium=action["position"]["current_premium"],
                            exit_type=action["exit_info"]["exit_type"]
                        )
                    elif action["type"] == "adjustment":
                        adj_info = action["adjustment_info"]
                        logger.info(f">>> [Selling] 🔧 ADJUSTMENT TRIGGERED: {adj_info['action']}")
                        
                        if not self.dry_run:
                            from bot.core.basket_manager import BasketManager
                            basket = BasketManager(self.api, dry_run=self.dry_run)
                            
                            # 1. Close Old Legs (the side we are rolling)
                            # Identify which legs to close based on 'side'
                            old_legs = {}
                            if adj_info["side"] == "call":
                                # Put side tested -> Roll Call DOWN
                                # Wait, the logic says 'Roll Call side DOWN'
                                # So we close sc and lc
                                old_legs = {k: v for k, v in pos["legs"].items() if k.startswith('c')}
                            else:
                                old_legs = {k: v for k, v in pos["legs"].items() if k.startswith('p')}
                            
                            logger.info(f">>> [Selling] [LIVE] Closing Old {adj_info['side']} legs...")
                            basket.close_basket(pos["strategy"], old_legs, pos["lots"], self.engine.config["lot_size"], mode="LIVE")
                            
                            # 2. Open New Legs
                            # We need to resolve tokens for the new short and its wing
                            new_strike = adj_info["new_short"]
                            opt_type = "CE" if adj_info["side"] == "call" else "PE"
                            wing_strike = new_strike + self.engine.config["ic_wing_gap"] if opt_type == "CE" else new_strike - self.engine.config["ic_wing_gap"]
                            
                            new_legs_to_open = {
                                "s_new": {"strike": new_strike, "type": opt_type},
                                "l_new": {"strike": wing_strike, "type": opt_type}
                            }
                            
                            # Resolve details
                            for k, v in new_legs_to_open.items():
                                from bot.config.settings import Config
                                from bot.config.instruments import get_instrument
                                active_sym = Config.ACTIVE_SYMBOL
                                instr = get_instrument(active_sym)
                                token, symbol = self.token_loader.get_token(active_sym, expiry, v["strike"], v["type"], instrument_type=instr.instrument_type, exchange=instr.option_exchange)
                                v.update({"token": token, "symbol": symbol, "initial_price": 10.0}) # Placeholder price
                            
                            logger.info(f">>> [Selling] [LIVE] Opening New {adj_info['side']} legs at {new_strike}...")
                            basket.execute_basket(pos["strategy"], new_legs_to_open, pos["lots"], self.engine.config["lot_size"], mode="LIVE")
                            
                            # 3. Update Engine Position State
                            # This ensures the engine monitors the NEW strikes
                            if adj_info["side"] == "call":
                                pos["short_call"] = new_strike
                                pos["long_call"] = wing_strike
                            else:
                                pos["short_put"] = new_strike
                                pos["long_put"] = wing_strike
                            
                            # Update 'legs' dict for MTM tracking
                            for k, v in new_legs_to_open.items():
                                key_in_pos = "sc" if k == "s_new" and opt_type == "CE" else \
                                             "sp" if k == "s_new" and opt_type == "PE" else \
                                             "lc" if k == "l_new" and opt_type == "CE" else "lp"
                                pos["legs"][key_in_pos] = v

                        # Mark as adjusted in engine (optional log)
                        logger.info(f">>> [Selling] ✅ Adjustment Completed for {adj_info['side']} side.")
                
                time.sleep(60)
                
            except Exception as e:
                logger.error(f">>> [Selling] Error in execution loop: {e}")
                time.sleep(10)

    def _resolve_leg_details(self, strategy, strikes, expiry):
        """Resolves strikes to full leg details (token, symbol, etc.)"""
        legs = {}
        try:
            mapping = {
                "iron_condor": {
                    "sc": (strikes["short_call"], "CE"),
                    "lc": (strikes["long_call"], "CE"),
                    "sp": (strikes["short_put"], "PE"),
                    "lp": (strikes["long_put"], "PE")
                },
                "short_strangle": {
                    "sc": (strikes["short_call"], "CE"),
                    "sp": (strikes["short_put"], "PE")
                },
                "iron_fly": {
                    "sc": (strikes["short_call"], "CE"),
                    "lc": (strikes["long_call"], "CE"),
                    "sp": (strikes["short_put"], "PE"),
                    "lp": (strikes["long_put"], "PE")
                }
            }.get(strategy, {})

            from bot.config.settings import Config
            from bot.config.instruments import get_instrument
            active_sym = Config.ACTIVE_SYMBOL
            instr = get_instrument(active_sym)
            for key, (strike, opt_type) in mapping.items():
                token, symbol = self.token_loader.get_token(active_sym, expiry, strike, opt_type, instrument_type=instr.instrument_type, exchange=instr.option_exchange)
                if not token:
                    return None
                legs[key] = {
                    "token": token,
                    "symbol": symbol,
                    "strike": strike,
                    "type": opt_type
                }
            return legs
        except Exception as e:
            logger.error(f"Error resolving leg details: {e}")
            return None

    def _fetch_leg_premiums_dict(self, legs):
        """Fetches LTPs for all legs in a dictionary."""
        from bot.core.data_fetcher import DataFetcher
        fetcher = DataFetcher(self.api)
        premiums = {}
        for key, leg in legs.items():
            ltp = fetcher.get_ltp(leg['token'], exchange="NFO")
            premiums[key] = ltp if ltp > 0 else 1.0 # Minimal fallback
        return premiums

    def stop(self):
        logger.info(">>> [Selling] Stopping Selling Engine...")
        self.running = False
