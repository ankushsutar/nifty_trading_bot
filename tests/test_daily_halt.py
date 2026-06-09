import unittest
from unittest.mock import MagicMock, patch
import os
import json
import datetime
from bot.core.safety_checks import SafetyGatekeeper
from bot.core.decision_engine import DecisionEngine
from bot.core.kill_switch import deactivate_kill_switch, is_kill_switch_active

class TestDailyHaltAndIsolation(unittest.TestCase):
    def setUp(self):
        self.mock_api = MagicMock()
        self.mock_loader = MagicMock()
        # Ensure kill switch starts deactivated
        deactivate_kill_switch()
        
        # Backup session_stats.json if it exists
        self.stats_file = os.path.join(os.getcwd(), "data", "session_stats.json")
        self.stats_backup = None
        if os.path.exists(self.stats_file):
            with open(self.stats_file, "r") as f:
                self.stats_backup = f.read()
            os.remove(self.stats_file)

        # Class-level and instance-level patch to avoid singleton mismatch/state leakage and querying DB for open trades
        from bot.core.trade_repo import TradeRepository, trade_repo
        self.patchers = [
            patch.object(TradeRepository, 'get_open_trades', return_value=[]),
            patch.object(trade_repo, 'get_open_trades', return_value=[])
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        deactivate_kill_switch()
        for p in self.patchers:
            p.stop()
        # Restore session_stats.json backup
        if self.stats_backup is not None:
            os.makedirs(os.path.dirname(self.stats_file), exist_ok=True)
            with open(self.stats_file, "w") as f:
                f.write(self.stats_backup)
        elif os.path.exists(self.stats_file):
            os.remove(self.stats_file)

    def test_session_stats_isolation(self):
        """Verify that starting capital and peak profit are isolated between live and paper modes."""
        gatekeeper_paper = SafetyGatekeeper(self.mock_api, dry_run=True)
        gatekeeper_live = SafetyGatekeeper(self.mock_api, dry_run=False)

        # Mock get_current_capital for initialization
        gatekeeper_paper.get_current_capital = MagicMock(return_value=50000.0)
        gatekeeper_live.get_current_capital = MagicMock(return_value=150000.0)

        # Initialize starting capital for both
        cap_paper = gatekeeper_paper.get_starting_capital()
        cap_live = gatekeeper_live.get_starting_capital()

        self.assertEqual(cap_paper, 50000.0)
        self.assertEqual(cap_live, 150000.0)

        # Record peak profits
        gatekeeper_paper.track_peak_profit(1000.0)
        gatekeeper_live.track_peak_profit(5000.0)

        # Read stats file and verify structure
        with open(self.stats_file, "r") as f:
            stats = json.load(f)
        
        today = datetime.date.today().isoformat()
        self.assertIn(today, stats)
        day_stats = stats[today]
        
        # Verify separate keys
        self.assertIn("paper", day_stats)
        self.assertIn("live", day_stats)
        
        self.assertEqual(day_stats["paper"]["starting_capital"], 50000.0)
        self.assertEqual(day_stats["paper"]["peak_profit"], 1000.0)
        
        self.assertEqual(day_stats["live"]["starting_capital"], 150000.0)
        self.assertEqual(day_stats["live"]["peak_profit"], 5000.0)

    def test_daily_loss_activates_kill_switch(self):
        """Verify that hitting the daily loss limit activates the global kill switch."""
        engine = DecisionEngine(self.mock_api, self.mock_loader, dry_run=True)
        
        # Mock safety gatekeeper checks to pass time checks and fail daily loss check
        engine.gatekeeper.is_market_open = MagicMock(return_value=True)
        engine.gatekeeper.is_blackout_period = MagicMock(return_value=False)
        engine.gatekeeper.get_starting_capital = MagicMock(return_value=100000.0)
        engine.gatekeeper.get_daily_realized_pnl = MagicMock(return_value=-20000.0) # -20% PnL (breaches limit)
        
        # Deactivate first
        self.assertFalse(is_kill_switch_active())
        
        # Trigger selection
        strat, risk = engine.analyze_and_select()
        
        self.assertIsNone(strat)
        self.assertTrue(is_kill_switch_active())

    def test_profit_protection_activates_kill_switch(self):
        """Verify that triggering profit protection activates the global kill switch."""
        engine = DecisionEngine(self.mock_api, self.mock_loader, dry_run=True)
        
        # Mock safety gatekeeper checks to pass time checks
        engine.gatekeeper.is_market_open = MagicMock(return_value=True)
        engine.gatekeeper.is_blackout_period = MagicMock(return_value=False)
        
        # Setup stats file with a high peak profit
        today = datetime.date.today().isoformat()
        os.makedirs(os.path.dirname(self.stats_file), exist_ok=True)
        with open(self.stats_file, "w") as f:
            json.dump({
                today: {
                    "paper": {
                        "starting_capital": 100000.0,
                        "peak_profit": 5000.0, # ₹5,000 peak profit
                        "last_updated": 0
                    }
                }
            }, f)
        
        # Mock realized pnl to be 2000 (which is < 50% drawdown threshold, i.e., 2500)
        engine.gatekeeper.get_daily_realized_pnl = MagicMock(return_value=2000.0)
        
        # Deactivate first
        self.assertFalse(is_kill_switch_active())
        
        # Trigger selection
        strat, risk = engine.analyze_and_select()
        
        self.assertIsNone(strat)
        self.assertTrue(is_kill_switch_active())

if __name__ == "__main__":
    unittest.main()
