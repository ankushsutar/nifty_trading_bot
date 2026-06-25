import time
import threading
import datetime

from bot.core.angel_connect import get_angel_session
from bot.config.settings import Config
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
            cls._instance.panic_data = {"panic_score": 50, "confidence": "NEUTRAL"}
            cls._instance.last_analysis_time = 0
            
            # --- STARTUP WARM-UP: Load Last Known Intelligence ---
            # This ensures /api/market-data is populated immediately for the UI
            try:
                from bot.config.settings import Config
                state_file = os.path.join(os.getcwd(), "data", f"market_analysis_{Config.ACTIVE_SYMBOL.lower()}.json")
                if os.path.exists(state_file):
                    with open(state_file, "r") as f:
                        shared_state = json.load(f)
                        cls._instance.analysis_data = shared_state.get('analysis', {})
                        cls._instance.oi_data = shared_state.get('oi_data', {})
                        cls._instance.levels_data = shared_state.get('levels', {})
                        cls._instance.panic_data = shared_state.get('panic_data', {"panic_score": 50, "confidence": "NEUTRAL"})
                        logger.info(f"MarketService: Startup Intelligence Loaded for {Config.ACTIVE_SYMBOL} from disk 💾")
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
                    logger.info(f"MarketService: Connected to {Config.BROKER} 🟢")
                    if not self.data_fetcher:
                        self.data_fetcher = DataFetcher(self.api)
                    else:
                        self.data_fetcher.api = self.api
            except Exception as e:
                logger.error(f"MarketService Connection Failed: {e}")


    def get_market_data(self):
        """
        Fetches Spot and VIX.
        Returns dict: { nifty: float, vix: float, pnl: float }
        """
        from bot.config.settings import Config
        state_file = f"data/market_analysis_{Config.ACTIVE_SYMBOL.lower()}.json"

        # Cache Check (Quick Read)
        if time.time() - self.last_fetch_time < 20 and self.cached_data:
            return self.cached_data
            
        with self._lock:
            if time.time() - self.last_fetch_time < 20 and self.cached_data:
                return self.cached_data

            # 1. Child Mode: Try loading shared intelligence from master first
            is_master = os.getenv("PROCESS_TYPE") == "BACKEND"
            if not is_master:
                # If file doesn't exist or is stale (> 300s), calculate it on-demand!
                needs_refresh = True
                if os.path.exists(state_file):
                    file_age = time.time() - os.path.getmtime(state_file)
                    if file_age < 310:
                        needs_refresh = False
                        
                if needs_refresh:
                    logger.info(f"MarketService: State file {state_file} is missing or stale. Calculating on-demand...")
                    try:
                        self.refresh_intelligence()
                    except Exception as e:
                        logger.error(f"On-demand refresh failed: {e}")

                try:
                    if os.path.exists(state_file):
                        with open(state_file, "r") as f:
                            shared_state = json.load(f)
                            self.analysis_data = shared_state.get('analysis', {})
                            if 'oi_data' in shared_state:
                                self.oi_data = shared_state['oi_data']
                            if 'panic_data' in shared_state:
                                self.panic_data = shared_state['panic_data']
                            if 'levels' in shared_state:
                                self.levels_data = shared_state['levels']
                            else:
                                self.oi_data = {
                                    "bias": shared_state.get("sentiment", "NEUTRAL"),
                                    "pcr": shared_state.get("pcr", 1.0),
                                    "delta_ratio": shared_state.get("oi_delta_ratio", 1.0),
                                }
                            logger.info(f"MarketService: Consumed Shared Intelligence for {Config.ACTIVE_SYMBOL} 📡")
                except Exception as e:
                    logger.warning(f"Intelligence Sharing Error: {e}")

            self._ensure_connection()
        
        if not self.api:
            # Fallback for UI if connection fails (or dry run without creds)
            return {
                "nifty": 0, "vix": 0, "pnl": 0, "error": "No API Connection",
                "analysis": self.analysis_data,
                "oi_data": self.oi_data,
                "levels": self.levels_data,
                "panic_data": self.panic_data
            }

        try:
            from bot.config.instruments import get_instrument
            instr = get_instrument(Config.ACTIVE_SYMBOL)
            nifty_ltp = self.get_ltp(instr.exchange, instr.spot_symbol, instr.analysis_token)
            
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
                "levels": self.levels_data,
                "panic_data": self.panic_data
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


    def refresh_intelligence(self):
        """
        Runs the full technical, levels, OI, and panic analysis,
        and saves it to the symbol-specific state file.
        """
        from bot.config.settings import Config
        from bot.config.instruments import get_instrument
        
        self._ensure_connection()
        if not self.api:
            logger.warning("MarketService: No API connection. Cannot refresh intelligence.")
            return

        if not self.data_fetcher: 
            self.data_fetcher = DataFetcher(self.api)
        if not self.oi_engine: 
            self.oi_engine = OIAnalyzer(self.api, self.token_lookup)
        if not hasattr(self, 'alpha_engine') or self.alpha_engine is None:
            self.alpha_engine = AlphaEngine(self.api, self.token_lookup)
            
        levels_provider.data_fetcher.api = self.api
        
        self.levels_data = levels_provider.get_levels() or {}
        
        instr = get_instrument(Config.ACTIVE_SYMBOL)
        analysis_tok = instr.analysis_token
        df = self.data_fetcher.fetch_latest_candles(analysis_tok)
        
        if df is None:
            logger.warning("MarketService: API BLOCKED. Falling back to 4h stale cache for Regime Analysis... 🏺")
            cache_key = f"{analysis_tok}_FIVE_MINUTE_1"
            df = self.data_fetcher._read_disk_cache(cache_key, force_fresh=False, max_age=14400)
            if df is not None:
                df = self.data_fetcher._merge_live_candle(df, analysis_tok, "FIVE_MINUTE")
                
        if df is not None:
            self.analysis_data = self.regime_engine.classify(df)
            
            try:
                now_dt = datetime.datetime.now()
                if 'timestamp' in df.columns:
                    dates = pd.to_datetime(df['timestamp']).dt.date
                else:
                    dates = pd.Series(df.index).dt.date if not isinstance(df.index, pd.DatetimeIndex) else df.index.date
                
                mask = (dates == now_dt.date())
                today_df = df[mask] if not df.empty else None
                
                if today_df is not None and not today_df.empty:
                    if len(today_df) > 1:
                        ref_df = today_df.iloc[:-1]
                    else:
                        ref_df = today_df
                    
                    self.analysis_data['hod'] = float(ref_df['high'].max())
                    self.analysis_data['lod'] = float(ref_df['low'].min())
                else:
                    self.analysis_data['hod'] = 0.0
                    self.analysis_data['lod'] = 0.0
            except Exception as re_err:
                logger.warning(f"MarketService: Failed to calculate HOD/LOD reference: {re_err}")
                self.analysis_data['hod'] = 0.0
                self.analysis_data['lod'] = 0.0
                
            ltp = df.iloc[-1]['close']
            base_atm = int(round(ltp / instr.strike_step) * instr.strike_step)
            from bot.utils.expiry_calculator import get_next_weekly_expiry
            expiry = get_next_weekly_expiry()
            
            vix_ltp = 0.0
            try:
                vix_ltp = self.get_ltp("NSE", "INDIA VIX", "99926017")
            except: pass
            
            if ltp > 0:
                analysis = self.oi_engine.get_oi_velocity(expiry, ltp)
                self.oi_data = analysis
                
                panic_analysis = {}
                try:
                    panic_analysis = self.alpha_engine.analyze_panic(expiry, base_atm, current_oi=analysis)
                except Exception as alpha_err:
                    logger.warning(f"Centralized Panic Analysis Failed: {alpha_err}")
                    
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
                    "sentiment": analysis.get("bias", "NEUTRAL"),
                    "pcr": analysis.get("pcr", 1.0),
                    "oi_delta_ratio": analysis.get("delta_ratio", 1.0)
                }
                if not os.path.exists("data"): os.makedirs("data")
                
                state_file = f"data/market_analysis_{Config.ACTIVE_SYMBOL.lower()}.json"
                temp_path = None
                try:
                    with tempfile.NamedTemporaryFile('w', dir="data", delete=False) as tf:
                        json.dump(state, tf, default=str)
                        temp_path = tf.name
                    os.replace(temp_path, state_file)
                except Exception as iox:
                    logger.error(f"Atomic File Refresh Failed: {iox}")
                    if temp_path and os.path.exists(temp_path):
                        try: os.remove(temp_path)
                        except: pass
                        
                logger.info(f"MarketService: Dynamic Tactical Intelligence Refreshed for {Config.ACTIVE_SYMBOL} 🛰️")

    def _analysis_loop(self):
        """
        Background loop (runs in MASTER process only).
        Calculates Trend, ADX, RSI, OI and panic levels, and saves to state file.
        """
        while True:
            try:
                # 2. Strict Master Check: only designated BACKEND may fetch
                is_master = os.getenv("PROCESS_TYPE") == "BACKEND"
                if not is_master:
                    logger.warning("MarketService Analysis Loop: [CHILD] Detected. Halting child loop.")
                    break
                
                self._ensure_connection()
                if self.api:
                    # Background Trade Reconciliation (syncs DB trades with broker tradeBook)
                    try:
                        from bot.core.trade_repo import trade_repo
                        trade_repo.reconcile_with_broker(self.api)
                    except Exception as rec_err:
                        logger.error(f"MarketService Background Reconciliation Error: {rec_err}")

                    # --- OPTIMIZATION: Check if another process already refreshed intelligence recently ---
                    # Prevents double-fetching if the server reloaded or if multiple instances are running.
                    from bot.config.settings import Config
                    state_file = f"data/market_analysis_{Config.ACTIVE_SYMBOL.lower()}.json"
                    if os.path.exists(state_file):
                        file_age = time.time() - os.path.getmtime(state_file)
                        if file_age < 120: # If less than 2 mins old, skip this cycle
                            logger.info(f"MarketService: Shared intelligence is fresh ({int(file_age)}s old). Skipping fetch.")
                            time.sleep(120) 
                            continue

                    self.refresh_intelligence()
                
                time.sleep(300) # Run every 5 minutes

            except Exception as e:
                logger.error(f"MarketService Analysis Loop Error: {e}")
                time.sleep(60) # Retry after 1 minute

market_service = MarketService()
