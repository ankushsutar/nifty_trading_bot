
import time
from bot.utils.logger import logger
from bot.utils.rate_limiter import rate_limiter

class OrderManager:
    def __init__(self, api):
        self.api = api

    def place_order(self, order_params):
        """Places an order with rate limiting and error handling."""
        try:
            rate_limiter.wait()
            oid = self.api.placeOrder(order_params)
            return oid
        except Exception as e:
            logger.error(f"Order Placement Error: {e}")
            return None

    def place_sl_order(self, symbol, token, qty, sl_price, leg, transaction_type="SELL"):
        """
        Places a STOPLOSS_MARKET order.
        transaction_type: "SELL" (for Long Exit) or "BUY" (for Short Exit)
        """
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

    def cancel_order(self, order_id, variety="STOPLOSS"):
        """Cancels an order."""
        try:
            rate_limiter.wait()
            self.api.cancelOrder(order_id, variety)
            logger.info(f"🚫 Cancelled Order: {order_id}")
            return True
        except Exception as e:
            logger.error(f"Cancel Order Error: {e}")
            return False

    def modify_sl_order(self, order_id, new_trigger_price, symbol, token, qty):
        """Modifies an existing SL Order."""
        try:
            price = round(new_trigger_price / 0.05) * 0.05
            
            orderparams = {
                "variety": "STOPLOSS",
                "orderid": order_id,
                "ordertype": "MARKET",
                "producttype": "INTRADAY",
                "duration": "DAY",
                "price": price,
                "quantity": qty,
                "triggerprice": price,
                "tradingsymbol": symbol,
                "symboltoken": token,
                "exchange": "NFO"
            }
            rate_limiter.wait()
            response = self.api.modifyOrder(orderparams)
            # Check API response status (modifyOrder returns full response dict)
            if response and response.get('status') == True:
                logger.info(f"📝 Modified SL Order {order_id} -> {price}")
                return True
            else:
                err_msg = response.get('message', 'Unknown error') if response else 'No response'
                logger.warning(f"⚠️ SL Modify Failed for {order_id}: {err_msg}")
                return False
        except Exception as e:
            logger.error(f"Modify SL Error: {e}")
            return False
