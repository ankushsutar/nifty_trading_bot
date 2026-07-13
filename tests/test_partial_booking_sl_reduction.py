import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.core.position_manager import LadderedTrailingManager
from bot.config.settings import Config

class TestPartialBookingSLReduction(unittest.TestCase):
    def setUp(self):
        self.mock_order_manager = MagicMock()
        self.mock_data_fetcher = MagicMock()
        self.manager = LadderedTrailingManager(self.mock_order_manager, self.mock_data_fetcher)
        
        # Default active position details
        self.active_position = {
            'id': 'trade_123',
            'symbol': 'NIFTY2662323900PE',
            'token': '12345',
            'leg': 'PE',
            'qty': 130, # 2 lots
            'entry_price': 100.0,
            'sl_price': 80.0,
            'sl_order_id': 'sl_order_999',
            'ladder_stage': 1.0,
            'initial_risk': 20.0,
            'atr': 20.0
        }
        
        # Reset mocks
        self.mock_order_manager.dry_run = False
        self.mock_order_manager.modify_sl_order.reset_mock()
        self.mock_order_manager.place_smart_limit.reset_mock()
        self.mock_data_fetcher.get_ltp.return_value = 150.0

    @patch('bot.core.position_manager.trade_repo')
    def test_partial_booking_success(self, mock_repo):
        """Test that the stop loss quantity is reduced first, then the partial limit order is placed successfully."""
        self.mock_order_manager.modify_sl_order.return_value = True
        self.mock_order_manager.place_smart_limit.return_value = 'partial_oid_111'
        
        # We need points_up >= threshold_2_0 (2.5 * atr = 50 pts)
        # Entry = 100, LTP = 155 (points_up = 55)
        ltp = 155.0
        
        should_close, exit_type = self.manager.update_trailing_sl(
            strategy_name="GAMMA_BLAST",
            active_position=self.active_position,
            ltp=ltp
        )
        
        # Assertions
        self.assertFalse(should_close)
        
        # Verify SL was modified to the reduced quantity of 65 first
        self.mock_order_manager.modify_sl_order.assert_any_call(
            'sl_order_999',
            self.active_position['sl_price'],
            'NIFTY2662323900PE',
            '12345',
            65 # total_qty (130) - qty_to_sell (65)
        )
        
        # Verify place_smart_limit was called for 65
        self.mock_order_manager.place_smart_limit.assert_called_once_with(
            'NIFTY2662323900PE',
            '12345',
            65,
            ltp,
            'SELL',
            strategy_name='GAMMA_BLAST'
        )
        
        # Verify trade repo was updated
        mock_repo.reduce_position.assert_called_once_with(
            'trade_123',
            65,
            ltp,
            (ltp - 100.0) * 65,
            'STAGE_2_PARTIAL'
        )
        
        # Verify position quantity was updated locally
        self.assertEqual(self.active_position['qty'], 65)

    @patch('bot.core.position_manager.trade_repo')
    def test_partial_booking_sl_modify_fail(self, mock_repo):
        """Test that if the SL order modification fails, we abort the partial booking entirely."""
        self.mock_order_manager.modify_sl_order.return_value = False
        
        ltp = 155.0
        should_close, exit_type = self.manager.update_trailing_sl(
            strategy_name="GAMMA_BLAST",
            active_position=self.active_position,
            ltp=ltp
        )
        
        self.assertFalse(should_close)
        
        # Verify modify_sl_order was called
        self.mock_order_manager.modify_sl_order.assert_called_once()
        
        # Verify place_smart_limit was NOT called
        self.mock_order_manager.place_smart_limit.assert_not_called()
        
        # Verify trade repo was NOT updated
        mock_repo.reduce_position.assert_not_called()
        
        # Verify position quantity remains 130
        self.assertEqual(self.active_position['qty'], 130)

    @patch('bot.core.position_manager.trade_repo')
    def test_partial_booking_limit_fail_rollback(self, mock_repo):
        """Test that if the SL order modification succeeds but placing the limit order fails, we restore/rollback the SL quantity."""
        self.mock_order_manager.modify_sl_order.return_value = True
        self.mock_order_manager.place_smart_limit.return_value = None # Failure
        
        ltp = 155.0
        should_close, exit_type = self.manager.update_trailing_sl(
            strategy_name="GAMMA_BLAST",
            active_position=self.active_position,
            ltp=ltp
        )
        
        self.assertFalse(should_close)
        
        # Verify modify_sl_order was called first to reduce quantity to 65
        self.mock_order_manager.modify_sl_order.assert_any_call(
            'sl_order_999',
            self.active_position['sl_price'],
            'NIFTY2662323900PE',
            '12345',
            65
        )
        
        # Verify modify_sl_order was called again to restore/rollback quantity to 130
        self.mock_order_manager.modify_sl_order.assert_any_call(
            'sl_order_999',
            self.active_position['sl_price'],
            'NIFTY2662323900PE',
            '12345',
            130
        )
        
        self.assertEqual(self.mock_order_manager.modify_sl_order.call_count, 2)
        
        # Verify trade repo was NOT updated
        mock_repo.reduce_position.assert_not_called()
        
        # Verify position quantity remains 130
        self.assertEqual(self.active_position['qty'], 130)

    @patch('bot.core.position_manager.trade_repo')
    def test_partial_booking_dry_run(self, mock_repo):
        """Test that in dry run mode, we do not call modify_sl_order, and successfully reduce the position in the local state."""
        self.mock_order_manager.dry_run = True
        self.mock_order_manager.place_smart_limit.return_value = 'dry_run_oid'
        
        ltp = 155.0
        should_close, exit_type = self.manager.update_trailing_sl(
            strategy_name="GAMMA_BLAST",
            active_position=self.active_position,
            ltp=ltp
        )
        
        self.assertFalse(should_close)
        
        # Verify modify_sl_order was NOT called
        self.mock_order_manager.modify_sl_order.assert_not_called()
        
        # Verify place_smart_limit was called
        self.mock_order_manager.place_smart_limit.assert_called_once()
        
        # Verify trade repo reduced the position
        mock_repo.reduce_position.assert_called_once()
        
        # Verify position quantity was updated locally
        self.assertEqual(self.active_position['qty'], 65)

    @patch('bot.core.position_manager.trade_repo')
    def test_roi_stop_gate_success_normal_day(self, mock_repo):
        """Test that Stage 3.5 ROI Stop Gate triggers and scales out 50% on a normal day (Friday)."""
        import datetime as dt_module
        self.manager._get_current_time = MagicMock(return_value=dt_module.datetime(2026, 3, 6, 11, 0)) # Friday
        self.mock_order_manager.modify_sl_order.return_value = True
        self.mock_order_manager.place_smart_limit.return_value = 'roi_oid_111'
        
        # Initialize stage to 3.0, atr to 30.0, and qty to 260 (4 lots of 65)
        self.active_position['ladder_stage'] = 3.0
        self.active_position['sl_price'] = 110.0
        self.active_position['atr'] = 30.0
        self.active_position['qty'] = 260
        
        # Entry = 100.0, LTP = 200.0 (ROI = 100%)
        ltp = 200.0
        
        should_close, exit_type = self.manager.update_trailing_sl(
            strategy_name="GAMMA_BLAST",
            active_position=self.active_position,
            ltp=ltp
        )
        
        self.assertFalse(should_close)
        
        # Verify SL was updated to entry_price * 1.5 = 150.0
        self.assertEqual(self.active_position['sl_price'], 150.0)
        self.assertEqual(self.active_position['ladder_stage'], 3.5)
        
        # For 50% scale-out on 4 lots (260 qty), we sell 2 lots (130 qty), leaving 130 qty
        self.mock_order_manager.modify_sl_order.assert_any_call(
            'sl_order_999',
            150.0,
            'NIFTY2662323900PE',
            '12345',
            130
        )
        
        # Verify place_smart_limit was called for 130 qty
        self.mock_order_manager.place_smart_limit.assert_called_once_with(
            'NIFTY2662323900PE',
            '12345',
            130,
            ltp,
            'SELL',
            strategy_name='GAMMA_BLAST'
        )

    @patch('bot.core.position_manager.trade_repo')
    def test_roi_stop_gate_expiry_day(self, mock_repo):
        """Test that Stage 3.5 ROI Stop Gate triggers and scales out only 25% on an Expiry Day (Tuesday)."""
        import datetime as dt_module
        self.manager._get_current_time = MagicMock(return_value=dt_module.datetime(2026, 3, 10, 11, 0)) # Tuesday (Expiry)
        self.mock_order_manager.modify_sl_order.return_value = True
        self.mock_order_manager.place_smart_limit.return_value = 'roi_oid_222'
        
        # Initialize stage to 3.0, atr to 30.0, and qty to 260 (4 lots of 65)
        self.active_position['ladder_stage'] = 3.0
        self.active_position['sl_price'] = 110.0
        self.active_position['atr'] = 30.0
        self.active_position['qty'] = 260
        
        # Entry = 100.0, LTP = 200.0 (ROI = 100%)
        ltp = 200.0
        
        should_close, exit_type = self.manager.update_trailing_sl(
            strategy_name="GAMMA_BLAST",
            active_position=self.active_position,
            ltp=ltp
        )
        
        self.assertFalse(should_close)
        
        # Verify SL was updated to entry_price * 1.5 = 150.0
        self.assertEqual(self.active_position['sl_price'], 150.0)
        self.assertEqual(self.active_position['ladder_stage'], 3.5)
        
        # For 25% scale-out on 4 lots (260 qty), we sell 1 lot (65 qty), leaving 195 qty
        self.mock_order_manager.modify_sl_order.assert_any_call(
            'sl_order_999',
            150.0,
            'NIFTY2662323900PE',
            '12345',
            195
        )
        
        # Verify place_smart_limit was called for 65 qty
        self.mock_order_manager.place_smart_limit.assert_called_once_with(
            'NIFTY2662323900PE',
            '12345',
            65,
            ltp,
            'SELL',
            strategy_name='GAMMA_BLAST'
        )

if __name__ == '__main__':
    unittest.main()
