import time
import datetime
from config.settings import Config
from core.trade_repo import trade_repo

class PositionManager:
    def __init__(self, api, dry_run=False):
        self.api = api
        self.dry_run = dry_run
        self.target_percent = 0.20 # 20% Profit Target

    def monitor(self, active_positions):
        """
        active_positions: List of dicts [{'symbol': '...', 'token': '...', 'entry_price': 100.0, 'qty': 50}]
        """
        print(f">>> [Manager] Activating Superhuman Trade Management (TSL) 🚀")
        print(f">>> [TSL] Logic: Initial SL -10%. At +10% Profit, SL moves to Breakeven. Then trails Peak - 10%.")
        
        # Initialize TSL State
        for pos in active_positions:
            pos['highest_pnl'] = -1.0 # Track Peak P&L
            pos['sl_level'] = -0.10   # Initial Hard SL (-10%)
            pos['tsl_active'] = False # Flag for Breakeven activation

        while True:
            try:
                # 1. Check Time Exit
                now = datetime.datetime.now().time()
                if now > datetime.time(15, 15):
                    print(">>> [Exit] Time is 15:15. Squaring off all positions.")
                    self.exit_all(active_positions, "TIME_EXIT")
                    break

                # 2. Check P&L & Manage TSL
                for pos in active_positions:
                    if pos.get('exited'): continue
                    
                    ltp = self.get_ltp(pos['token'])
                    if not ltp: continue
                    
                    buy_price = pos['entry_price']
                    pnl_pct = (ltp - buy_price) / buy_price
                    
                    # Track Highest P&L (Peak)
                    if pnl_pct > pos['highest_pnl']:
                        pos['highest_pnl'] = pnl_pct

                    # --- TSL LOGIC START ---
                    
                    # A. Activate Breakeven (Risk-Free) at +10%
                    if pnl_pct >= 0.10 and not pos['tsl_active']:
                        pos['sl_level'] = 0.0
                        pos['tsl_active'] = True
                        print(f">>> [TSL] 🔒 Profit Hit +10%. Risk Eliminated! SL moved to BREAKEVEN (0%).")

                    # B. Dynamic Trailing (Keep 10% distance from Peak)
                    # Only active after Breakeven is triggered
                    if pos['tsl_active']:
                        # Example: Peak 15% -> SL 5%. Peak 20% -> SL 10%.
                        potential_sl = pos['highest_pnl'] - 0.10
                        
                        # Optimization: Round to 1 decimal (e.g. 0.05) to avoid noise
                        potential_sl = round(potential_sl, 2)
                        
                        # Only move SL UP, never down
                        if potential_sl > pos['sl_level']:
                             pos['sl_level'] = potential_sl
                             print(f">>> [TSL] 🚀 Rally! Peak: {pos['highest_pnl']*100:.1f}%. Trailing SL moved up to {pos['sl_level']*100:.1f}%")

                    # --- TSL LOGIC END ---

                    tsl_status = f"SL: {pos['sl_level']*100:.1f}%"
                    print(f"    {pos['symbol']} | CMP: {ltp} | P&L: {pnl_pct*100:.2f}% | {tsl_status}")
                    
                    # 3. Check Exit Condition (Price hits SL)
                    if pnl_pct <= pos['sl_level']:
                        reason = "TRAILING_SL_HIT" if pos['tsl_active'] else "STOP_LOSS_HIT"
                        print(f">>> [Exit] {reason} 🔻 P&L: {pnl_pct*100:.2f}% dropped below SL {pos['sl_level']*100:.2f}%")
                        self.exit_trade(pos, ltp, reason=reason)
                        pos['exited'] = True

                # Check if all exited
                if all(p.get('exited') for p in active_positions):
                    print(">>> [Manager] All positions closed.")
                    break
                    
                time.sleep(5)
                
            except KeyboardInterrupt:
                print(">>> [User] Manual Stop.")
                break
            except Exception as e:
                print(f">>> [Error] Monitor Loop: {e}")
                time.sleep(5)

    def get_ltp(self, token):
        try:
            # Exchange is usually NFO for options
            resp = self.api.ltpData("NFO", "token_lookup", token)
            if resp and resp.get('status'):
                return resp['data']['ltp']
            
            # Check for Mock Mode Fallback if API returns None and we are testing
            if self.dry_run or (hasattr(self.api, 'api_key') and self.api.api_key is None):
                # Simulte fluctuating price for testing
                import random
                return 100.0 + random.uniform(-5, 25) # Simulate slight profit
                
        except Exception as e:
            pass
        return None

    def exit_all(self, positions, reason):
        for pos in positions:
            if not pos.get('exited'):
                self.exit_trade(pos, "MKT", reason)

    def exit_trade(self, pos, price, reason="TARGET"):
        if self.dry_run:
            print(f">>> [Dry Run] Selling {pos['symbol']} at Market.")
            return

        try:
            orderparams = {
                "variety": "NORMAL",
                "tradingsymbol": pos['symbol'],
                "symboltoken": pos['token'],
                "transactiontype": "SELL",
                "exchange": "NFO",
                "ordertype": "MARKET",
                "producttype": "INTRADAY",
                "duration": "DAY",
                "quantity": pos['qty']
            }
            order_id = self.api.placeOrder(orderparams)
            print(f">>> [Exit] Sold {pos['symbol']} | Order ID: {order_id} | Reason: {reason}")
            
            # TODO: Cancel the SL Order if possible. (Requires storing SL Order ID)
            print(">>> [Reminder] Please manually cancel pending SL orders if not triggered.")
            
        except Exception as e:
            print(f">>> [Error] Exit Failed: {e}")

        # Update DB
        if 'id' in pos:
             trade_repo.close_trade(trade_id=pos['id'])
        else:
             trade_repo.close_trade(symbol=pos['symbol'])
