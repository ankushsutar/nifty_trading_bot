import datetime
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
        Caches results per date to avoid redundant API calls.
        """
        today = datetime.datetime.now().date()
        if today in self._levels_cache:
            return self._levels_cache[today]

        logger.info(f">>> [Levels] 🏛️ Calculating Institutional Levels for {today}...")
        levels = self._calculate_levels()
        if levels:
            self._levels_cache[today] = levels
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

            today_start = pd.Timestamp(datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0))
            
            def _to_naive(val):
                if pd.isna(val): return val
                if isinstance(val, str):
                    if '+' in val: val = val.split('+')[0]
                    if 'T' in val: val = val.replace('T', ' ')
                    return pd.to_datetime(val)
                if hasattr(val, 'tzinfo') and val.tzinfo is not None:
                    return val.replace(tzinfo=None)
                return pd.to_datetime(val)
                
            naive_timestamps = pd.Series([_to_naive(v) for v in df['timestamp']], index=df.index)
            prev_day_df = df[naive_timestamps < today_start]
            
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
