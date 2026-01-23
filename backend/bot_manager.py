import threading
import time
from core.angel_connect import get_angel_session
from utils.token_lookup import TokenLookup
from strategies.momentum_strategy import MomentumStrategy
from utils.logger import logger
from utils.expiry_calculator import get_next_weekly_expiry

class BotManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(BotManager, cls).__new__(cls)
            cls._instance.thread = None
            cls._instance.strategy_instance = None
            cls._instance.is_running = False
        return cls._instance

    def start_bot(self, strategy_type="MOMENTUM", dry_run=True):
        if self.is_running:
             # Graceful Restart Logic:
             # If the specific strategy instance has been signaled to stop, wait for it.
            if self.strategy_instance and self.strategy_instance.stop_requested and self.thread:
                 print("BotManager: Previous instance stopping... Waiting for cleanup.")
                 self.thread.join(timeout=5) # Wait up to 5s
                 if self.thread.is_alive():
                     return {"status": "error", "message": "Bot is still shutting down. Please wait."}
            else:
                return {"status": "error", "message": "Bot is already running."}

        logger.info(f"Starting Bot Manager... Strategy: {strategy_type}, DryRun: {dry_run}")
        
        # 1. Initialize API & Data
        try:
            # Note: In a real app, we might want to dependency inject these or cache them
            # to avoid relogin every start if not expired.
            api = get_angel_session()
            if not api:
                 return {"status": "error", "message": "Login Failed"}
            
            token_loader = TokenLookup()
            # Loading scrip master takes time, might block the request. 
            # Ideally this happens on startup or is cached.
            # token_loader.load_scrip_master() # strategy will call this or we do it here.
            # MomentumStrategy calls token_loader.get_token which calls load if needed.
            
            if strategy_type == "MOMENTUM":
                self.strategy_instance = MomentumStrategy(api, token_loader, dry_run=dry_run)
            else:
                return {"status": "error", "message": f"Strategy {strategy_type} not implemented."}

            # 2. Start Thread
            self.thread = threading.Thread(target=self._run_strategy)
            self.thread.daemon = True
            self.thread.start()
            self.is_running = True
            
            return {"status": "success", "message": f"Bot started in {'DRY RUN' if dry_run else 'LIVE'} mode."}

        except Exception as e:
            logger.error(f"Failed to start bot: {e}")
            return {"status": "error", "message": str(e)}

    def _run_strategy(self):
        """Wrapper to run the strategy execute method."""
        try:
            expiry = get_next_weekly_expiry()
            self.strategy_instance.execute(expiry)
        except Exception as e:
            logger.error(f"Thread Error: {e}")
        finally:
            self.is_running = False
            self.strategy_instance = None
            logger.info("Bot Thread Finished.")

    def stop_bot(self):
        if not self.is_running or not self.strategy_instance:
             return {"status": "error", "message": "Bot is not running."}
        
        self.strategy_instance.stop()
        # Wait a bit? No, just return accepted. 
        # The thread will break the loop and set is_running = False via finally block.
        return {"status": "success", "message": "Stop signal sent."}

    def get_status(self):
        return {
            "status": "RUNNING" if self.is_running else "STOPPED",
            "strategy": "MOMENTUM" if self.is_running else None
        }

    def get_active_trade(self):
        if self.is_running and self.strategy_instance:
             pos = self.strategy_instance.get_current_position()
             if pos:
                 # Calculate Unrealized PnL (Approximate)
                 # Note: Real realtime fetching might slow down the loop if shared API object is used unsafely.
                 # For now, we return static details and maybe last known LTP if strategy tracked it.
                 # Better approach: Strategy updates 'last_ltp' key in position dict during its loop.
                 return {"active": True, "details": pos}
        return {"active": False}

bot_manager = BotManager()
