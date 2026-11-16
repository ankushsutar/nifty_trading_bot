import unittest
from unittest.mock import MagicMock, patch
import os, sys, datetime

class TestAuditFixes(unittest.TestCase):
    @patch('bot.utils.trade_journal.TradeJournal.log_trade')
    def test_close_trade_safeguards_zero_exit_price(self, mock_log_trade):
        """Verify close_trade does not wipe out position to -100% loss when exit_price is 0."""
        from bot.core.trade_repo import TradeRepository, InMemoryCollection
        
        # Test direct instance with test environment in-memory collection
        repo = TradeRepository()
        repo.collection = InMemoryCollection()
        
        # Save a test trade
        doc = {
            "id": 9999,
            "symbol": "NIFTY_TEST_PE",
            "token": "12345",
            "entry_price": 100.0,
            "qty": 50,
            "side": "BUY",
            "status": "OPEN",
            "last_ltp": 125.0,
            "pnl": 0.0
        }
        repo.collection.insert_one(doc)
        
        # Close trade with exit_price=0.0 (simulating EXTERNAL_SYNC_RESET)
        repo.close_trade(trade_id=9999, exit_price=0.0, exit_reason="EXTERNAL_SYNC_RESET")
        
        updated = repo.collection.find_one({"id": 9999})
        self.assertEqual(updated["status"], "CLOSED")
        # Should fallback to last_ltp (125.0), NOT 0.0
        self.assertEqual(updated["exit_price"], 125.0)
        # PnL should be (125.0 - 100.0) * 50 = +1250.0, NOT -5000.0
        self.assertEqual(updated["pnl"], 1250.0)
        
    def test_kite_market_protection_passed_for_market_orders(self):
        """Verify Kite broker adapter attaches market_protection for market orders."""
        from bot.core.broker_adapter import KiteBrokerAdapter
        
        mock_kite = MagicMock()
        mock_kite.VARIETY_REGULAR = "regular"
        mock_kite.PRODUCT_MIS = "MIS"
        mock_kite.ORDER_TYPE_MARKET = "MARKET"
        mock_kite.place_order.return_value = "ORDER_99"
        
        adapter = KiteBrokerAdapter(mock_kite, access_token="mock_token")
        
        orderparams = {
            "exchange": "NFO",
            "tradingsymbol": "NIFTY26NOV24000CE",
            "transactiontype": "BUY",
            "quantity": 65,
            "producttype": "INTRADAY",
            "ordertype": "MARKET"
        }
        
        resp = adapter.placeOrder(orderparams)
        self.assertTrue(resp["status"])
        
        mock_kite.place_order.assert_called_once_with(
            variety="regular",
            exchange="NFO",
            tradingsymbol="NIFTY26NOV24000CE",
            transaction_type="BUY",
            quantity=65,
            product="MIS",
            order_type="MARKET",
            price=None,
            trigger_price=None,
            market_protection=5
        )

if __name__ == "__main__":
    unittest.main()
