import time
from bot.utils.logger import logger
from bot.core.order_manager import OrderManager

class BasketManager:
    """
    Handles multi-leg execution for options strategies.
    Ensures margin-efficient execution by buying wings before selling shorts.
    """
    def __init__(self, api, dry_run=False):
        self.order_manager = OrderManager(api, dry_run=dry_run)
        self.dry_run = dry_run

    def execute_basket(self, strategy_name, legs, lots, lot_size, mode="PAPER"):
        """
        Executes a basket of orders.
        legs: dict of { 'sc': {token, symbol, strike, type}, ... }
        """
        logger.info(f">>> [Basket] Starting Execution for {strategy_name} ({lots} lots)...")
        
        results = {
            "strategy": strategy_name,
            "status": "FAILED",
            "legs_filled": {},
            "total_premium": 0.0
        }

        try:
            # 1. Sort Legs: LONG (Buy) legs MUST go first for margin benefit
            long_legs = {k: v for k, v in legs.items() if k.startswith('l')}
            short_legs = {k: v for k, v in legs.items() if k.startswith('s')}

            # --- PHASE 1: BUY WINGS ---
            for key, leg in long_legs.items():
                logger.info(f">>> [Basket] Phase 1: Buying Wing {leg['symbol']}...")
                # Wings are usually far OTM, so we use Smart Limit but with wider walk for speed
                # In dry run, this is instant. In live, it walks.
                qty = lots * lot_size
                oid = self.order_manager.place_smart_limit(
                    symbol=leg['symbol'],
                    token=leg['token'],
                    qty=qty,
                    initial_price=1.0, # Placeholder, in reality we'd fetch LTP
                    transaction_type="BUY",
                    strategy_name=strategy_name,
                    mode=mode
                )
                if not oid:
                    logger.error(f">>> [Basket] CRITICAL: Failed to fill Wing {leg['symbol']}. Aborting basket.")
                    return results
                results["legs_filled"][key] = oid

            # --- PHASE 2: SELL SHORTS ---
            for key, leg in short_legs.items():
                logger.info(f">>> [Basket] Phase 2: Selling Short {leg['symbol']}...")
                qty = lots * lot_size
                # Short legs are where we want the best credit
                oid = self.order_manager.place_smart_limit(
                    symbol=leg['symbol'],
                    token=leg['token'],
                    qty=qty,
                    initial_price=100.0, # Placeholder, fetched in wrapper
                    transaction_type="SELL",
                    strategy_name=strategy_name,
                    mode=mode
                )
                if not oid:
                    logger.error(f">>> [Basket] CRITICAL: Failed to fill Short {leg['symbol']}. Manual intervention required!")
                    # Note: We don't abort here if wings are already bought, we just log failure.
                    # In production, we'd need a recovery loop.
                    continue
                results["legs_filled"][key] = oid

            results["status"] = "SUCCESS"
            return results
        except Exception as e:
            logger.error(f">>> [Basket] Execution Error: {e}")
            return results

    def close_basket(self, strategy_name, legs, lots, lot_size, mode="PAPER"):
        """
        Closes a multi-leg position.
        Shorts should be closed FIRST to release margin and lock in profit.
        """
        logger.info(f">>> [Basket] Closing {strategy_name} ({lots} lots)...")
        
        # 1. Sort Legs: SHORT legs FIRST
        short_legs = {k: v for k, v in legs.items() if k.startswith('s')}
        long_legs = {k: v for k, v in legs.items() if k.startswith('l')}

        for key, leg in short_legs.items():
            logger.info(f">>> [Basket] Closing Short {leg['symbol']}...")
            qty = lots * lot_size
            self.order_manager.place_smart_limit(
                symbol=leg['symbol'],
                token=leg['token'],
                qty=qty,
                initial_price=leg.get('current_price', 1.0),
                transaction_type="BUY",
                strategy_name=strategy_name,
                mode=mode
            )

        for key, leg in long_legs.items():
            logger.info(f">>> [Basket] Closing Wing {leg['symbol']}...")
            qty = lots * lot_size
            self.order_manager.place_smart_limit(
                symbol=leg['symbol'],
                token=leg['token'],
                qty=qty,
                initial_price=leg.get('current_price', 1.0),
                transaction_type="SELL",
                strategy_name=strategy_name,
                mode=mode
            )

        return True
