import threading
import time
from utils.logger import logger
from lifecycle_manager import LifecycleManager

class BotManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(BotManager, cls).__new__(cls)
            cls._instance.manager = None
            cls._instance.is_running = False
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
        from core.trade_repo import trade_repo
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
                 # We mark it as 'active' logic
                 return {"active": True, "details": primary_trade}
                 
        except Exception as e:
            logger.error(f"Error reading trade/pnl from DB: {e}")
            pass
        
        return {"active": False}

bot_manager = BotManager()
