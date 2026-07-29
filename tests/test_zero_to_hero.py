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
            symbol="NIFTY26JUN23000CE", token="mock_token", qty=65, initial_price=10.0,
            transaction_type="BUY", strategy_name="ZERO_TO_HERO", mode="PAPER"
        )
        self.strategy.order_manager.place_sl_order.assert_called_with(
            "NIFTY26JUN23000CE", "mock_token", 65, 6.5, "CE"
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

    @patch('backend.market_service.market_service.get_market_data')
    @patch('bot.core.trade_repo.trade_repo.save_trade')
    @patch('bot.core.trade_repo.trade_repo.update_sl_order_id')
    @patch('bot.core.trade_repo.trade_repo.get_open_trades')
    def test_dynamic_stop_loss_low_and_high_vix(self, mock_get_open_trades, mock_update_sl, mock_save_trade, mock_get_market_data):
        # 1. Low VIX (< 12.0) -> 30% SL
        mock_get_open_trades.return_value = []
        mock_save_trade.return_value = "trade_123"
        mock_get_market_data.return_value = {
            'nifty': 23000.0,
            'vix': 10.0,
            'analysis': {'adx': 50.0, 'ema9': 22950.0, 'ema21': 22900.0}
        }
        self.strategy.gatekeeper.is_market_open = MagicMock(side_effect=[True, False])
        self.strategy._find_deep_otm_contract = MagicMock(return_value=("mock_token", "NIFTY26JUN23000CE", 10.0, 23000))
        self.strategy.order_manager.place_smart_limit = MagicMock(return_value="order_abc")
        self.strategy.order_manager.place_sl_order = MagicMock(return_value="sl_xyz")

        self.strategy.execute(expiry="26JUN2026")
        
        # Verify 30% SL is calculated (initial_price 10.0 -> SL 7.0)
        self.strategy.order_manager.place_sl_order.assert_called_with(
            "NIFTY26JUN23000CE", "mock_token", 65, 7.0, "CE"
        )

        # 2. High VIX (> 17.0) -> 45% SL
        self.strategy.active_position = None
        self.strategy.gatekeeper.is_market_open = MagicMock(side_effect=[True, False])
        self.strategy.order_manager.place_sl_order.reset_mock()
        mock_get_market_data.return_value['vix'] = 20.0
        self.strategy.execute(expiry="26JUN2026")
        # Verify 45% SL is calculated (initial_price 10.0 -> SL 5.5)
        self.strategy.order_manager.place_sl_order.assert_called_with(
            "NIFTY26JUN23000CE", "mock_token", 65, 5.5, "CE"
        )

    def test_get_expiry_date_from_symbol(self):
        import datetime
        self.assertEqual(
            self.strategy._get_expiry_date_from_symbol("NIFTY26JUN23000CE").strftime("%d%b").upper(),
            "26JUN"
        )
        self.assertIsNone(self.strategy._get_expiry_date_from_symbol("INVALID"))

    @patch('backend.market_service.market_service.get_market_data')
    @patch('bot.core.trade_repo.trade_repo.close_trade')
    @patch('bot.core.trade_repo.trade_repo.update_exit_order_id')
    @patch('bot.strategies.zero_to_hero_strategy.notifier.notify_trade_exit')
    def test_double_exit_protection(self, mock_notify, mock_update_exit, mock_close_trade, mock_get_market_data):
        # Setup active position
        self.strategy.active_position = {
            'id': "trade_123",
            'symbol': "NIFTY26JUN23000CE",
            'token': "mock_token",
            'qty': 65,
            'entry_price': 10.0,
            'sl_oid': "sl_xyz",
            'partially_booked': False
        }
        
        self.strategy.dry_run = False
        # Mock cancel_order to fail (False) indicating it was already filled
        self.strategy._is_contract_expired = MagicMock(return_value=False)
        self.strategy.order_manager.cancel_order = MagicMock(return_value=False)
        self.strategy.order_manager.get_order_status = MagicMock(return_value={
            'status': 'FILLED',
            'price': 6.5
        })
        self.strategy.order_manager.place_market = MagicMock()
        
        # Stagnant timer threshold reached
        mock_get_market_data.return_value = {
            'nifty': 23000.0,
            'vix': 15.0,
            'analysis': {'adx': 50.0, 'ema9': 22950.0, 'ema21': 22900.0}
        }
        
        import time
        with patch('time.time', return_value=time.time() + 3600.0):
            self.strategy.running = True
            def stop_running(*args, **kwargs):
                self.strategy.running = False
                return 5.0
            self.strategy.data_fetcher.get_ltp = MagicMock(side_effect=stop_running)
            
            self.strategy._monitor_wildcard()
            
            self.strategy.order_manager.cancel_order.assert_called_with("sl_xyz", variety="STOPLOSS")
            self.strategy.order_manager.get_order_status.assert_called_with("sl_xyz")
            self.strategy.order_manager.place_market.assert_not_called()
            mock_close_trade.assert_called_with(trade_id="trade_123", exit_price=6.5, pnl=-227.5, exit_reason="SL_HIT")

if __name__ == "__main__":
    unittest.main()
