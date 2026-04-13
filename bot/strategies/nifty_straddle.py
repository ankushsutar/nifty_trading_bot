import time
import datetime
from bot.config.settings import Config
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.trade_repo import trade_repo
from bot.core.oi_analyzer import OIAnalyzer
from bot.core.order_manager import OrderManager
from bot.core.data_fetcher import DataFetcher
from bot.utils.logger import logger
from bot.config.instruments import get_instrument

from bot.strategies.base_strategy import BaseStrategy

class NiftyStraddle(BaseStrategy):
    def __init__(self, api, token_loader, dry_run=False):
        super().__init__(api, token_loader, "STRADDLE", dry_run)
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

        # 3. Dynamic Position Sizing — use tier-driven risk percentage
        capital = self.gatekeeper.get_current_capital()
        tier = Config.get_tier(capital)
        risk_per_trade = capital * tier.risk_per_trade_pct
        if risk_per_trade < tier.min_risk_floor: risk_per_trade = tier.min_risk_floor
        
        # Estimate Premium ~ 1.5% of symbol LTP combined.
        instr = get_instrument(Config.ACTIVE_SYMBOL)
        symbol_ltp = self.data_fetcher.get_ltp(instr.analysis_token, exchange=instr.exchange) or 22000
        est_combined_premium = symbol_ltp * 0.015
        est_risk_pts = est_combined_premium * 0.25
        
        calc_qty = int(risk_per_trade / est_risk_pts)
        lot_size = instr.lot_size
        lots = max(1, int(calc_qty / lot_size))
        quantity = lots * lot_size
        
        logger.info(f">>> [Sizing] Capital: {capital:.0f} | Calc Qty: {quantity} ({lots} lots)")

        # 4. Get Tokens
        ce_token, ce_symbol = self.token_loader.get_token(instr.name, expiry, atm_strike, "CE", instrument_type=instr.trading_type, exchange=instr.exchange)
        pe_token, pe_symbol = self.token_loader.get_token(instr.name, expiry, atm_strike, "PE", instrument_type=instr.trading_type, exchange=instr.exchange)
        
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
        logger.info(f">>> [Trade] Selling Straddle Legs via Smart-Limit: {ce_symbol} & {pe_symbol}")
        mode = "PAPER" if self.dry_run else "LIVE"
        
        # Fetch LTPs for buffer calculation
        ce_quote_ltp = self.data_fetcher.get_ltp(ce_token) or 150.0
        pe_quote_ltp = self.data_fetcher.get_ltp(pe_token) or 150.0

        try:
             # CE Leg
             ce_limit = round(ce_quote_ltp * 0.95, 1) # Willing to sell up to 5% below LTP
             ce_oid = self.order_manager.place_smart_limit(
                 ce_symbol, ce_token, quantity, ce_limit, 
                 transaction_type="SELL",
                 strategy_name="STRADDLE"
             )
             ce_tid = None
             if ce_oid:
                 self.leg_metadata['CE'] = {'token': ce_token, 'symbol': ce_symbol, 'qty': quantity}

             # PE Leg
             pe_limit = round(pe_quote_ltp * 0.95, 1)
             pe_oid = self.order_manager.place_smart_limit(
                 pe_symbol, pe_token, quantity, pe_limit, 
                 transaction_type="SELL",
                 strategy_name="STRADDLE"
             )
             pe_tid = None
             if pe_oid:
                 self.leg_metadata['PE'] = {'token': pe_token, 'symbol': pe_symbol, 'qty': quantity}

             if not ce_oid and not pe_oid: return 

             # 6. Wait for Fills
             if ce_oid:
                 ce_fill = self.wait_for_fill(ce_oid)
                 if ce_fill['status'] == 'FILLED':
                      self.entry_prices['CE'] = ce_fill['price']
                      self.legs_active['CE'] = True
                      ce_tid = self.order_manager.update_trade_fill(ce_symbol, "STRADDLE", ce_fill['price'], expected_price=ce_quote_ltp)
                      if ce_tid: self.leg_metadata['CE']['id'] = ce_tid
                 else:
                      logger.error(f"❌ CE Order Failed: {ce_fill.get('message')}")
                      # Search for PLACED trade to close if possible, or just log
                      
             if pe_oid:
                 pe_fill = self.wait_for_fill(pe_oid)
                 if pe_fill['status'] == 'FILLED':
                      self.entry_prices['PE'] = pe_fill['price']
                      self.legs_active['PE'] = True
                      pe_tid = self.order_manager.update_trade_fill(pe_symbol, "STRADDLE", pe_fill['price'], expected_price=pe_quote_ltp)
                      if pe_tid: self.leg_metadata['PE']['id'] = pe_tid
                 else:
                      logger.error(f"❌ PE Order Failed: {pe_fill.get('message')}")
        except Exception as e:
             logger.error(f"Error during straddle entry: {e}")

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
        instr = get_instrument(Config.ACTIVE_SYMBOL)
        orderparams = {
            "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
            "transactiontype": action, "exchange": instr.exchange, "ordertype": "MARKET",
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
                if not self.gatekeeper.is_market_open():
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
        
        instr = get_instrument(Config.ACTIVE_SYMBOL)
        success = self.order_manager.modify_sl_order(sl_oid, new_price, symbol, token, quantity, exchange=instr.exchange)
        if success:
             logger.info(f"    >>> Modified {leg_type} SL to {entry_price}")
        else:
             logger.error(f"    >>> Modification Failed. Cancelling and Replacing.")
             self.order_manager.cancel_order(sl_oid, variety="STOPLOSS")
             new_id = self.order_manager.place_sl_order(symbol, token, quantity, new_price, leg_type, exchange=instr.exchange)
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
        instr = get_instrument(Config.ACTIVE_SYMBOL)
        orderparams = {
            "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
            "transactiontype": "BUY", "exchange": instr.exchange, "ordertype": "MARKET",
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
            instr = get_instrument(Config.ACTIVE_SYMBOL)
            ltp = self.data_fetcher.get_ltp(instr.analysis_token)
            if ltp and ltp > 0: 
                return int(round(ltp / instr.strike_step) * instr.strike_step)
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

            instr = get_instrument(Config.ACTIVE_SYMBOL)
            val = self.data_fetcher.get_ltp(token, exchange=instr.exchange)
            if val:
                self._ltp_cache[token] = (now, val)
                return val
        except Exception as e:
            logger.warning(f"Straddle get_ltp error: {e}")
        return None

