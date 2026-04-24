
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
        self._slippage_adjustment = 0.0
        self._last_slippage_check = 0

    def _get_tick_size(self, exchange):
        """Returns the minimum price variation (tick size) for the exchange."""
        # MCX Crude/Gold = 1.0, Nifty/NSE Options = 0.05
        return 1.0 if exchange == "MCX" else 0.05

    def _round_to_tick(self, price, exchange):
        """Rounds a price to the nearest valid tick size for the exchange."""
        tick = self._get_tick_size(exchange)
        # Round to 2 decimal places to avoid floating point artifacts (e.g. 5400.0000000001)
        return round(round(price / tick) * tick, 2)

    def _get_slippage_adjustment(self):
        """Fetches recent slippage stats to adjust execution aggressiveness."""
        now = time.time()
        # Refresh every 30 minutes
        if now - self._last_slippage_check < 1800:
            return self._slippage_adjustment
        
        try:
            stats = trade_repo.get_slippage_stats(days=3)
            avg_pct = stats.get('avg_slippage_pct', 0.0)
            # If slippage is > 1.5%, we increase the walk sensitivity
            if avg_pct > 1.5:
                self._slippage_adjustment = 0.5  # Boost factor
                logger.info(f"⚡ [OrderManager] High Slippage detected ({avg_pct:.2f}%). Execution adjustment enabled.")
            else:
                self._slippage_adjustment = 0.0
            self._last_slippage_check = now
        except Exception:
            self._slippage_adjustment = 0.0
        
        return self._slippage_adjustment

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
                if response.get('status') == True:
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
                    logger.error(f"❌ Order Placement Rejected: {response.get('message')}")
                    return None
            
            # Mock or direct string return
            from bot.core.order_feed import order_feed
            order_feed.register_order(response)
            return response
        except Exception as e:
            logger.error(f"Order Placement Error: {e}")
            return None

    def place_limit_order(self, symbol, token, qty, price, transaction_type="BUY", exchange="NFO", producttype="INTRADAY"):
        """
        Places a LIMIT order with optional price rounding.
        producttype: "INTRADAY" for NSE options, "CARRYFORWARD" for MCX futures.
        """
        try:
            # Round to exchange-specific tick size
            limit_price = self._round_to_tick(price, exchange)

            orderparams = {
                "variety": "NORMAL",
                "tradingsymbol": symbol,
                "symboltoken": token,
                "transactiontype": transaction_type,
                "exchange": exchange,
                "ordertype": "LIMIT",
                "producttype": producttype,
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

    def place_smart_limit(self, symbol, token, qty, initial_price, transaction_type="BUY", max_walk_ticks=5, strategy_name=None, mode=None, exchange="NFO", producttype="INTRADAY"):
        """
        Next-Level Execution: Places a limit order and 'walks' the price until filled.
        Reduces slippage dramatically compared to MARKET orders.
        producttype: "INTRADAY" for NSE options, "CARRYFORWARD" for MCX futures.
        """
        if self.dry_run or not self.live_trade_enabled:
            return self.place_limit_order(symbol, token, qty, initial_price, transaction_type, exchange=exchange, producttype=producttype)

        try:
            current_price = self._round_to_tick(initial_price, exchange)
            oid = self.place_limit_order(symbol, token, qty, current_price, transaction_type, exchange=exchange, producttype=producttype)
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

            # --- Execution Feedback Loop Adjustment ---
            adj = self._get_slippage_adjustment()
            walk_limit = max_walk_ticks + (2 if adj > 0 else 0)
            if adj > 0:
                logger.info(f"🚶 [Execution] Slippage Boost: Increasing max_walk_ticks to {walk_limit}")

            for attempt in range(walk_limit):
                # Wait for fill with shorter timeout per walk
                result = order_feed.wait_for_fill(oid, timeout=3)

                if result['status'] == 'FILLED':
                    logger.info(f"✨ Smart-Limit Filled: {symbol} @ {result['price']} (Attempt {attempt+1})")
                    return oid

                if result['status'] in ['REJECTED', 'CANCELLED']:
                    logger.error(f"❌ Smart-Limit Failed: Order {result['status']}")
                    return None

                # If TIMEOUT, walk the price one tick
                tick_size = self._get_tick_size(exchange)
                if transaction_type == "BUY":
                    current_price += tick_size
                else:
                    current_price -= tick_size
                
                # Ensure rounding after walk
                current_price = self._round_to_tick(current_price, exchange)

                logger.info(f"🚶 Walking Smart-Limit: {symbol} -> New Price: {current_price:.2f} (Attempt {attempt+2})")

                # Modify existing order — producttype must match original
                success = self.modify_order_price(oid, current_price, symbol, token, qty, exchange=exchange, producttype=producttype)
                if not success:
                    logger.warning("⚠️ Walk failed: Modification error. Aborting walk.")
                    break
            
            # Final attempt: Wait longer on last price
            result = order_feed.wait_for_fill(oid, timeout=5)
            if result['status'] == 'FILLED':
                logger.info(f"✨ Smart-Limit Filled on final attempt: {symbol} @ {result['price']}")
                return oid
            
            # If still not filled, we should probably cancel it to avoid ghost entries
            logger.warning(f"⚠️ Smart-Limit timed out after walking. Status: {result.get('status')}. Cancelling order {oid} for safety.")
            self.cancel_order(oid, variety="NORMAL")
            return None

        except Exception as e:
            logger.error(f"Smart-Limit Error: {e}")
            return None

    def modify_order_price(self, order_id, new_price, symbol, token, qty, variety="NORMAL", exchange="NFO", producttype="INTRADAY"):
        """Utility for Smart-Limit to change price of an open order.
        producttype must match the original order (INTRADAY or CARRYFORWARD).
        """
        try:
            price = self._round_to_tick(new_price, exchange)
            orderparams = {
                "variety": variety,
                "orderid": order_id,
                "ordertype": "LIMIT",
                "producttype": producttype,
                "duration": "DAY",
                "price": price,
                "quantity": qty,
                "tradingsymbol": symbol,
                "symboltoken": token,
                "exchange": exchange,
                "disclosedquantity": 0
            }
            if self.dry_run or not self.live_trade_enabled:
                logger.info(f"🧪 [DRY RUN] Simulating Modify Limit Price: {order_id} -> {price}")
                return True
            rate_limiter.wait()
            response = self.api.modifyOrder(orderparams)
            return response and response.get('status') == True
        except: return False

    def place_sl_order(self, symbol, token, qty, sl_price, leg, transaction_type="SELL", exchange="NFO", producttype="INTRADAY"):
        """
        Places a STOPLOSS_LIMIT order.
        transaction_type: "SELL" (long exit) or "BUY" (short exit).
        producttype: "INTRADAY" for NSE options, "CARRYFORWARD" for MCX futures.
        leg: option type label (CE/PE/BUY/SELL) — used for logging only.
        """
        if is_kill_switch_active():
            logger.critical("🛑 KILL SWITCH ACTIVE. SL Order Rejected.")
            return None

        try:
            # Round trigger to exchange-specific tick size
            trigger_price = self._round_to_tick(sl_price, exchange)

            # Corridor keeps the limit within exchange LPP (Limit Price Protection) rules.
            # SELL SL (long exit): limit slightly below trigger so it fills on the way down.
            # BUY  SL (short exit): limit slightly above trigger so it fills on the way up.
            limit_raw = trigger_price * 0.95 if transaction_type == "SELL" else trigger_price * 1.05
            limit_price = self._round_to_tick(limit_raw, exchange)

            orderparams = {
                "variety": "STOPLOSS",
                "tradingsymbol": symbol,
                "symboltoken": token,
                "transactiontype": transaction_type,
                "exchange": exchange,
                "ordertype": "STOPLOSS_LIMIT",
                "producttype": producttype,
                "duration": "DAY",
                "quantity": qty,
                "triggerprice": trigger_price,
                "price": limit_price,
                "disclosedquantity": 0
            }

            logger.info(f"🛡️ Placing Broker-Side SL [{leg}] for {symbol} @ {trigger_price}")
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

    def modify_sl_order(self, order_id, new_trigger_price, symbol, token, qty, variety="STOPLOSS", exchange="NFO", transaction_type="SELL", producttype=None):
        """Modifies an existing SL Order.
        transaction_type: "SELL" for long-position SL, "BUY" for short-position SL.
        producttype: if None, derived from exchange (CARRYFORWARD for MCX, INTRADAY for NSE).
        """
        if is_kill_switch_active():
            logger.critical("🛑 KILL SWITCH ACTIVE. Modification Rejected.")
            return False

        try:
            # Round trigger to exchange-specific tick size
            trigger_price = self._round_to_tick(new_trigger_price, exchange)

            # Corridor keeps the limit within exchange LPP (Limit Price Protection) rules.
            # SELL SL (long exit): limit slightly below trigger so it fills on the way down.
            # BUY  SL (short exit): limit slightly above trigger so it fills on the way up.
            limit_raw = trigger_price * 0.95 if transaction_type == "SELL" else trigger_price * 1.05
            limit_price = self._round_to_tick(limit_raw, exchange)
            
            # Dynamic Product Type: Must match original order (CARRYFORWARD for MCX)
            if producttype is None:
                producttype = "CARRYFORWARD" if exchange == "MCX" else "INTRADAY"

            orderparams = {
                "variety": variety,
                "orderid": order_id,
                "ordertype": "STOPLOSS_LIMIT",
                "producttype": producttype,
                "duration": "DAY",
                "price": limit_price,
                "quantity": qty,
                "triggerprice": trigger_price,
                "tradingsymbol": symbol,
                "symboltoken": token,
                "exchange": exchange,
                "disclosedquantity": 0
            }
            if self.dry_run or not self.live_trade_enabled:
                logger.info(f"🧪 [DRY RUN] Simulating Modify: {order_id} -> {trigger_price}")
                return True

            rate_limiter.wait()
            response = self.api.modifyOrder(orderparams)
            
            if isinstance(response, dict):
                # Check API response status (modifyOrder returns full response dict)
                if response and response.get('status') == True:
                    logger.info(f"📝 Modified SL Order {order_id} -> {trigger_price}")
                    return True
                else:
                    err_msg = response.get('message', 'Unknown error') if response else 'No response'
                    logger.warning(f"⚠️ SL Modify Failed for {order_id}: {err_msg}")
                    return False
            
            # Mock might return something else, assume True if no exception
            logger.info(f"📝 Modified SL Order {order_id} -> {trigger_price}")
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
