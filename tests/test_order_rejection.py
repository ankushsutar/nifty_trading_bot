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
        self.strategy.gatekeeper.get_compounded_lots.return_value = 1
        self.strategy.gatekeeper.check_trade_viability.return_value = True
        self.strategy.gatekeeper.check_instrument_cooldown.return_value = True
        self.strategy.gatekeeper.check_sentiment_risk.return_value = True
        self.strategy.data_fetcher = MagicMock()
        self.strategy.trade_repo = MagicMock()
        
    @patch('bot.core.order_feed.OrderFeedService.wait_for_fill')
    def test_wait_for_fill_rejection(self, mock_wait_for_fill):
        """Test that wait_for_fill correctly parses a rejected order."""
        mock_wait_for_fill.return_value = {'status': 'REJECTED', 'message': 'Margin Shortfall'}
        
        result = self.strategy.wait_for_fill('1001')
        self.assertEqual(result['status'], 'REJECTED')
        self.assertEqual(result['message'], 'Margin Shortfall')

    @patch('bot.core.order_feed.OrderFeedService.wait_for_fill')
    def test_wait_for_fill_success(self, mock_wait_for_fill):
        """Test that wait_for_fill correctly parses a filled order."""
        mock_wait_for_fill.return_value = {'status': 'FILLED', 'price': 150.5}
        
        result = self.strategy.wait_for_fill('1002')
        self.assertEqual(result['status'], 'FILLED')
        self.assertEqual(result['price'], 150.5)

    @patch('bot.core.order_feed.OrderFeedService.wait_for_fill')
    def test_wait_for_fill_timeout(self, mock_wait_for_fill):
        """Test that wait_for_fill returns TIMEOUT if order is not found/pending long."""
        mock_wait_for_fill.return_value = {'status': 'TIMEOUT', 'price': None}
        
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
