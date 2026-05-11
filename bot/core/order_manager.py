
import time
from bot.utils.logger import logger
from bot.utils.rate_limiter import rate_limiter
from bot.core.kill_switch import is_kill_switch_active
from bot.core.trade_repo import trade_repo

class OrderManager:
    def __init__(self, api, dry_run=False):
        self.api = api
        self.dry_run = dry_run
        from bot.config.settings import Config
        self.live_trade_enabled = Config.LIVE_TRADE_ENABLED

    def place_order(self, order_params, strategy_name=None, mode=None):
        """Places an order with rate limiting and error handling."""
        if is_kill_switch_active():
            logger.critical("🛑 KILL SWITCH ACTIVE. Order Rejected.")
            return None

        # Mode Safety Check
        if not self.dry_run and not self.live_trade_enabled:
            logger.warning(f"🛡️ [Safety] Order Blocked: Bot is in LIVE mode but LIVE_TRADE_ENABLED is FALSE in .env.")
            logger.warning(f"    Simulating for {order_params.get('tradingsymbol')} instead.")
            return f"DRY_SAFETY_{int(time.time())}"

        try:
            if self.dry_run:
                # Add price if available for more realistic dry run logging
                price_str = f" @ {order_params.get('price')}" if order_params.get('price') else ""
                logger.info(f"🧪 [DRY RUN] Simulating Order: {order_params.get('tradingsymbol')} {order_params.get('transactiontype')} {order_params.get('quantity')}{price_str}")
                return f"DRY_{int(time.time())}"

            rate_limiter.wait()
            response = self.api.placeOrder(order_params)
            
            if isinstance(response, dict):
                # Angel One SDK uses 'status' (True/False) or sometimes 'success'
                is_success = response.get('status') == True or response.get('success') == True
                
                if is_success:
                    oid = response.get('data', {}).get('orderid')
                    logger.info(f"✅ Order Placed Successfully: {oid}")
                    
                    # Register for WebSocket tracking
                    from bot.core.order_feed import order_feed
                    order_feed.register_order(oid)

                    # --- Persistence Integration ---
                    if strategy_name:
                        try:
                            trade_repo.save_trade(
                                symbol=order_params.get('tradingsymbol'),
                                token=order_params.get('symboltoken'),
                                leg="CE" if "CE" in order_params.get('tradingsymbol', '') else "PE",
                                qty=order_params.get('quantity'),
                                entry_price=0.0, # PLACED status
                                status="PLACED",
                                mode=mode if mode else ("PAPER" if self.dry_run else "LIVE"),
                                strategy=strategy_name
                            )
                        except Exception as e:
                            logger.error(f"Persistence Integration Error: {e}")

                    return oid
                else:
                    error_msg = response.get('message') or response.get('error_message') or "Unknown Error"
                    error_code = response.get('errorCode') or response.get('errorcode') or "???"
                    logger.error(f"❌ Order Placement Rejected: {error_msg} (Code: {error_code})")
                    return None
            
            # Mock or direct string return
            from bot.core.order_feed import order_feed
            order_feed.register_order(response)
            return response
        except Exception as e:
            logger.error(f"Order Placement Error: {e}")
            return None

    def place_limit_order(self, symbol, token, qty, price, transaction_type="BUY"):
        """
        Places a LIMIT order with optional price rounding.
        """
        try:
            # Round to 0.05 tick size
            limit_price = round(price / 0.05) * 0.05
            
            orderparams = {
                "variety": "NORMAL",
                "tradingsymbol": symbol,
                "symboltoken": token,
                "transactiontype": transaction_type,
                "exchange": "NFO",
                "ordertype": "LIMIT",
                "producttype": "INTRADAY",
                "duration": "DAY",
                "quantity": qty,
                "price": limit_price,
                "disclosedquantity": 0
            }
            
            logger.info(f"⚡ Placing LIMIT Order for {symbol} @ {limit_price}")
            return self.place_order(orderparams)
            
        except Exception as e:
            logger.error(f"Limit Order Error: {e}")
            return None

    def place_smart_limit(self, symbol, token, qty, initial_price, transaction_type="BUY", max_walk_ticks=5, strategy_name=None, mode=None):
        """
        Next-Level Execution: Places a limit order and 'walks' the price until filled.
        Reduces slippage dramatically compared to MARKET orders.
        """
        if self.dry_run or not self.live_trade_enabled:
            return self.place_limit_order(symbol, token, qty, initial_price, transaction_type)

        try:
            current_price = round(initial_price / 0.05) * 0.05
            oid = self.place_limit_order(symbol, token, qty, current_price, transaction_type)
            if not oid: return None

            # --- Persistence Integration (Early Record) ---
            if strategy_name:
                trade_repo.save_trade(
                    symbol=symbol,
                    token=token,
                    leg="CE" if "CE" in symbol else "PE",
                    qty=qty,
                    entry_price=0.0,
                    status="PLACED",
                    mode=mode if mode else ("PAPER" if self.dry_run else "LIVE"),
                    strategy=strategy_name
                )

            from bot.core.order_feed import order_feed
            
            for attempt in range(max_walk_ticks):
                # Wait for fill with shorter timeout per walk
                result = order_feed.wait_for_fill(oid, timeout=3)
                
                if result['status'] == 'FILLED':
                    logger.info(f"✨ Smart-Limit Filled: {symbol} @ {result['price']} (Attempt {attempt+1})")
                    return oid
                
                if result['status'] in ['REJECTED', 'CANCELLED']:
                    logger.error(f"❌ Smart-Limit Failed: Order {result['status']}")
                    return None

                # If TIMEOUT, walk the price one tick
                tick_size = 0.05
                if transaction_type == "BUY":
                    current_price += tick_size 
                else:
                    current_price -= tick_size
                
                logger.info(f"🚶 Walking Smart-Limit: {symbol} -> New Price: {current_price:.2f} (Attempt {attempt+2})")
                
                # Modify existing order
                success = self.modify_order_price(oid, current_price, symbol, token, qty)
                if not success:
                    logger.warning("⚠️ Walk failed: Modification error. Aborting walk.")
                    break
            
            # Final attempt: Wait longer on last price
            result = order_feed.wait_for_fill(oid, timeout=5)
            if result['status'] == 'FILLED':
                logger.info(f"✨ Smart-Limit Filled on final attempt: {symbol} @ {result['price']}")
                return oid
            
            # If still not filled, we attempt cancellation.
            logger.warning(f"⚠️ Smart-Limit timed out after walking. Status: {result.get('status')}. Attempting cancellation of order {oid}...")
            
            # --- SAFETY FALLBACK (Fixes Fill-on-Cancel Trap) ---
            cancel_status = self.cancel_order(oid, variety="NORMAL")
            
            if not cancel_status:
                logger.warning(f"⚠️ Cancel rejected for {oid}. Checking for last-microsecond fill before aborting.")
                verify_status = self.get_order_status(oid)
                if verify_status and verify_status.get('status') in ['COMPLETE', 'FILLED']:
                    logger.critical(f"🚨 FILL-ON-CANCEL DETECTED! Order {oid} filled despite timeout. Transitioning to trade.")
                    return oid
            
            return None

        except Exception as e:
            logger.error(f"Smart-Limit Error: {e}")
            return None

    def modify_order_price(self, order_id, new_price, symbol, token, qty, variety="NORMAL"):
        """Utility for Smart-Limit to change price of an open order."""
        try:
            price = round(new_price / 0.05) * 0.05
            orderparams = {
                "variety": variety,
                "orderid": order_id,
                "ordertype": "LIMIT",
                "producttype": "INTRADAY",
                "duration": "DAY",
                "price": price,
                "quantity": qty,
                "tradingsymbol": symbol,
                "symboltoken": token,
                "exchange": "NFO",
                "disclosedquantity": 0
            }
            if self.dry_run or not self.live_trade_enabled:
                logger.info(f"🧪 [DRY RUN] Simulating Modify Limit Price: {order_id} -> {price}")
                return True
            rate_limiter.wait()
            response = self.api.modifyOrder(orderparams)
            return response and response.get('status') == True
        except: return False

    def place_sl_order(self, symbol, token, qty, sl_price, leg, transaction_type="SELL"):
        """
        Places a STOPLOSS_MARKET order.
        transaction_type: "SELL" (for Long Exit) or "BUY" (for Short Exit)
        """
        if is_kill_switch_active():
            logger.critical("🛑 KILL SWITCH ACTIVE. SL Order Rejected.")
            return None

        try:
            # Round SL to 0.05 tick size
            price = round(sl_price / 0.05) * 0.05
            trigger_price = price 
            
            # Institutional Grade: Use STOPLOSS_LIMIT to prevent broker rejections and flash-crash slippage.
            # We set 'price' slightly below 'trigger_price' for SELL SL to ensure fill within a corridor.
            # Using 5% corridor to stay within exchange LPP (Limit Price Protection) rules.
            trigger_price = price
            limit_price = round(price * 0.95 / 0.05) * 0.05 if transaction_type == "SELL" else round(price * 1.05 / 0.05) * 0.05
            limit_price = round(limit_price, 2)
            
            orderparams = {
                "variety": "STOPLOSS",
                "tradingsymbol": symbol,
                "symboltoken": token,
                "transactiontype": transaction_type,
                "exchange": "NFO",
                "ordertype": "STOPLOSS_LIMIT",
                "producttype": "INTRADAY",
                "duration": "DAY",
                "quantity": qty,
                "triggerprice": trigger_price,
                "price": limit_price,
                "disclosedquantity": 0
            }
            
            logger.info(f"🛡️ Placing Broker-Side SL (SL-M) for {symbol} @ {trigger_price}")
            return self.place_order(orderparams)
            
        except Exception as e:
            logger.error(f"SL Order Error: {e}")
            return None

    def cancel_order(self, order_id, variety="NORMAL"):
        """Cancels an existing order."""
        if is_kill_switch_active():
            logger.critical("🛑 KILL SWITCH ACTIVE. Cancellation Rejected.")
            return False

        try:
            if self.dry_run or not self.live_trade_enabled:
                logger.info(f"🧪 [DRY RUN] Simulating Cancel: {order_id}")
                return True

            rate_limiter.wait()
            response = self.api.cancelOrder(order_id, variety)
            
            if isinstance(response, dict):
                if response.get('status') == True:
                    logger.info(f"🚫 Cancelled Order: {order_id}")
                    return True
                else:
                    logger.warning(f"⚠️ Cancel Failed for {order_id}: {response.get('message')}")
                    return False
            
            # Mock might return something else, assume True if no exception
            logger.info(f"🚫 Cancelled Order: {order_id}")
            return True
        except Exception as e:
            logger.error(f"Cancel Order Error: {e}")
            return False

    def modify_sl_order(self, order_id, new_trigger_price, symbol, token, qty, transaction_type="SELL"):
        """Modifies an existing SL Order."""
        if is_kill_switch_active():
            logger.critical("🛑 KILL SWITCH ACTIVE. Modification Rejected.")
            return False

        try:
            # Round SL to 0.05 tick size
            price = round(new_trigger_price / 0.05) * 0.05
            trigger_price = price
            
            # Using same corridor logic as placement to maintain institutional quality
            # Using 5% corridor to stay within exchange LPP (Limit Price Protection) rules.
            limit_price = round((price * 0.95) / 0.05) * 0.05 if transaction_type == "SELL" else round((price * 1.05) / 0.05) * 0.05
            limit_price = round(limit_price, 2)
            
            orderparams = {
                "variety": "STOPLOSS",
                "orderid": order_id,
                "ordertype": "STOPLOSS_LIMIT",
                "producttype": "INTRADAY",
                "duration": "DAY",
                "price": limit_price,
                "quantity": qty,
                "triggerprice": trigger_price,
                "tradingsymbol": symbol,
                "symboltoken": token,
                "exchange": "NFO",
                "transactiontype": transaction_type,
                "disclosedquantity": 0
            }
            if self.dry_run or not self.live_trade_enabled:
                logger.info(f"🧪 [DRY RUN] Simulating Modify: {order_id} -> {price}")
                return True

            rate_limiter.wait()
            response = self.api.modifyOrder(orderparams)
            
            if isinstance(response, dict):
                # Check API response status (modifyOrder returns full response dict)
                if response and response.get('status') == True:
                    logger.info(f"📝 Modified SL Order {order_id} -> {price}")
                    return True
                else:
                    err_msg = response.get('message', 'Unknown error') if response else 'No response'
                    logger.warning(f"⚠️ SL Modify Failed for {order_id}: {err_msg}")
                    return False
            
            # Mock might return something else, assume True if no exception
            logger.info(f"📝 Modified SL Order {order_id} -> {price}")
            return True
        except Exception as e:
            logger.error(f"Modify SL Error: {e}")
            return False

    # --- Throttled API Read Methods ---

    def get_order_book(self):
        """Fetches order book with rate limiting."""
        try:
            if self.dry_run: return {"status": True, "data": []}
            rate_limiter.wait()
            res = self.api.orderBook()
            if res is None:
                logger.error("OrderBook Fetch Error: API returned None")
            return res
        except Exception as e:
            logger.error(f"OrderBook Fetch Exception: {e}")
            return None

    def get_positions(self):
        """Fetches positions with rate limiting."""
        try:
            if self.dry_run: return {"status": True, "data": []}
            rate_limiter.wait()
            res = self.api.position()
            if res is None:
                 logger.error("Position Fetch Error: API returned None")
            return res
        except Exception as e:
            logger.error(f"Position Fetch Exception: {e}")
            return None

    def get_rms_limit(self):
        """Fetches RMS limits (funds) with rate limiting."""
        try:
            if self.dry_run: return {"status": True, "data": {"net": "1000000"}}
            rate_limiter.wait()
            res = self.api.rmsLimit()
            if res is None:
                logger.error("RMS Limit Fetch Error: API returned None")
            return res
        except Exception as e:
            logger.error(f"RMS Limit Fetch Exception: {e}")
            return None

    def update_trade_fill(self, symbol, strategy_name, fill_price, expected_price=None):
        """Helper to unify fill updates across strategies."""
        try:
            db_trade = trade_repo.get_active_trade(strategy=strategy_name, symbol=symbol)
            if db_trade:
                trade_repo.update_entry_price(db_trade['id'], fill_price, expected_price=expected_price)
                return db_trade['id']
            return None
        except Exception as e:
            logger.error(f"update_trade_fill Error: {e}")
            return None

    def get_order_status(self, order_id):
        """
        Queries the broker for the status of a specific order ID.
        """
        try:
            if self.dry_run: return {"status": "FILLED", "price": 0.0}
            
            res = self.get_order_book()
            if res and res.get('status') == True:
                orders = res.get('data', [])
                for order in orders:
                    if order.get('orderid') == str(order_id):
                        status = order.get('status', '').lower()
                        price = float(order.get('averageprice', 0))
                        return {"status": status.upper(), "price": price}
            return None
        except Exception as e:
            logger.error(f"get_order_status Error: {e}")
            return None

