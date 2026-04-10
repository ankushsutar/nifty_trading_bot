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
        Caches results per symbol/date to avoid redundant API calls.
        """
        from bot.config.settings import Config
        symbol = Config.ACTIVE_SYMBOL
        today = datetime.datetime.now().date()
        cache_key = f"{symbol}_{today}"
        
        if cache_key in self._levels_cache:
            return self._levels_cache[cache_key]

        logger.info(f">>> [Levels] 🏛️ Calculating Institutional Levels for {symbol} on {today}...")
        levels = self._calculate_levels(symbol)
        if levels:
            self._levels_cache[cache_key] = levels
        return levels

    def _calculate_levels(self, symbol):
        """
        Fetches previous session data and calculates Camarilla Pivots + PDH/L/C.
        """
        from bot.config.instruments import get_instrument
        instr = get_instrument(symbol)
        try:
            # Fetch 2 days of data to be sure we cross the previous session boundary
            df = self.data_fetcher.fetch_latest_candles(instr.analysis_token, interval="FIVE_MINUTE", days=2, exchange=instr.exchange)
            
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
