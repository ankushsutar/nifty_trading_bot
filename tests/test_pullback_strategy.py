import unittest
from unittest.mock import MagicMock, patch
import os
import sys
import pandas as pd
import datetime

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.strategies.pullback_strategy import PullbackStrategy

class TestPullbackStrategy(unittest.TestCase):
    def setUp(self):
        self.mock_api = MagicMock()
        self.mock_loader = MagicMock()
        self.strategy = PullbackStrategy(self.mock_api, self.mock_loader, dry_run=True)

    def test_indicator_calculations(self):
        # Build mock dataframe of 5-min candles
        timestamps = pd.date_range(start="2026-06-25 09:15:00", periods=25, freq="5min")
        data = {
            'timestamp': timestamps,
            'open': [23000.0 + i * 2 for i in range(25)],
            'high': [23005.0 + i * 2 for i in range(25)],
            'low': [22995.0 + i * 2 for i in range(25)],
            'close': [23001.0 + i * 2 for i in range(25)],
            'volume': [1000 + i * 10 for i in range(25)]
        }
        df = pd.DataFrame(data)
        
        df_indicators = self.strategy.calculate_indicators(df)
        
        # Verify columns exist
        self.assertIn('EMA20', df_indicators.columns)
        self.assertIn('VWAP', df_indicators.columns)
        
        # Spot check last value is positive and reasonable
        self.assertGreater(df_indicators['EMA20'].iloc[-1], 23000)
        self.assertGreater(df_indicators['VWAP'].iloc[-1], 23000)

    def test_indicator_calculations_zero_volume(self):
        # Build mock dataframe of 5-min candles with 0 volume
        timestamps = pd.date_range(start="2026-06-25 09:15:00", periods=25, freq="5min")
        data = {
            'timestamp': timestamps,
            'open': [23000.0 + i * 2 for i in range(25)],
            'high': [23005.0 + i * 2 for i in range(25)],
            'low': [22995.0 + i * 2 for i in range(25)],
            'close': [23001.0 + i * 2 for i in range(25)],
            'volume': [0.0 for _ in range(25)]
        }
        df = pd.DataFrame(data)
        
        df_indicators = self.strategy.calculate_indicators(df)
        
        # Verify VWAP is reasonable (not quadrillions, but close to spot/average close)
        last_vwap = df_indicators['VWAP'].iloc[-1]
        self.assertLess(last_vwap, 24000)
        self.assertGreater(last_vwap, 22000)

    def test_check_pullback_signal_bullish(self):
        # Build candles representing a bullish trend that pulled back to EMA20/VWAP and rejected it
        timestamps = pd.date_range(start="2026-06-25 09:15:00", periods=23, freq="5min")
        df = pd.DataFrame({
            'timestamp': timestamps,
            'open': [float(23000 + i * 5) for i in range(23)],
            'high': [float(23005 + i * 5) for i in range(23)],
            'low': [float(22995 + i * 5) for i in range(23)],
            'close': [float(23002 + i * 5) for i in range(23)],
            'volume': [1000.0 for _ in range(23)]
        })
        
        df = self.strategy.calculate_indicators(df)
        
        # Modify the last two rows to mock a pullback and rejection
        # EMA20 will be around 23075 at row 21. Let's make the low touch it.
        # Row -2 (prev_row): Pullback candle
        df.loc[21, 'open'] = df.loc[21, 'EMA20'] - 1.0
        df.loc[21, 'high'] = df.loc[21, 'EMA20'] + 5.0
        df.loc[21, 'low'] = df.loc[21, 'EMA20'] - 2.0  # Cross below EMA20
        df.loc[21, 'close'] = df.loc[21, 'EMA20'] + 1.0 # Close above EMA20 (Bullish Rejection)
        
        # Row -1 (last_row): Trend confirmation
        df.loc[22, 'open'] = df.loc[21, 'close']
        df.loc[22, 'high'] = df.loc[22, 'open'] + 10.0
        df.loc[22, 'low'] = df.loc[22, 'open'] - 1.0
        df.loc[22, 'close'] = df.loc[22, 'open'] + 8.0 # Above EMA20 and VWAP
        
        signal = self.strategy.check_pullback_signal(df)
        self.assertEqual(signal, "CE")
 
    def test_check_pullback_signal_bearish(self):
        timestamps = pd.date_range(start="2026-06-25 09:15:00", periods=23, freq="5min")
        df = pd.DataFrame({
            'timestamp': timestamps,
            'open': [float(23000 - i * 5) for i in range(23)],
            'high': [float(23005 - i * 5) for i in range(23)],
            'low': [float(22995 - i * 5) for i in range(23)],
            'close': [float(22998 - i * 5) for i in range(23)],
            'volume': [1000.0 for _ in range(23)]
        })
        
        df = self.strategy.calculate_indicators(df)
        
        # Modify the last two rows to mock a bearish pullback (bounce to resistance) and rejection
        df.loc[21, 'open'] = df.loc[21, 'EMA20'] + 1.0
        df.loc[21, 'high'] = df.loc[21, 'EMA20'] + 2.0  # Cross above EMA20 resistance
        df.loc[21, 'low'] = df.loc[21, 'EMA20'] - 5.0
        df.loc[21, 'close'] = df.loc[21, 'EMA20'] - 1.0 # Close below EMA20 (Bearish Rejection)
        
        df.loc[22, 'open'] = df.loc[21, 'close']
        df.loc[22, 'high'] = df.loc[22, 'open'] + 1.0
        df.loc[22, 'low'] = df.loc[22, 'open'] - 10.0
        df.loc[22, 'close'] = df.loc[22, 'open'] - 8.0 # Below EMA20 and VWAP
        
        signal = self.strategy.check_pullback_signal(df)
        self.assertEqual(signal, "PE")

    @patch('bot.strategies.pullback_strategy.PullbackStrategy.enter_position')
    @patch('bot.core.data_fetcher.DataFetcher.fetch_latest_candles')
    def test_execute_loop_calls_analysis(self, mock_fetch_candles, mock_enter_position):
        # Mock strategy gatekeeper and other loops to terminate after one pass
        self.strategy.gatekeeper.is_market_open = MagicMock(side_effect=[True, False])
        self.strategy.gatekeeper.is_blackout_period = MagicMock(return_value=False)
        self.strategy.gatekeeper.check_max_daily_loss = MagicMock(return_value=True)
        
        timestamps = pd.date_range(start="2026-06-25 09:15:00", periods=25, freq="5min")
        df = pd.DataFrame({
            'timestamp': timestamps,
            'open': [23000 + i * 5 for i in range(25)],
            'high': [23005 + i * 5 for i in range(25)],
            'low': [22995 + i * 5 for i in range(25)],
            'close': [23002 + i * 5 for i in range(25)],
            'volume': [1000 for _ in range(25)]
        })
        mock_fetch_candles.return_value = df
        
        # Call execute but break the loop after one iteration
        def fake_sleep(*args, **kwargs):
            self.strategy.running = False
            
        with patch('time.sleep', side_effect=fake_sleep):
            self.strategy.execute(expiry="26JUN2026")
        
        # Verify it fetched candles
        mock_fetch_candles.assert_called()

if __name__ == "__main__":
    unittest.main()
