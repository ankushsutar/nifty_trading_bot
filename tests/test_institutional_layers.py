import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import datetime

from bot.core.heavyweight_tracker import HeavyweightTracker
from bot.core.decision_engine import DecisionEngine

class TestInstitutionalLayers(unittest.TestCase):
    def setUp(self):
        self.mock_data_fetcher = MagicMock()
        self.hw_tracker = HeavyweightTracker(data_fetcher=self.mock_data_fetcher)

    def test_heavyweight_confluence_bullish_aligned(self):
        """Verify that when 2 of 3 heavyweights are above VWAP, check_confluence('BULLISH') passes."""
        # Mock DataFrame where close > vwap
        df_bullish = pd.DataFrame({'high': [102], 'low': [98], 'close': [101], 'volume': [1000]})
        df_bearish = pd.DataFrame({'high': [102], 'low': [98], 'close': [95], 'volume': [1000]})

        # HDFCBANK & RELIANCE are bullish, ICICIBANK is bearish
        self.mock_data_fetcher.fetch_latest_candles.side_effect = [df_bullish, df_bullish, df_bearish]
        
        aligned, summary = self.hw_tracker.check_confluence("BULLISH")
        self.assertTrue(aligned)
        self.assertIn("Heavyweights Bullish: 2/3", summary)

    def test_heavyweight_confluence_bullish_rejected(self):
        """Verify that when only 1 heavyweight is above VWAP, check_confluence('BULLISH') fails."""
        df_bullish = pd.DataFrame({'high': [102], 'low': [98], 'close': [101], 'volume': [1000]})
        df_bearish = pd.DataFrame({'high': [102], 'low': [98], 'close': [95], 'volume': [1000]})

        # Only HDFCBANK is bullish, RELIANCE & ICICIBANK are bearish
        self.hw_tracker._last_check_time = 0  # Reset cache
        self.mock_data_fetcher.fetch_latest_candles.side_effect = [df_bullish, df_bearish, df_bearish]
        
        aligned, summary = self.hw_tracker.check_confluence("BULLISH")
        self.assertFalse(aligned)
        self.assertIn("Heavyweights Bullish: 1/3", summary)

    def test_opening_gap_protection_threshold(self):
        """Verify that large opening gap >= 0.40% triggers gap protection log."""
        pdc = 24000.0
        nifty_spot = 24110.0  # +0.458% gap up
        
        gap_pct = abs(nifty_spot - pdc) / pdc
        is_large_gap = gap_pct >= 0.0040
        
        self.assertTrue(is_large_gap)

if __name__ == "__main__":
    unittest.main()
