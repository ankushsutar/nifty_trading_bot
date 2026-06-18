import time
import unittest
from unittest.mock import MagicMock
from bot.core.data_fetcher import DataFetcher
from bot.strategies.momentum_strategy import MomentumStrategy
from bot.strategies.gamma_blast_strategy import GammaBlastStrategy
from bot.strategies.nifty_straddle import NiftyStrategy

class TestProductionReadiness(unittest.TestCase):
    def setUp(self):
        self.mock_api = MagicMock()
        self.mock_api.ltpData.return_value = {'status': True, 'data': {'ltp': 100.0}}
        self.mock_api.getCandleData.return_value = {'status': True, 'data': [[0,0,0,0,100.0,1000]]}
        self.mock_token_loader = MagicMock()

    def test_singleton_data_fetcher(self):
        print("\n--- Testing Singleton DataFetcher ---")
        df1 = DataFetcher(self.mock_api)
        df2 = DataFetcher(self.mock_api)
        self.assertIs(df1, df2, "DataFetcher MUST be a singleton")
        
        # Test Cache Sharing
        token = "99926000"
        df1.get_ltp(token)
        self.assertEqual(self.mock_api.ltpData.call_count, 1)
        
        df2.get_ltp(token)
        # Should stay 1 because df2 uses df1's cache
        self.assertEqual(self.mock_api.ltpData.call_count, 1, "Cache NOT shared across singleton instances")
        print("✅ Singleton and Cache Sharing verified.")

    def test_graceful_shutdown_momentum(self):
        print("\n--- Testing Graceful Shutdown (Momentum) ---")
        strategy = MomentumStrategy(self.mock_api, self.mock_token_loader, dry_run=True)
        
        # Start monitoring in a way we can stop it
        strategy.running = True
        strategy.stop()
        self.assertFalse(strategy.running, "Strategy.stop() did not set running=False")
        print("✅ Momentum stop() verified.")

    def test_graceful_shutdown_gamma_blast(self):
        print("\n--- Testing Graceful Shutdown (GammaBlast) ---")
        strategy = GammaBlastStrategy(self.mock_api, self.mock_token_loader, dry_run=True)
        strategy.running = True
        strategy.stop()
        self.assertFalse(strategy.running, "GammaBlast Strategy.stop() did not set running=False")
        print("✅ GammaBlast stop() verified.")

    def test_graceful_shutdown_straddle(self):
        print("\n--- Testing Graceful Shutdown (Straddle) ---")
        strategy = NiftyStrategy(self.mock_api, self.mock_token_loader, dry_run=True)
        strategy.legs_active['CE'] = True
        strategy.leg_metadata['CE'] = {'symbol': 'TEST', 'token': '123', 'qty': 50}
        
        strategy.stop()
        self.assertFalse(strategy.running, "Straddle Strategy.stop() did not set running=False")
        # Ensure exit_at_market was called (implied by cleanup prints but let's be sure)
        self.assertFalse(strategy.legs_active['CE'])
        print("✅ Straddle stop() and position cleanup verified.")

if __name__ == "__main__":
    unittest.main()
