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
        self.strategy.gatekeeper.get_iv_rank.return_value = 0.5
        self.strategy.gatekeeper.get_current_capital.return_value = 50000.0
        
        from bot.config.settings import CAPITAL_TIERS
        tier = CAPITAL_TIERS["MICRO"]
        self.strategy.gatekeeper.get_tier.return_value = tier
        self.strategy.gatekeeper.get_compounded_lots.return_value = 1
        self.strategy.data_fetcher = MagicMock()
        self.strategy.trade_repo = MagicMock()
        
    @patch('bot.core.order_feed.get_session')
    @patch('threading.Event.wait', return_value=False)
    def test_wait_for_fill_rejection(self, mock_event_wait, mock_get_session):
        """Test that wait_for_fill correctly parses a rejected order."""
        mock_get_session.return_value = self.mock_api
        from bot.core.order_feed import order_feed
        order_feed.register_order('1001')
        
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

    @patch('bot.core.order_feed.get_session')
    @patch('threading.Event.wait', return_value=False)
    def test_wait_for_fill_success(self, mock_event_wait, mock_get_session):
        """Test that wait_for_fill correctly parses a filled order."""
        mock_get_session.return_value = self.mock_api
        from bot.core.order_feed import order_feed
        order_feed.register_order('1002')
        
        self.mock_api.orderBook.return_value = {
            'status': True,
            'data': [
                {'orderid': '1002', 'status': 'complete', 'averageprice': 150.5}
            ]
        }
        
        result = self.strategy.wait_for_fill('1002')
        self.assertEqual(result['status'], 'FILLED')
        self.assertEqual(result['price'], 150.5)

    @patch('bot.core.order_feed.get_session')
    @patch('threading.Event.wait', return_value=False)
    def test_wait_for_fill_timeout(self, mock_event_wait, mock_get_session):
        """Test that wait_for_fill returns TIMEOUT if order is not found/pending long."""
        mock_get_session.return_value = self.mock_api
        from bot.core.order_feed import order_feed
        order_feed.register_order('1003')
        
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
        self.assertIsNone(result.get('price'))
        
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
