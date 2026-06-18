import sys
import os
import datetime
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.getcwd())

from bot.core.data_fetcher import DataFetcher

class TestAB1012Fix(unittest.TestCase):
    def setUp(self):
        self.mock_api = MagicMock()
        # Ensure we are in BACKEND process for testing REST fetch
        os.environ["PROCESS_TYPE"] = "BACKEND"
        # We need to reset the singleton or bypass it for testing with different mocks
        DataFetcher._instance = None 
        self.fetcher = DataFetcher(self.mock_api)

    def test_todate_capping_after_hours(self):
        """Verify that todate is capped at 15:30 if current time is after hours."""
        # Mock datetime.now() to 19:00 IST
        fixed_now = datetime.datetime(2026, 2, 24, 19, 0, 0)
        
        with patch('datetime.datetime') as mock_datetime, \
             patch.object(DataFetcher, '_read_disk_cache', return_value=None), \
             patch.object(DataFetcher, '_write_disk_cache'), \
             patch.object(DataFetcher, '_merge_live_candle', side_effect=lambda df, t, i: df):
            
            mock_datetime.now.return_value = fixed_now
            mock_datetime.combine = datetime.datetime.combine
            mock_datetime.timedelta = datetime.timedelta
            mock_datetime.strptime = datetime.datetime.strptime
            
            # Let's mock the actual getCandleData call and check the parameters
            self.mock_api.getCandleData.return_value = {
                'status': True, 
                'data': [['2026-02-24 15:25', 100, 105, 95, 102, 1000]]
            }
            
            # We also need to mock is_trading_day to avoid external dependencies
            with patch('bot.core.data_fetcher.is_trading_day', return_value=True):
                self.fetcher.fetch_latest_candles("99926000", interval="FIVE_MINUTE")
            
            args, kwargs = self.mock_api.getCandleData.call_args
            historic_param = args[0]
            
            # todate should be 15:30 (or aligned/shifted)
            self.assertIn("15:30", historic_param['todate'])
            print(f"Verified todate capping: {historic_param['todate']}")

    def test_ab1012_retry_logic(self):
        """Verify that AB1012 triggers a retry with shifted todate."""
        # First call returns AB1012, second returns success
        self.mock_api.getCandleData.side_effect = [
            {'status': False, 'errorcode': 'AB1012', 'message': "To datetime can't be greater than current datetime"},
            {'status': True, 'data': [['2026-02-24 15:25', 100, 105, 95, 102, 1000]]}
        ]
        
        # Mock time.sleep to speed up test
        with patch('time.sleep'), \
             patch.object(DataFetcher, '_read_disk_cache', return_value=None), \
             patch.object(DataFetcher, '_write_disk_cache'), \
             patch.object(DataFetcher, '_merge_live_candle', side_effect=lambda df, t, i: df):
            
            res = self.fetcher.fetch_latest_candles("99926000", interval="FIVE_MINUTE")
            
            self.assertEqual(self.mock_api.getCandleData.call_count, 2)
            
            # Check the second call's parameters
            second_call_args = self.mock_api.getCandleData.call_args_list[1][0][0]
            orig_call_args = self.mock_api.getCandleData.call_args_list[0][0][0]
            
            print(f"First attempt todate: {orig_call_args['todate']}")
            print(f"Second attempt todate (shifted): {second_call_args['todate']}")
            
            # second todate should be before first
            self.assertNotEqual(orig_call_args['todate'], second_call_args['todate'])
            self.assertIsNotNone(res)

if __name__ == "__main__":
    unittest.main()
