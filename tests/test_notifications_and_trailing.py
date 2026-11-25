import unittest
from unittest.mock import MagicMock, patch
import time
from bot.utils.notifier import TelegramNotifier
from bot.core.position_manager import LadderedTrailingManager
from bot.config.settings import Config


class TestTelegramNotifications(unittest.TestCase):
    def setUp(self):
        self.notifier = TelegramNotifier()
        self.notifier.enabled = True
        self.notifier.send_message = MagicMock()

    def test_gamma_blast_entry_notification(self):
        self.notifier.notify_gamma_blast_entry(
            symbol="NIFTY26NOV24350CE",
            leg="CE",
            qty=65,
            price=120.0,
            sl=90.0,
            sl_points=30.0,
            adx=45.5,
            atr=25.0
        )
        self.notifier.send_message.assert_called_once()
        msg = self.notifier.send_message.call_args[0][0]
        self.assertIn("GAMMA BLAST — NEW TRADE", msg)
        self.assertIn("NIFTY26NOV24350CE", msg)
        self.assertIn("BUY 65", msg)
        self.assertIn("₹120.00", msg)
        self.assertIn("₹90.00", msg)
        self.assertIn("Max Risk: ₹1,950", msg)

    def test_gamma_blast_exit_notification_profit(self):
        self.notifier.notify_gamma_blast_exit(
            symbol="NIFTY26NOV24350CE",
            pnl=3900.0,
            exit_price=180.0,
            entry_price=120.0,
            reason="LADDERED_SL_MARKET",
            qty=65
        )
        self.notifier.send_message.assert_called_once()
        msg = self.notifier.send_message.call_args[0][0]
        self.assertIn("GAMMA BLAST — PROFIT", msg)
        self.assertIn("+₹3,900.00", msg)
        self.assertIn("+50.0%", msg)
        self.assertIn("Trailing Stop-Loss Hit", msg)

    def test_zero_to_hero_entry_notification(self):
        self.notifier.notify_zero_to_hero_entry(
            symbol="NIFTY26NOV24500CE",
            leg="CE",
            qty=260,
            prem=12.5,
            sl_init=3.75,
            target_strike=24500,
            expiry="26NOV2026"
        )
        self.notifier.send_message.assert_called_once()
        msg = self.notifier.send_message.call_args[0][0]
        self.assertIn("ZERO-TO-HERO — NEW TRADE", msg)
        self.assertIn("NIFTY26NOV24500CE", msg)
        self.assertIn("₹12.50", msg)
        self.assertIn("BUY 260", msg)
        self.assertIn("Book 50% at 3x", msg)

    def test_zero_to_hero_scale_out_notification(self):
        self.notifier.notify_zero_to_hero_scale_out(
            symbol="NIFTY26NOV24500CE",
            sell_qty=130,
            exit_price=37.5,
            entry_price=12.5,
            booked_pnl=3250.0,
            remaining_qty=130
        )
        self.notifier.send_message.assert_called_once()
        msg = self.notifier.send_message.call_args[0][0]
        self.assertIn("ZERO-TO-HERO — HALF PROFIT BOOKED (3x)", msg)
        self.assertIn("SOLD <b>130</b>", msg)
        self.assertIn("+200%", msg)
        self.assertIn("+₹3,250.00", msg)
        self.assertIn("risk-free", msg)

    def test_zero_to_hero_milestone_notification(self):
        self.notifier.notify_zero_to_hero_milestone(
            symbol="NIFTY26NOV24500CE",
            ltp=25.0,
            entry_price=12.5,
            milestone_desc="2X DOUBLER REACHED (+100% GAIN)"
        )
        self.notifier.send_message.assert_called_once()
        msg = self.notifier.send_message.call_args[0][0]
        self.assertIn("PRICE DOUBLED (2x)", msg)
        self.assertIn("+100%", msg)

    def test_trailing_sl_modify_notification_floor_locked(self):
        self.notifier.notify_trailing_sl_modify(
            strategy="GAMMA_BLAST",
            symbol="NIFTY26NOV24350CE",
            old_sl=90.0,
            new_sl=135.0,
            ltp=170.0,
            stage_name="Adaptive Buffer",
            stage=2,
            entry_price=120.0,
            qty=65,
            extra_note="Secured 1 lot! Remaining trailing."
        )
        self.notifier.send_message.assert_called_once()
        msg = self.notifier.send_message.call_args[0][0]
        self.assertIn("PROFIT LOCKED — SL MOVED UP (GAMMA BLAST)", msg)
        self.assertIn("₹90.00 ➔ <b>₹135.00</b>", msg)
        self.assertIn("Min Guaranteed Profit: <b>+₹975.00</b>", msg)
        self.assertIn("Secured 1 lot", msg)


class TestLadderedTrailingNotifications(unittest.TestCase):
    def setUp(self):
        Config.ACTIVE_SYMBOL = "NIFTY"
        self.mock_order_mgr = MagicMock()
        self.mock_order_mgr.dry_run = False
        self.mock_fetcher = MagicMock()
        self.manager = LadderedTrailingManager(self.mock_order_mgr, self.mock_fetcher)
        self.position = {
            'id': 'test_trade_1',
            'symbol': 'NIFTY26NOV24350CE',
            'token': '999123',
            'qty': 130, # 2 lots
            'entry_price': 100.0,
            'sl_price': 80.0,
            'sl_order_id': 'sl_order_001',
            'ladder_stage': 0.0,
            'initial_risk': 20.0,
            'atr': 20.0
        }

    @patch('bot.utils.notifier.notifier.notify_trailing_sl_modify')
    @patch('bot.core.trade_repo.trade_repo.update_sl')
    def test_stage_0_5_triggers_trailing_notification(self, mock_db_sl, mock_notify):
        # threshold_0_5 = 1.0 * atr = 20 pts. LTP = 122 -> points_up = 22
        should_close, exit_type = self.manager.update_trailing_sl("GAMMA_BLAST", self.position, ltp=122.0)
        self.assertFalse(should_close)
        mock_notify.assert_called_once()
        kwargs = mock_notify.call_args[1]
        self.assertEqual(kwargs['strategy'], "GAMMA_BLAST")
        self.assertEqual(kwargs['stage'], 0.5)
        self.assertIn("Breakeven Shield", kwargs['stage_name'])
        self.assertEqual(kwargs['new_sl'], 102.0)

    @patch('bot.utils.notifier.notifier.notify_trailing_sl_modify')
    @patch('bot.core.trade_repo.trade_repo.reduce_position')
    @patch('bot.core.trade_repo.trade_repo.update_sl')
    def test_stage_2_partial_booking_triggers_trailing_notification(self, mock_db_sl, mock_reduce, mock_notify):
        # Position has 130 units (2 lots). Threshold 2.0 = 2.5 * atr = 50 pts.
        # Set ladder_stage = 1 so it triggers Stage 2
        self.position['ladder_stage'] = 1
        self.mock_fetcher.get_ltp.return_value = 155.0
        self.mock_order_mgr.modify_sl_order.return_value = True
        self.mock_order_mgr.place_smart_limit.return_value = 'oid_part_123'
        
        should_close, exit_type = self.manager.update_trailing_sl("GAMMA_BLAST", self.position, ltp=155.0)
        self.assertFalse(should_close)
        mock_notify.assert_called_once()
        kwargs = mock_notify.call_args[1]
        self.assertEqual(kwargs['stage'], 2)
        self.assertIn("Partial Booking", kwargs['stage_name'])
        self.assertIn("Secured 1 lot", kwargs['extra_note'])


if __name__ == "__main__":
    unittest.main()
