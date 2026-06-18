import threading
import time
from bot.utils.logger import logger
from bot.lifecycle_manager import LifecycleManager

class BotManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(BotManager, cls).__new__(cls)
            cls._instance.manager = None
            cls._instance.is_running = False
            cls._instance.current_mode = "PAPER" # Default to PAPER
            
            # Startup Cleanup: Flush any stale trades from previous days
            try:
                from bot.core.trade_repo import trade_repo
                trade_repo.cleanup_stale_trades()
                
                # Fetch Today's Open Trades for Resumption
                open_trades = trade_repo.get_open_trades()
                if open_trades:
                    print(f">>> [Startup] Found {len(open_trades)} Open Trades for today in MongoDB.")
                    for t in open_trades:
                        print(f"    - {t['strategy']} | {t['symbol']} | Qty: {t['qty']} | Status: {t['status']}")
                else:
                    print(">>> [Startup] No open trades found in MongoDB for today.")
            except Exception as e:
                print(f"BotManager Startup Cleanup Error: {e}")
                
        return cls._instance

    def start_bot(self, strategy_type="AUTO", dry_run=True):
        if self.is_running and self.manager and self.manager.running:
            return {"status": "error", "message": "Bot is already running."}

        logger.info(f"Starting Bot Manager (Lifecycle Mode)... DryRun: {dry_run}")
        
        try:
            # Initialize Lifecycle Manager instead of direct Strategy
            # We treat 'test_mode' as False by default for UI starts (unless implied?)
            # Usually UI 'Dry Run' maps to dry_run=True.
            
            self.manager = LifecycleManager(dry_run=dry_run, test_mode=False)
            self.manager.start_lifecycle()
            self.is_running = True
            self.current_mode = "PAPER" if dry_run else "LIVE"
            
            return {"status": "success", "message": f"Bot Lifecycle started in {'DRY RUN' if dry_run else 'LIVE'} mode."}

        except Exception as e:
            logger.error(f"Failed to start bot: {e}")
            self.is_running = False
            return {"status": "error", "message": str(e)}

    def stop_bot(self):
        if not self.is_running or not self.manager:
             return {"status": "error", "message": "Bot is not running."}
        
        self.manager.stop_lifecycle()
        self.is_running = False
        return {"status": "success", "message": "Stop signal sent to Lifecycle Manager."}

    def get_status(self):
        running = self.manager.running if (self.manager and self.manager.running) else False
        # Sync local state
        self.is_running = running
        
        return {
            "status": "RUNNING" if running else "STOPPED",
            "strategy": "LIFECYCLE_MANAGER"
        }

    def get_active_trade(self):
        # Lifecycle Manager runs a subprocess. We don't have direct access to the RAM of the child process 
        # to get its active_position variable easily without an IPC (Inter-Process Communication) file or database.
        # However, the child process (main.py) writes to logs.
        # For a Senior Architect solution, we should have the child process write its state to a JSON file 
        # (like 'trade_state.json') which we already implemented in MomentumStrategy!
        
        # So we can just read that file.
        # Use SQLite Repository
        from bot.core.trade_repo import trade_repo
        from backend.market_service import market_service
        
        try:
            open_trades = trade_repo.get_open_trades()
            
            if open_trades:
                 total_pnl = 0.0
                 primary_trade = open_trades[0] # Use first trade for display details
                 
                 for trade in open_trades:
                     token = trade['token']
                     symbol = trade['symbol']
                     entry_price = float(trade['entry_price'])
                     qty = int(trade['qty'])
                     leg = trade['leg']
                     
                     # Fetch Live Price
                     current_price = market_service.get_ltp("NFO", symbol, token)
                     
                     trade_pnl = 0.0
                     
                     # PnL Calculation based on Side (BUY vs SELL)
                     trade_side = trade.get('side', 'BUY')
                     
                     if current_price > 0:
                         if trade_side == 'SELL':
                              # Short: Profit if Price Falls
                              trade_pnl = (entry_price - current_price) * qty
                         else:
                              # Long: Profit if Price Rises
                              trade_pnl = (current_price - entry_price) * qty
                     
                     total_pnl += trade_pnl
                 
                 primary_trade['pnl'] = round(total_pnl, 2)
                 # Sanitize for JSON (Remove ObjectId)
                 if "_id" in primary_trade: del primary_trade["_id"]
                 
                 return {"active": True, "details": primary_trade}
                 
        except Exception as e:
            logger.error(f"Error reading trade/pnl from DB: {e}")
            pass
        
        return {"active": False}

    def _get_intrinsic_value(self, symbol, spot_price):
        """
        Calculates intrinsic value of an option symbol based on Nifty Spot.
        Symbol Format: NIFTY10FEB2625950PE
        """
        try:
            import re
            # Extract strike (5 digits before CE/PE)
            match = re.search(r'(\d{5})([CP]E)$', symbol)
            if match:
                strike = float(match.group(1))
                opt_type = match.group(2)
                if opt_type == 'CE':
                    return max(0.0, spot_price - strike)
                else:
                    return max(0.0, strike - spot_price)
        except:
            pass
        return 0.0

    def get_daily_summary(self):
        """
        Returns {
            "daily_pnl": float,
            "trades": [list of trade dicts]
        }
        """
        from bot.core.trade_repo import trade_repo
        from backend.market_service import market_service
        
        try:
            # Fetch all daily trades regardless of session mode.
            today_trades = trade_repo.get_today_trades()
            
            total_realized_pnl = 0.0
            total_unrealized_pnl = 0.0
            
            summary_trades = []
            
            for trade in today_trades:
                trade_pnl = 0.0
                status = trade['status']
                
                # If Closed, use stored PnL
                if status == 'CLOSED':
                    trade_pnl = trade.get('pnl', 0.0) or 0.0 # Handle None
                    total_realized_pnl += trade_pnl
                    
                # If Open, calculate unrealized PnL
                elif status == 'OPEN':
                    try:
                        entry_price = float(trade['entry_price'])
                        qty = int(trade['qty'])
                        token = trade['token']
                        symbol = trade['symbol']
                        side = trade.get('side', 'BUY')
                        
                        current_price = market_service.get_ltp("NFO", symbol, token)
                        
                        # Expiry Guard: If LTP is lower than intrinsic, use intrinsic
                        # (Market spreads can be huge at expiry close)
                        nifty_data = market_service.get_market_data()
                        spot = nifty_data.get('nifty', 0)
                        if spot > 0:
                            intrinsic = self._get_intrinsic_value(symbol, spot)
                            if current_price < intrinsic:
                                # logger.info(f"Expiry Guard: Adjusting {symbol} price {current_price} -> {intrinsic} (Intrinsic)")
                                current_price = intrinsic

                        if current_price >= 0:
                            if side == 'SELL':
                                trade_pnl = (entry_price - current_price) * qty
                            else:
                                trade_pnl = (current_price - entry_price) * qty
                                
                        total_unrealized_pnl += trade_pnl
                        # Inject current PnL into trade dict for UI
                        trade['current_pnl'] = round(trade_pnl, 2)
                    except Exception as e:
                        logger.error(f"Daily Summary Open Trade Calc Error: {e}")
                
                # Sanitize for JSON (Remove ObjectId and serialize dates)
                if "_id" in trade: del trade["_id"]
                for k, v in trade.items():
                    if hasattr(v, 'isoformat'):
                        trade[k] = v.isoformat()
                
                summary_trades.append(trade)
                
            return {
                "daily_pnl": round(total_realized_pnl + total_unrealized_pnl, 2),
                "realized_pnl": round(total_realized_pnl, 2),
                "unrealized_pnl": round(total_unrealized_pnl, 2),
                "trades": summary_trades,
                "mode": self.current_mode
            }
            
        except Exception as e:
            logger.error(f"Daily Summary Error: {e}")
            return {"daily_pnl": 0.0, "trades": []}

bot_manager = BotManager()
