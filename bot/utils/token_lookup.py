import os
import json
import time
import datetime
import tempfile
import requests
import pandas as pd
from bot.config.settings import Config
from bot.utils.logger import logger


class TokenLookup:
    def __init__(self):
        self.df = None
        self._cache_date = None  # Track which date the in-memory df was loaded for

    def _get_cache_path(self):
        """
        Cache file is date-stamped — auto-invalidates each new trading day.
        Uses the OS temp directory so it works on both Windows and Linux.
        """
        today = datetime.date.today().strftime("%Y%m%d")
        broker_suffix = "_zerodha" if Config.BROKER == "ZERODHA" else ""
        return os.path.join(tempfile.gettempdir(), f"scrip_master{broker_suffix}_{today}.json")

    def load_scrip_master(self):
        """
        3-Layer loading strategy:
          1. In-memory (self.df) — fastest, already loaded for today
          2. Disk cache (/tmp/scrip_master_YYYYMMDD.json) — survives process restarts
          3. Fresh download (Angel URL or Zerodha URL) — fallback, once per day
        """
        today = datetime.date.today()

        # Layer 1: In-memory cache (same day)
        if self.df is not None and self._cache_date == today:
            return

        cache_path = self._get_cache_path()

        # Layer 2: Disk cache (today's file exists)
        if os.path.exists(cache_path):
            try:
                logger.info(f">>> [Data] Loading Scrip Master ({Config.BROKER}) from disk cache (instant)...")
                with open(cache_path, 'r') as f:
                    data = json.load(f)
                self._build_df(data)
                self._cache_date = today
                logger.info(f">>> [Data] Scrip Master loaded from cache ({len(self.df):,} instruments).")
                return
            except Exception as e:
                logger.warning(f">>> [Data] Disk cache corrupt, re-downloading: {e}")
                try:
                    os.remove(cache_path)
                except:
                    pass

        # Layer 3: Fresh download
        if Config.BROKER == "ZERODHA":
            logger.info(">>> [Data] Downloading Scrip Master from Zerodha (first run today)...")
            try:
                # Public Zerodha instruments list CSV
                url = "https://api.kite.trade/instruments"
                import requests
                import io
                response = requests.get(url, timeout=30)
                response.raise_for_status()
                df_kite = pd.read_csv(io.StringIO(response.text))
                
                # Standardize columns
                df_std = pd.DataFrame()
                df_std['token'] = df_kite['instrument_token'].astype(str)
                df_std['symbol'] = df_kite['tradingsymbol'].astype(str)
                df_std['name'] = df_kite['name'].astype(str).str.upper()
                
                # Format expiry from YYYY-MM-DD to DDMMMYYYY
                expiry_parsed = pd.to_datetime(df_kite['expiry'], errors='coerce')
                from bot.utils.expiry_calculator import _MONTH_ABBR
                
                df_std['expiry'] = expiry_parsed.apply(
                    lambda dt: f"{dt.day:02d}{_MONTH_ABBR[dt.month]}{dt.year}" if not pd.isna(dt) else ""
                )
                df_std['strike'] = pd.to_numeric(df_kite['strike'], errors='coerce') * 100.0
                
                # Map instrumenttype
                is_index_option = df_std['name'].isin(['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY']) & (df_kite['segment'].str.upper() == 'NFO-OPT')
                is_stock_option = (~df_std['name'].isin(['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY'])) & (df_kite['segment'].str.upper() == 'NFO-OPT')
                is_index_future = df_std['name'].isin(['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY']) & (df_kite['segment'].str.upper() == 'NFO-FUT')
                is_stock_future = (~df_std['name'].isin(['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY'])) & (df_kite['segment'].str.upper() == 'NFO-FUT')
                
                df_std['instrumenttype'] = df_kite['instrument_type'].astype(str).str.upper()
                df_std.loc[is_index_option, 'instrumenttype'] = 'OPTIDX'
                df_std.loc[is_stock_option, 'instrumenttype'] = 'OPTSTK'
                df_std.loc[is_index_future, 'instrumenttype'] = 'FUTIDX'
                df_std.loc[is_stock_future, 'instrumenttype'] = 'FUTSTK'
                
                df_std['exch_seg'] = df_kite['exchange'].astype(str).str.upper()
                
                data = df_std.to_dict(orient='records')
                
                # Save to disk cache for the rest of the day
                try:
                    with open(cache_path, 'w') as f:
                        json.dump(data, f)
                    logger.info(f">>> [Data] Scrip Master cached to disk: {cache_path}")
                except Exception as e:
                    logger.warning(f">>> [Data] Could not write disk cache: {e}")
                
                self._build_df(data)
                self._cache_date = today
                logger.info(f">>> [Data] Scrip Master downloaded from Zerodha ({len(self.df):,} instruments).")
            except Exception as e:
                logger.error(f">>> [Error] Failed to load Scrip Master from Zerodha: {e}")
        else:
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
        self.df['strike'] = pd.to_numeric(self.df['strike'], errors='coerce')

    def get_instrument_by_token(self, token):
        """Returns (tradingsymbol, exch_seg) for a given token."""
        if self.df is None:
            self.load_scrip_master()
        if self.df is not None:
            mask = (self.df['token'] == str(token))
            subset = self.df[mask]
            if not subset.empty:
                return subset.iloc[0]['symbol'], subset.iloc[0]['exch_seg']
        return None, None

    def get_token(self, symbol_name, expiry_date, strike, option_type, instrument_type='OPTIDX', exchange='NFO'):
        """
        Finds the Angel One token for any instrument.
        symbol_name: 'NIFTY', 'BANKNIFTY', 'CRUDEOIL', etc.
        expiry_date: '29JAN2026'
        strike: 23000 (in rupees)
        option_type: 'CE' or 'PE'
        instrument_type: 'OPTIDX', 'OPTCOM', 'FUTCOM', etc.
        exchange: 'NFO', 'MCX', etc.
        """
        if self.df is None:
            self.load_scrip_master()

        if self.df is None:
            logger.error(">>> [Error] Scrip Master not available. Cannot resolve token.")
            return None, None

        # Input strike is in rupees — convert to paise for comparison
        strike_paise = float(strike) * 100.0

        mask = (
            (self.df['name'] == symbol_name.upper()) &
            (self.df['instrumenttype'] == instrument_type.upper()) &
            (self.df['exch_seg'] == exchange.upper())
        )
        
        # Only check strike and option_type if it's an Option
        if "OPT" in instrument_type.upper():
            mask &= (self.df['strike'] == strike_paise)
            mask &= (self.df['symbol'].str.endswith(option_type))

        if expiry_date:
            mask &= (self.df['expiry'] == expiry_date)

        row = self.df[mask]

        if not row.empty:
            return row.iloc[0]['token'], row.iloc[0]['symbol']

        logger.warning(f">>> [Warning] Token NOT FOUND: {symbol_name} {expiry_date} {strike} {option_type} ({instrument_type} {exchange})")
        return None, None

    def get_option_bucket(self, symbol_name, expiry_date, atm_strike, range_points=500, instrument_type='OPTIDX', exchange='NFO'):
        """
        Returns a dict of relevant option tokens around the ATM strike.
        Range: ATM +/- range_points
        """
        if self.df is None:
            self.load_scrip_master()

        if self.df is None:
            return {}

        min_strike = (atm_strike - range_points) * 100.0
        max_strike = (atm_strike + range_points) * 100.0

        mask = (
            (self.df['name'] == symbol_name.upper()) &
            (self.df['instrumenttype'] == instrument_type.upper()) &
            (self.df['exch_seg'] == exchange.upper()) &
            (self.df['expiry'] == expiry_date) &
            (self.df['strike'] >= min_strike) &
            (self.df['strike'] <= max_strike)
        )

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
