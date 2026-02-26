import datetime
import time
import pandas as pd
from bot.config.settings import Config
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.trade_repo import trade_repo
from bot.core.data_fetcher import DataFetcher
from bot.core.order_manager import OrderManager
from bot.utils.logger import logger

class InsideBarStrategy:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(self.api, dry_run=self.dry_run)
        self.data_fetcher = DataFetcher(self.api)
        self.order_manager = OrderManager(self.api, dry_run=self.dry_run)
        self.running = True

    def fetch_candles(self, interval="FIFTEEN_MINUTE"):
        # Nifty 50 Token
        token = "99926000"
        return self.data_fetcher.fetch_latest_candles(token, interval=interval)

    def get_nifty_ltp(self):
        return self.data_fetcher.get_ltp("99926000")

    def execute(self, expiry, action="BUY"):
        """
        Executes Inside Bar Breakout Strategy.
        Timeframe: 15-Minute Candles.
        Pattern: Mother Candle, then Baby Candle inside Mother's High/Low.
        """
        logger.info(f"--- INSIDE BAR STRATEGY ({expiry}) ---")

        # Check for Resumption
        mode = "PAPER" if self.dry_run else "LIVE"
        active_trade = trade_repo.get_active_trade(mode=mode, strategy="INSIDE_BAR")
        
        if active_trade:
            logger.info(f">>> [Resumption] Found Open Trade: {active_trade['symbol']} (ID: {active_trade['id']})")
            
            # Recalculate Target/SL from DB or defaults
            fill = active_trade['entry_price']
            sl = active_trade['sl_price']
            # Re-derive Index Level SL approx if needed, or just use stored SL price.
            # Target 1:2
            risk = abs(fill - sl)
            target = round(fill + (risk * 2), 1)
            
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

        # 0. Global Safety Guards
        if not self.gatekeeper.is_market_open():
            logger.warning("InsideBar: 🛑 Execution Aborted - Market is Closed.")
            return
        if self.gatekeeper.is_blackout_period():
            logger.info("InsideBar: ⏸️ Execution Suspended - Mid-day Blackout.")
            return
        if not self.gatekeeper.check_max_daily_loss(0.0):
            logger.critical("InsideBar: 🛑 Execution Blocked - Max Daily Loss reached.")
            return
        if not self.gatekeeper.check_funds(required_margin_per_lot=5000): return

        # 1. Fetch Data (15 Min Candles)
        df = self.fetch_candles("FIFTEEN_MINUTE")
        if df is None or len(df) < 2:
             logger.error(">>> [Error] Insufficient Data.")
             return
        
        # 2. Identify Pattern (Last 2 completed candles)
        mother = df.iloc[-2]
        baby = df.iloc[-1]
        
        logger.info(f">>> [Analysis] Checking Inside Bar Pattern...")
        logger.info(f"    Mother ({-2}): H:{mother['high']} L:{mother['low']}")
        logger.info(f"    Baby   ({-1}): H:{baby['high']} L:{baby['low']}")

        is_inside_bar = (baby['high'] <= mother['high']) and (baby['low'] >= mother['low'])
        
        if not is_inside_bar:
            logger.info(">>> [Result] No Inside Bar Pattern detected.")
            return
            
        logger.info(">>> [Signal] 🔥 INSIDE BAR DETECTED!")
        
        # 3. Check Breakout (Current Market Price vs Mother Range)
        ltp = self.get_nifty_ltp()
        logger.info(f">>> [Market] Current Price: {ltp}")
        
        signal = None
        index_sl_level = 0.0
        
        if ltp > mother['high']:
            logger.info(">>> [Breakout] Price broke Mother HIGH -> BUY CE")
            signal = "BUY_CE"
            index_sl_level = mother['low'] # SL is opposite end
        elif ltp < mother['low']:
            logger.info(">>> [Breakout] Price broke Mother LOW -> BUY PE")
            signal = "BUY_PE"
            index_sl_level = mother['high']
        else:
            logger.info(">>> [Wait] Pattern formed but NO BREAKOUT yet.")
            return

        # Apply Compounding (Exponential Scaling)
        lots = self.gatekeeper.get_compounded_lots(margin_per_lot=5000)
        qty = lots * Config.NIFTY_LOT_SIZE
        
        logger.info(f">>> [Sizing] Method=Exponential Compounding | Qty: {qty} ({lots} lots)")

        # ATM Strike (rounded to nearest 50)
        strike = round(ltp / 50) * 50

        # 5. Entry
        if signal == "BUY_CE":
            self.place_trade(expiry, strike, "CE", qty, index_sl_level)
        elif signal == "BUY_PE":
            self.place_trade(expiry, strike, "PE", qty, index_sl_level)

    def place_trade(self, expiry, strike, leg_type, qty, index_sl_level):
        token, symbol = self.token_loader.get_token("NIFTY", expiry, strike, leg_type)
        if not token: 
             logger.error(">>> [Error] Token Not Found")
             return
        
        # Viability Check: Option Premium vs Brokerage
        quote_ltp = self.data_fetcher.get_ltp(token) or 100.0
        if not self.gatekeeper.check_trade_viability(quote_ltp, qty):
             return
             
        # Pre-Trade Margin Check
        estimated_cost = quote_ltp * qty
        if not self.dry_run and not self.gatekeeper.check_trade_margin(estimated_cost):
             return

        # Place Buy Order
        logger.info(f">>> [Trade] Entering {symbol} (Qty: {qty})")
        
        try:
             orderparams = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "BUY", "exchange": "NFO", "ordertype": "MARKET",
                "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
            }
             
             oid = self.order_manager.place_order(orderparams)
             if not oid: return

             # 1. Early Record (Visibility)
             mode = "PAPER" if self.dry_run else "LIVE"
             trade_id = trade_repo.save_trade(symbol, token, leg_type, qty, 0.0, 0.0, mode=mode, strategy="INSIDE_BAR")

             # 2. Wait for Fill
             fill_result = self.wait_for_fill(oid) 
             
             if fill_result['status'] in ['REJECTED', 'CANCELLED']:
                  logger.error(f"❌ Order {oid} failed: {fill_result.get('message')}")
                  # If trade was recorded, mark it as failed or remove it
                  if trade_id: trade_repo.close_trade(trade_id=trade_id, exit_reason="ORDER_FAILED")
                  return

             fill_price = fill_result['price'] or quote_ltp
             
             # Calculate Option SL (Structural with 5pt Buffer)
             curr_index = self.get_nifty_ltp() or 22000.0
             points_risk = abs(curr_index - index_sl_level) + 5.0 # Added 5pt buffer for noise
             option_risk = points_risk * 0.5
             
             sl_price = max(0.1, round(fill_price - option_risk, 1))
             target_price = round(fill_price + (option_risk * 2), 1)
             
             logger.info(f">>> [Risk] SL: {sl_price} | Target: {target_price}")
             
             # 3. Update Trade Record
             if trade_id:
                 trade_repo.update_entry_price(trade_id, fill_price)
                 trade_repo.update_sl(trade_id, sl_price)

             # Place Broker SL
             sl_oid = self.order_manager.place_sl_order(symbol, token, qty, sl_price, leg_type)
             
             # Monitor
             self.monitor_trade(token, symbol, qty, target_price, sl_price, fill_price, trade_id, sl_oid, leg_type)
             
        except Exception as e:
             logger.error(f">>> [Error] Entry Failed: {e}")

    def monitor_trade(self, token, symbol, qty, target, sl, entry_price, trade_id, sl_oid, leg_type):
         logger.info(f"InsideBar: Monitoring. Target: {target} | SL: {sl} | Entry: {entry_price}")
         
         breakeven_hit = False
         
         while self.running:
            try:
                time.sleep(0.5) 
                
                ltp = self.data_fetcher.get_ltp(token, exchange="NFO")
                if not ltp: continue
                
                # Risk-Free Pivot (Breakeven) Logic
                # If Price moves 1:1 RR in our favor, move SL to Entry.
                if not breakeven_hit:
                    # Risk = Entry - SL
                    risk = abs(entry_price - sl)
                    threshold = entry_price + risk if leg_type == "CE" else entry_price - risk
                    
                    if (leg_type == "CE" and ltp >= threshold) or (leg_type == "PE" and ltp <= threshold):
                        logger.info(f"InsideBar: 🛡️ 1:1 RR reached (LTP: {ltp}). Moving SL to Breakeven (₹{entry_price})")
                        if sl_oid and not self.dry_run:
                            self.order_manager.modify_sl_order(sl_oid, entry_price, symbol, token, qty)
                        
                        sl = entry_price # Update local SL for monitoring
                        if trade_id: trade_repo.update_sl(trade_id, sl)
                        breakeven_hit = True

                # --- GLOBAL SAFETY KILL SWITCH ---
                unrealized_pnl = (ltp - entry_price) * qty
                if not self.gatekeeper.check_max_daily_loss(unrealized_pnl):
                    logger.critical(f"InsideBar: 🛑 EMERGENCY EXIT - Global Loss Limit Hit.")
                    self.exit_at_market(token, symbol, qty, "MAX_DAILY_LOSS", trade_id, sl_oid)
                    break
                # Check Target 
                if ltp >= target:
                     logger.info(f"InsideBar: 🎯 Target Hit ({ltp}). Closing.")
                     self.exit_at_market(token, symbol, qty, "TARGET", trade_id, sl_oid)
                     break

                # Check SL Hit
                if ltp <= sl:
                     logger.info(f"InsideBar: 🛑 SL Hit ({ltp}).")
                     if trade_id: trade_repo.close_trade(trade_id=trade_id, exit_price=ltp, exit_reason="SL_HIT")
                     break

                # Time Exit
                if datetime.datetime.now().time() >= datetime.time(15, 15):
                     logger.info("InsideBar: ⏰ Time Exit.")
                     self.exit_at_market(token, symbol, qty, "TIME", trade_id, sl_oid)
                     break
                
            except Exception as e:
                  logger.error(f"InsideBar Monitor: {e}")
                  time.sleep(5)

    def exit_at_market(self, token, symbol, qty, reason, trade_id, sl_oid):
        try:
            if sl_oid: self.order_manager.cancel_order(sl_oid, variety="STOPLOSS")
            
            orderparams = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "SELL", "exchange": "NFO", "ordertype": "MARKET",
                "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
            }
            oid = self.order_manager.place_order(orderparams)
            
            if oid and trade_id:
                 # Ideally wait for fill logic
                 trade_repo.close_trade(trade_id=trade_id, exit_reason=reason)
                 
        except Exception as e:
            logger.error(f"InsideBar Exit Failed: {e}")

    def wait_for_fill(self, order_id):
        """Uses WebSocket Order Feed for sub-second fill detection."""
        if self.dry_run: return {'status': 'FILLED', 'price': 100.0}
        
        from bot.core.order_feed import order_feed
        logger.info(f">>> [InsideBar] Waiting for WebSocket Fill Event ({order_id})...")
        
        result = order_feed.wait_for_fill(order_id, timeout=10)
        
        if result['status'] == 'TIMEOUT':
             logger.warning(f"⚠️ Order {order_id} fill TIMEOUT via WebSocket.")
        
        return result

    def stop(self):
        logger.info("InsideBar: Stop Signal.")
        self.running = False
