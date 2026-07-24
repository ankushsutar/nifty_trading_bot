import time
import pandas as pd
from bot.utils.logger import logger

class HeavyweightTracker:
    """
    Layer 9: Heavyweight Sector Confluence Tracker.
    Monitors top Nifty 50 weighted stocks (HDFCBANK, RELIANCE, ICICIBANK)
    to confirm whether institutional big money is moving in alignment with the index.
    """
    
    HEAVYWEIGHTS = [
        {"name": "HDFCBANK", "symbol": "HDFCBANK", "token": "1333", "exchange": "NSE", "weight": 0.135},
        {"name": "RELIANCE", "symbol": "RELIANCE", "token": "2885", "exchange": "NSE", "weight": 0.092},
        {"name": "ICICIBANK", "symbol": "ICICIBANK", "token": "4963", "exchange": "NSE", "weight": 0.078},
    ]

    def __init__(self, data_fetcher=None):
        self.data_fetcher = data_fetcher
        self._last_check_time = 0
        self._cached_results = {"bullish_count": 0, "bearish_count": 0, "details": []}

    def _calculate_stock_vwap(self, df: pd.DataFrame) -> float:
        """Calculates session VWAP for a given stock candle dataframe."""
        if df is None or df.empty or 'volume' not in df.columns:
            return 0.0
        
        vol_sum = df['volume'].sum()
        if vol_sum == 0:
            return 0.0
        
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        vwap = (typical_price * df['volume']).sum() / vol_sum
        return vwap

    def analyze_heavyweights(self) -> dict:
        """
        Fetches latest candle data for heavyweights and evaluates their position relative to VWAP.
        Caches results for 60 seconds to avoid unnecessary API overhead.
        """
        now = time.time()
        if now - self._last_check_time < 60 and self._cached_results.get("details"):
            return self._cached_results

        bullish_count = 0
        bearish_count = 0
        details = []

        if not self.data_fetcher:
            logger.warning("HeavyweightTracker: No DataFetcher provided. Returning neutral fallback.")
            return {"bullish_count": 0, "bearish_count": 0, "details": details}

        from bot.utils.token_lookup import TokenLookup
        lookup = TokenLookup()
        lookup.load_scrip_master()

        for stock in self.HEAVYWEIGHTS:
            try:
                stock_tok = lookup.get_token_by_symbol(stock["symbol"]) or stock["token"]
                df = self.data_fetcher.fetch_latest_candles(stock_tok, interval="FIVE_MINUTE", days=1)
                
                if df is not None and not df.empty:
                    vwap = self._calculate_stock_vwap(df)
                    ltp = float(df['close'].iloc[-1])
                    
                    is_bullish = ltp >= vwap if vwap > 0 else True
                    if is_bullish:
                        bullish_count += 1
                    else:
                        bearish_count += 1

                    status_str = "ABOVE_VWAP" if is_bullish else "BELOW_VWAP"
                    details.append(f"{stock['name']}: ₹{ltp:.1f} ({status_str} VWAP: ₹{vwap:.1f})")
                else:
                    # Fail-safe assumption if stock candle fetch fails
                    bullish_count += 1
                    details.append(f"{stock['name']}: Data Unavailable")
            except Exception as e:
                logger.warning(f"HeavyweightTracker error for {stock['name']}: {e}")
                bullish_count += 1

        self._cached_results = {
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "details": details
        }
        self._last_check_time = now
        return self._cached_results

    def check_confluence(self, trend_direction: str) -> tuple[bool, str]:
        """
        Checks if heavyweight stocks align with the target Nifty trend direction.
        - CE (BULLISH): Requires >= 2 of 3 heavyweights to be ABOVE VWAP.
        - PE (BEARISH): Requires >= 2 of 3 heavyweights to be BELOW VWAP.
        Returns (is_aligned: bool, summary_string: str)
        """
        data = self.analyze_heavyweights()
        bull_cnt = data.get("bullish_count", 0)
        bear_cnt = data.get("bearish_count", 0)
        details_str = " | ".join(data.get("details", []))

        if trend_direction == "BULLISH":
            aligned = bull_cnt >= 2
            reason = f"Heavyweights Bullish: {bull_cnt}/3 [{details_str}]"
            return aligned, reason
        elif trend_direction == "BEARISH":
            aligned = bear_cnt >= 2
            reason = f"Heavyweights Bearish: {bear_cnt}/3 [{details_str}]"
            return aligned, reason

        return True, "Neutral Direction"

heavyweight_tracker = HeavyweightTracker()
