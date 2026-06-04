import sys
import os
import unittest
import datetime
from unittest.mock import MagicMock, patch

# Mock pymongo before importing anything that uses trade_repo
sys.modules['pymongo'] = MagicMock()

# Ensure project root is in path
sys.path.append(os.getcwd())

from bot.core.trade_repo import TradeRepository
from bot.core.safety_checks import SafetyGatekeeper

class TestSafetyRefactoring(unittest.TestCase):
    def setUp(self):
        # Setup mock trade repository client
        self.mock_collection = MagicMock()
        self.trade_repo = TradeRepository()
        self.trade_repo.collection = self.mock_collection
        self.trade_repo.client = MagicMock()
        
        # Patch the singleton trade_repo
        self.repo_patcher = patch('bot.core.trade_repo.trade_repo', self.trade_repo)
        self.repo_patcher.start()

        # Clean up trade journal file if it exists
        self.journal_file = os.path.join(os.getcwd(), "logs", "trade_journal.csv")
        if os.path.exists(self.journal_file):
            try:
                os.remove(self.journal_file)
            except Exception:
                pass

    def tearDown(self):
        self.repo_patcher.stop()
        if os.path.exists(self.journal_file):
            try:
                os.remove(self.journal_file)
            except Exception:
                pass

    def test_timezone_robust_today_trades(self):
        """Verify get_today_trades uses timezone-robust IST start of day."""
        self.mock_collection.find.return_value.sort.return_value = []
        
        # Run method
        self.trade_repo.get_today_trades(mode="LIVE")
        
        # Verify query checks created_at $gte
        self.mock_collection.find.assert_called_once()
        args, kwargs = self.mock_collection.find.call_args
        query = args[0]
        self.assertIn("created_at", query)
        self.assertIn("$gte", query["created_at"])
        
        # Check start day timestamp is naive datetime (MongoDB compatible)
        today_start_dt = query["created_at"]["$gte"]
        self.assertIsInstance(today_start_dt, datetime.datetime)
        self.assertIsNone(today_start_dt.tzinfo)

    def test_centralized_journaling_close_trade(self):
        """Verify close_trade triggers TradeJournal.log_trade automatically."""
        mock_trade = {
            "id": 404,
            "symbol": "NIFTY27FEB2622000CE",
            "qty": 50,
            "entry_price": 100.0,
            "pnl": 0.0,
            "status": "OPEN",
            "strategy": "MOMENTUM"
        }
        
        updated_trade = {
            "id": 404,
            "symbol": "NIFTY27FEB2622000CE",
            "qty": 50,
            "entry_price": 100.0,
            "exit_price": 120.0,
            "pnl": 1000.0,
            "status": "CLOSED",
            "strategy": "MOMENTUM",
            "exit_reason": "TARGET_HIT"
        }
        
        # Mock database actions
        self.mock_collection.find_one.side_effect = [mock_trade, updated_trade]
        
        # Close trade which should trigger journaling
        self.trade_repo.close_trade(trade_id=404, exit_price=120.0, exit_reason="TARGET_HIT")
        
        # Verify the CSV log file was generated or updated
        self.assertTrue(os.path.exists(self.journal_file), "trade_journal.csv was not created by close_trade")
        
        # Read the file content and check values
        with open(self.journal_file, "r") as f:
            content = f.read()
            self.assertIn("MOMENTUM", content)
            self.assertIn("NIFTY27FEB2622000CE", content)
            self.assertIn("1000.0", content)
            self.assertIn("TARGET_HIT", content)

    def test_broker_realized_pnl_calculation(self):
        """Verify get_broker_realized_pnl correctly computes realized PnL from Angel One positions."""
        mock_api = MagicMock()
        mock_api.position.return_value = {
            "status": True,
            "data": [
                {
                    "tradingsymbol": "NIFTY27FEB2622000CE",
                    "buyqty": "100",
                    "sellqty": "100",
                    "buyavgprice": "100.0",
                    "sellavgprice": "120.0",
                    "realisedprice": "2000.0"
                },
                {
                    "tradingsymbol": "NIFTY27FEB2622000PE",
                    "buyqty": "50",
                    "sellqty": "50",
                    "buyavgprice": "150.0",
                    "sellavgprice": "130.0",
                    "realisedprice": "-1000.0"
                }
            ]
        }
        
        gatekeeper = SafetyGatekeeper(mock_api, dry_run=False)
        pnl = gatekeeper.get_broker_realized_pnl()
        
        # Total expected P&L = 2000 - 1000 = 1000.0
        self.assertEqual(pnl, 1000.0)

    def test_instrument_cooldown_detection(self):
        """Verify check_instrument_cooldown handles descending sort index and closed_at correctly."""
        # Setup mock trades sorted descending (index 0 is the most recent trade)
        recent_lost_trade = {
            "id": 502,
            "symbol": "COOLDOWN_TEST_CE",
            "status": "CLOSED",
            "pnl": -500.0,
            "closed_at": datetime.datetime.now() - datetime.timedelta(minutes=30) # 30 mins ago
        }
        older_won_trade = {
            "id": 501,
            "symbol": "COOLDOWN_TEST_CE",
            "status": "CLOSED",
            "pnl": 1000.0,
            "closed_at": datetime.datetime.now() - datetime.timedelta(hours=2) # 2 hours ago
        }
        
        self.mock_collection.find.return_value.sort.return_value = [recent_lost_trade, older_won_trade]
        
        gatekeeper = SafetyGatekeeper(MagicMock(), dry_run=False)
        allowed = gatekeeper.check_instrument_cooldown("COOLDOWN_TEST_CE")
        
        # Cooldown should block re-entry (returns False) because the most recent trade was a loss
        self.assertFalse(allowed)

        # Now test with a winning recent trade
        recent_won_trade = {
            "id": 503,
            "symbol": "COOLDOWN_TEST_CE",
            "status": "CLOSED",
            "pnl": 500.0,
            "closed_at": datetime.datetime.now() - datetime.timedelta(minutes=10) # 10 mins ago
        }
        self.mock_collection.find.return_value.sort.return_value = [recent_won_trade, recent_lost_trade, older_won_trade]
        allowed_after_win = gatekeeper.check_instrument_cooldown("COOLDOWN_TEST_CE")
        
        # Should allow trading because the latest trade won
        self.assertTrue(allowed_after_win)

if __name__ == "__main__":
    unittest.main()
