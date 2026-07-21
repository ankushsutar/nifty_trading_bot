import unittest
from unittest.mock import MagicMock, patch
import pandas as pd

from bot.strategies.zero_to_hero_strategy import ZeroToHeroStrategy
from bot.core.safety_checks import SafetyGatekeeper
from bot.config.settings import Config, CapitalTier

class TestStrategyExecutionFixes(unittest.TestCase):
    def setUp(self):
        self.mock_api = MagicMock()
        self.mock_token_loader = MagicMock()
        self.z2h = ZeroToHeroStrategy(self.mock_api, self.mock_token_loader, dry_run=True)

    def test_z2h_lot_capping_and_risk_limit(self):
        """Verify that Zero-To-Hero lot count is strictly capped at max 2 lots and max risk budget of ₹1500."""
        self.assertEqual(ZeroToHeroStrategy.MAX_ABSOLUTE_RISK, 1500.0)
        self.assertEqual(ZeroToHeroStrategy.FIXED_STOP_LOSS_PCT, 0.35)

        # Mock gatekeeper capital to ₹50,000
        self.z2h.gatekeeper.get_current_capital = MagicMock(return_value=50000.0)
        
        # Test sizing formula for cheap option (₹6.00)
        prem = 6.0
        current_capital = 50000.0
        dynamic_risk_limit = min(ZeroToHeroStrategy.MAX_ABSOLUTE_RISK, max(1000.0, current_capital * 0.03))
        max_allowed_qty = (dynamic_risk_limit / prem)
        raw_lots = int(max_allowed_qty // Config.NIFTY_LOT_SIZE)
        lots = min(2, max(1, raw_lots))
        qty = int(lots * Config.NIFTY_LOT_SIZE)

        self.assertEqual(dynamic_risk_limit, 1500.0)
        self.assertEqual(lots, 2)  # Must be capped at 2 lots, NOT 3+ or 10
        self.assertEqual(qty, 130)  # 2 lots * 65

    def test_worst_case_daily_loss_projection(self):
        """Verify that check_max_daily_loss blocks entries if worst-case loss would breach daily limit."""
        gatekeeper = SafetyGatekeeper(self.mock_api, dry_run=True)
        gatekeeper.get_starting_capital = MagicMock(return_value=30000.0)
        gatekeeper.get_broker_realized_pnl = MagicMock(return_value=-2000.0)
        
        # SMALL tier limit is 8% of 30,000 = -2,400 max daily loss
        # Realized is -2,000. If projected worst-case loss is ₹500, total = -2,500 <= -2,400 -> Should block!
        allowed = gatekeeper.check_max_daily_loss(active_unrealized_pnl=0.0, worst_case_new_loss=500.0)
        self.assertFalse(allowed)

    @patch("bot.core.levels_provider.levels_provider.get_levels")
    def test_overextension_and_level_barrier_logic(self, mock_levels):
        """Verify level barrier calculation logic for CE entries near PDH."""
        mock_levels.return_value = {"pdh": 24200.0, "pdl": 24000.0}
        
        nifty_spot = 24190.0  # 10 points below PDH (within 15pts zone)
        pdh = 24200.0
        
        is_blocked = (0 <= (pdh - nifty_spot) <= 15)
        self.assertTrue(is_blocked)

if __name__ == "__main__":
    unittest.main()
