
import time
from bot.utils.logger import logger
from bot.utils.rate_limiter import rate_limiter
from bot.core.kill_switch import is_kill_switch_active

class OrderManager:
    def __init__(self, api, dry_run=False):
        self.api = api
        self.dry_run = dry_run

    def place_order(self, order_params):
        """Places an order with rate limiting and error handling."""
        if is_kill_switch_active():
            logger.critical("🛑 KILL SWITCH ACTIVE. Order Rejected.")
            return None

        try:
            if self.dry_run:
                logger.info(f"🧪 [DRY RUN] Simulating Order: {order_params.get('tradingsymbol')} {order_params.get('transactiontype')} {order_params.get('quantity')}")
                return f"DRY_{int(time.time())}"

            rate_limiter.wait()
            response = self.api.placeOrder(order_params)
            
            if isinstance(response, dict):
                if response.get('status') == True:
                    oid = response.get('data', {}).get('orderid')
                    logger.info(f"✅ Order Placed Successfully: {oid}")
                    return oid
                else:
                    logger.error(f"❌ Order Placement Rejected: {response.get('message')}")
                    return None
            return response # Mock returns string ID directly
        except Exception as e:
            logger.error(f"Order Placement Error: {e}")
            return None

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
            
            orderparams = {
                "variety": "STOPLOSS",
                "tradingsymbol": symbol,
                "symboltoken": token,
                "transactiontype": transaction_type, 
                "exchange": "NFO",
                "ordertype": "MARKET", # SL-M
                "producttype": "INTRADAY",
                "duration": "DAY",
                "quantity": qty,
                "triggerprice": trigger_price, # Most Important for SL
                "price": 0  # Must be 0 for SL-Market orders per Angel One API spec
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
            if self.dry_run:
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

    def modify_sl_order(self, order_id, new_trigger_price, symbol, token, qty):
        """Modifies an existing SL Order."""
        if is_kill_switch_active():
            logger.critical("🛑 KILL SWITCH ACTIVE. Modification Rejected.")
            return False

        try:
            price = round(new_trigger_price / 0.05) * 0.05
            
            orderparams = {
                "variety": "STOPLOSS",
                "orderid": order_id,
                "ordertype": "MARKET",
                "producttype": "INTRADAY",
                "duration": "DAY",
                "price": 0,           # SL-M: price MUST be 0 (non-zero = SL-Limit, wrong order type)
                "quantity": qty,
                "triggerprice": price,
                "tradingsymbol": symbol,
                "symboltoken": token,
                "exchange": "NFO"
            }
            if self.dry_run:
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
            return self.api.orderBook()
        except Exception as e:
            logger.error(f"OrderBook Fetch Error: {e}")
            return None

    def get_positions(self):
        """Fetches positions with rate limiting."""
        try:
            if self.dry_run: return {"status": True, "data": []}
            rate_limiter.wait()
            return self.api.position()
        except Exception as e:
            logger.error(f"Position Fetch Error: {e}")
            return None

    def get_rms_limit(self):
        """Fetches RMS limits (funds) with rate limiting."""
        try:
            if self.dry_run: return {"status": True, "data": {"net": "1000000"}}
            rate_limiter.wait()
            return self.api.rmsLimit()
        except Exception as e:
            logger.error(f"RMS Limit Fetch Error: {e}")
            return None
