import sys
import os
import unittest
from unittest.mock import MagicMock, patch
import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.core.trade_repo import TradeRepository
from bot.core.order_manager import OrderManager

class TestUniquenessAndRollback(unittest.TestCase):
    def setUp(self):
        TradeRepository._instance = None
        self.repo = TradeRepository()
        self.repo.client = MagicMock()
        self.repo.collection = MagicMock()
        self.repo._get_next_sequence = MagicMock(return_value=123)

    def test_save_trade_updates_existing_placed_to_open(self):
        """Verify save_trade updates an existing PLACED trade to OPEN instead of duplicating it."""
        existing_placed = {
            "id": 123,
            "symbol": "NIFTY_TEST_CE",
            "token": "12345",
            "leg": "CE",
            "qty": 50,
            "entry_price": 0.0,
            "status": "PLACED",
            "mode": "LIVE",
            "strategy": "MOMENTUM"
        }
        self.repo.collection.find_one.return_value = existing_placed

        trade_id = self.repo.save_trade(
            symbol="NIFTY_TEST_CE",
            token="12345",
            leg="LC",
            qty=50,
            entry_price=10.5,
            mode="LIVE",
            strategy="MOMENTUM"
        )

        self.assertEqual(trade_id, 123)
        self.repo.collection.update_one.assert_called_once()
        self.repo.collection.insert_one.assert_not_called()
        
        args, kwargs = self.repo.collection.update_one.call_args
        self.assertEqual(args[0], {"id": 123})
        self.assertEqual(args[1]["$set"]["status"], "OPEN")
        self.assertEqual(args[1]["$set"]["entry_price"], 10.5)
        self.assertEqual(args[1]["$set"]["leg"], "LC")
        print("✅ PASS: save_trade updates existing PLACED trade to OPEN instead of duplicating.")

    def test_place_smart_limit_deletes_on_timeout(self):
        """Verify place_smart_limit deletes the early PLACED record if the order placement times out."""
        mock_api = MagicMock()
        
        om = OrderManager(mock_api, dry_run=False)
        om.live_trade_enabled = True
        om.place_limit_order = MagicMock(return_value="ORDER_123")
        
        mock_feed = MagicMock()
        mock_feed.wait_for_fill.return_value = {"status": "TIMEOUT", "price": 0.0}
        
        om.cancel_order = MagicMock(return_value=True)

        with patch('bot.core.order_manager.trade_repo') as mock_repo, \
             patch('bot.core.order_feed.order_feed', mock_feed):
            
            mock_repo.save_trade.return_value = 999
            
            oid = om.place_smart_limit(
                symbol="NIFTY_TEST_CE",
                token="12345",
                qty=50,
                initial_price=100.0,
                transaction_type="BUY",
                max_walk_ticks=1,
                strategy_name="MOMENTUM",
                mode="LIVE"
            )
            
            self.assertIsNone(oid)
            mock_repo.save_trade.assert_called_once()
            mock_repo.collection.delete_one.assert_called_once_with({"id": 999})
            print("✅ PASS: place_smart_limit deletes early PLACED record on failure/timeout.")

if __name__ == "__main__":
    unittest.main()
