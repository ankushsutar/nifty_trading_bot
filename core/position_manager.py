import time
import datetime
from config.settings import Config

class PositionManager:
    def __init__(self, api, dry_run=False):
        self.api = api
        self.dry_run = dry_run
        self.target_percent = 0.20 # 20% Profit Target

    def monitor(self, active_positions):
        """
        active_positions: List of dicts [{'symbol': '...', 'token': '...', 'entry_price': 100.0, 'qty': 50}]
        """
        print(f">>> [Manager] Monitoring {len(active_positions)} positions for {int(self.target_percent*100)}% Profit...")
        
        while True:
            try:
                # 1. Check Time Exit
                now = datetime.datetime.now().time()
                if now > datetime.time(15, 15):
                    print(">>> [Exit] Time is 15:15. Squaring off all positions.")
                    self.exit_all(active_positions, "TIME_EXIT")
                    break

                # 2. Check P&L
                for pos in active_positions:
                    if pos.get('exited'): continue
                    
                    ltp = self.get_ltp(pos['token'])
                    if not ltp: continue
                    
                    buy_price = pos['entry_price']
                    pnl_pct = (ltp - buy_price) / buy_price
                    
                    print(f"    {pos['symbol']} | Buy: {buy_price} | CMP: {ltp} | P&L: {pnl_pct*100:.2f}%")
                    
                    if pnl_pct >= self.target_percent:
                        print(f">>> [Profit] Target Hit for {pos['symbol']}! Exiting...")
                        self.exit_trade(pos, ltp)
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
            if self.dry_run or self.api.api_key is None:
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
