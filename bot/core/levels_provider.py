import datetime
import os
import json
import pandas as pd
from bot.core.data_fetcher import DataFetcher
from bot.utils.logger import logger

class LevelsProvider:
    _instance = None

    def __new__(cls, api=None):
        if cls._instance is None:
            cls._instance = super(LevelsProvider, cls).__new__(cls)
            cls._instance.data_fetcher = DataFetcher(api)
            cls._instance._levels_cache = {} # Key: date, Value: levels dict
        return cls._instance

    def get_levels(self):
        """
        Returns institutional levels (Camarilla + PDH/L/C) for the current session.
        Caches results per date (both in-memory and on disk) to avoid redundant API calls.
        """
        today = datetime.datetime.now().date()
        today_str = str(today)

        # 1. Check in-memory cache
        if today in self._levels_cache:
            return self._levels_cache[today]

        # 2. Check disk cache
        levels_file = "data/institutional_levels.json"
        
        # --- FIX: Prevent REST fallback in Child processes ---
        is_master = os.getenv("PROCESS_TYPE") == "BACKEND"
        if not is_master:
            # Child process: wait for master to write the file
            if not os.path.exists(levels_file):
                startup_wait_start = time.time()
                while time.time() - startup_wait_start < 15:
                    if os.path.exists(levels_file):
                        break
                    time.sleep(1)
            
            # Read from disk if exists (even if stale/fallback)
            if os.path.exists(levels_file):
                try:
                    with open(levels_file, "r") as f:
                        cached_data = json.load(f)
                        levels = cached_data.get("levels")
                        self._levels_cache[today] = levels
                        logger.info(f"[Levels] Child loaded levels from disk: {levels}")
                        return levels
                except Exception as e:
                    logger.warning(f"[Levels] Failed to read disk cache in child: {e}")
            
            # If still missing, return empty levels to prevent REST calls
            logger.error("[Levels] Child process could not find institutional_levels.json! Returning empty levels to prevent REST calls.")
            return {}

        # 3. Master process path
        if os.path.exists(levels_file):
            try:
                with open(levels_file, "r") as f:
                    cached_data = json.load(f)
                    if cached_data.get("date") == today_str:
                        levels = cached_data.get("levels")
                        self._levels_cache[today] = levels
                        logger.info(f"[Levels] Loaded from disk cache for {today_str}: {levels}")
                        return levels
            except Exception as e:
                logger.warning(f"[Levels] Failed to read disk cache: {e}")

        logger.info(f">>> [Levels] 🏛️ Calculating Institutional Levels for {today}...")
        levels = self._calculate_levels()
        if levels:
            self._levels_cache[today] = levels
            # Save to disk cache
            try:
                os.makedirs("data", exist_ok=True)
                with open(levels_file, "w") as f:
                    json.dump({"date": today_str, "levels": levels}, f)
                logger.info(f"[Levels] Institutional Levels cached to disk for {today_str}")
            except Exception as e:
                logger.warning(f"[Levels] Failed to write disk cache: {e}")
        return levels

    def _calculate_levels(self):
        """
        Fetches previous session data and calculates Camarilla Pivots + PDH/L/C.
        Target: Nifty 50 Index (99926000)
        """
        try:
            # Use 1 day; DataFetcher automatically pads it to include the full previous session.
            # Standardizing to 1 ensures we hit the shared unified cache key on startup.
            df = self.data_fetcher.fetch_latest_candles("99926000", interval="FIVE_MINUTE", days=1)
            
            if df is None or df.empty:
                logger.error("[Levels] Failed to fetch data for level calculation.")
                return None

            # Filter for Previous Day Data (Excluding Today)
            # Use timezone-naive comparison — NIFTY candle timestamps are already in IST without tz info.
            today_start = pd.Timestamp(datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0))
            prev_day_df = df[pd.to_datetime(df['timestamp']).dt.tz_localize(None) < today_start]
            
            if prev_day_df.empty:
                logger.warning("[Levels] No previous day data found. Using earliest available session.")
                # Fallback: Just take the first session in the DF if it's not today
                # (Handle cases where today just started and DF only has one previous session)
                prev_day_df = df.copy() # Simplification for now

            pdh = prev_day_df['high'].max()
            pdl = prev_day_df['low'].min()
            pdc = prev_day_df.iloc[-1]['close'] # Previous Day Close
            
            range_val = pdh - pdl
            
            # Camarilla Formulas
            # H4 = C + (H-L) * 1.1/2
            # H3 = C + (H-L) * 1.1/4
            # L3 = C - (H-L) * 1.1/4
            # L4 = C - (H-L) * 1.1/2
            
            h4 = pdc + (range_val * 1.1 / 2)
            h3 = pdc + (range_val * 1.1 / 4)
            l3 = pdc - (range_val * 1.1 / 4)
            l4 = pdc - (range_val * 1.1 / 2)
            
            levels = {
                "pdh": round(pdh, 2),
                "pdl": round(pdl, 2),
                "pdc": round(pdc, 2),
                "cam_h4": round(h4, 2),
                "cam_h3": round(h3, 2),
                "cam_l3": round(l3, 2),
                "cam_l4": round(l4, 2)
            }
            
            logger.info(f"[Levels] Calculated: PDH={pdh:.0f}, PDL={pdl:.0f}, H4={h4:.0f}, L4={l4:.0f}")
            return levels
            
        except Exception as e:
            logger.error(f"[Levels] Error calculating levels: {e}")
            return None

# Singleton Export
levels_provider = LevelsProvider()
