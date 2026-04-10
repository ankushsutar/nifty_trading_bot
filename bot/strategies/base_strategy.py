import time
import datetime
import pandas as pd
from bot.config.settings import Config
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.trade_repo import trade_repo
from bot.core.data_fetcher import DataFetcher
from bot.core.order_manager import OrderManager
from bot.core.oi_analyzer import OIAnalyzer
from bot.core.regime_classifier import RegimeClassifier
from bot.utils.logger import logger
from bot.config.instruments import get_instrument

class BaseStrategy:
    """
    Universal Base Strategy class to standardize API connections,
    state sync, order execution, and error handling.
    """
    def __init__(self, api, token_loader, strategy_name, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.strategy_name = strategy_name
        self.dry_run = dry_run
        
        # Centralized Core Services
        self.gatekeeper = SafetyGatekeeper(self.api, dry_run=self.dry_run)
        self.data_fetcher = DataFetcher(self.api)
        self.order_manager = OrderManager(self.api, dry_run=self.dry_run)
        self.oi_analyzer = OIAnalyzer(self.api, self.token_loader)
        self.regime_classifier = RegimeClassifier()
        
        # Standard State
        self.running = True
        self.active_position = None
        self.last_sync_time = 0
        self.risk_multiplier = 1.0
        self._last_sl_hit_time = 0
        self._last_status_log = 0

    def sync_state(self):
        """
        Standardized state synchronization from Broker API.
        Attempts to recover active position from DB and confirm via Broker.
        """
        mode = "PAPER" if self.dry_run else "LIVE"
        
        if self.dry_run:
            if self.active_position is None:
                db_trade = trade_repo.get_active_trade(mode=mode, strategy=self.strategy_name)
                if db_trade:
                    self.active_position = {
                        'id': db_trade['id'],
                        'leg': db_trade.get('leg'),
                        'symbol': db_trade['symbol'],
                        'token': db_trade['token'],
                        'qty': db_trade['qty'],
                        'entry_price': db_trade['entry_price'],
                        'sl_price': db_trade['sl_price'],
                        'sl_order_id': db_trade.get('sl_order_id')
                    }
                    logger.info(f"♻️ [{self.strategy_name}] PAPER RECOVERY: Found Active Trade in DB! {db_trade['symbol']}")
            return

        try:
            from bot.utils.rate_limiter import rate_limiter
            rate_limiter.wait()
            pos_resp = self.order_manager.get_positions()
            
            if pos_resp is None or not pos_resp.get('status'):
                logger.warning(f"⚠️ [{self.strategy_name}] Sync State: API failure. Skipping sync to preserve local state.")
                return

            found_active = None
            pos_data = pos_resp.get('data') or []
            instr = get_instrument(Config.ACTIVE_SYMBOL)
            
            for pos in pos_data:
                if (pos.get('symbolname') == instr.name and 
                    pos.get('producttype') == 'INTRADAY' and 
                    int(pos.get('netqty', 0)) != 0):
                    
                    qty = int(pos['netqty'])
                    found_active = {
                        'leg': "CE" if "CE" in pos['tradingsymbol'] else "PE", 
                        'symbol': pos['tradingsymbol'],
                        'token': pos['symboltoken'],
                        'qty': abs(qty),
                        'entry_price': float(pos['avgnetprice']),
                        'sl_price': float(pos['avgnetprice']) * 0.8 # Default fallback
                    }
                    
                    db_trade = trade_repo.get_active_trade(mode="LIVE", strategy=self.strategy_name, symbol=found_active['symbol'])
                    if db_trade:
                        found_active['id'] = db_trade['id']
                        found_active['sl_price'] = db_trade.get('sl_price', found_active['sl_price'])
                        logger.info(f"♻️ [{self.strategy_name}] RECOVERY: Linked to DB Trade #{db_trade['id']}")
                    
                    if self.active_position is None:
                        logger.info(f"♻️ [{self.strategy_name}] RECOVERY: Found Active Trade on Broker! {found_active['symbol']}")
                    break 
            
            if found_active:
                self.active_position = found_active
            elif self.active_position is not None:
                logger.warning(f"⚠️ [{self.strategy_name}] SYNC: Position closed externally! Resetting State.")
                trade_repo.close_trade(symbol=self.active_position['symbol'])
                self.active_position = None
                    
        except Exception as e:
            logger.error(f"[{self.strategy_name}] Sync State Error: {e}")

    def place_entry(self, expiry, strike, option_type, qty, quote_ltp, transaction_type="BUY"):
        """
        Unified utility for calculating entry slippage, checking margins,
        and placing smart-limit entry orders with robust timeout and DB cleanup.
        """
        instr = get_instrument(Config.ACTIVE_SYMBOL)
        token, symbol = self.token_loader.get_token(instr.name, expiry, strike, option_type, instrument_type=instr.instrument_type, exchange=instr.exchange)
        if not token:
            logger.error(f"[{self.strategy_name}] Token not found for {strike} {option_type}")
            return None, None, None

        estimated_cost = quote_ltp * qty
        if not self.gatekeeper.check_trade_margin(estimated_cost):
            logger.warning(f"[{self.strategy_name}] ❌ Margin check failed. Need ₹{estimated_cost:,.0f}.")
            return None, None, None

        _entry_tier = Config.get_tier(self.gatekeeper.get_current_capital())
        limit_price = round(quote_ltp * (1.0 + _entry_tier.entry_slippage_pct), 1)
        
        logger.info(f">>> [Trade] [{self.strategy_name}] Entering {symbol} (Qty: {qty}) @ ₹{limit_price}")
        
        oid = self.order_manager.place_smart_limit(
            symbol, token, qty, limit_price, 
            transaction_type=transaction_type, 
            strategy_name=self.strategy_name,
            exchange=instr.exchange
        )
        if not oid: return None, None, None

        # 2. Wait for fill (WebSocket or REST fallback)
        fill_result = self.wait_for_fill(oid)
        
        # FINAL REST FALLBACK IF TIMEOUT
        if fill_result['status'] == 'TIMEOUT' and not self.dry_run:
            logger.info(f"[{self.strategy_name}] ⏳ Order {oid} timed out. Doing one final REST API check...")
            try:
                ob_res = self.api.orderBook()
                if ob_res and ob_res.get('status'):
                    for ord_info in ob_res.get('data', []):
                        if ord_info.get('orderid') == oid:
                            rest_status = ord_info.get('status', '').lower()
                            if rest_status == 'complete':
                                logger.info(f"[{self.strategy_name}] ✅ Order {oid} actually FILLED on REST check!")
                                try:
                                    avg_price = float(ord_info.get('averageprice') or 0)
                                except (ValueError, TypeError):
                                    avg_price = 0.0
                                fill_result = {'status': 'FILLED', 'price': avg_price}
                            break
            except Exception as e:
                logger.error(f"[{self.strategy_name}] Final REST Check Error: {e}")

        if fill_result['status'] != 'FILLED':
            logger.warning(f"[{self.strategy_name}] Entry failed or timed out permanently. Status: {fill_result['status']}")
            
            cancel_success = True
            if fill_result['status'] == 'TIMEOUT':
                cancel_success = self.order_manager.cancel_order(oid, variety="NORMAL")
                
            if cancel_success or fill_result['status'] in ['REJECTED', 'CANCELLED']:
                failed_trade = trade_repo.get_active_trade(strategy=self.strategy_name, symbol=symbol)
                if failed_trade:
                    trade_repo.collection.delete_one({"id": failed_trade['id']})
                    logger.info(f"[{self.strategy_name}] Cleaned up failed entry record #{failed_trade['id']} from DB.")
                return None, symbol, None
            else:
                logger.critical(f"[{self.strategy_name}] 🚨 DANGER! Order {oid} timed out but CANCEL FAILED! Assuming filled.")
                fill_result = {'status': 'FILLED', 'price': limit_price} 
                
        fill_price = fill_result['price']

        if not fill_price or fill_price <= 0:
            logger.warning(f"[{self.strategy_name}] fill_price is 0 — using limit_price {limit_price} as fallback.")
            fill_price = limit_price

        # 3. Update Trade with Actual Fill
        _tier = Config.get_tier(self.gatekeeper.get_current_capital())
        sl_price = round(fill_price * (1 - _tier.sl_pct), 1)

        trade_id = self.order_manager.update_trade_fill(symbol, self.strategy_name, fill_price, expected_price=quote_ltp)
        if trade_id:
            trade_repo.update_sl(trade_id, sl_price)
        else:
            logger.warning(f"[{self.strategy_name}] Could not link fill to DB record.")

        return oid, symbol, fill_price

    def wait_for_fill(self, order_id):
        """Uses WebSocket Order Feed for sub-second fill detection if available, else polls."""
        if self.dry_run: return {'status': 'FILLED', 'price': 50.0}
        
        try:
            from bot.core.order_feed import order_feed
            logger.info(f">>> [{self.strategy_name}] Waiting for WebSocket Fill Event ({order_id})...")
            result = order_feed.wait_for_fill(order_id, timeout=30)
            if result['status'] == 'TIMEOUT':
                 logger.warning(f"⚠️ Order {order_id} fill TIMEOUT via WebSocket.")
            # Returns a dict with status & price
            return result
        except ImportError:
            # Fallback
            for _ in range(12):
                status = self.order_manager.get_order_status(order_id)
                if status and status.lower() in ['complete', 'completed']:
                    price = self.order_manager.get_fill_price(order_id)
                    return {'status': 'FILLED', 'price': price}
                time.sleep(0.5)
            return {'status': 'TIMEOUT', 'price': 0.0}

    def stop(self):
        """Gracefully shuts down the strategy loop."""
        logger.info(f"[{self.strategy_name}] Stopping strategy loop...")
        self.running = False
