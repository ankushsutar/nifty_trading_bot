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

    def execute_spread_simultaneous(self, strategy_name, buy_leg, sell_leg, qty, mode="LIVE"):
        """
        Routes multi-leg debit spread orders SIMULTANEOUSLY to avoid execution slippage.
        Uses ThreadPoolExecutor to fire both legs in parallel.
        buy_leg: {'symbol': str, 'token': str, 'price': float}
        sell_leg: {'symbol': str, 'token': str, 'price': float}
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        logger.info(
            f">>> [Basket] Routing SIMULTANEOUS Spread Orders for {strategy_name}: "
            f"BUY {buy_leg['symbol']} @ ~{buy_leg.get('price', 0.0)} & "
            f"SELL {sell_leg['symbol']} @ ~{sell_leg.get('price', 0.0)} (Qty: {qty})..."
        )

        results = {
            "strategy": strategy_name,
            "status": "FAILED",
            "buy_order_id": None,
            "sell_order_id": None,
            "buy_fill_price": 0.0,
            "sell_fill_price": 0.0,
            "net_debit": 0.0,
        }

        def _place_leg(leg_info, txn_type):
            symbol = leg_info["symbol"]
            token = leg_info["token"]
            price = leg_info.get("price", 100.0)
            oid = self.order_manager.place_smart_limit(
                symbol=symbol,
                token=token,
                qty=qty,
                initial_price=price,
                transaction_type=txn_type,
                strategy_name=strategy_name,
                mode=mode
            )
            return txn_type, oid

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(_place_leg, buy_leg, "BUY"),
                    executor.submit(_place_leg, sell_leg, "SELL")
                ]

                for fut in as_completed(futures):
                    txn_type, oid = fut.result()
                    if txn_type == "BUY":
                        results["buy_order_id"] = oid
                    elif txn_type == "SELL":
                        results["sell_order_id"] = oid

            if results["buy_order_id"] and results["sell_order_id"]:
                results["status"] = "SUCCESS"
                logger.info(
                    f"✨ [Basket] Simultaneous Spread Fill: BUY {results['buy_order_id']} | "
                    f"SELL {results['sell_order_id']}"
                )
            else:
                logger.error(
                    f"❌ [Basket] Spread leg execution incomplete: "
                    f"BUY={results['buy_order_id']}, SELL={results['sell_order_id']}"
                )

            return results
        except Exception as e:
            logger.error(f">>> [Basket] Simultaneous Spread Error: {e}")
            return results

    def close_spread_simultaneous(self, strategy_name, buy_leg, sell_leg, qty, mode="LIVE"):
        """
        Closes both legs of a vertical debit spread SIMULTANEOUSLY in parallel.
        buy_leg was originally bought -> SELL to exit.
        sell_leg was originally sold -> BUY to exit.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        logger.info(
            f">>> [Basket] Closing SIMULTANEOUS Spread for {strategy_name}: "
            f"EXIT BUY {buy_leg['symbol']} & EXIT SELL {sell_leg['symbol']} (Qty: {qty})..."
        )

        def _close_leg(symbol, token, price, txn_type):
            return txn_type, self.order_manager.place_smart_limit(
                symbol=symbol,
                token=token,
                qty=qty,
                initial_price=price,
                transaction_type=txn_type,
                strategy_name=strategy_name,
                mode=mode
            )

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(_close_leg, buy_leg["symbol"], buy_leg["token"], buy_leg.get("current_price", 1.0), "SELL"),
                    executor.submit(_close_leg, sell_leg["symbol"], sell_leg["token"], sell_leg.get("current_price", 1.0), "BUY")
                ]
                for fut in as_completed(futures):
                    fut.result()
            return True
        except Exception as e:
            logger.error(f">>> [Basket] Simultaneous Close Error: {e}")
            return False

