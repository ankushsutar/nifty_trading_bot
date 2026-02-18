import datetime
import time
from bot.config.settings import Config
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.trade_repo import trade_repo
from bot.core.data_fetcher import DataFetcher
from bot.utils.logger import logger


class OHLStrategy:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(self.api, dry_run=self.dry_run)
        self.data_fetcher = DataFetcher(self.api)
        self.running = True


    def execute(self, expiry, action="BUY"):
        """
        Executes Open High Low (OHL) Scalp.
        Time: 09:16 AM (After first 1-min candle 09:15-09:16)
        """
        print(f"\n--- OHL SCALP STRATEGY ({expiry}) ---")

        # Check for Resumption
        mode = "PAPER" if self.dry_run else "LIVE"
        active_trade = trade_repo.get_active_trade(mode=mode, strategy="OHL")
        
        if active_trade:
            print(f">>> [Resumption] Found Open Trade: {active_trade['symbol']} (ID: {active_trade['id']})")
            print(">>> [Resumption] Resuming Monitoring...")
            # For OHL, we need target_price. We'll derive it or store it.
            # Since we don't store target in DB yet, we'll recalculate or use 1:2 R:R from entry.
            # Better: derive from sl_price in DB.
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
                active_trade['id']
            )
            return

        # 0. Risk Checks
        if not self.gatekeeper.check_funds(required_margin_per_lot=5000): return
        if not self.gatekeeper.check_max_daily_loss(0): return
        if self.gatekeeper.is_blackout_period(): return
        
        # 1. Fetch First 1-Minute Candle (09:15)
        candle = self.get_first_minute_candle()
        if not candle:
            print(">>> [Error] Could not fetch 09:15 Candle.")
            return

        c_open = candle['open']
        c_high = candle['high']
        c_low = candle['low']
        c_close = candle['close']
        
        print(f">>> [Market] 09:15 Candle | O: {c_open} H: {c_high} L: {c_low} C: {c_close}")

        # 2. Logic Check
        signal = None
        stop_loss_level = 0.0
        
        # Buffer for 'equal' comparison (e.g. within 0.5 points)
        buffer = 1.0 
        
        if abs(c_open - c_low) <= buffer:
            # Open ~ Low -> Bullish
            print(">>> [Signal] OPEN ~= LOW (Strong Buying) 🐂")
            signal = "BUY_CE"
            stop_loss_level = c_low # SL is Candle Low
            
        elif abs(c_open - c_high) <= buffer:
            # Open ~ High -> Bearish
            print(">>> [Signal] OPEN ~= HIGH (Strong Selling) 🐻")
            signal = "BUY_PE"
            stop_loss_level = c_high # SL is Candle High
        else:
            print(">>> [Signal] No clear OHL Pattern.")
            return

        # 3. Calculate Quantity (VIX Adjusted)
        mult = self.gatekeeper.get_vix_adjustment()
        adjusted_lots = max(1, int(mult))
        qty = int(Config.NIFTY_LOT_SIZE * adjusted_lots)

        # 4. Entry
        strike = round(c_close / 50) * 50
        print(f">>> [Trade] Target Strike: {strike} | Qty: {qty}")
        
        if signal == "BUY_CE":
            self.place_entry(expiry, strike, "CE", qty, stop_loss_level, direction="UP")
        elif signal == "BUY_PE":
            self.place_entry(expiry, strike, "PE", qty, stop_loss_level, direction="DOWN")

    def place_entry(self, expiry, strike, leg_type, qty, index_sl_level, direction):
        # 1. Get Token
        token, symbol = self.token_loader.get_token("NIFTY", expiry, strike, leg_type)
        if not token: 
             print(">>> [Error] Token Not Found")
             return

        if self.dry_run:
             print(f">>> [Dry Run] Buy {symbol} | Index SL: {index_sl_level}")
             # Save
             fill_price = self.get_nifty_ltp() or 22000.0
             sl_price = fill_price * 0.9
             mode = "PAPER" if self.dry_run else "LIVE"
             tid = trade_repo.save_trade(symbol, token, leg_type, qty, fill_price, sl_price, mode=mode, strategy="OHL")
             
             # Calculate Target for Monitor
             # Logic copied from real trade block generally
             curr_index = self.get_nifty_ltp() or 22000.0
             points_risk = abs(curr_index - index_sl_level)
             option_risk = points_risk * 0.5 
             target_price = round(fill_price + (option_risk * 2), 1)

             self.monitor_trade(token, symbol, qty, target_price, sl_price, tid)
             return

        # 2. Buy Order
        try:
             orderparams = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "BUY", "exchange": "NFO", "ordertype": "MARKET",
                "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
            }
             order_id = self.api.placeOrder(orderparams)
             if not order_id:
                 print(">>> [Error] Entry Order Failed (None returned)")
                 return

             print(f">>> [Success] Entry Order: {order_id}")
             
             # 3. Wait for Fill
             print(">>> [Trade] Waiting for fill...")
             fill_result = self.wait_for_fill(order_id)
             
             if fill_result['status'] in ['REJECTED', 'CANCELLED']:
                 print(f"❌ Order {order_id} was {fill_result['status']}. Reason: {fill_result.get('message', 'Unknown')}")
                 return

             fill_price = fill_result['price']
             if not fill_price:
                 fill_price = self.get_nifty_ltp() # Fallback
                 print(f"OHL: Fill not caught (Status: {fill_result['status']}), using: {fill_price}")
             
             # 4. Calculate Option SL & Target
             # NOTE: SL is based on Index Level. Option Price SL is approximate.
             # Option Delta approx 0.5 (ATM).
             # Risk = (Entry Index - SL Index). Option Risk ~= Risk * 0.5.
             
             # Get Index LTP to calculate Points Risk
             curr_index = self.get_nifty_ltp() 
             points_risk = abs(curr_index - index_sl_level)
             option_risk = points_risk * 0.5 # Delta 0.5 assumption
             
             sl_price = round(fill_price - option_risk, 1)
             target_price = round(fill_price + (option_risk * 2), 1) # 1:2 R:R
             
             print(f">>> [Risk] Index Risk: {points_risk:.1f} pts. Option Risk: {option_risk:.1f} pts.")
             print(f">>> [Risk] SL: {sl_price} | Target: {target_price}")
             
             # 5. Place SL Order
             sl_oid = self.place_sl_order(token, symbol, sl_price, qty)
             
             # Save
             mode = "PAPER" if self.dry_run else "LIVE"
             tid = trade_repo.save_trade(symbol, token, leg_type, qty, fill_price, sl_price, mode=mode, strategy="OHL")

             # 6. Monitor
             self.monitor_trade(token, symbol, qty, target_price, sl_price, tid, sl_oid)

        except Exception as e:
             print(f">>> [Error] Entry Failed: {e}")

    def get_first_minute_candle(self):
        """Fetches the 09:15 one-minute candle using DataFetcher."""
        try:
             # Use the centralized DataFetcher which already handles AB1004 alignment
             df = self.data_fetcher.fetch_latest_candles("99926000", interval="ONE_MINUTE")
             
             if df is not None and not df.empty:
                  # Look for the 09:15 candle in the last 10 minutes of data
                  for index, row in df.iterrows():
                      if "09:15" in str(row['timestamp']):
                          return {'open': row['open'], 'high': row['high'], 'low': row['low'], 'close': row['close']}
        except Exception as e:
            logger.error(f"OHL: Candle Fetch Error: {e}")
            
        is_mock_api = self.api.__class__.__name__ == 'MockSmartConnect'
        if is_mock_api: return self.get_mock_candle()
        return None

        
        is_mock_api = self.api.__class__.__name__ == 'MockSmartConnect'
        if is_mock_api: return self.get_mock_candle()
        return None

    def get_mock_candle(self):
         # Return a Bullish OHL candle
         return {'open': 22000, 'low': 22000, 'high': 22050, 'close': 22040}

    def wait_for_fill(self, order_id):
        """Polls for order completion. Returns dict with status and price."""
        if not order_id: return {'status': 'ERROR', 'price': None}
        if self.dry_run: return {'status': 'FILLED', 'price': 100.0}
        
        logger.info(f"OHL: Waiting for order {order_id} to fill...")
        
        for _ in range(10):
            try:
                time.sleep(1)
                book = self.api.orderBook()
                if book and book.get('data'):
                    for o in book['data']:
                        if o['orderid'] == order_id:
                            if o['status'] == 'complete':
                                fill_price = float(o['averageprice'])
                                logger.info(f"OHL: Order Filled at ₹{fill_price}")
                                return {'status': 'FILLED', 'price': fill_price}
                            elif o['status'] == 'rejected':
                                return {'status': 'REJECTED', 'message': o.get('text', 'No Reason')}
                            elif o['status'] == 'cancelled':
                                return {'status': 'CANCELLED', 'message': o.get('text', 'No Reason')}
            except: pass
        
        logger.warning(f"OHL: Order {order_id} fill timeout.")
        return {'status': 'TIMEOUT', 'price': None}

    def get_nifty_ltp(self):
        try:
            from backend.market_service import market_service
            data = market_service.get_market_data()
            return data.get('nifty', 0.0)
        except: pass
        
        is_mock_api = self.api.__class__.__name__ == 'MockSmartConnect'
        if is_mock_api: return 22040.0 
        return None

    def place_sl_order(self, token, symbol, price, qty):
        # SL for Buy is SELL STOP
        try:
            # Trigger slightly higher than limit price for SELL
            trig = round(price + 0.5, 1)
            orderparams = {
                "variety": "STOPLOSS", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "SELL", "exchange": "NFO", "ordertype": "STOPLOSS_LIMIT",
                "producttype": "INTRADAY", "duration": "DAY", "triggerprice": trig, "price": price, "quantity": qty
            }
            oid = self.api.placeOrder(orderparams)
            logger.info(f"OHL: SL Order Placed: {oid} at {price}")
            return oid
        except Exception as e:
            logger.error(f"OHL: SL Order Failed: {e}")
            return None

    def monitor_trade(self, token, symbol, qty, target, sl, trade_id=None, sl_oid=None):
         logger.info(f"OHL: Monitoring Trade. Target: {target} | SL: {sl}")
         
         while self.running:
            try:
                time.sleep(0.5) # Veteran Speed
                
                # 1. Fetch Current Price
                from backend.market_service import market_service
                ltp = market_service.get_ltp("NFO", symbol, token)
                if ltp == 0: continue
                
                # 2. Check Target Hit (Exit at Market)
                if ltp >= target:
                     logger.info(f"OHL: 🎯 Target Hit ({ltp} >= {target}). Closing Position.")
                     self.exit_at_market(token, symbol, qty, "TARGET", trade_id, sl_oid)
                     break


                # 3. Check SL Hit (The Broker SL should already trigger, but we monitor for state sync)
                if ltp <= sl:
                     logger.info(f"OHL: 🛑 Stop Loss Hit ({ltp} <= {sl}).")
                     # We assume the broker SL order closed this. 
                     # We just need to ensure the DB is updated.
                     if trade_id: trade_repo.close_trade(trade_id=trade_id, exit_price=ltp, exit_reason="SL_HIT")
                     break

                # 4. Time Check (15:15)
                if datetime.datetime.now().time() >= datetime.time(15, 15):
                     logger.info("OHL: ⏰ Time 15:15. Closing.")
                     self.exit_at_market(token, symbol, qty, "TIME", trade_id, sl_oid)
                     break
                
            except Exception as e:
                  logger.error(f"OHL Monitor Error: {e}")
                  time.sleep(10)

    def stop(self):
        """Signal strategy to stop monitoring and exit."""
        logger.info("OHL: Strategy Stop Signal Received.")
        self.running = False

    def exit_at_market(self, token, symbol, qty, reason, trade_id=None, sl_oid=None):
        """Exits position at market price."""
        try:
            if self.dry_run:
                logger.info(f"OHL: [Dry Run] Exit {symbol} ({reason})")
                if trade_id: trade_repo.close_trade(trade_id=trade_id, exit_reason=reason)
                return

            orderparams = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": "SELL", "exchange": "NFO", "ordertype": "MARKET",
                "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
            }
            oid = self.api.placeOrder(orderparams)
            logger.info(f"OHL: Market Exit Order: {oid} ({reason})")
            
            # Cancel SL
            if sl_oid:
                try:
                    self.api.cancelOrder(sl_oid, "STOPLOSS")
                    logger.info(f"OHL: Cancelled SL {sl_oid}")
                except: pass
            
            if trade_id:
                 # Fetch final fill for PnL
                 time.sleep(1)
                 trade_repo.close_trade(trade_id=trade_id, exit_reason=reason)
        except Exception as e:
            logger.error(f"OHL Exit Failed: {e}")

