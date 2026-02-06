import time
import threading
import traceback
from core.angel_connect import get_angel_session
from utils.logger import logger

class MarketService:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MarketService, cls).__new__(cls)
            cls._instance.api = None
            cls._instance.last_fetch_time = 0
            cls._instance.cache_expiry = 2 # Seconds
            cls._instance.cached_data = None
            cls._instance._lock = threading.Lock()
        return cls._instance

    def _ensure_connection(self):
        if self.api is None:
            try:
                self.api = get_angel_session()
                if self.api:
                    logger.info("MarketService: Connected to Angel One 🟢")
            except Exception as e:
                logger.error(f"MarketService Connection Failed: {e}")

    def get_market_data(self):
        """
        Fetches Nifty 50 Spot and India VIX.
        Returns dict: { nifty: float, vix: float, pnl: float }
        """
        # Cache Check (Quick Read)
        if time.time() - self.last_fetch_time < self.cache_expiry and self.cached_data:
            return self.cached_data
            
        with self._lock:
            # Double-Checked Locking
            if time.time() - self.last_fetch_time < self.cache_expiry and self.cached_data:
                return self.cached_data

            self._ensure_connection()
        
        if not self.api:
            # Fallback for UI if connection fails (or dry run without creds)
            return {"nifty": 0, "vix": 0, "pnl": 0, "error": "No API Connection"}

        try:
            # 1. Fetch Nifty 50 Spot (Token: 99926000, Exchange: NSE)
            nifty_ltp = 0.0
            resp_nifty = self.api.ltpData("NSE", "Nifty 50", "99926000")
            if resp_nifty and resp_nifty.get('status'):
                nifty_ltp = float(resp_nifty['data']['ltp'])

            # 2. Fetch India VIX (Token: 99926017, Exchange: NSE)
            vix_ltp = 0.0
            try:
                resp_vix = self.api.ltpData("NSE", "INDIA VIX", "99926017")
                if resp_vix and resp_vix.get('status'):
                    vix_ltp = float(resp_vix['data']['ltp'])
            except: 
                pass

            data = {
                "nifty": nifty_ltp,
                "vix": vix_ltp,
                "pnl": 0.0 # TODO: connect to PositionManager for real PnL
            }
            
            # Update Cache
            self.cached_data = data
            self.last_fetch_time = time.time()
            
            return data

        except Exception as e:
            logger.error(f"Market Data Fetch Error: {e}")
            return {"nifty": 0, "vix": 0, "pnl": 0, "error": str(e)}

    def get_ltp(self, exchange, symbol, token):
        """
        Generic method to fetch LTP for any token.
        """
        self._ensure_connection()
        if not self.api: return 0.0
        
        # Simple Rate Limiter / Cache could go here
        # For now, just a try-except wrapper
        
        try:
            time.sleep(0.34) # Ensure max 3 req/sec (~333ms gap)
            resp = self.api.ltpData(exchange, symbol, token)
            if resp and resp.get('status'):
                return float(resp['data']['ltp'])
        except Exception as e:
            # logger.error(f"LTP Fetch Error ({symbol}): {e}")
            pass # Suppress log spam
            
        return 0.0

market_service = MarketService()
