import time
import datetime
from bot.config.settings import Config
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.trade_repo import trade_repo
from bot.core.data_fetcher import DataFetcher
from bot.core.order_manager import OrderManager
from bot.utils.logger import logger

class ORBStrategy:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(self.api, dry_run=self.dry_run)
        self.data_fetcher = DataFetcher(self.api)
        self.order_manager = OrderManager(self.api, dry_run=self.dry_run)
        self.running = True
        
        # State
        self.range_high = -1
        self.range_low = 999999
        self.range_set = False

    def execute(self, expiry, action="BUY"):
        """
        ORB Logic:
        1. 09:15 - 09:30: Monitor High/Low
        2. 09:30+: Wait for Breakout
        """
        logger.info(f">>> [Strategy] Initializing ORB Strategy for {expiry}")
        
        # Check for Resumption
        mode = "PAPER" if self.dry_run else "LIVE"
        active_trade = trade_repo.get_active_trade(mode=mode, strategy="ORB")
        
        if active_trade:
            logger.info(f">>> [Resumption] Found Open Trade: {active_trade['symbol']} (ID: {active_trade['id']})")
            
            fill = active_trade['entry_price']
            # Default ORB Target 20% if not in DB
            target = round(fill * 1.2, 1)
            sl = active_trade.get('sl_price', round(fill * 0.9, 1))
            
            self.monitor_position(
                active_trade['symbol'], 
                active_trade['token'], 
                active_trade['qty'],
                target,
                sl,
                active_trade['id'],
                active_trade.get('sl_order_id'),
                active_trade.get('leg')
            )
            return

        # 1. Establish Range (Simulated or Real)
        self.establish_opening_range()
        
        if not self.range_set:
            logger.error(">>> [Error] Failed to establish Opening Range.")
            return

        logger.info(f">>> [ORB] Range Set: High={self.range_high}, Low={self.range_low}")
        
        # 2. Monitor for Breakout
        self.monitor_breakout(expiry)

    def establish_opening_range(self):
        """
        In a real scenario, this would loop from 09:15 to 09:30 updating high/low.
        """
        logger.info(">>> [ORB] Establishing Range...")
        # Simulating fetch via DataFetcher or LTP
        ltp = self.data_fetcher.get_ltp("99926000")
        if ltp and ltp > 0:
            # Fake range for demo: +/- 20 points
            self.range_high = round(ltp + 20, 2)
            self.range_low = round(ltp - 20, 2)
            self.range_set = True
        else:
            logger.warning(">>> [ORB] Could not fetch LTP.")

    def monitor_breakout(self, expiry):
        logger.info(">>> [ORB] Monitoring for Breakout...")
        
        while self.running:
            # 1. Safety Check
            if not self.gatekeeper.check_funds(required_margin_per_lot=7000): break
            if self.gatekeeper.is_blackout_period(): break
                
            ltp = self.data_fetcher.get_ltp("99926000")
            if not ltp:
                time.sleep(1)
                continue
                
            # logger.info(f"    LTP: {ltp} | Range: {self.range_low} - {self.range_high}")
            
            # 2. Check Breakout
            if ltp > self.range_high:
                logger.info(">>> [ORB] Upside Breakout! Buying CE.")
                self.place_entry_order(expiry, "CE")
                break 
                
            elif ltp < self.range_low:
                logger.info(">>> [ORB] Downside Breakout! Buying PE.")
                self.place_entry_order(expiry, "PE")
                break
            
            time.sleep(2)
            
            # Stop if time crosses 10:00 AM? Or run all day? ORB usually morning.
            if datetime.datetime.now().time() > datetime.time(10, 30):
                logger.info(">>> [ORB] Time limit reached (10:30 AM). No breakout.")
                break

    def place_entry_order(self, expiry, option_type):
        current_ltp = self.data_fetcher.get_ltp("99926000") or 0.0
        strike = round(current_ltp / 50) * 50
        
        token, symbol = self.token_loader.get_token("NIFTY", expiry, strike, option_type)
        if not token:
            logger.error(">>> [Error] Token not found.")
            return

        # Apply Compounding (Exponential Scaling)
        lots = self.gatekeeper.get_compounded_lots(margin_per_lot=5000)
        qty = lots * Config.NIFTY_LOT_SIZE
        
        logger.info(f">>> [Sizing] Capital: {capital:.0f} | Qty: {qty} ({lots} lots)")

        # Viability Check: Option Premium vs Brokerage
        quote_ltp = self.data_fetcher.get_ltp(token) or 100.0
        if not self.gatekeeper.check_trade_viability(quote_ltp, qty):
             return
             
        est_cost = quote_ltp * qty
        if not self.dry_run and not self.gatekeeper.check_trade_margin(est_cost):
             return

        # Place Order
        logger.info(f">>> [Trade] Placing BUY order for {symbol}")
        try:
             orderparams = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "BUY", "exchange": "NFO", "ordertype": "MARKET",
                "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
            }
             
             oid = self.order_manager.place_order(orderparams)
             if not oid: return

             # Wait for Fill
             fill_result = self.wait_for_fill(oid)
             
             if fill_result['status'] in ['REJECTED', 'CANCELLED']:
                 logger.error(f"❌ Order {oid} failed: {fill_result.get('message')}")
                 return

             fill_price = fill_result['price'] or quote_ltp
                         # Structural Stop Loss (Range High/Low)
             # If Buying CE: SL = Range Low | If Buying PE: SL = Range High
             # Cap SL at 15% max to protect the small account.
             structural_sl_points = abs(current_ltp - (self.range_low if option_type == "CE" else self.range_high))
             # Convert index risk to approx option risk (Delta ~ 0.5)
             option_sl_points = structural_sl_points * 0.5
             
             sl_price = fill_price - option_sl_points
             # Safety Floor: Never risk more than 15% on a small account
             min_sl_allowed = fill_price * 0.85
             if sl_price < min_sl_allowed:
                 sl_price = round(min_sl_allowed, 1)
                 logger.info(f">>> [Risk] Structural SL too wide. Capping at 15% (₹{sl_price})")
             else:
                 sl_price = round(sl_price, 1)
             
             target_price = round(fill_price + (abs(fill_price - sl_price) * 2), 1) # Maintain 1:2
             
             logger.info(f">>> [Risk] Structural SL: {sl_price} | Target: {target_price}")
             
             # Save to DB
             mode = "PAPER" if self.dry_run else "LIVE"
             tid = trade_repo.save_trade(symbol, token, option_type, qty, fill_price, sl_price, mode=mode, strategy="ORB")
             
             # Place Broker SL
             sl_oid = self.order_manager.place_sl_order(symbol, token, qty, sl_price, option_type)
                         # Start Monitoring
             self.monitor_position(symbol, token, qty, target_price, sl_price, fill_price, tid, sl_oid, option_type)
             
        except Exception as e:
            logger.error(f">>> [Error] Order Failed: {e}")

    def monitor_position(self, symbol, token, qty, target, sl, entry_price, trade_id, sl_oid, leg_type):
        logger.info(f"ORB: Monitoring. Target: {target} | SL: {sl} | Entry: {entry_price}")
        
        breakeven_hit = False
        
        while self.running:
            try:
                time.sleep(0.5)
                ltp = self.data_fetcher.get_ltp(token)
                # Risk-Free Pivot (Breakeven) Logic
                # If Price moves 1:1 RR in our favor, move SL to Entry.
                if not breakeven_hit:
                    # Risk = Entry - SL
                    risk = abs(entry_price - sl)
                    threshold = entry_price + risk if leg_type == "CE" else entry_price - risk
                    
                    if (leg_type == "CE" and ltp >= threshold) or (leg_type == "PE" and ltp <= threshold):
                        logger.info(f"ORB: 🛡️ 1:1 RR reached (LTP: {ltp}). Moving SL to Breakeven (₹{entry_price})")
                        if sl_oid and not self.dry_run:
                            # Move SL to entry_price + 0.1 (to ensure no loss after brokerage if possible, but entry is standard)
                            self.order_manager.modify_sl_order(sl_oid, entry_price, symbol, token, qty)
                        
                        sl = entry_price # Update local SL for monitoring
                        if trade_id: trade_repo.update_sl(trade_id, sl)
                        breakeven_hit = True
                if ltp >= target:
                    logger.info(f"ORB: 🎯 Target Hit ({ltp}). Closing.")
                    self.exit_at_market(token, symbol, qty, "TARGET", trade_id, sl_oid)
                    break 
                
                # SL Check
                if ltp <= sl:
                    logger.info(f"ORB: 🛑 SL Hit ({ltp}).")
                    if trade_id: trade_repo.close_trade(trade_id=trade_id, exit_price=ltp, exit_reason="SL_HIT")
                    break
                    
                # Time Exit
                if datetime.datetime.now().time() >= datetime.time(15, 15):
                     logger.info("ORB: ⏰ Time Exit.")
                     self.exit_at_market(token, symbol, qty, "TIME", trade_id, sl_oid)
                     break
                     
            except Exception as e:
                logger.error(f"ORB Monitor: {e}")
                time.sleep(5)

    def exit_at_market(self, token, symbol, qty, reason, trade_id, sl_oid):
        try:
            if sl_oid: self.order_manager.cancel_order(sl_oid, "STOPLOSS")
            
            orderparams = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "SELL", "exchange": "NFO", "ordertype": "MARKET",
                "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
            }
            oid = self.order_manager.place_order(orderparams)
            
            if oid and trade_id:
                 trade_repo.close_trade(trade_id=trade_id, exit_reason=reason)
                 
        except Exception as e:
            logger.error(f"ORB Exit Failed: {e}")

    def wait_for_fill(self, order_id):
        if not order_id: return {'status': 'ERROR', 'price': None}
        if self.dry_run: return {'status': 'FILLED', 'price': 100.0}
        
        for _ in range(5):
            try:
                time.sleep(0.5)
                book = self.api.orderBook()
                if book and book.get('data'):
                    for o in book['data']:
                        if o['orderid'] == order_id:
                            if o['status'] == 'complete':
                                return {'status': 'FILLED', 'price': float(o['averageprice'])}
                            elif o['status'] in ['rejected', 'cancelled']:
                                return {'status': o['status'].upper(), 'message': o.get('text')}
            except: pass
        return {'status': 'TIMEOUT', 'price': None}

    def stop(self):
        logger.info("ORB: Stop Signal.")
        self.running = False
