import time
import threading
import datetime

from core.angel_connect import get_angel_session
from core.regime_classifier import RegimeClassifier
from core.oi_analyzer import OIAnalyzer
from core.data_fetcher import DataFetcher
from utils.token_lookup import TokenLookup
from utils.logger import logger
import json
import os

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
            
            # Intelligence Components
            cls._instance.token_lookup = TokenLookup()
            cls._instance.token_lookup.load_scrip_master()
            cls._instance.regime_engine = RegimeClassifier()
            cls._instance.oi_engine = None # Initialize after API connect
            cls._instance.data_fetcher = None
            
            cls._instance.analysis_data = {}
            cls._instance.oi_data = {}
            cls._instance.last_analysis_time = 0
            
            # Start Background Analysis Thread only in MASTER process (Designated Backend)
            is_master = os.getenv("PROCESS_TYPE") == "BACKEND"
            if is_master:
                logger.info("MarketService: [MASTER] Starting Intelligence Loop... 🛰️")
                threading.Thread(target=cls._instance._analysis_loop, daemon=True).start()
                logger.info("MarketService: [MASTER] Starting Session Heartbeat... 💓")
                threading.Thread(target=cls._instance._heartbeat_loop, daemon=True).start()
            else:

                logger.info("MarketService: [CHILD] Passive Mode (Consuming Shared Data) 🛰️")

            
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

            # 1. Child Mode: Try loading shared intelligence from master first
            is_master = os.getenv("PROCESS_TYPE") == "BACKEND"
            if not is_master:
                try:
                    state_file = "data/market_analysis.json"
                    if os.path.exists(state_file):
                        # Only read if file is fresh (< 3 mins)
                        if time.time() - os.path.getmtime(state_file) < 185:
                             with open(state_file, "r") as f:
                                 shared_state = json.load(f)
                                 self.analysis_data = shared_state.get('analysis', {})
                                 self.oi_data = shared_state.get('oi_data', {})
                                 logger.info("MarketService: Consumed Shared Intelligence 📡")

                except Exception as e:

                    # logger.error(f"Intelligence Sharing Error: {e}")
                    pass

            self._ensure_connection()
        
        if not self.api:
            # Fallback for UI if connection fails (or dry run without creds)
            return {
                "nifty": 0, "vix": 0, "pnl": 0, "error": "No API Connection",
                "analysis": self.analysis_data,
                "oi_data": self.oi_data
            }

        try:
            # 2. Fetch LTPs (All processes still do this for real-time accuracy, 
            # but only Master does the heavy technical analysis)
            nifty_ltp = 0.0
            resp_nifty = self.api.ltpData("NSE", "Nifty 50", "99926000")
            if resp_nifty and resp_nifty.get('status'):
                nifty_ltp = float(resp_nifty['data']['ltp'])

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
                "pnl": 0.0,
                "analysis": self.analysis_data,
                "oi_data": self.oi_data
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
        Generic method to fetch LTP for any token with strict rate limiting.
        """
        self._ensure_connection()
        if not self.api: return 0.0
        
        # 1. Use shared lock to prevent concurrent API calls within this process
        with self._lock:
            try:
                # 2. Strict Rate Limiting (Angel One: ~3 req/sec)
                # We enforce a 350ms gap between any two direct API calls.
                now = time.time()
                elapsed = now - getattr(self, '_last_api_call_time', 0)
                if elapsed < 0.35:
                    time.sleep(0.35 - elapsed)
                
                self._last_api_call_time = time.time()
                
                resp = self.api.ltpData(exchange, symbol, token)
                if resp and resp.get('status'):
                    return float(resp['data']['ltp'])
            except Exception as e:
                # logger.error(f"LTP Fetch Error ({symbol}): {e}")
                pass 
            
        return 0.0

    def _heartbeat_loop(self):
        """Background loop to keep the Angel One session alive."""
        while True:
            try:
                time.sleep(600) # Every 10 minutes
                if self.api:
                    # Small harmless API call to prevent idle timeout
                    profile = self.api.getProfile()
                    if profile and profile.get('status'):
                        logger.debug("MarketService: Session Heartbeat Successful 💓")
                    else:
                        logger.warning("MarketService: Session Heartbeat Failed. Reconnecting...")
                        self.api = None
                        self._ensure_connection()
            except Exception as e:
                logger.error(f"MarketService Heartbeat Error: {e}")
                time.sleep(60)


    def _analysis_loop(self):
        """Background loop to refresh Regime and OI analysis every 3 minutes."""
        while True:
            try:
                self._ensure_connection()
                if self.api:
                    if not self.data_fetcher: self.data_fetcher = DataFetcher(self.api)
                    if not self.oi_engine: self.oi_engine = OIAnalyzer(self.api, self.token_lookup)

                    # 1. Regime Analysis
                    df = self.data_fetcher.fetch_latest_candles("99926000") # Nifty 50
                    if df is not None:
                        self.analysis_data = self.regime_engine.classify(df)
                        
                        # 2. OI Sentiment Analysis
                        ltp = df.iloc[-1]['close']
                        strike = int(round(ltp / 50) * 50)
                        from utils.expiry_calculator import get_next_weekly_expiry
                        expiry = get_next_weekly_expiry()
                        
                        self.oi_data = self.oi_engine.get_market_sentiment(expiry, strike)
                        
                        # 3. Save Shared Intelligence for Child Processes
                        state = {
                            "timestamp": datetime.datetime.now().isoformat(),
                            "analysis": self.analysis_data,
                            "oi_data": self.oi_data
                        }
                        if not os.path.exists("data"): os.makedirs("data")
                        with open("data/market_analysis.json", "w") as f:
                            json.dump(state, f, default=str)

                        
                    logger.info("MarketService: Tactical Intelligence Refreshed 🛰️")
                
                time.sleep(180) # Run every 3 minutes

            except Exception as e:
                logger.error(f"MarketService Analysis Loop Error: {e}")
                time.sleep(60) # Retry after 1 minute

market_service = MarketService()
