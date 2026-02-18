import datetime
import time
from bot.config.settings import Config
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.trade_repo import trade_repo
from bot.core.data_fetcher import DataFetcher
from bot.core.order_manager import OrderManager
from bot.utils.logger import logger

class OHLStrategy:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(self.api, dry_run=self.dry_run)
        self.data_fetcher = DataFetcher(self.api)
        self.order_manager = OrderManager(self.api, dry_run=self.dry_run) # 1. Inject OrderManager
        self.running = True

    def execute(self, expiry, action="BUY"):
        """
        Executes Open High Low (OHL) Scalp.
        Time: 09:16 AM (After first 1-min candle 09:15-09:16)
        """
        logger.info(f"--- OHL SCALP STRATEGY ({expiry}) ---")

        # Check for Resumption
        mode = "PAPER" if self.dry_run else "LIVE"
        active_trade = trade_repo.get_active_trade(mode=mode, strategy="OHL")
        
        if active_trade:
            logger.info(f">>> [Resumption] Found Open Trade: {active_trade['symbol']} (ID: {active_trade['id']})")
            
            fill = active_trade['entry_price']
            sl = active_trade['sl_price']
            opt_risk = abs(fill - sl)
            target = round(fill + (opt_risk * 2), 1)
            
            self.monitor_trade(
                active_trade['token'], 
                active_trade['symbol'], 
                active_trade['qty'], 
                target, 
                sl, 
                active_trade['id'],
                active_trade.get('sl_order_id'),
                active_trade.get('leg')
            )
            return

        # 0. Risk Checks
        if not self.gatekeeper.check_funds(required_margin_per_lot=5000): return
        if not self.gatekeeper.check_max_daily_loss(0): return
        if self.gatekeeper.is_blackout_period(): return
        
        # 1. Fetch First 1-Minute Candle (09:15)
        candle = self.get_first_minute_candle()
        if not candle:
            logger.error(">>> [Error] Could not fetch 09:15 Candle.")
            return

        c_open = candle['open']
        c_high = candle['high']
        c_low = candle['low']
        c_close = candle['close']
        
        logger.info(f">>> [Market] 09:15 Candle | O: {c_open} H: {c_high} L: {c_low} C: {c_close}")

        # 2. Logic Check
        signal = None
        index_sl_level = 0.0
        buffer = 1.0 
        
        if abs(c_open - c_low) <= buffer:
            logger.info(">>> [Signal] OPEN ~= LOW (Strong Buying) 🐂")
            signal = "BUY_CE"
            index_sl_level = c_low 
            
        elif abs(c_open - c_high) <= buffer:
            logger.info(">>> [Signal] OPEN ~= HIGH (Strong Selling) 🐻")
            signal = "BUY_PE"
            index_sl_level = c_high 
        else:
            logger.info(">>> [Signal] No clear OHL Pattern.")
            return

        # Apply Compounding (Exponential Scaling)
        lots = self.gatekeeper.get_compounded_lots(margin_per_lot=5000)
        qty = lots * Config.NIFTY_LOT_SIZE
        
        logger.info(f">>> [Sizing] Method=Exponential Compounding | Qty: {qty} ({lots} lots)")
        strike = round(c_close / 50) * 50

        # 4. Entry
        if signal == "BUY_CE":
            self.place_entry(expiry, strike, "CE", qty, index_sl_level)
        elif signal == "BUY_PE":
            self.place_entry(expiry, strike, "PE", qty, index_sl_level)

    def place_entry(self, expiry, strike, leg_type, qty, index_sl_level):
        # 1. Get Token
        token, symbol = self.token_loader.get_token("NIFTY", expiry, strike, leg_type)
        if not token: 
             logger.error(">>> [Error] Token Not Found")
             return

        # Viability Check: Option Premium vs Brokerage
        quote_ltp = self.data_fetcher.get_ltp(token) or 100.0
        if not self.gatekeeper.check_trade_viability(quote_ltp, qty):
             return
             
        estimated_cost = quote_ltp * qty
        
        if not self.dry_run and not self.gatekeeper.check_trade_margin(estimated_cost):
             return

        # 2. Place Order (Using OrderManager)
        logger.info(f">>> [Trade] Entering {symbol} (Qty: {qty})")
        
        try:
             orderparams = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "BUY", "exchange": "NFO", "ordertype": "MARKET",
                "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
            }
             
             # Use OrderManager to Place & Wait
             oid = self.order_manager.place_order(orderparams)
             if not oid: return

             fill_result = self.wait_for_fill(oid) # Reuse local or OrderManager? Local is fine, but OrderManager has none.
             # Actually, OrderManager place_order returns ID. We need to wait for fill manually or add helper?
             # MomentumStrategy has wait_for_fill. Let's keep a local wait_for_fill for now or move it to OrderManager later.
             # Reusing local wait_for_fill logic (improved)
             
             if fill_result['status'] in ['REJECTED', 'CANCELLED']:
                 logger.error(f"❌ Order {oid} failed: {fill_result.get('message')}")
                 return

             fill_price = fill_result['price']
             if not fill_price: fill_price = quote_ltp
             
             # 3. Calculate Option SL (Structural with 5pt Buffer)
             curr_index = self.get_nifty_ltp() or c_close
             points_risk = abs(curr_index - index_sl_level) + 5.0 # Added 5pt buffer for noise
             option_risk = points_risk * 0.5 
             
             sl_price = max(0.1, round(fill_price - option_risk, 1))
             target_price = round(fill_price + (option_risk * 2), 1) # 1:2 R:R
             
             logger.info(f">>> [Risk] SL: {sl_price} | Target: {target_price}")
             
             # 4. Save Trade
             mode = "PAPER" if self.dry_run else "LIVE"
             tid = trade_repo.save_trade(symbol, token, leg_type, qty, fill_price, sl_price, mode=mode, strategy="OHL")

             # 5. Place Broker-Side SL
             sl_oid = self.order_manager.place_sl_order(symbol, token, qty, sl_price, leg_type)
             
             # 6. Monitor
             self.monitor_trade(token, symbol, qty, target_price, sl_price, fill_price, tid, sl_oid, leg_type)

        except Exception as e:
             logger.error(f">>> [Error] Entry Failed: {e}")

    def monitor_trade(self, token, symbol, qty, target, sl, entry_price, trade_id, sl_oid, leg_type):
         logger.info(f"OHL: Monitoring Trade. Target: {target} | SL: {sl} | Entry: {entry_price}")
         
         breakeven_hit = False
         
         while self.running:
            try:
                time.sleep(0.5) 
                
                ltp = self.data_fetcher.get_ltp(token)
                if not ltp: continue
                
                # Risk-Free Pivot (Breakeven) Logic
                # If Price moves 1:1 RR in our favor, move SL to Entry.
                if not breakeven_hit:
                    # Risk = Entry - SL
                    risk = abs(entry_price - sl)
                    threshold = entry_price + risk if leg_type == "CE" else entry_price - risk
                    
                    if (leg_type == "CE" and ltp >= threshold) or (leg_type == "PE" and ltp <= threshold):
                        logger.info(f"OHL: 🛡️ 1:1 RR reached (LTP: {ltp}). Moving SL to Breakeven (₹{entry_price})")
                        if sl_oid and not self.dry_run:
                            self.order_manager.modify_sl_order(sl_oid, entry_price, symbol, token, qty)
                        
                        sl = entry_price # Update local SL for monitoring
                        if trade_id: trade_repo.update_sl(trade_id, sl)
                        breakeven_hit = True
                # Check Target (Exit Market)
                if ltp >= target:
                     logger.info(f"OHL: 🎯 Target Hit ({ltp} >= {target}). Closing.")
                     self.exit_at_market(token, symbol, qty, "TARGET", trade_id, sl_oid)
                     break

                # Check SL Hit (Broker SL would trigger, but we monitor)
                if ltp <= sl:
                     logger.info(f"OHL: 🛑 Stop Loss Hit ({ltp} <= {sl}).")
                     # Assume broker fired. Sync DB.
                     if trade_id: trade_repo.close_trade(trade_id=trade_id, exit_price=ltp, exit_reason="SL_HIT")
                     break

                # Check Time Exit
                if datetime.datetime.now().time() >= datetime.time(15, 15):
                     logger.info("OHL: ⏰ Time 15:15. Closing.")
                     self.exit_at_market(token, symbol, qty, "TIME", trade_id, sl_oid)
                     break
                
            except Exception as e:
                  logger.error(f"OHL Monitor Error: {e}")
                  time.sleep(5)

    def exit_at_market(self, token, symbol, qty, reason, trade_id=None, sl_oid=None):
        """Exits position at market price using OrderManager."""
        try:
            # 1. Cancel SL if exists
            if sl_oid:
                self.order_manager.cancel_order(sl_oid, variety="STOPLOSS")

            # 2. Place Exit Order
            orderparams = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "SELL", "exchange": "NFO", "ordertype": "MARKET",
                "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
            }
            
            oid = self.order_manager.place_order(orderparams)
            
            if oid:
                logger.info(f"OHL: Exit Order Placed: {oid}")
                # Wait for fill?
                fill = self.wait_for_fill(oid)
                exit_price = fill.get('price', 0.0)
                
                if trade_id:
                     trade_repo.close_trade(trade_id=trade_id, exit_price=exit_price, exit_reason=reason)
            else:
                logger.error("OHL: Exit Failed.")

        except Exception as e:
            logger.error(f"OHL Exit Failed: {e}")

    # --- Helpers ---
    def get_first_minute_candle(self):
        try:
             df = self.data_fetcher.fetch_latest_candles("99926000", interval="ONE_MINUTE")
             if df is not None and not df.empty:
                  # Naive check for 09:15
                  mask = df['timestamp'].astype(str).str.contains("09:15")
                  rows = df[mask]
                  if not rows.empty:
                      return rows.iloc[0].to_dict()
        except: pass
        if self.dry_run: return {'open': 22000, 'low': 22000, 'high': 22050, 'close': 22040}
        return None

    def get_nifty_ltp(self):
        return self.data_fetcher.get_ltp("99926000")

    def wait_for_fill(self, order_id):
        if not order_id: return {'status': 'ERROR', 'price': None}
        if self.dry_run: return {'status': 'FILLED', 'price': 100.0}
        
        for _ in range(10):
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
        self.running = False

