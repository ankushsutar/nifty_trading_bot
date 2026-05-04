import unittest
from unittest.mock import MagicMock
from bot.core.position_manager import LadderedTrailingManager
from bot.config.settings import Config

class TestLadderedTrailing(unittest.TestCase):
    def setUp(self):
        self.order_manager = MagicMock()
        self.data_fetcher = MagicMock()
        self.manager = LadderedTrailingManager(self.order_manager, self.data_fetcher)

    def test_stage_gates(self):
        # Entry Price 100, Qty 65 (1 lot)
        active_position = {
            'symbol': 'NIFTY-OPT',
            'token': '12345',
            'entry_price': 100,
            'qty': 65,
            'sl_price': 80,
            'ladder_stage': 0
        }

        # Case 1: PnL < 1500
        # PnL = (120 - 100) * 65 = 1300
        should_close, exit_type = self.manager.update_trailing_sl("TEST", active_position, 120)
        self.assertFalse(should_close)
        self.assertEqual(active_position['ladder_stage'], 0)
        self.assertEqual(active_position['sl_price'], 80)

        # Case 2: Stage 1 (PnL >= 1500)
        # PnL = (125 - 100) * 65 = 1625
        should_close, exit_type = self.manager.update_trailing_sl("TEST", active_position, 125)
        self.assertFalse(should_close)
        self.assertEqual(active_position['ladder_stage'], 1)
        self.assertEqual(active_position['sl_price'], 116) # Entry + 16

        # Case 3: Stage 2 (PnL >= 2600)
        # PnL = (140 - 100) * 65 = 2600
        should_close, exit_type = self.manager.update_trailing_sl("TEST", active_position, 140)
        self.assertFalse(should_close)
        self.assertEqual(active_position['ladder_stage'], 2)
        self.assertEqual(active_position['sl_price'], 130) # Entry + 30

        # Case 4: Stage 3 (PnL >= 3900)
        # PnL = (160 - 100) * 65 = 3900
        should_close, exit_type = self.manager.update_trailing_sl("TEST", active_position, 160)
        self.assertFalse(should_close)
        self.assertEqual(active_position['ladder_stage'], 3)

    def test_smart_exit(self):
        active_position = {
            'symbol': 'NIFTY-OPT',
            'token': '12345',
            'entry_price': 100,
            'qty': 65,
            'sl_price': 116,
            'ladder_stage': 1
        }
        
        # SL hit (Stage 1 reached)
        should_close, exit_type = self.manager.update_trailing_sl("TEST", active_position, 115)
        self.assertTrue(should_close)
        self.assertEqual(exit_type, "LIMIT")

if __name__ == '__main__':
    unittest.main()
