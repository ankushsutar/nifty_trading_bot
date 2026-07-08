import unittest
from unittest.mock import MagicMock, patch
import os
import sys

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime as real_datetime
from bot.strategies.zero_to_hero_strategy import ZeroToHeroStrategy

class TestZeroToHeroStrategy(unittest.TestCase):
    def setUp(self):
        self.mock_api = MagicMock()
        self.mock_loader = MagicMock()
        self.strategy = ZeroToHeroStrategy(self.mock_api, self.mock_loader, dry_run=True)

    @patch('backend.market_service.market_service.get_market_data')
    @patch('bot.core.trade_repo.trade_repo.save_trade')
    @patch('bot.core.trade_repo.trade_repo.update_sl_order_id')
    @patch('bot.core.trade_repo.trade_repo.get_open_trades')
    def test_execute_bullish_trigger(self, mock_get_open_trades, mock_update_sl, mock_save_trade, mock_get_market_data):
        # Setup mocks
        mock_get_open_trades.return_value = []
        mock_save_trade.return_value = "trade_123"
        
        # Mock market data with high ADX and bullish EMA cloud
        mock_get_market_data.return_value = {
            'nifty': 23000.0,
            'vix': 15.0,
            'analysis': {
                'adx': 50.0,
                'ema9': 22950.0,
                'ema21': 22900.0
            }
        }
        
        # Mock strategy gatekeeper and other internal checks
        self.strategy.gatekeeper.is_market_open = MagicMock(side_effect=[True, False]) # Run one iteration, then exit
        self.strategy._find_deep_otm_contract = MagicMock(return_value=("mock_token", "NIFTY26JUN23000CE", 10.0, 23000))
        self.strategy.order_manager.place_smart_limit = MagicMock(return_value="order_abc")
        self.strategy.order_manager.place_sl_order = MagicMock(return_value="sl_xyz")

        # Execute
        self.strategy.execute(expiry="26JUN2026")

        # Verify logic
        self.strategy.gatekeeper.is_market_open.assert_called()
        self.strategy._find_deep_otm_contract.assert_called_with("CE", 23000.0, "26JUN2026")
        
        # Verify order manager calls
        self.strategy.order_manager.place_smart_limit.assert_called_with(
            symbol="NIFTY26JUN23000CE", token="mock_token", qty=260, initial_price=10.0,
            transaction_type="BUY", strategy_name="ZERO_TO_HERO", mode="PAPER"
        )
        self.strategy.order_manager.place_sl_order.assert_called_with(
            "NIFTY26JUN23000CE", "mock_token", 260, 4.0, "CE"
        )

        mock_save_trade.assert_called_once()
        mock_update_sl.assert_called_once_with("trade_123", "sl_xyz")

    @patch('backend.market_service.market_service.get_market_data')
    @patch('bot.core.trade_repo.trade_repo.save_trade')
    @patch('bot.core.trade_repo.trade_repo.update_sl_order_id')
    @patch('bot.core.trade_repo.trade_repo.get_open_trades')
    @patch('bot.strategies.zero_to_hero_strategy.datetime.datetime')
    def test_execute_lotto_window_low_adx(self, mock_dt, mock_get_open_trades, mock_update_sl, mock_save_trade, mock_get_market_data):
        # Mock time: 14:45 PM on 26-JUN-2026
        mock_dt.now.return_value = real_datetime(2026, 6, 26, 14, 45)
        
        # Setup mocks
        mock_get_open_trades.return_value = []
        mock_save_trade.return_value = "trade_123"
        
        # Mock market data with low ADX (20.0) but EMA cloud alignment
        mock_get_market_data.return_value = {
            'nifty': 23000.0,
            'vix': 15.0,
            'analysis': {
                'adx': 20.0,
                'ema9': 22950.0,
                'ema21': 22900.0
            }
        }
        
        # Mock strategy gatekeeper and other internal checks
        self.strategy.gatekeeper.is_market_open = MagicMock(side_effect=[True, False]) # Run one iteration, then exit
        self.strategy._find_deep_otm_contract = MagicMock(return_value=("mock_token", "NIFTY26JUN23000CE", 10.0, 23000))
        self.strategy.order_manager.place_smart_limit = MagicMock(return_value="order_abc")
        self.strategy.order_manager.place_sl_order = MagicMock(return_value="sl_xyz")

        # Execute with expiry matching mocked date
        self.strategy.execute(expiry="26JUN2026")

        # Verify it bypassed ADX gate and called contract finder
        self.strategy._find_deep_otm_contract.assert_called_with("CE", 23000.0, "26JUN2026")
        self.strategy.order_manager.place_smart_limit.assert_called_once()

if __name__ == "__main__":
    unittest.main()
