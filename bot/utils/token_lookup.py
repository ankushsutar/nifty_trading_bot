import os
import json
import time
import datetime
import tempfile
import requests
import pandas as pd
from bot.config.settings import Config
from bot.utils.logger import logger
from bot.utils.expiry_calculator import _MONTH_ABBR


class TokenLookup:
    # --- SHARED STATE (Memory Optimization) ---
    # These class-level variables ensure that the 100MB+ DataFrame is only 
    # loaded ONCE per process, regardless of how many TokenLookup instances 
    # are created (e.g. by bot, backend, and strategies).
    _shared_df = None
    _lookup_cache = {} # (name, instrumenttype, strike, opt_type, expiry, exchange) -> (token, symbol)
    _last_load_date = None

    def __init__(self):
        # Local reference for convenience, points to class-level shared state
        self.df = TokenLookup._shared_df

    def _get_cache_path(self):
        """
        Cache file is date-stamped — auto-invalidates each new trading day.
        """
        today = datetime.date.today().strftime("%Y%m%d")
        return os.path.join(tempfile.gettempdir(), f"scrip_master_{today}.json")

    def load_scrip_master(self):
        """
        3-Layer loading strategy with Singleton-like process-level caching.
        """
        today = datetime.date.today()

        # Layer 1: In-memory process-level cache
        if TokenLookup._shared_df is not None and TokenLookup._last_load_date == today:
            self.df = TokenLookup._shared_df
            return

        cache_path = self._get_cache_path()

        # Layer 2: Disk cache
        if os.path.exists(cache_path):
            try:
                logger.info(">>> [Data] Loading Scrip Master from disk cache...")
                with open(cache_path, 'r') as f:
                    data = json.load(f)
                self._build_df(data)
                TokenLookup._last_load_date = today
                logger.info(f">>> [Data] Scrip Master loaded ({len(self.df):,} instruments).")
                # Clear raw data from memory immediately
                del data
                return
            except Exception as e:
                logger.warning(f">>> [Data] Disk cache corrupt, re-downloading: {e}")
                if os.path.exists(cache_path): os.remove(cache_path)

        # Layer 3: Fresh download
        logger.info(">>> [Data] Downloading Scrip Master from Angel One...")
        try:
            response = requests.get(Config.SCRIP_MASTER_URL, timeout=30)
            response.raise_for_status()
            data = response.json()

            # Save to disk cache
            try:
                with open(cache_path, 'w') as f:
                    json.dump(data, f)
            except Exception as e:
                logger.warning(f">>> [Data] Could not write disk cache: {e}")

            self._build_df(data)
            TokenLookup._last_load_date = today
            logger.info(f">>> [Data] Scrip Master downloaded ({len(self.df):,} instruments).")
            del data

        except Exception as e:
            logger.error(f">>> [Error] Failed to load Scrip Master: {e}")

    def _build_df(self, data):
        """Builds and optimises the DataFrame from raw JSON data."""
        df = pd.DataFrame(data)
        
        # 1. Optimize Memory: Use 'category' for repetitive strings
        # This reduces memory usage for these columns by ~90%
        for col in ['name', 'instrumenttype', 'exch_seg']:
            if col in df.columns:
                df[col] = df[col].astype('category')
        
        # 2. Convert strike to numeric (paise)
        df['strike'] = pd.to_numeric(df['strike'], errors='coerce').fillna(0)
        
        # 3. Store in class variable for sharing across instances
        TokenLookup._shared_df = df
        self.df = df
        
        # 4. Clear lookup cache on new load
        TokenLookup._lookup_cache = {}

    def _format_date_for_exchange(self, date_val, exchange):
        """
        Inconsistent Angel One naming conventions:
        NFO (Index Options) usually uses 'DDMMMYY' (e.g., 14APR26)
        MCX (Commodities) usually uses 'DDMMMYYYY' (e.g., 20APR2026)
        """
        if isinstance(date_val, str):
            # Normalise: if it's DDMMMYYYY but exchange is NFO, truncate to YY
            if exchange == "NFO" and len(date_val) == 9: 
                return date_val[:5] + date_val[7:] 
            return date_val
            
        if not isinstance(date_val, (datetime.date, datetime.datetime)):
            return date_val

        month = _MONTH_ABBR[date_val.month]
        if exchange == "NFO":
            year_suffix = str(date_val.year)[2:] # 2026 -> 26
            return f"{date_val.day:02d}{month}{year_suffix}"
        else:
            return f"{date_val.day:02d}{month}{date_val.year}"

    def get_token(self, symbol_name, expiry_date, strike, option_type, instrument_type="OPTIDX", exchange="NFO"):
        """
        Finds the Angel One token for an option instrument.
        Uses a process-level lookup cache for O(1) performance.
        """
        if self.df is None:
            self.load_scrip_master()

        if self.df is None: return None, None

        formatted_expiry = self._format_date_for_exchange(expiry_date, exchange)
        strike_paise = float(strike) * 100.0

        # --- CACHE LOOKUP ---
        cache_key = (symbol_name, instrument_type, strike_paise, option_type, formatted_expiry, exchange)
        if cache_key in TokenLookup._lookup_cache:
            return TokenLookup._lookup_cache[cache_key]

        def _search(exp):
            mask = (
                (self.df['name'] == symbol_name) &
                (self.df['instrumenttype'] == instrument_type) &
                (self.df['strike'] == strike_paise) &
                (self.df['symbol'].str.endswith(option_type)) &
                (self.df['expiry'] == exp) &
                (self.df['exch_seg'] == exchange)
            )
            rows = self.df[mask]
            if not rows.empty:
                res = (rows.iloc[0]['token'], rows.iloc[0]['symbol'])
                TokenLookup._lookup_cache[cache_key] = res
                return res
            return None, None

        # 1. Primary Search
        token, symbol = _search(formatted_expiry)
        if token: return token, symbol

        # 2. Holiday Fallback
        if isinstance(expiry_date, (datetime.date, datetime.datetime)):
            shifted_date = expiry_date + datetime.timedelta(days=1)
            token, symbol = _search(self._format_date_for_exchange(shifted_date, exchange))
            if token: return token, symbol

        logger.warning(f">>> [Warning] Token NOT FOUND: {symbol_name} {formatted_expiry} {strike} {option_type} ({instrument_type})")
        return None, None

    def get_futures_token(self, symbol_name, expiry_date, instrument_type="FUTCOM", exchange="MCX"):
        """
        Finds the Angel One token for a futures contract.
        """
        if self.df is None:
            self.load_scrip_master()
        if self.df is None: return None, None

        formatted_expiry = self._format_date_for_exchange(expiry_date, exchange)

        def _search(exp):
            mask = (
                (self.df['name'] == symbol_name) &
                (self.df['instrumenttype'] == instrument_type) &
                (self.df['expiry'] == exp) &
                (self.df['exch_seg'] == exchange)
            )
            rows = self.df[mask]
            if not rows.empty:
                return rows.iloc[0]['token'], rows.iloc[0]['symbol']
            return None, None

        token, symbol = _search(formatted_expiry)
        if token: return token, symbol

        if isinstance(expiry_date, (datetime.date, datetime.datetime)):
            shifted_date = expiry_date + datetime.timedelta(days=1)
            token, symbol = _search(self._format_date_for_exchange(shifted_date, exchange))
            if token: return token, symbol

        return None, None

    def get_option_bucket(self, symbol_name, expiry_date, atm_strike, range_points=500, instrument_type="OPTIDX", exchange="NFO"):
        """
        Returns a dict of relevant option tokens around the ATM strike.
        """
        if self.df is None:
            self.load_scrip_master()
        if self.df is None: return {}

        formatted_expiry = self._format_date_for_exchange(expiry_date, exchange)
        min_strike = (atm_strike - range_points) * 100.0
        max_strike = (atm_strike + range_points) * 100.0

        def _get_mask(exp):
            return (
                (self.df['name'] == symbol_name) &
                (self.df['instrumenttype'] == instrument_type) &
                (self.df['expiry'] == exp) &
                (self.df['strike'] >= min_strike) &
                (self.df['strike'] <= max_strike) &
                (self.df['exch_seg'] == exchange)
            )

        mask = _get_mask(formatted_expiry)
        subset = self.df[mask].copy()

        if subset.empty and isinstance(expiry_date, (datetime.date, datetime.datetime)):
            shifted_date = expiry_date + datetime.timedelta(days=1)
            mask = _get_mask(self._format_date_for_exchange(shifted_date, exchange))
            subset = self.df[mask].copy()

        bucket = {}
        for _, row in subset.iterrows():
            strike_val = int(row['strike'] / 100)
            opt_type = "CE" if row['symbol'].endswith("CE") else "PE"
            bucket[f"{strike_val}_{opt_type}"] = {
                "token": row['token'], "symbol": row['symbol'],
                "strike": strike_val, "type": opt_type
            }
        return bucket

    def get_nearest_expiry_token(self, symbol_name, instrument_type, exchange):
        """
        Dynamically finds the front-month contract (nearest expiry >= today).
        """
        if self.df is None:
            self.load_scrip_master()
        if self.df is None: return None

        mask = (
            (self.df['name'] == symbol_name) &
            (self.df['exch_seg'] == exchange) &
            (self.df['instrumenttype'] == instrument_type)
        )
        candidates = self.df[mask].copy()
        if candidates.empty: return None

        today = datetime.date.today()
        
        def _parse_exp(row):
            try:
                exp_str = row['expiry']
                if not exp_str or len(exp_str) < 7: return datetime.date(2000, 1, 1)
                return datetime.datetime.strptime(exp_str, "%d%b%Y").date()
            except Exception:
                return datetime.date(2000, 1, 1)

        candidates['parsed_expiry'] = candidates.apply(_parse_exp, axis=1)
        valid = candidates[candidates['parsed_expiry'] >= today]
        
        if valid.empty: 
            return {
                "token": candidates.iloc[0]['token'],
                "symbol": candidates.iloc[0]['symbol'],
                "expiry": candidates.iloc[0]['expiry']
            }
        
        nearest = valid.sort_values('parsed_expiry').iloc[0]
        return {
            "token": nearest['token'],
            "symbol": nearest['symbol'],
            "expiry": nearest['expiry'],
            "date": nearest.get('parsed_expiry')
        }
