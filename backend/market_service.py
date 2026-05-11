import time
import threading
import datetime

from bot.core.angel_connect import get_angel_session
from bot.core.regime_classifier import RegimeClassifier
from bot.core.oi_analyzer import OIAnalyzer
from bot.core.alpha_engine import AlphaEngine
from bot.core.data_fetcher import DataFetcher
from bot.utils.token_lookup import TokenLookup
from bot.core.levels_provider import levels_provider
from bot.utils.logger import logger
import json
import os
import tempfile
import pandas as pd

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
            cls._instance.levels_data = {}
            cls._instance.last_analysis_time = 0
            
            # --- STARTUP WARM-UP: Load Last Known Intelligence ---
            # This ensures /api/market-data is populated immediately for the UI
            try:
                state_file = os.path.join(os.getcwd(), "data", "market_analysis.json")
                if os.path.exists(state_file):
                    with open(state_file, "r") as f:
                        shared_state = json.load(f)
                        cls._instance.analysis_data = shared_state.get('analysis', {})
                        cls._instance.oi_data = shared_state.get('oi_data', {})
                        cls._instance.levels_data = shared_state.get('levels', {})
                        logger.info("MarketService: Startup Intelligence Loaded from disk 💾")
            except Exception as e:
                logger.warning(f"MarketService: Startup Warm-up Failed: {e}")

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
        # 1. Check for Forced Refresh Flag (from Rate Limiter)
        flag_file = os.path.join(os.getcwd(), "data", "session_refresh.flag")
        if os.path.exists(flag_file):
            logger.warning(">>> [MarketService] Session Refresh Flag Detected! Forcing New Session...")
            try:
                # 1. Reset Circuit Breaker FIRST to avoid waiting for the penalty time during recovery
                from bot.utils.rate_limiter import rate_limiter
                rate_limiter.reset_circuit_breaker()
                
                # 2. Force Refresh (Now it won't be blocked by CB)
                self.api = get_angel_session(force_refresh=True)
                
                if self.api:
                    logger.info("MarketService: Session Refreshed Successfully 🟢")
                    
                    # Update Components with new API instance
                    if self.data_fetcher: 
                        self.data_fetcher.api = self.api
                    else:
                        self.data_fetcher = DataFetcher(self.api)
                        
                    if self.oi_engine: self.oi_engine.api = self.api
                    
                    # Remove Flag
                    os.remove(flag_file)
                else:
                    logger.error("Session Refresh Failed: API is None.")
            except Exception as e:
                logger.error(f"Session Refresh Error: {e}")

        # 2. Normal Connection Check
        if self.api is None:
            try:
                self.api = get_angel_session()
                if self.api:
                    logger.info("MarketService: Connected to Angel One 🟢")
                    if not self.data_fetcher:
                        self.data_fetcher = DataFetcher(self.api)
                    else:
                        self.data_fetcher.api = self.api
            except Exception as e:
                logger.error(f"MarketService Connection Failed: {e}")


    def get_market_data(self):
        """
        Fetches Nifty 50 Spot and India VIX.
        Returns dict: { nifty: float, vix: float, pnl: float }
        """
        # Cache Check (Quick Read)
        # Increased cache to 20s to further reduce load (Combined with DataFetcher 15s cache)
        if time.time() - self.last_fetch_time < 20 and self.cached_data:
            return self.cached_data
            
        with self._lock:
            # Double-Checked Locking
            if time.time() - self.last_fetch_time < 20 and self.cached_data:
                return self.cached_data

            # 1. Child Mode: Try loading shared intelligence from master first
            is_master = os.getenv("PROCESS_TYPE") == "BACKEND"
            if not is_master:
                # --- FIX: Startup Sequencing Guard ---
                # On first call, poll for the shared file for up to 15s so the
                # backend master process has time to write its first analysis.
                # This prevents child processes from firing REST calls at startup
                # simultaneously with the backend.
                state_file = "data/market_analysis.json"
                if not os.path.exists(state_file) or time.time() - os.path.getmtime(state_file) > 300:
                    # File missing or very stale — wait for backend to warm up
                    startup_wait_start = time.time()
                    while time.time() - startup_wait_start < 15:
                        if os.path.exists(state_file) and time.time() - os.path.getmtime(state_file) < 300:
                            break
                        time.sleep(1)

                try:
                    if os.path.exists(state_file):
                        # Only read if file is fresh (< 3 mins)
                        if time.time() - os.path.getmtime(state_file) < 310:  # FIX: match 300s backend refresh cadence (+10s buffer)
                            with open(state_file, "r") as f:
                                shared_state = json.load(f)
                                self.analysis_data = shared_state.get('analysis', {})
                                # Read oi_data dict (new format) or fall back to legacy flat keys
                                if 'oi_data' in shared_state:
                                    self.oi_data = shared_state['oi_data']
                                if 'levels' in shared_state:
                                    self.levels_data = shared_state['levels']
                                else:
                                    # Legacy format compatibility
                                    self.oi_data = {
                                        "bias": shared_state.get("sentiment", "NEUTRAL"),
                                        "pcr": shared_state.get("pcr", 1.0),
                                        "delta_ratio": shared_state.get("oi_delta_ratio", 1.0),
                                    }
                                logger.info("MarketService: Consumed Shared Intelligence 📡")
                except Exception as e:
                    logger.warning(f"Intelligence Sharing Error: {e}")

            self._ensure_connection()
        
        if not self.api:
            # Fallback for UI if connection fails (or dry run without creds)
            return {
                "nifty": 0, "vix": 0, "pnl": 0, "error": "No API Connection",
                "analysis": self.analysis_data,
                "oi_data": self.oi_data,
                "levels": self.levels_data
            }

        try:
            # 2. Fetch LTPs using centralized DataFetcher (with 5s Cache)
            # This prevents 1 req/sec polling from UI saturating the API
            nifty_ltp = self.get_ltp("NSE", "Nifty 50", "99926000")
            
            vix_ltp = 0.0
            try:
                vix_ltp = self.get_ltp("NSE", "INDIA VIX", "99926017")
            except: pass

            data = {
                "nifty": nifty_ltp,
                "vix": vix_ltp,
                "pnl": 0.0,
                "analysis": self.analysis_data,
                "oi_data": self.oi_data,
                "levels": self.levels_data
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
        Delegates to DataFetcher to ensure global rate limiting and caching (5s).
        """
        self._ensure_connection()
        
        # FIX: Use DataFetcher instead of direct API call
        # DataFetcher handles:
        # 1. 5-second In-Memory Cache (prevents spam from UI)
        # 2. Rate Limiting
        # 3. Connection Checking
        if not self.data_fetcher:
            self.data_fetcher = DataFetcher(self.api)
            
        return self.data_fetcher.get_ltp(token, exchange)

    def _heartbeat_loop(self):
        """Background loop to keep the Angel One session alive."""
        while True:
            try:
                time.sleep(600) # Every 10 minutes
                if self.api:
                    # Small harmless API call to prevent idle timeout
                    from bot.utils.rate_limiter import rate_limiter
                    rate_limiter.wait()
                    
                    profile = self.api.getProfile(self.api.refresh_token)
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
        """Background loop to refresh Regime and OI analysis every 5 minutes."""
        import random
        # 1. Startup De-sync Jitter: prevent master/child overlapping on startup
        time.sleep(random.uniform(5, 15)) 
        
        while True:
            try:
                # 2. Strict Master Check: only designated BACKEND may fetch
                is_master = os.getenv("PROCESS_TYPE") == "BACKEND"
                if not is_master:
                    logger.warning("MarketService Analysis Loop: [CHILD] Detected. Halting child loop.")
                    break
                self._ensure_connection()
                if self.api:
                    if not self.data_fetcher: self.data_fetcher = DataFetcher(self.api)
                    if not self.oi_engine: self.oi_engine = OIAnalyzer(self.api, self.token_lookup)
                    if not hasattr(self, 'alpha_engine') or self.alpha_engine is None:
                        self.alpha_engine = AlphaEngine(self.api, self.token_lookup)
                    levels_provider.data_fetcher.api = self.api # Keep sync

                    # --- OPTIMIZATION: Check if another process already refreshed intelligence recently ---
                    # Prevents double-fetching if the server reloaded or if multiple instances are running.
                    state_file = "data/market_analysis.json"
                    if os.path.exists(state_file):
                        file_age = time.time() - os.path.getmtime(state_file)
                        if file_age < 120: # If less than 2 mins old, skip this cycle
                            logger.info(f"MarketService: Shared intelligence is fresh ({int(file_age)}s old). Skipping fetch.")
                            time.sleep(120) 
                            continue

                    # 0. Levels Analysis (S&R)
                    self.levels_data = levels_provider.get_levels() or {}
                    
                    # 1. Regime Analysis
                    # fetch_latest_candles handles 5-min caching AND real-time WebSocket hybrid-merge.
                    df = self.data_fetcher.fetch_latest_candles("99926000") # Nifty 50
                    
                    if df is None:
                        # EMERGENCY FALLBACK: If API is blocked (AB1004), use ANY cache for up to 4h
                        logger.warning("MarketService: API BLOCKED. Falling back to 4h stale cache for Regime Analysis... 🏺")
                        cache_key = "99926000_FIVE_MINUTE_1"
                        df = self.data_fetcher._read_disk_cache(cache_key, force_fresh=False, max_age=14400)
                        if df is not None:
                            df = self.data_fetcher._merge_live_candle(df, "99926000", "FIVE_MINUTE")
                    
                    if df is not None:
                        self.analysis_data = self.regime_engine.classify(df)
                        
                        # 1b. Intraday High/Low (HOD/LOD) Extraction for Sniper Logic
                        try:
                            now_dt = datetime.datetime.now()
                            
                            # Select only candles from today
                            # Armored Fix: Check for 'timestamp' column before defaulting to index.date
                            # preventing 'RangeIndex has no attribute date' failures.
                            if 'timestamp' in df.columns:
                                dates = pd.to_datetime(df['timestamp']).dt.date
                            else:
                                # Fallback assuming index might be DatetimeIndex
                                dates = pd.Series(df.index).dt.date if not isinstance(df.index, pd.DatetimeIndex) else df.index.date
                            
                            mask = (dates == now_dt.date())
                            today_df = df[mask] if not df.empty else None
                            
                            if today_df is not None and not today_df.empty:
                                # Exclude the current active/most-recent candle to get the 
                                # reference CEILING/FLOOR we are testing a breakout against.
                                if len(today_df) > 1:
                                    ref_df = today_df.iloc[:-1]
                                else:
                                    ref_df = today_df
                                
                                self.analysis_data['hod'] = float(ref_df['high'].max())
                                self.analysis_data['lod'] = float(ref_df['low'].min())
                                logger.debug(f"MarketService: Updated Reference Range [LOD: {self.analysis_data['lod']:.1f} | HOD: {self.analysis_data['hod']:.1f}]")
                            else:
                                self.analysis_data['hod'] = 0.0
                                self.analysis_data['lod'] = 0.0
                        except Exception as re_err:
                            logger.warning(f"MarketService: Failed to calculate HOD/LOD reference: {re_err}")
                            self.analysis_data['hod'] = 0.0
                            self.analysis_data['lod'] = 0.0
                        
                        # 2. OI & Panic Sentiment Analysis
                        ltp = df.iloc[-1]['close']
                        base_atm = int(round(ltp / 50) * 50)
                        from bot.utils.expiry_calculator import get_next_weekly_expiry
                        expiry = get_next_weekly_expiry()
                        
                        # Fetch VIX for shared state
                        vix_ltp = 0.0
                        try:
                            vix_ltp = self.get_ltp("NSE", "INDIA VIX", "99926017")
                        except: pass

                        if ltp > 0:
                            # Use get_oi_velocity to capture centralized history / ROC
                            analysis = self.oi_engine.get_oi_velocity(expiry, ltp)
                            self.oi_data = analysis
                            
                            # Generate Institutional Panic Metric
                            panic_analysis = {}
                            try:
                                panic_analysis = self.alpha_engine.analyze_panic(expiry, base_atm)
                            except Exception as alpha_err:
                                logger.warning(f"Centralized Panic Analysis Failed: {alpha_err}")

                            # 3. Save Shared Intelligence for Child Processes
                            state = {
                                "timestamp": datetime.datetime.now().isoformat(),
                                "nifty_ltp": ltp,
                                "vix": vix_ltp,
                                "analysis": self.analysis_data,
                                "oi_data": {
                                    "bias": analysis.get("bias", "NEUTRAL"),
                                    "pcr": analysis.get("pcr", 1.0),
                                    "pcr_velocity": analysis.get("pcr_velocity", 0.0),
                                    "delta_ratio": analysis.get("delta_ratio", 1.0),
                                    "total_ce_oi": analysis.get("total_ce_oi", 0),
                                    "total_pe_oi": analysis.get("total_pe_oi", 0),
                                },
                                "panic_data": panic_analysis,
                                "levels": self.levels_data,
                                # Keep legacy flat keys for compatibility
                                "sentiment": analysis.get("bias", "NEUTRAL"),
                                "pcr": analysis.get("pcr", 1.0),
                                "oi_delta_ratio": analysis.get("delta_ratio", 1.0)
                            }
                            if not os.path.exists("data"): os.makedirs("data")
                            
                            # --- ATOMIC FILE SWAP (Fixes Race Truncation Condition) ---
                            target_path = "data/market_analysis.json"
                            temp_path = None
                            try:
                                with tempfile.NamedTemporaryFile('w', dir="data", delete=False) as tf:
                                    json.dump(state, tf, default=str)
                                    temp_path = tf.name
                                os.replace(temp_path, target_path) # Atomic swap at OS level
                            except Exception as iox:
                                logger.error(f"Atomic File Refresh Failed: {iox}")
                                if temp_path and os.path.exists(temp_path):
                                    try: os.remove(temp_path)
                                    except: pass

                            
                            logger.info("MarketService: Tactical Intelligence Refreshed 🛰️")
                        else:
                            logger.warning("MarketService: Skipping OI analysis - Nifty LTP is zero.")
                    else:
                        logger.warning("MarketService: Skipping refresh - No candle data available.")
                
                time.sleep(300) # Run every 5 minutes

            except Exception as e:
                logger.error(f"MarketService Analysis Loop Error: {e}")
                time.sleep(60) # Retry after 1 minute

market_service = MarketService()
