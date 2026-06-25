import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Save original modules to prevent pollution
orig_pymongo = sys.modules.get('pymongo')
orig_trade_repo = sys.modules.get('bot.core.trade_repo')
orig_order_manager = sys.modules.get('bot.core.order_manager')

# CRITICAL: Mock pymongo before importing anything that uses trade_repo
sys.modules['pymongo'] = MagicMock()

# Ensure project root is in path
sys.path.append(os.getcwd())

from bot.core.order_manager import OrderManager
from bot.core.trade_repo import TradeRepository

# Restore original modules so other tests get fresh/real modules
if orig_pymongo is not None:
    sys.modules['pymongo'] = orig_pymongo
else:
    sys.modules.pop('pymongo', None)

if orig_trade_repo is not None:
    sys.modules['bot.core.trade_repo'] = orig_trade_repo
else:
    sys.modules.pop('bot.core.trade_repo', None)

if orig_order_manager is not None:
    sys.modules['bot.core.order_manager'] = orig_order_manager
else:
    sys.modules.pop('bot.core.order_manager', None)

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

        # Patch is_kill_switch_active cleanly to avoid module pollution
        self.kill_switch_patcher = patch('bot.core.order_manager.is_kill_switch_active', return_value=False)
        self.kill_switch_patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.kill_switch_patcher.stop()

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
        # Mock find_one to return None so it doesn't try to update an existing active trade
        self.mock_collection.find_one.return_value = None
        
        # We need a real counter for save_trade
        self.trade_repo.counters = MagicMock()
        self.trade_repo.counters.find_one_and_update.return_value = {"seq": 1}

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

    def test_cleanup_stale_trades(self):
        """Verify that cleanup_stale_trades runs without raising MagicMock-related errors on InMemoryCollection."""
        import datetime
        from bot.core.trade_repo import InMemoryCollection
        self.trade_repo.collection = InMemoryCollection()
        
        # Insert a stale trade (created yesterday)
        yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
        stale_trade = {
            "_id": "stale_trade",
            "id": 1,
            "status": "OPEN",
            "created_at": yesterday,
            "updated_at": yesterday
        }
        self.trade_repo.collection.insert_one(stale_trade)
        
        # Insert a fresh trade (created today)
        fresh_trade = {
            "_id": "fresh_trade",
            "id": 2,
            "status": "OPEN",
            "created_at": datetime.datetime.now(),
            "updated_at": datetime.datetime.now()
        }
        self.trade_repo.collection.insert_one(fresh_trade)
        
        # Run cleanup
        modified_count = self.trade_repo.cleanup_stale_trades()
        
        # Verify result is correct and count returned is an integer
        self.assertEqual(modified_count, 1)
        
        # Verify stale trade is closed
        stale_updated = self.trade_repo.collection.find_one({"id": 1})
        self.assertEqual(stale_updated["status"], "CLOSED")
        self.assertEqual(stale_updated["exit_reason"], "STALE_OVERNIGHT")
        
        # Verify fresh trade remains open
        fresh_updated = self.trade_repo.collection.find_one({"id": 2})
        self.assertEqual(fresh_updated["status"], "OPEN")

    def test_get_open_trades_symbol_filtering(self):
        """Verify that get_open_trades correctly filters by symbol/index."""
        from bot.core.trade_repo import InMemoryCollection
        self.trade_repo.collection = InMemoryCollection()
        
        # Insert a Nifty trade
        nifty_trade = {
            "id": 1,
            "status": "OPEN",
            "symbol": "NIFTY26JUN22000CE",
            "strategy": "MOMENTUM"
        }
        self.trade_repo.collection.insert_one(nifty_trade)
        
        # Insert a FinNifty trade
        finnifty_trade = {
            "id": 2,
            "status": "OPEN",
            "symbol": "FINNIFTY26JUN23000PE",
            "strategy": "MOMENTUM"
        }
        self.trade_repo.collection.insert_one(finnifty_trade)
        
        # 1. Fetch with NIFTY filter
        nifty_results = self.trade_repo.get_open_trades(symbol="NIFTY")
        self.assertEqual(len(nifty_results), 1)
        self.assertEqual(nifty_results[0]["symbol"], "NIFTY26JUN22000CE")
        
        # 2. Fetch with FINNIFTY filter
        finnifty_results = self.trade_repo.get_open_trades(symbol="FINNIFTY")
        self.assertEqual(len(finnifty_results), 1)
        self.assertEqual(finnifty_results[0]["symbol"], "FINNIFTY26JUN23000PE")
        
        # 3. Fetch with no symbol filter
        all_results = self.trade_repo.get_open_trades()
        self.assertEqual(len(all_results), 2)

if __name__ == "__main__":
    unittest.main()
