import time
import datetime
from bot.config.settings import Config
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.trade_repo import trade_repo
from bot.core.oi_analyzer import OIAnalyzer

class NiftyStrategy:
    def __init__(self, api, token_loader, dry_run=False):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.gatekeeper = SafetyGatekeeper(self.api, dry_run=self.dry_run)
        self.sl_orders = {} # { 'CE': order_id, 'PE': order_id }
        self.entry_prices = {} # { 'CE': price, 'PE': price }
        self.legs_active = {'CE': False, 'PE': False}
        self.leg_metadata = {'CE': None, 'PE': None} # { 'CE': {'token': ..., 'symbol': ..., 'qty': ...} }
        self.oi_analyzer = OIAnalyzer(self.api, self.token_loader)
        self.running = True
        self._ltp_cache = {} # SafeLTP Cache

    def get_atm_strike(self):
        """
        Fetches NIFTY 50 Spot Price and rounds to nearest 50.
        """
        try:
            from backend.market_service import market_service
            data = market_service.get_market_data()
            ltp = data.get('nifty', 0.0)
            
            if ltp > 0:
                print(f">>> [Market] Nifty Spot Price: {ltp}")
                return int(round(ltp / 50) * 50)
            else:
                return None
        except Exception as e:
            print(f">>> [Error] get_atm_strike failed: {e}")
            return None


    def resume(self):
        """Recover existing STRADDLE trades from MongoDB"""
        mode = "PAPER" if self.dry_run else "LIVE"
        open_trades = trade_repo.get_open_trades(mode=mode, strategy="STRADDLE")
        
        if not open_trades:
            return False
            
        print(f">>> [Resumption] Found {len(open_trades)} Open Straddle Legs.")
        for trade in open_trades:
            leg = trade['leg']
            self.legs_active[leg] = True
            self.entry_prices[leg] = trade['entry_price']
            self.leg_metadata[leg] = {
                'token': trade['token'], 
                'symbol': trade['symbol'], 
                'qty': trade['qty'],
                'id': trade['id']
            }
            # For Straddle, we often need to recover the SL orders from broker too.
            # But here we'll assume the monitor loop will find them or we manage manually.
            # (In a real pro bot, we'd fetch orderBook to find pending SLs)
            
        return True

    def execute(self, expiry, action="SELL"): # Default to SELL for Straddle (Short)
        """
        Executes the 9:20 Straddle (Short ATM CE & PE).
        """
        print(f"\n--- 9:20 STRADDLE STRATEGY ({expiry}) ---")

        # Check for Resumption
        if self.resume():
            print(">>> [Resumption] Resuming Monitoring...")
            # We need to jump to monitor. We'll pick ce/pe tokens from metadata
            ce_meta = self.leg_metadata.get('CE')
            pe_meta = self.leg_metadata.get('PE')
            
            # Simple assumption: Both legs might not exist (one could have hit SL)
            ce_token = ce_meta['token'] if ce_meta else None
            pe_token = pe_meta['token'] if pe_meta else None
            ce_symbol = ce_meta['symbol'] if ce_meta else None
            pe_symbol = pe_meta['symbol'] if pe_meta else None
            qty = (ce_meta['qty'] if ce_meta else pe_meta['qty']) if (ce_meta or pe_meta) else 0
            
            self.monitor_straddle(ce_token, pe_token, ce_symbol, pe_symbol, qty)
            return

        # 1. Time Check (Ideally run at 09:20, but we allow manual run with check)
        now = datetime.datetime.now().time()
        # if not (datetime.time(9, 15) <= now <= datetime.time(9, 30)):
        #     print(f">>> [Warning] Running 9:20 Strategy at {now}. Ensure this is intended.")

        if not self.gatekeeper.check_funds(required_margin_per_lot=150000):
             return
        if not self.gatekeeper.check_max_daily_loss(0): # Initialize with 0 loss
             return
        if self.gatekeeper.is_blackout_period():
             return

        # 2. Market Sentiment Guard (Professional Enhancement)
        print(">>> [Market] Running Pre-Entry Sentiment Analysis...")
        atm_strike = self.get_atm_strike()
        if not atm_strike:
            print(">>> [Error] Could not fetch ATM Strike for entry. Aborting.")
            return

        sentiment = self.oi_analyzer.get_market_sentiment(expiry, atm_strike)
        if sentiment['bias'] != "NEUTRAL":
            print(f">>> [Risk] ⚠️ Sentiment Bias is {sentiment['bias']}. Straddle entry postponed (Neutral preferred).")
            return
        else:
            print(">>> [Market] ✅ Sentiment is Neutral. Proceeding with Straddle.")

        # 3. VIX Check & Sizing
        quantity_multiplier = self.gatekeeper.get_vix_adjustment()
        # Fix: Ensure quantity is a multiple of Lot Size (Min 1 Lot)
        # If multiplier is 0.5, we cannot trade 0.5 lots. Default to 1 lot.
        adjusted_lots = max(1, int(quantity_multiplier))
        quantity = int(Config.NIFTY_LOT_SIZE * adjusted_lots)
        print(f">>> [Setup] Quantity per leg: {quantity} (VIX Multiplier: {quantity_multiplier} -> {adjusted_lots} Lots)")

        # 3. ATM Strike
        strike = self.get_atm_strike()
        if not strike:
            if self.dry_run: strike = 23000 # Mock
            else: return
        print(f">>> [Setup] ATM Strike: {strike}")

        # 4. Get Tokens
        ce_token, ce_symbol = self.token_loader.get_token("NIFTY", expiry, strike, "CE")
        pe_token, pe_symbol = self.token_loader.get_token("NIFTY", expiry, strike, "PE")
        
        if not ce_token or not pe_token:
            print(">>> [Error] Tokens not found.")
            return

        # 5. Place Entry Orders (SELL)
        print(">>> [Trade] Selling Straddle Legs...")
        ce_order = self.place_order(ce_token, ce_symbol, "SELL", quantity)
        pe_order = self.place_order(pe_token, pe_symbol, "SELL", quantity)
        
        if self.dry_run:
             print(">>> [Dry Run] End of execution path (Simulated).")
             # In Dry Run, we proceed to simulate Stop Loss placement if IDs are valid mocks
             pass
        else:
             pass

        # 6. Wait for Fills & Capture Prices
        print(">>> [Trade] Waiting for fills to set SL...")
        ce_result = self.wait_for_fill(ce_order, symbol=ce_symbol, token=ce_token)
        pe_result = self.wait_for_fill(pe_order, symbol=pe_symbol, token=pe_token)

        mode = "PAPER" if self.dry_run else "LIVE"
        
        # Handle CE Fill
        if ce_result['status'] == 'FILLED':
             ce_price = ce_result['price']
             self.entry_prices['CE'] = ce_price
             self.legs_active['CE'] = True
             tid = trade_repo.save_trade(ce_symbol, ce_token, "CE", quantity, ce_price, 0.0, side="SELL", mode=mode, strategy="STRADDLE")
             self.leg_metadata['CE'] = {'token': ce_token, 'symbol': ce_symbol, 'qty': quantity, 'id': tid}
        else:
             print(f"❌ CE Order {ce_result['status']}: {ce_result.get('message', 'Unknown')}")
             
        # Handle PE Fill
        if pe_result['status'] == 'FILLED':
             pe_price = pe_result['price']
             self.entry_prices['PE'] = pe_price
             self.legs_active['PE'] = True
             tid = trade_repo.save_trade(pe_symbol, pe_token, "PE", quantity, pe_price, 0.0, side="SELL", mode=mode, strategy="STRADDLE")
             self.leg_metadata['PE'] = {'token': pe_token, 'symbol': pe_symbol, 'qty': quantity, 'id': tid}
        else:
             print(f"❌ PE Order {pe_result['status']}: {pe_result.get('message', 'Unknown')}")

        # 7. Place Initial Stop Loss (25%)
        # For Sell Order, SL is Buy Stop Limit at (Price * 1.25)
        if self.legs_active['CE']:
            sl_price = round(ce_price * 1.25, 1)
            buy_trigger = sl_price
            buy_price = round(sl_price + 1.0, 1)
            self.sl_orders['CE'] = self.place_sl_order(ce_token, ce_symbol, buy_trigger, buy_price, quantity)
            
        if self.legs_active['PE']:
            sl_price = round(pe_price * 1.25, 1)
            buy_trigger = sl_price
            buy_price = round(sl_price + 1.0, 1)
            self.sl_orders['PE'] = self.place_sl_order(pe_token, pe_symbol, buy_trigger, buy_price, quantity)

        # 8. Monitor Loop
        self.monitor_straddle(ce_token, pe_token, ce_symbol, pe_symbol, quantity)


    def get_ltp(self, token):
        try:
             # throttle to 1s
            now = time.time()
            if hasattr(self, '_ltp_cache') and token in self._ltp_cache:
                last_time, last_val = self._ltp_cache[token]
                if now - last_time < 0.9: return last_val

            resp = self.api.ltpData("NFO", "token_lookup", token)
            if resp and resp.get('status'):
                val = float(resp['data']['ltp'])
                self._ltp_cache[token] = (now, val)
                return val
        except Exception as e:
            print(f">>> [Error] Straddle get_ltp: {e}")
        if self.dry_run: return 100.0 # Mock
        return None

    def monitor_straddle(self, ce_token, pe_token, ce_symbol, pe_symbol, quantity):
        print(f"\n>>> [Monitor] Straddle Active. SL Orders: {self.sl_orders}")
        sl_moved_to_cost = False
        
        while self.running:
            try:
                time.sleep(3)
                now = datetime.datetime.now().time()
                
                # Check Time Exit
                if now >= datetime.time(15, 15):
                    print(">>> [Exit] Time 15:15. Closing all positions.")
                    self.exit_at_market(ce_token, ce_symbol, quantity, "TIME")
                    self.exit_at_market(pe_token, pe_symbol, quantity, "TIME")
                    break

                # Check SL Status
                # If one leg hits SL (SL Order Complete), move other to Cost.
                ce_sl_status = self.get_order_status(self.sl_orders.get('CE'))
                pe_sl_status = self.get_order_status(self.sl_orders.get('PE'))
                
                # Leg 1 Hit SL -> Move Leg 2 to Cost
                if ce_sl_status == 'complete' and self.legs_active['CE'] and not sl_moved_to_cost:
                    print(f">>> [Risk] CE Stop Loss Hit! Moving PE SL to Cost.")
                    self.legs_active['CE'] = False
                    self.modify_sl_to_cost('PE', pe_token, pe_symbol, quantity)
                    sl_moved_to_cost = True

                # Leg 2 Hit SL -> Move Leg 1 to Cost
                if pe_sl_status == 'complete' and self.legs_active['PE'] and not sl_moved_to_cost:
                    print(f">>> [Risk] PE Stop Loss Hit! Moving CE SL to Cost.")
                    self.legs_active['PE'] = False
                    self.modify_sl_to_cost('CE', ce_token, ce_symbol, quantity)
                    sl_moved_to_cost = True

                # Check if Both Exited
                if not self.legs_active['CE'] and not self.legs_active['PE']:
                    print(">>> [Exit] Both Legs Closed.")
                    break
                    
                # Check Global P&L for Target (25% Decay of Combined Premium)
                total_entry = self.entry_prices.get('CE', 0) + self.entry_prices.get('PE', 0)
                
                # Get Current Prices
                ce_ltp = self.get_ltp(ce_token)
                pe_ltp = self.get_ltp(pe_token)
                
                if total_entry > 0 and ce_ltp and pe_ltp:
                    current_sum = ce_ltp + pe_ltp
                    
                    # Target: 25% Profit (Premium decayed by 25%)
                    target_sum = total_entry * 0.75 
                    
                    if current_sum <= target_sum:
                        print(f">>> [Profit] Target Hit! Combined Premium {current_sum} <= {target_sum} (Entry: {total_entry})")
                        self.exit_at_market(ce_token, ce_symbol, quantity, "TARGET")
                        self.exit_at_market(pe_token, pe_symbol, quantity, "TARGET")
                        break
                
            except KeyboardInterrupt:
                print(">>> [User] Stop Signal.")
                break
            except Exception as e:
                print(f">>> [Error] Monitor: {e}")
                time.sleep(5)

    def modify_sl_to_cost(self, leg_type, token, symbol, quantity):
        # Move SL to Entry Price
        # Fallback: Cancel Old -> Place New
        
        order_id = self.sl_orders.get(leg_type)
        if not order_id: return
        
        entry_price = self.entry_prices.get(leg_type)
        if not entry_price: return
        
        print(f">>> [Risk] Modifying {leg_type} SL to Cost: {entry_price}")
        
        try:
             # 1. Cancel Old SL
             if not self.dry_run:
                 self.api.cancelOrder(order_id, "NORMAL") # Need to check if cancelOrder needs more args? Usually orderid.
                 # SmartAPI: cancelOrder(orderid, variety)
                 # Wait a bit?
                 time.sleep(1)
             
             # 2. Place New SL at Cost
             # Trigger at Cost, Price slightly above (Buy SL for Sell Entry)
             trigger_price = entry_price
             price = round(entry_price + 1.0, 1)
             
             new_id = self.place_sl_order(token, symbol, trigger_price, price, quantity)
             self.sl_orders[leg_type] = new_id
             
             print(f"    >>> Modified {leg_type} SL to {entry_price}")
             
        except Exception as e:
             print(f">>> [Error] Modify SL Failed: {e}")

    def place_order(self, token, symbol, action, qty, exit_price=0.0, pnl=0.0, reason="TIME"):
        if self.dry_run:
            print(f">>> [Dry Run] Simulated {action} MARKET Order for {symbol}")
            if action == "BUY" and qty > 0:
                 # Closing Short Straddle
                 trade_repo.close_trade(symbol=symbol, exit_price=exit_price, pnl=pnl, exit_reason=reason)
            return "dry_run_id"

        try:
            orderparams = {
                "variety": "NORMAL",
                "tradingsymbol": symbol,
                "symboltoken": token,
                "transactiontype": action,
                "exchange": "NFO",
                "ordertype": "MARKET",
                "producttype": "INTRADAY",
                "duration": "DAY",
                "quantity": qty
            }
            order_id = self.api.placeOrder(orderparams)
            
            if not order_id:
                print(f">>> [Error] {action} Order Failed (None returned)")
                return None

            print(f">>> [Order] {action} {symbol} | ID: {order_id}")
            
            # If Exit Order (BUY), Verify Fill before DB Close
            if action == "BUY" and qty > 0:
                 print(f">>> [Exit] Verifying Fill for {symbol}...")
                 fill_result = self.wait_for_fill(order_id)
                 
                 if fill_result['status'] == 'FILLED':
                     real_exit_price = fill_result['price']
                     print(f">>> [Success] Exit Filled @ {real_exit_price}")
                     # Recalculate PnL with real price if possible, or just log valid exit
                     # Note: pnl passed in arg was estimated. 
                     # Ideally we recalculate, but for now we trust the flow or update if needed.
                     
                     trade_repo.close_trade(symbol=symbol, exit_price=real_exit_price, pnl=pnl, exit_reason=reason)
                     
                 elif fill_result['status'] in ['REJECTED', 'CANCELLED']:
                     print(f"❌ Exit REJECTED. Reason: {fill_result.get('message')}")
                     # Do not close DB. Keep position active.
                     return None
                 else:
                     print(f"⚠️ Exit Verification Timeout. Status: {fill_result['status']}")
                     # Do not close DB? Or assume it worked? 
                     # Safest: Don't close DB. Let next loop retry or manual intervention.
                     return None
            
            return order_id
        except Exception as e:
            print(f">>> [Error] Place Order: {e}")
            return None

    def place_sl_order(self, token, symbol, trigger_price, price, qty):
        if self.dry_run:
             print(f">>> [Dry Run] Would place SL for {symbol} | Trig: {trigger_price}")
             return "dry_run_sl_id"

        try:
            # SL for Sell Entry is a BUY Order
            orderparams = {
                "variety": "STOPLOSS",
                "tradingsymbol": symbol,
                "symboltoken": token,
                "transactiontype": "BUY",
                "exchange": "NFO",
                "ordertype": "STOPLOSS_LIMIT",
                "producttype": "INTRADAY",
                "duration": "DAY",
                "triggerprice": trigger_price,
                "price": price, 
                "quantity": qty
            }
            oid = self.api.placeOrder(orderparams)
            print(f">>> [Risk] SL Placed {symbol} | Trig: {trigger_price} | ID: {oid}")
            return oid
        except Exception as e:
            print(f">>> [Error] SL Place: {e}")
            return None

    def wait_for_fill(self, order_id, exchange="NFO", symbol=None, token=None):
        if not order_id: return {'status': 'ERROR', 'price': None}
        
        if self.dry_run:
            # In Dry Run, we use REAL Market Price at time of "entry"
            fill_price = 150.0
            if symbol and token:
                try:
                    resp = self.api.ltpData(exchange, symbol, token)
                    if resp and resp.get('status'):
                        fill_price = float(resp['data']['ltp'])
                except: pass
            return {'status': 'FILLED', 'price': fill_price}
        
        # Simple polling
        for _ in range(5):
             try:
                 book = self.api.orderBook()
                 if book and book.get('data'):
                     for o in book['data']:
                         if o['orderid'] == order_id:
                             if o['status'] == 'complete':
                                 return {'status': 'FILLED', 'price': float(o['averageprice'])}
                             elif o['status'] == 'rejected':
                                 return {'status': 'REJECTED', 'message': o.get('text', 'No Reason')}
                             elif o['status'] == 'cancelled':
                                 return {'status': 'CANCELLED', 'message': o.get('text', 'No Reason')}
             except: pass
             time.sleep(1)
        return {'status': 'TIMEOUT', 'price': None}

    def get_order_status(self, order_id):
        if not order_id: return None
        # return 'complete' or 'open'
        if self.dry_run: return 'open' # Mock always open
        try:
             book = self.api.orderBook()
             if book and book.get('data'):
                 for o in book['data']:
                     if o['orderid'] == order_id:
                         return o['status']
        except: pass
        return 'unknown'

    def exit_at_market(self, token, symbol, qty, reason):
        if self.active_position_exists(symbol): # Check if open
             # Fetch Current LTP for P&L recording
             current_price = 0.0
             pnl = 0.0
             try:
                 resp = self.api.ltpData("NFO", symbol, token)
                 if resp and resp.get('status'):
                     current_price = float(resp['data']['ltp'])
                     # Calculate P&L for Short Position (Entry - Exit)
                     leg_type = 'CE' if 'CE' in symbol else 'PE'
                     entry = self.entry_prices.get(leg_type, 0)
                     if entry > 0:
                         pnl = (entry - current_price) * qty
             except: pass

             self.place_order(token, symbol, "BUY", qty, exit_price=current_price, pnl=pnl, reason=reason) # Buy to Cover
             print(f">>> [Exit] Covered {symbol} ({reason}) | PnL: {pnl}")

    def active_position_exists(self, symbol):
        # Implementation to check net qty or rely on internal flag
        return True # Simplified

    def stop(self):
        """Graceful Square-off on Shutdown"""
        print("\n>>> [Strategy] Stop Signal Received. Squaring off positions...")
        self.running = False
        for leg in ['CE', 'PE']:
            if self.legs_active[leg]:
                meta = self.leg_metadata[leg]
                if meta:
                    print(f">>> [Stop] Closing {meta['symbol']}...")
                    self.exit_at_market(meta['token'], meta['symbol'], meta['qty'], "MANUAL_STOP")
                    self.legs_active[leg] = False
        print(">>> [Strategy] Shutdown Cleanup Complete.")
