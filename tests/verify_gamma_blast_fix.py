import sys
import unittest
from unittest.mock import MagicMock, patch

class TestGammaBlastFix(unittest.TestCase):
    def setUp(self):
        # We start patching sys.modules to avoid global state pollution
        self.mock_modules = {
            'bot.utils.logger': MagicMock(),
            'bot.core.trade_repo': MagicMock(),
            'bot.core.order_manager': MagicMock(),
            'bot.core.angel_connect': MagicMock(),
            'bot.core.safety_checks': MagicMock(),
            'bot.core.market_feed': MagicMock(),
            'bot.core.data_fetcher': MagicMock(),
            'bot.core.oi_analyzer': MagicMock(),
            'backend.market_service': MagicMock(),
        }
        self.patcher = patch.dict(sys.modules, self.mock_modules)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_gamma_blast_no_crash(self):
        # Now import inside the test method
        from bot.core.trade_repo import trade_repo
        from bot.strategies.gamma_blast_strategy import GammaBlastStrategy
        
        api = MagicMock()
        loader = MagicMock()
        
        # 1. Mock trade_repo.get_active_trade to return None (no active trade)
        trade_repo.get_active_trade.return_value = None
        
        strategy = GammaBlastStrategy(api, loader, dry_run=False)
        
        # Mock gatekeeper to return False for market open to avoid further execution
        strategy.gatekeeper.is_market_open = MagicMock(return_value=False)
        
        try:
            strategy.execute(expiry="27FEB26")
        except NameError as e:
            self.fail(f"strategy.execute() crashed with NameError: {e}")
        except Exception:
            # Other exceptions are fine as we mocked things to stop early
            pass

if __name__ == "__main__":
    unittest.main()
