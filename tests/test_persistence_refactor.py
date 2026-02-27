import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# CRITICAL: Mock pymongo before importing anything that uses trade_repo
sys.modules['pymongo'] = MagicMock()

# Ensure project root is in path
sys.path.append(os.getcwd())

from bot.core.order_manager import OrderManager
from bot.core.trade_repo import TradeRepository

# Mock is_kill_switch_active
import bot.core.order_manager
bot.core.order_manager.is_kill_switch_active = MagicMock(return_value=False)

class TestPersistenceFlow(unittest.TestCase):
    def setUp(self):
        # Mock API
        self.mock_api = MagicMock()
        self.order_manager = OrderManager(self.mock_api, dry_run=False)
        self.order_manager.live_trade_enabled = True
        
        # Mock TradeRepo Collection
        self.mock_collection = MagicMock()
        self.trade_repo = TradeRepository()
        self.trade_repo.collection = self.mock_collection
        self.trade_repo.client = MagicMock() # Ensure it looks connected
        
        # Patch the singleton trade_repo used by order_manager
        self.patcher = patch('bot.core.order_manager.trade_repo', self.trade_repo)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_place_order_with_persistence(self):
        """Verify that place_order calls save_trade when strategy_name is provided."""
        self.mock_api.placeOrder.return_value = {"status": True, "data": {"orderid": "12345"}}
        self.order_manager.place_limit_order = MagicMock(return_value="12345")
        
        params = {
            "tradingsymbol": "NIFTY27FEB2622000CE",
            "symboltoken": "12345",
            "quantity": 50,
            "transactiontype": "BUY"
        }
        
        # Mock save_trade to verify it's called
        with patch.object(self.trade_repo, 'save_trade', wraps=self.trade_repo.save_trade) as mocked_save:
            oid = self.order_manager.place_order(params, strategy_name="TEST_STRAT", mode="PAPER")
            
            self.assertEqual(oid, "12345")
            mocked_save.assert_called_once()
            args, kwargs = mocked_save.call_args
            self.assertEqual(kwargs['strategy'], "TEST_STRAT")
            self.assertEqual(kwargs['status'], "PLACED")
            self.assertEqual(kwargs['mode'], "PAPER")

    def test_update_trade_fill(self):
        """Verify update_trade_fill correctly links and updates the DB record."""
        # Setup mock active trade
        self.trade_repo.get_active_trade = MagicMock(return_value={
            "id": 101, "symbol": "NIFTY_CE", "status": "PLACED"
        })
        
        with patch.object(self.trade_repo, 'update_entry_price') as mocked_update:
            tid = self.order_manager.update_trade_fill("NIFTY_CE", "TEST_STRAT", 105.5, expected_price=105.0)
            
            self.assertEqual(tid, 101)
            mocked_update.assert_called_once_with(101, 105.5, expected_price=105.0)

    def test_trade_repo_retry_logic(self):
        """Verify that TradeRepository.save_trade retries on failure (and import time is fixed)."""
        # Mock insert_one to fail twice then succeed
        self.mock_collection.insert_one.side_effect = [Exception("DB Fail"), Exception("DB Fail"), MagicMock()]
        
        # We need a real counter for save_trade
        self.trade_repo.counter_collection = MagicMock()
        self.trade_repo.counter_collection.find_one_and_update.return_value = {"seq": 1}

        # This should succeed after 2 retries (3 attempts total)
        # If 'time' was missing, this would raise NameError
        try:
            tid = self.trade_repo.save_trade("SYM", "TOK", "CE", 50, 0.0, strategy="RETRY_TEST")
            self.assertEqual(tid, 1)
            self.assertEqual(self.mock_collection.insert_one.call_count, 3)
            print("✅ TradeRepository retry logic verified (and 'time' is imported!)")
        except NameError:
            self.fail("TradeRepository.save_trade raised NameError (likely missing time import)")
        except Exception as e:
            self.fail(f"TradeRepository.save_trade failed unexpectedly: {e}")

if __name__ == "__main__":
    unittest.main()
