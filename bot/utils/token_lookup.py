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
    def __init__(self):
        self.df = None
        self._cache_date = None  # Track which date the in-memory df was loaded for

    def _get_cache_path(self):
        """
        Cache file is date-stamped — auto-invalidates each new trading day.
        Uses the OS temp directory so it works on both Windows and Linux.
        Windows: C:\\Users\\<user>\\AppData\\Local\\Temp\\
        Linux:   /tmp/
        """
        today = datetime.date.today().strftime("%Y%m%d")
        return os.path.join(tempfile.gettempdir(), f"scrip_master_{today}.json")

    def load_scrip_master(self):
        """
        3-Layer loading strategy:
          1. In-memory (self.df) — fastest, already loaded for today
          2. Disk cache (/tmp/scrip_master_YYYYMMDD.json) — survives process restarts
          3. Fresh download from Angel One URL — fallback, once per day
        Cache is date-keyed: a new trading day always triggers a fresh download.
        """
        today = datetime.date.today()

        # Layer 1: In-memory cache (same day)
        if self.df is not None and self._cache_date == today:
            return

        cache_path = self._get_cache_path()

        # Layer 2: Disk cache (today's file exists)
        if os.path.exists(cache_path):
            try:
                logger.info(">>> [Data] Loading Scrip Master from disk cache (instant)...")
                with open(cache_path, 'r') as f:
                    data = json.load(f)
                self._build_df(data)
                self._cache_date = today
                logger.info(f">>> [Data] Scrip Master loaded from cache ({len(self.df):,} instruments).")
                return
            except Exception as e:
                logger.warning(f">>> [Data] Disk cache corrupt, re-downloading: {e}")
                os.remove(cache_path)

        # Layer 3: Fresh download
        logger.info(">>> [Data] Downloading Scrip Master from Angel One (first run today)...")
        try:
            response = requests.get(Config.SCRIP_MASTER_URL, timeout=30)
            response.raise_for_status()
            data = response.json()

            # Save to disk cache for the rest of the day
            try:
                with open(cache_path, 'w') as f:
                    json.dump(data, f)
                logger.info(f">>> [Data] Scrip Master cached to disk: {cache_path}")
            except Exception as e:
                logger.warning(f">>> [Data] Could not write disk cache: {e}")

            self._build_df(data)
            self._cache_date = today
            logger.info(f">>> [Data] Scrip Master downloaded ({len(self.df):,} instruments).")

        except requests.exceptions.Timeout:
            logger.error(">>> [Error] Scrip Master download TIMED OUT (30s). Check network.")
        except Exception as e:
            logger.error(f">>> [Error] Failed to load Scrip Master: {e}")

    def _build_df(self, data):
        """Builds and optimises the DataFrame from raw JSON data."""
        self.df = pd.DataFrame(data)
        # Angel One 'strike' is in paise (e.g. 2300000.00 = ₹23,000)
        self.df['strike'] = pd.to_numeric(self.df['strike'], errors='coerce')

    def _format_date_for_exchange(self, date_val, exchange):
        """
        Inconsistent Angel One naming conventions:
        NFO (Index Options) usually uses 'DDMMMYY' (e.g., 14APR26)
        MCX (Commodities) usually uses 'DDMMMYYYY' (e.g., 20APR2026)
        """
        if isinstance(date_val, str):
            # Legacy string support: If it was already formatted as YYYY, 
            # we might need to truncate it for NFO.
            if exchange == "NFO" and len(date_val) == 9: # e.g. 14APR2026
                return date_val[:5] + date_val[7:] # -> 14APR26
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
        expiry_date can be a 'DDMMMYYYY' string or a datetime.date object.
        """
        if self.df is None:
            self.load_scrip_master()

        if self.df is None:
            logger.error(">>> [Error] Scrip Master not available. Cannot resolve token.")
            return None, None

        # 1. Format date for this specific exchange
        formatted_expiry = self._format_date_for_exchange(expiry_date, exchange)
        strike_paise = float(strike) * 100.0

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
                return rows.iloc[0]['token'], rows.iloc[0]['symbol']
            return None, None

        # 2. Initial Search
        token, symbol = _search(formatted_expiry)
        if token: return token, symbol

        # 3. Fallback: If original date was shifted by holiday logic (e.g. 13-APR),
        # try the NEXT day (e.g. 14-APR) because Angel One often labels contracts
        # with the original intended date.
        if isinstance(expiry_date, (datetime.date, datetime.datetime)):
            shifted_date = expiry_date + datetime.timedelta(days=1)
            token, symbol = _search(self._format_date_for_exchange(shifted_date, exchange))
            if token:
                logger.debug(f">>> [Token] Found match via holiday fallback (using +1 day): {symbol}")
                return token, symbol

        logger.warning(f">>> [Warning] Token NOT FOUND: {symbol_name} {formatted_expiry} {strike} {option_type} ({instrument_type})")
        return None, None

    def get_futures_token(self, symbol_name, expiry_date, instrument_type="FUTCOM", exchange="MCX"):
        """
        Finds the Angel One token for a futures contract.
        """
        if self.df is None:
            self.load_scrip_master()
        if self.df is None:
            logger.error(">>> [Error] Scrip Master not available. Cannot resolve futures token.")
            return None, None

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

        # 1. Initial Search
        token, symbol = _search(formatted_expiry)
        if token: return token, symbol

        # 2. Fallback
        if isinstance(expiry_date, (datetime.date, datetime.datetime)):
            shifted_date = expiry_date + datetime.timedelta(days=1)
            token, symbol = _search(self._format_date_for_exchange(shifted_date, exchange))
            if token: return token, symbol

        logger.warning(f">>> [Warning] Futures Token NOT FOUND: {symbol_name} {formatted_expiry} ({instrument_type}/{exchange})")
        return None, None

    def get_option_bucket(self, symbol_name, expiry_date, atm_strike, range_points=500, instrument_type="OPTIDX", exchange="NFO"):
        """
        Returns a dict of relevant option tokens around the ATM strike for a given symbol.
        Range: ATM +/- range_points
        """
        if self.df is None:
            self.load_scrip_master()

        if self.df is None:
            return {}

        # Format date for this exchange
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

        # Fallback for buckets
        if subset.empty and isinstance(expiry_date, (datetime.date, datetime.datetime)):
            shifted_date = expiry_date + datetime.timedelta(days=1)
            mask = _get_mask(self._format_date_for_exchange(shifted_date, exchange))
            subset = self.df[mask].copy()

        bucket = {}
        for _, row in subset.iterrows():
            strike_val = int(row['strike'] / 100)
            opt_type = "CE" if row['symbol'].endswith("CE") else "PE"
            key = f"{strike_val}_{opt_type}"
            bucket[key] = {
                "token": row['token'],
                "symbol": row['symbol'],
                "strike": strike_val,
                "type": opt_type
            }

        return bucket
