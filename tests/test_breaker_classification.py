import unittest
from unittest.mock import MagicMock, patch
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.trade_repo import TradeRepository

class TestBreakerClassification(unittest.TestCase):
    def setUp(self):
        # Patch both class and instance to be immune to singleton resets and instance overrides
        from bot.core.trade_repo import trade_repo
        self.patchers = [
            patch.object(TradeRepository, 'get_open_trades', return_value=[]),
            patch.object(trade_repo, 'get_open_trades', return_value=[])
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()

    def test_breaker_classification(self):
        api = MagicMock()
        gatekeeper = SafetyGatekeeper(api, dry_run=True)
        
        # Mock starting capital to 100,000 (MEDIUM tier)
        # MEDIUM tier max daily loss pct is 0.06 -> max_loss = -6,000
        gatekeeper.get_starting_capital = MagicMock(return_value=100000.0)
        
        # Test case 1: No breach, last_breaker_triggered should be None
        gatekeeper.get_daily_realized_pnl = MagicMock(return_value=0.0)
        gatekeeper.track_peak_profit = MagicMock(return_value=0.0)
        
        passed = gatekeeper.check_max_daily_loss(active_unrealized_pnl=0.0)
        self.assertTrue(passed)
        self.assertIsNone(gatekeeper.last_breaker_triggered)
        
        # Test case 2: Profit Protection Triggered
        # Peak profit: 10,000 -> Floor: 5,000 (50% of peak)
        # Current daily P&L: 3,000 (drawdown from peak > 50%)
        gatekeeper.get_daily_realized_pnl = MagicMock(return_value=3000.0)
        gatekeeper.track_peak_profit = MagicMock(return_value=10000.0)
        
        passed = gatekeeper.check_max_daily_loss(active_unrealized_pnl=0.0)
        self.assertFalse(passed)
        self.assertEqual(gatekeeper.last_breaker_triggered, "PROFIT_PROTECTION")
        
        # Test case 3: Global Max Daily Loss Triggered
        # Peak profit: 0 -> Floor not active
        # Current daily P&L: -10,000 (exceeds -6,000 limit)
        gatekeeper.get_daily_realized_pnl = MagicMock(return_value=-10000.0)
        gatekeeper.track_peak_profit = MagicMock(return_value=0.0)
        
        passed = gatekeeper.check_max_daily_loss(active_unrealized_pnl=0.0)
        self.assertFalse(passed)
        self.assertEqual(gatekeeper.last_breaker_triggered, "MAX_DAILY_LOSS")

if __name__ == '__main__':
    unittest.main()
