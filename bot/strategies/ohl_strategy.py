import datetime
import time
from bot.config.settings import Config
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.trade_repo import trade_repo
from bot.core.data_fetcher import DataFetcher
from bot.core.order_manager import OrderManager
from bot.utils.logger import logger
from bot.config.instruments import get_instrument

from bot.strategies.base_strategy import BaseStrategy

class OHLStrategy(BaseStrategy):
    def __init__(self, api, token_loader, dry_run=False):
        super().__init__(api, token_loader, "OHL", dry_run)
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

        # 0. Global Safety Guards
        if not self.gatekeeper.is_market_open():
            logger.warning("OHL: 🛑 Execution Aborted - Market is Closed.")
            return
        if self.gatekeeper.is_blackout_period():
            logger.info("OHL: ⏸️ Execution Suspended - Mid-day Blackout.")
            return
        if not self.gatekeeper.check_max_daily_loss(0.0):
            logger.critical("OHL: 🛑 Execution Blocked - Max Daily Loss reached.")
            return
        if not self.gatekeeper.check_funds(required_margin_per_lot=5000): return
        
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
        leg_type = None
        index_sl_level = 0.0
        buffer = 1.0 
        
        if abs(c_open - c_low) <= buffer:
            logger.info(">>> [Signal] OPEN ~= LOW (Strong Buying) 🐂")
            signal = "BUY_CE"
            leg_type = "CE"
            index_sl_level = c_low 
            
        elif abs(c_open - c_high) <= buffer:
            logger.info(">>> [Signal] OPEN ~= HIGH (Strong Selling) 🐻")
            signal = "BUY_PE"
            leg_type = "PE"
            index_sl_level = c_high 
        else:
            logger.info(">>> [Signal] No clear OHL Pattern.")
            return

        instr = get_instrument(Config.ACTIVE_SYMBOL)
        strike = round(c_close / instr.strike_step) * instr.strike_step
        token, symbol = self.token_loader.get_token(instr.name, expiry, strike, leg_type, instrument_type=instr.instrument_type, exchange=instr.exchange)
        if not token: 
             logger.error(">>> [Error] Token Not Found")
             return

        # Viability Check: Option Premium vs Brokerage
        quote_ltp = self.data_fetcher.get_ltp(token) or 100.0
        
        # Apply Compounding (Exponential Scaling)
        margin_per_lot = (quote_ltp * instr.lot_size) if quote_ltp > 0 else 5000.0
        lots = self.gatekeeper.get_compounded_lots(margin_per_lot=margin_per_lot)
        qty = lots * instr.lot_size
        
        logger.info(f">>> [Sizing] Method=Exponential Compounding | Qty: {qty} ({lots} lots)")

        if not self.gatekeeper.check_trade_viability(quote_ltp, qty):
             return
             
        estimated_cost = quote_ltp * qty
        
        if not self.dry_run and not self.gatekeeper.check_trade_margin(estimated_cost):
             return

        # 2. Place Order (Using OrderManager)
        # Place Smart-Limit with 5% buffer
        limit_price = round(quote_ltp * 1.05, 1)
        
        logger.info(f">>> [Trade] OHL Scalp: Entering {symbol} via Smart-Limit @ ₹{limit_price}")
        
        oid = self.order_manager.place_smart_limit(
            symbol, token, qty, limit_price, 
            transaction_type="BUY",
            strategy_name="OHL"
        )
        if not oid: return

        try:
             # 2. Wait for fill
             fill_result = self.wait_for_fill(oid) 
             
             if fill_result['status'] != 'FILLED':
                  logger.error(f"❌ Order {oid} failed or timed out: {fill_result.get('message')}")
                  if fill_result['status'] == 'TIMEOUT':
                      self.order_manager.cancel_order(oid, variety="NORMAL")
                  return

             fill_price = fill_result['price']
             
             # Risk Strategy: SL @ Candle Low (for CE) or High (for PE)
             sl_price = candle['low'] if leg_type == "CE" else candle['high']
             
             # Buffer SL to avoid noise
             if leg_type == "CE": sl_price -= 5.0
             else: sl_price += 5.0
             
             # Calculate Target (1.5x Risk)
             risk = abs(fill_price - sl_price)
             target_price = round(fill_price + (risk * 1.5), 1)
             
             # 3. Update Trade Record (with Slippage Tracking)
             trade_id = self.order_manager.update_trade_fill(symbol, "OHL", fill_price, expected_price=quote_ltp)
             if trade_id:
                  trade_repo.update_sl(trade_id, sl_price)

             # Place Broker SL
             sl_oid = self.order_manager.place_sl_order(symbol, token, qty, sl_price, leg_type)
             
             # Monitor
             self.monitor_trade(token, symbol, qty, target_price, sl_price, fill_price, trade_id, sl_oid, leg_type)

        except Exception as e:
             logger.error(f">>> [Error] Entry Failed: {e}")

    def monitor_trade(self, token, symbol, qty, target, sl, entry_price, trade_id, sl_oid, leg_type):
         logger.info(f"OHL: Monitoring Trade. Target: {target} | SL: {sl} | Entry: {entry_price}")
         
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
                        logger.info(f"OHL: 🛡️ 1:1 RR reached (LTP: {ltp}). Moving SL to Breakeven (₹{entry_price})")
                        if sl_oid and not self.dry_run:
                            self.order_manager.modify_sl_order(sl_oid, entry_price, symbol, token, qty)
                        
                        sl = entry_price # Update local SL for monitoring
                        if trade_id: trade_repo.update_sl(trade_id, sl)
                        breakeven_hit = True

                # --- GLOBAL SAFETY KILL SWITCH ---
                unrealized_pnl = (ltp - entry_price) * qty
                if not self.gatekeeper.check_max_daily_loss(unrealized_pnl):
                    logger.critical(f"OHL: 🛑 EMERGENCY EXIT - Global Loss Limit Hit.")
                    self.exit_at_market(token, symbol, qty, "MAX_DAILY_LOSS", trade_id, sl_oid)
                    break 
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
        """Fetches the 09:15-09:16 candle. Retries up to 3 times for data stability."""
        instr = get_instrument(Config.ACTIVE_SYMBOL)
        for attempt in range(4): # Total 4 attempts
            try:
                 df = self.data_fetcher.fetch_latest_candles(instr.analysis_token, interval="ONE_MINUTE")
                 if df is not None and not df.empty:
                      # Look for 09:15 candle
                      mask = df['timestamp'].astype(str).str.contains("09:15")
                      rows = df[mask]
                      if not rows.empty:
                          return rows.iloc[0].to_dict()
            except Exception as e:
                logger.debug(f"OHL Candle Fetch Attempt {attempt+1} failed: {e}")
            
            if attempt < 3:
                logger.info(f"OHL: 09:15 candle not ready. Retrying in 2s... (Attempt {attempt+1}/4)")
                time.sleep(2)

        if self.dry_run: 
            logger.info("OHL: Running in DRY MODE, using dummy 09:15 candle.")
            return {'open': 22000, 'low': 22000, 'high': 22050, 'close': 22040}
        return None

    def get_index_ltp(self):
        instr = get_instrument(Config.ACTIVE_SYMBOL)
        return self.data_fetcher.get_ltp(instr.analysis_token)

