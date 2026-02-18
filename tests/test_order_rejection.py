import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.strategies.momentum_strategy import MomentumStrategy

class TestOrderRejection(unittest.TestCase):
    def setUp(self):
        self.mock_api = MagicMock()
        self.mock_token_loader = MagicMock()
        self.strategy = MomentumStrategy(self.mock_api, self.mock_token_loader, dry_run=False)
        
        # Mock internal components to avoid side effects
        self.strategy.gatekeeper = MagicMock()
        self.strategy.data_fetcher = MagicMock()
        self.strategy.trade_repo = MagicMock()
        
    def test_wait_for_fill_rejection(self):
        """Test that wait_for_fill correctly parses a rejected order."""
        # Mock orderBook response
        self.mock_api.orderBook.return_value = {
            'status': True,
            'data': [
                {'orderid': '1001', 'status': 'rejected', 'text': 'Margin Shortfall', 'averageprice': 0}
            ]
        }
        
        result = self.strategy.wait_for_fill('1001')
        self.assertEqual(result['status'], 'REJECTED')
        self.assertEqual(result['message'], 'Margin Shortfall')

    def test_wait_for_fill_success(self):
        """Test that wait_for_fill correctly parses a filled order."""
        self.mock_api.orderBook.return_value = {
            'status': True,
            'data': [
                {'orderid': '1002', 'status': 'complete', 'averageprice': 150.5}
            ]
        }
        
        result = self.strategy.wait_for_fill('1002')
        self.assertEqual(result['status'], 'FILLED')
        self.assertEqual(result['price'], 150.5)

    def test_wait_for_fill_timeout(self):
        """Test that wait_for_fill returns TIMEOUT if order is not found/pending long."""
        self.mock_api.orderBook.return_value = {
            'status': True,
            'data': [
                {'orderid': '1003', 'status': 'open'} 
            ]
        }
        
        # Patch time.sleep to run fast
        with patch('time.sleep', return_value=None):
            result = self.strategy.wait_for_fill('1003')
            
        self.assertEqual(result['status'], 'TIMEOUT')
        self.assertIsNone(result['price'])
        
    @patch('bot.strategies.momentum_strategy.trade_repo')
    def test_enter_position_aborts_on_rejection(self, mock_repo):
        """Verify enter_position aborts if order is rejected."""
        # Setup Mocks
        self.strategy.get_nifty_ltp = MagicMock(return_value=22000)
        self.strategy.last_analysis = {'atr': 50}
        self.strategy.token_loader.get_token.return_value = ('123', 'NIFTY22000CE')
        self.mock_api.placeOrder.return_value = '1001'
        
        # Mock wait_for_fill to return REJECTED
        self.strategy.wait_for_fill = MagicMock(return_value={'status': 'REJECTED', 'message': 'Fund Error'})
        
        # Execute
        self.strategy.enter_position('DATE', 'CE')
        
        # Assertions
        # Should NOT have active position
        self.assertIsNone(self.strategy.active_position)
        # Should NOT save trade
        mock_repo.save_trade.assert_not_called()
        
if __name__ == '__main__':
    unittest.main()
