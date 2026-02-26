import time
import datetime
from bot.config.settings import Config
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.trade_repo import trade_repo
from bot.core.oi_analyzer import OIAnalyzer
from bot.core.order_manager import OrderManager
from bot.core.data_fetcher import DataFetcher
from bot.utils.logger import logger

class NiftyStrategy:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(self.api, dry_run=self.dry_run)
        self.order_manager = OrderManager(self.api, dry_run=self.dry_run)
        self.data_fetcher = DataFetcher(self.api)
        self.oi_analyzer = OIAnalyzer(self.api, self.token_loader)
        
        self.sl_orders = {} # { 'CE': order_id, 'PE': order_id }
        self.entry_prices = {} # { 'CE': price, 'PE': price }
        self.legs_active = {'CE': False, 'PE': False}
        self.leg_metadata = {'CE': None, 'PE': None} 
        self.running = True
        self._ltp_cache = {} 

    def execute(self, expiry, action="SELL"): 
        """
        Executes the 9:20 Straddle (Short ATM CE & PE).
        """
        logger.info(f"--- 9:20 STRADDLE STRATEGY ({expiry}) ---")

        # Check for Resumption
        if self.resume():
            logger.info(">>> [Resumption] Resuming Monitoring...")
            ce_meta = self.leg_metadata.get('CE')
            pe_meta = self.leg_metadata.get('PE')
            
            ce_token = ce_meta['token'] if ce_meta else None
            pe_token = pe_meta['token'] if pe_meta else None
            ce_symbol = ce_meta['symbol'] if ce_meta else None
            pe_symbol = pe_meta['symbol'] if pe_meta else None
            qty = (ce_meta['qty'] if ce_meta else pe_meta['qty']) if (ce_meta or pe_meta) else 0
            
            self.monitor_straddle(ce_token, pe_token, ce_symbol, pe_symbol, qty)
            return

        # 1. Global Safety Guards
        if not self.gatekeeper.is_market_open():
            logger.warning("Straddle: 🛑 Execution Aborted - Market is Closed.")
            return
        if self.gatekeeper.is_blackout_period():
            logger.info("Straddle: ⏸️ Execution Suspended - Mid-day Blackout.")
            return
        if not self.gatekeeper.check_max_daily_loss(0.0):
            logger.critical("Straddle: 🛑 Execution Blocked - Max Daily Loss reached.")
            return
        if not self.gatekeeper.check_funds(required_margin_per_lot=150000): return

        # 2. Market Sentiment Guard
        atm_strike = self.get_atm_strike()
        if not atm_strike:
            logger.error(">>> [Error] Could not fetch ATM Strike for entry. Aborting.")
            return

        sentiment = self.oi_analyzer.get_market_sentiment(expiry, atm_strike)
        if sentiment['bias'] != "NEUTRAL":
            logger.warning(f">>> [Risk] ⚠️ Sentiment is {sentiment['bias']}. Straddle postponed.")
            return

        # 3. Dynamic Position Sizing (1% Risk / Max Cap)
        # Straddle Risk is usually undefined (Unlimited), but we assume SL of 25%.
        # Risk = Premium * 0.25 * Qty.
        
        capital = self.gatekeeper.get_current_capital()
        risk_per_trade = capital * Config.RISK_PER_TRADE_PERCENT
        if risk_per_trade < 1000: risk_per_trade = 1000
        
        # Estimate Premium ~ 150 * 2 = 300. Risk 25% = 75 pts.
        est_combined_premium = 300.0 
        est_risk_pts = est_combined_premium * 0.25
        
        calc_qty = int(risk_per_trade / est_risk_pts)
        lot_size = Config.NIFTY_LOT_SIZE
        lots = max(1, int(calc_qty / lot_size))
        quantity = lots * lot_size
        
        logger.info(f">>> [Sizing] Capital: {capital:.0f} | Calc Qty: {quantity} ({lots} lots)")

        # 4. Get Tokens
        ce_token, ce_symbol = self.token_loader.get_token("NIFTY", expiry, atm_strike, "CE")
        pe_token, pe_symbol = self.token_loader.get_token("NIFTY", expiry, atm_strike, "PE")
        
        if not ce_token or not pe_token:
            logger.error(">>> [Error] Tokens not found.")
            return

        # Margin Check
        # Short Straddle Margin is High (~1.5L per lot).
        # We need to check if we have enough margin for 'quantity' lots.
        # Approx 1.5L * Lots.
        est_margin = 150000 * lots 
        if est_margin > capital:
            logger.warning(f"⚠️ Insufficient Margin for {lots} lots. Resizing...")
            lots = int(capital / 150000)
            quantity = max(1, lots) * lot_size
            if quantity == 0: 
                logger.error("❌ Insufficient Capital for even 1 lot.")
                return

        # 5. Place Entry Orders (SELL)
        logger.info(f">>> [Trade] Selling Straddle Legs: {ce_symbol} & {pe_symbol}")
        
        ce_oid = self.place_leg(ce_token, ce_symbol, "SELL", quantity)
        # 1. Early Record CE
        mode = "PAPER" if self.dry_run else "LIVE"
        ce_tid = trade_repo.save_trade(ce_symbol, ce_token, "CE", quantity, 0.0, 0.0, side="SELL", mode=mode, strategy="STRADDLE")
        
        pe_oid = self.place_leg(pe_token, pe_symbol, "SELL", quantity)
        # 2. Early Record PE
        pe_tid = trade_repo.save_trade(pe_symbol, pe_token, "PE", quantity, 0.0, 0.0, side="SELL", mode=mode, strategy="STRADDLE")
        
        if not ce_oid and not pe_oid: return 

        # 6. Wait for Fills & Update Records
        ce_fill = self.wait_for_fill(ce_oid)
        if ce_fill['status'] == 'FILLED':
             self.entry_prices['CE'] = ce_fill['price']
             self.legs_active['CE'] = True
             if ce_tid: trade_repo.update_entry_price(ce_tid, ce_fill['price'])
             self.leg_metadata['CE'] = {'token': ce_token, 'symbol': ce_symbol, 'qty': quantity, 'id': ce_tid}
        else:
             logger.error(f"❌ CE Order Failed: {ce_fill.get('message')}")
             if ce_tid: trade_repo.close_trade(trade_id=ce_tid, exit_reason="ORDER_FAILED")
             
        pe_fill = self.wait_for_fill(pe_oid)
        if pe_fill['status'] == 'FILLED':
             self.entry_prices['PE'] = pe_fill['price']
             self.legs_active['PE'] = True
             if pe_tid: trade_repo.update_entry_price(pe_tid, pe_fill['price'])
             self.leg_metadata['PE'] = {'token': pe_token, 'symbol': pe_symbol, 'qty': quantity, 'id': pe_tid}
        else:
             logger.error(f"❌ PE Order Failed: {pe_fill.get('message')}")
             if pe_tid: trade_repo.close_trade(trade_id=pe_tid, exit_reason="ORDER_FAILED")

        # 7. Place Initial Broker-Side Stop Loss (25%)
        # For Short, SL is BUY STOP.
        if self.legs_active['CE']:
            ce_price = self.entry_prices['CE']
            sl_price = round(ce_price * 1.25, 1) # 25% SL
            # Use OrderManager for SL
            sl_id = self.order_manager.place_sl_order(ce_symbol, ce_token, quantity, sl_price, "CE", transaction_type="BUY") # side=SELL means Entry was SELL, so SL is BUY
            if sl_id: self.sl_orders['CE'] = sl_id
            
        if self.legs_active['PE']:
            pe_price = self.entry_prices['PE']
            sl_price = round(pe_price * 1.25, 1) # 25% SL
            sl_id = self.order_manager.place_sl_order(pe_symbol, pe_token, quantity, sl_price, "PE", transaction_type="BUY")
            if sl_id: self.sl_orders['PE'] = sl_id

        # 8. Monitor Loop
        self.monitor_straddle(ce_token, pe_token, ce_symbol, pe_symbol, quantity)


    def place_leg(self, token, symbol, action, qty):
        orderparams = {
            "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
            "transactiontype": action, "exchange": "NFO", "ordertype": "MARKET",
            "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
        }
        return self.order_manager.place_order(orderparams)

    def monitor_straddle(self, ce_token, pe_token, ce_symbol, pe_symbol, quantity):
        logger.info(f">>> [Monitor] Straddle Active. SL Orders: {self.sl_orders}")
        sl_moved_to_cost = False
        
        while self.running:
            try:
                time.sleep(3)
                now = datetime.datetime.now().time()
                
                # Check Time Exit
                if now >= datetime.time(15, 15):
                    logger.info(">>> [Exit] Time 15:15. Closing all positions.")
                    self.exit_all_market(quantity, "TIME")
                    break

                # Check SL Status via OrderManager
                # Leg 1 Hit SL -> Move Leg 2 to Cost
                ce_sl_status = self.check_sl_status('CE')
                pe_sl_status = self.check_sl_status('PE')
                
                if ce_sl_status == 'complete' and self.legs_active['CE']:
                    logger.info(f">>> [Risk] CE Stop Loss Hit! Moving PE SL to Cost.")
                    self.legs_active['CE'] = False
                    self.modify_sl_to_cost('PE', pe_token, pe_symbol, quantity)
                    sl_moved_to_cost = True # Flag logic needs refinement for re-entry? No, standard adjustment.

                if pe_sl_status == 'complete' and self.legs_active['PE']:
                    logger.info(f">>> [Risk] PE Stop Loss Hit! Moving CE SL to Cost.")
                    self.legs_active['PE'] = False
                    self.modify_sl_to_cost('CE', ce_token, ce_symbol, quantity)

                if not self.legs_active['CE'] and not self.legs_active['PE']:
                    logger.info(">>> [Exit] Both Legs Closed.")
                    break
                    
                # Check Global P&L for Target (25% Decay)
                # ... (Logic remains same, but ensure we fetch fresh LTP)
                ce_ltp = self.get_ltp(ce_token)
                pe_ltp = self.get_ltp(pe_token)
                
                if ce_ltp and pe_ltp:
                    current_sum = ce_ltp + pe_ltp
                    total_entry = self.entry_prices.get('CE', 0) + self.entry_prices.get('PE', 0)
                    target_sum = total_entry * 0.75 
                    
                    if current_sum <= target_sum:
                        logger.info(f">>> [Profit] Target Hit! Combined: {current_sum}")
                        self.exit_all_market(quantity, "TARGET")
                        break
                
                # --- GLOBAL SAFETY KILL SWITCH ---
                # Combined Unrealized P&L for Short Straddle
                if ce_ltp and pe_ltp:
                    # For SELL, PnL = (Entry - Current)
                    ce_pnl = (self.entry_prices.get('CE', 0) - ce_ltp) * quantity
                    pe_pnl = (self.entry_prices.get('PE', 0) - pe_ltp) * quantity
                    combined_unrealized = ce_pnl + pe_pnl
                    
                    if not self.gatekeeper.check_max_daily_loss(combined_unrealized):
                        logger.critical(f"Straddle: 🛑 EMERGENCY EXIT - Global Loss Limit Hit.")
                        self.exit_all_market(quantity, "MAX_DAILY_LOSS")
                        break
                
            except Exception as e:
                logger.error(f"Monitor: {e}")
                time.sleep(5)

    def modify_sl_to_cost(self, leg_type, token, symbol, quantity):
        sl_oid = self.sl_orders.get(leg_type)
        if not sl_oid: return
        
        entry_price = self.entry_prices.get(leg_type)
        if not entry_price: return
        
        logger.info(f">>> [Risk] Modifying {leg_type} SL to Cost: {entry_price}")
        
        # Use OrderManager to Modify
        # New Trigger = Entry Price
        # New Price = Entry Price + Buffer (Buy SL)
        
        new_trigger = entry_price
        new_price = round(entry_price + 1.0, 1)
        
        success = self.order_manager.modify_sl_order(sl_oid, new_price, symbol, token, quantity)
        if success:
             logger.info(f"    >>> Modified {leg_type} SL to {entry_price}")
        else:
             logger.error(f"    >>> Modification Failed. Cancelling and Replacing.")
             self.order_manager.cancel_order(sl_oid, variety="STOPLOSS")
             new_id = self.order_manager.place_sl_order(symbol, token, quantity, new_price, leg_type)
             if new_id: self.sl_orders[leg_type] = new_id

    def exit_all_market(self, quantity, reason):
        if self.legs_active['CE']:
            ce_meta = self.leg_metadata['CE']
            self.exit_leg(ce_meta['token'], ce_meta['symbol'], quantity, reason, ce_meta.get('id'), self.sl_orders.get('CE'))
            self.legs_active['CE'] = False
            
        if self.legs_active['PE']:
            pe_meta = self.leg_metadata['PE']
            self.exit_leg(pe_meta['token'], pe_meta['symbol'], quantity, reason, pe_meta.get('id'), self.sl_orders.get('PE'))
            self.legs_active['PE'] = False

    def exit_leg(self, token, symbol, qty, reason, trade_id, sl_oid):
        # 1. Cancel SL
        if sl_oid: self.order_manager.cancel_order(sl_oid, variety="STOPLOSS")
        
        # 2. Buy to Cover
        orderparams = {
            "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
            "transactiontype": "BUY", "exchange": "NFO", "ordertype": "MARKET",
            "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
        }
        oid = self.order_manager.place_order(orderparams)
        
        if oid and trade_id:
             # Use WebSocket wait for exact price
             fill = self.wait_for_fill(oid)
             exit_price = fill.get('price', 0.0)
             trade_repo.close_trade(trade_id=trade_id, exit_price=exit_price, exit_reason=reason)

    # --- Helpers ---
    def get_atm_strike(self):
        try:
            ltp = self.data_fetcher.get_ltp("99926000")
            if ltp and ltp > 0: 
                return int(round(ltp / 50) * 50)
        except Exception as e:
            logger.warning(f"Straddle get_atm_strike error: {e}")
        return None

    def get_ltp(self, token):
        """
        Returns LTP using DataFetcher (WebSocket cache first, API fallback).
        Uses 1s throttle cache to avoid hammering the API during the monitor loop.
        """
        try:
            now = time.time()
            if token in self._ltp_cache:
                last_time, last_val = self._ltp_cache[token]
                if now - last_time < 0.9:
                    return last_val

            val = self.data_fetcher.get_ltp(token, exchange="NFO")
            if val:
                self._ltp_cache[token] = (now, val)
                return val
        except Exception as e:
            logger.warning(f"Straddle get_ltp error: {e}")
        return None

    def wait_for_fill(self, order_id):
        """Uses WebSocket Order Feed for sub-second fill detection."""
        if self.dry_run: return {'status': 'FILLED', 'price': 100.0}
        
        from bot.core.order_feed import order_feed
        logger.info(f">>> [Straddle] Waiting for WebSocket Fill Event ({order_id})...")
        
        result = order_feed.wait_for_fill(order_id, timeout=10)
        
        if result['status'] == 'TIMEOUT':
             logger.warning(f"⚠️ Order {order_id} fill TIMEOUT via WebSocket.")
        
        return result
        
    def resume(self):
        mode = "PAPER" if self.dry_run else "LIVE"
        open_trades = trade_repo.get_open_trades(mode=mode, strategy="STRADDLE")
        if not open_trades: return False
        
        logger.info(f">>> [Resumption] Found {len(open_trades)} Open Straddle Legs.")
        for trade in open_trades:
            leg = trade['leg']
            self.legs_active[leg] = True
            self.entry_prices[leg] = trade['entry_price']
            self.leg_metadata[leg] = {
                'token': trade['token'], 'symbol': trade['symbol'], 
                'qty': trade['qty'], 'id': trade['id']
            }
        return True

    def stop(self):
        logger.info(">>> [Strategy] Stop Signal. Squaring off...")
        self.running = False
        ce_qty = self.leg_metadata['CE']['qty'] if self.leg_metadata.get('CE') else 0
        pe_qty = self.leg_metadata['PE']['qty'] if self.leg_metadata.get('PE') else 0
        qty = max(ce_qty, pe_qty) # Approximate
        if qty > 0: self.exit_all_market(qty, "MANUAL_STOP")

