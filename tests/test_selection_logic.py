import datetime
from datetime import datetime as real_datetime
import unittest
from unittest.mock import MagicMock, patch
from bot.core.trade_repo import trade_repo
from bot.core.decision_engine import DecisionEngine

class TestSelection(unittest.TestCase):
    def setUp(self):
        self.api = MagicMock()
        self.loader = MagicMock()
        self.engine = DecisionEngine(self.api, self.loader, dry_run=True)
        # Mock Safety Checks to pass
        self.engine.gatekeeper.is_market_open = MagicMock(return_value=True)
        self.engine.gatekeeper.is_blackout_period = MagicMock(return_value=False)
        self.engine.gatekeeper.check_max_daily_loss = MagicMock(return_value=True)
        self.engine.gatekeeper.check_funds = MagicMock(return_value=True)
        self.engine.gatekeeper.get_current_capital = MagicMock(return_value=50000)
        self.engine.MAX_TRADES_PER_DAY = 10

    @patch('bot.core.trade_repo.trade_repo.get_today_trades', return_value=[])
    @patch('backend.market_service.market_service.get_market_data')
    @patch('bot.core.decision_engine.datetime.datetime')
    def test_logic(self, mock_dt, mock_market, mock_trades):
        # Case 1: 9:45 AM, Friday (weekday 4), ADX 35 (Should be MOMENTUM)
        mock_dt.now.return_value = real_datetime(2026, 2, 27, 9, 45)  # Friday
        mock_market.return_value = {
            'nifty': 22000,
            'analysis': {'regime': 'TRENDING', 'trend': 'BULLISH', 'adx': 35},
            'oi_data': {'bias': 'BULLISH', 'pcr': 1.2},
            'levels': {}
        }
        strat, risk = self.engine.analyze_and_select()
        print(f"9:45 AM, ADX 35 -> Expected: MOMENTUM, Got: {strat}")
        self.assertEqual(strat, "MOMENTUM")

        # Case 2: 11:00 AM, Friday, ADX 35 (Should be MOMENTUM)
        mock_dt.now.return_value = real_datetime(2026, 2, 27, 11, 0)
        mock_market.return_value = {
            'nifty': 22000,
            'analysis': {'regime': 'TRENDING', 'trend': 'BULLISH', 'adx': 35},
            'oi_data': {'bias': 'BULLISH', 'pcr': 1.2},
            'levels': {}
        }
        strat, risk = self.engine.analyze_and_select()
        print(f"11:00 AM, ADX 35 -> Expected: MOMENTUM, Got: {strat}")
        self.assertEqual(strat, "MOMENTUM")

        # Case 3: 11:00 AM, Friday, ADX 50 (Should be GAMMA_BLAST)
        mock_dt.now.return_value = real_datetime(2026, 2, 27, 11, 0)
        mock_market.return_value = {
            'nifty': 22000,
            'analysis': {'regime': 'TRENDING', 'trend': 'BULLISH', 'adx': 50, 'rsi': 80.0},
            'oi_data': {'bias': 'BULLISH', 'pcr': 1.2},
            'levels': {}
        }
        strat, risk = self.engine.analyze_and_select()
        print(f"11:00 AM, ADX 50 -> Expected: GAMMA_BLAST, Got: {strat}")
        self.assertEqual(strat, "GAMMA_BLAST")

        # Case 4: 11:00 AM, Friday, ADX 20 (Should be None/CASH because STRADDLE_SCALP is removed)
        mock_dt.now.return_value = real_datetime(2026, 2, 27, 11, 0)
        mock_market.return_value = {
            'nifty': 22000,
            'analysis': {'regime': 'SIDEWAYS', 'trend': 'BULLISH', 'adx': 20},
            'oi_data': {'bias': 'BULLISH', 'pcr': 1.2},
            'levels': {}
        }
        strat, risk = self.engine.analyze_and_select()
        print(f"11:00 AM, ADX 20 -> Expected: None, Got: {strat}")
        self.assertIsNone(strat)

    @patch('bot.core.trade_repo.trade_repo.get_today_trades', return_value=[])
    @patch('backend.market_service.market_service.get_market_data')
    @patch('bot.core.decision_engine.datetime.datetime')
    def test_tuesday_expiry_priority(self, mock_dt, mock_market, mock_trades):
        # Tuesday (weekday 1), 11:00 AM, ADX 20, trending -> Should force GAMMA_BLAST
        mock_dt.now.return_value = real_datetime(2026, 3, 10, 11, 0)  # Tuesday
        mock_market.return_value = {
            'nifty': 22000,
            'analysis': {'regime': 'TRENDING', 'trend': 'BULLISH', 'adx': 20},
            'oi_data': {'bias': 'BULLISH', 'pcr': 1.2},
            'levels': {}
        }
        strat, risk = self.engine.analyze_and_select()
        self.assertEqual(strat, "GAMMA_BLAST")



    @patch('bot.core.trade_repo.trade_repo.get_today_trades', return_value=[])
    @patch('backend.market_service.market_service.get_market_data')
    @patch('bot.core.decision_engine.datetime.datetime')
    def test_expiry_lotto_window_selection(self, mock_dt, mock_market, mock_trades):
        from bot.utils.expiry_calculator import get_next_weekly_expiry
        next_expiry = get_next_weekly_expiry()
        expiry_dt = real_datetime.strptime(next_expiry, "%d%b%Y")

        # Expiry day, 14:00 PM (After 13:30, before 14:30) -> Should return None (CASH)
        mock_dt.now.return_value = real_datetime(expiry_dt.year, expiry_dt.month, expiry_dt.day, 14, 0)
        mock_market.return_value = {
            'nifty': 22000,
            'vix': 15.0,
            'analysis': {'regime': 'TRENDING', 'trend': 'BULLISH', 'adx': 25},
            'oi_data': {'bias': 'BULLISH', 'pcr': 1.2},
            'levels': {}
        }
        strat, risk = self.engine.analyze_and_select()
        self.assertIsNone(strat)

        # Expiry day, 14:45 PM (During lotto window, ADX 20, volume_spike=True) -> Should select ZERO_TO_HERO
        mock_dt.now.return_value = real_datetime(expiry_dt.year, expiry_dt.month, expiry_dt.day, 14, 45)
        mock_market.return_value = {
            'nifty': 22000,
            'vix': 15.0,
            'analysis': {'regime': 'TRENDING', 'trend': 'BULLISH', 'adx': 20, 'volume_spike': True},
            'oi_data': {'bias': 'BULLISH', 'pcr': 1.2},
            'levels': {}
        }
        strat, risk = self.engine.analyze_and_select()
        self.assertEqual(strat, "ZERO_TO_HERO")

if __name__ == '__main__':
    unittest.main()
