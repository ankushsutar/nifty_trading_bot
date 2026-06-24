
import sys
import unittest
from unittest.mock import MagicMock, patch
import datetime

# Save original modules to prevent pollution
orig_logger = sys.modules.get('bot.utils.logger')
orig_rate_limiter = sys.modules.get('bot.utils.rate_limiter')
orig_pymongo = sys.modules.get('pymongo')
orig_trade_repo = sys.modules.get('bot.core.trade_repo')

# Mock dependencies
sys.modules['bot.utils.logger'] = MagicMock()
sys.modules['bot.utils.rate_limiter'] = MagicMock()
sys.modules['pymongo'] = MagicMock()

from bot.core.trade_repo import TradeRepository

# Restore original modules so other tests get fresh/real modules
if orig_logger is not None:
    sys.modules['bot.utils.logger'] = orig_logger
else:
    sys.modules.pop('bot.utils.logger', None)

if orig_rate_limiter is not None:
    sys.modules['bot.utils.rate_limiter'] = orig_rate_limiter
else:
    sys.modules.pop('bot.utils.rate_limiter', None)

if orig_pymongo is not None:
    sys.modules['pymongo'] = orig_pymongo
else:
    sys.modules.pop('pymongo', None)

if orig_trade_repo is not None:
    sys.modules['bot.core.trade_repo'] = orig_trade_repo
else:
    sys.modules.pop('bot.core.trade_repo', None)

class TestReconciliation(unittest.TestCase):
    def setUp(self):
        # Reset the Singleton manually for the test
        TradeRepository._instance = None
        self.repo = TradeRepository()
        # Mocking the client and collection after instance creation
        self.repo.client = MagicMock()
        self.repo.collection = MagicMock()
        
    def test_reconcile_placed_trade(self):
        """Verify that a PLACED trade is updated to OPEN when a BUY fill is found."""
        # 1. Setup Mock DB state (A PLACED trade)
        placed_trade = {
            'id': 100,
            'symbol': 'NIFTY_TEST_CE',
            'status': 'PLACED',
            'qty': 50
        }
        self.repo.collection.find.return_value = [placed_trade]
        
        # 2. Setup Mock Broker state (A BUY fill for that symbol)
        mock_api = MagicMock()
        mock_api.tradeBook.return_value = {
            'status': True,
            'data': [{
                'tradingsymbol': 'NIFTY_TEST_CE',
                'transactiontype': 'BUY',
                'quantity': '50',
                'averageprice': '150.5'
            }]
        }
        
        # 3. Mock updating method
        self.repo.update_entry_price = MagicMock()
        
        # 4. Trigger reconciliation
        print("Running reconciliation for PLACED trade...")
        self.repo.reconcile_with_broker(mock_api)
        
        # 5. Verify update_entry_price was called with correct price
        self.repo.update_entry_price.assert_called_once_with(100, 150.5)
        print("✅ PASS: reconcile_with_broker correctly updated PLACED trade to OPEN.")

    def test_reconcile_open_trade(self):
        """Verify that an OPEN trade is closed when a SELL fill is found."""
        # 1. Setup Mock DB state (An OPEN trade)
        open_trade = {
            'id': 101,
            'symbol': 'NIFTY_TEST_PE',
            'status': 'OPEN',
            'qty': 50,
            'entry_price': 100.0
        }
        self.repo.collection.find.return_value = [open_trade]
        
        # 2. Setup Mock Broker state (A SELL fill)
        mock_api = MagicMock()
        mock_api.tradeBook.return_value = {
            'status': True,
            'data': [{
                'tradingsymbol': 'NIFTY_TEST_PE',
                'transactiontype': 'SELL',
                'quantity': '50',
                'averageprice': '120.0'
            }]
        }
        
        # 3. Mock close_trade
        self.repo.close_trade = MagicMock()
        
        # 4. Trigger reconciliation
        print("Running reconciliation for OPEN trade...")
        self.repo.reconcile_with_broker(mock_api)
        
        # 5. Verify close_trade was called with correct PnL
        self.repo.close_trade.assert_called_once()
        args, kwargs = self.repo.close_trade.call_args
        self.assertEqual(kwargs['trade_id'], 101)
        self.assertEqual(kwargs['exit_price'], 120.0)
        self.assertEqual(kwargs['pnl'], 1000.0) # (120 - 100) * 50
        print("✅ PASS: reconcile_with_broker correctly closed OPEN trade with SELL fill.")

    def test_reconcile_placed_sell_trade(self):
        """Verify that a PLACED SELL (short) trade is updated to OPEN when a SELL fill is found."""
        # 1. Setup Mock DB state (A PLACED SELL trade)
        placed_trade = {
            'id': 200,
            'symbol': 'NIFTY_SHORT_TEST',
            'status': 'PLACED',
            'side': 'SELL',
            'qty': 50
        }
        self.repo.collection.find.return_value = [placed_trade]
        
        # 2. Setup Mock Broker state (A SELL fill for that symbol)
        mock_api = MagicMock()
        mock_api.tradeBook.return_value = {
            'status': True,
            'data': [{
                'tradingsymbol': 'NIFTY_SHORT_TEST',
                'transactiontype': 'SELL',
                'quantity': '50',
                'averageprice': '85.5'
            }]
        }
        
        self.repo.update_entry_price = MagicMock()
        
        # 3. Trigger reconciliation
        print("Running reconciliation for PLACED SELL trade...")
        self.repo.reconcile_with_broker(mock_api)
        
        # 4. Verify update_entry_price was called with correct price
        self.repo.update_entry_price.assert_called_once_with(200, 85.5)
        print("✅ PASS: reconcile_with_broker correctly updated PLACED SELL trade to OPEN.")

    def test_reconcile_open_sell_trade(self):
        """Verify that an OPEN SELL (short) trade is closed when a BUY fill is found."""
        # 1. Setup Mock DB state (An OPEN SELL trade)
        open_trade = {
            'id': 201,
            'symbol': 'NIFTY_SHORT_TEST_2',
            'status': 'OPEN',
            'side': 'SELL',
            'qty': 50,
            'entry_price': 100.0
        }
        self.repo.collection.find.return_value = [open_trade]
        
        # 2. Setup Mock Broker state (A BUY fill)
        mock_api = MagicMock()
        mock_api.tradeBook.return_value = {
            'status': True,
            'data': [{
                'tradingsymbol': 'NIFTY_SHORT_TEST_2',
                'transactiontype': 'BUY',
                'quantity': '50',
                'averageprice': '60.0'
            }]
        }
        
        self.repo.close_trade = MagicMock()
        
        # 3. Trigger reconciliation
        print("Running reconciliation for OPEN SELL trade...")
        self.repo.reconcile_with_broker(mock_api)
        
        # 5. Verify close_trade was called with correct PnL: (100 - 60) * 50 = 2000.0
        self.repo.close_trade.assert_called_once()
        args, kwargs = self.repo.close_trade.call_args
        self.assertEqual(kwargs['trade_id'], 201)
        self.assertEqual(kwargs['exit_price'], 60.0)
        self.assertEqual(kwargs['pnl'], 2000.0)
        print("✅ PASS: reconcile_with_broker correctly closed OPEN SELL trade with BUY fill.")

if __name__ == '__main__':
    unittest.main()
