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

        # Case 4: 11:00 AM, Friday, ADX 15 (Should be None/CASH because ADX < 18)
        mock_dt.now.return_value = real_datetime(2026, 2, 27, 11, 0)
        mock_market.return_value = {
            'nifty': 22000,
            'analysis': {'regime': 'SIDEWAYS', 'trend': 'BULLISH', 'adx': 15},
            'oi_data': {'bias': 'BULLISH', 'pcr': 1.2},
            'levels': {}
        }
        strat, risk = self.engine.analyze_and_select()
        print(f"11:00 AM, ADX 15 -> Expected: None, Got: {strat}")
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

    @patch('backend.market_service.market_service.get_market_data')
    @patch('bot.core.decision_engine.datetime.datetime')
    def test_consecutive_losses_and_adx_boost(self, mock_dt, mock_market):
        # Setup market mock with ADX = 28.0 at 12:00 PM (outside chop hours)
        mock_dt.now.return_value = real_datetime(2026, 2, 27, 12, 0)
        mock_market.return_value = {
            'nifty': 22000,
            'analysis': {'regime': 'TRENDING', 'trend': 'BULLISH', 'adx': 28},
            'oi_data': {'bias': 'BULLISH', 'pcr': 1.2},
            'levels': {}
        }
        
        # Configure starting capital to 150,000 (MEDIUM tier)
        # MEDIUM tier max_consecutive_losses = 3, max_trades_per_day = 8, min_adx_to_trade = 25
        self.engine.gatekeeper.get_starting_capital = MagicMock(return_value=150000.0)
        self.engine.gatekeeper.get_current_capital = MagicMock(return_value=150000.0)

        # 1. Verify consecutive loss circuit breaker triggers on RECENT losses
        # Case A: Oldest trades (lower IDs) are losses, recent trades (higher IDs) are wins.
        # This should NOT trigger the consecutive loss circuit breaker.
        trades_recent_wins = [
            {"id": 3, "status": "CLOSED", "pnl": 500.0},   # Recent win
            {"id": 2, "status": "CLOSED", "pnl": -200.0},  # Old loss
            {"id": 1, "status": "CLOSED", "pnl": -100.0},  # Old loss
        ]
        
        with patch('bot.core.trade_repo.trade_repo.get_today_trades', return_value=trades_recent_wins):
            # Should NOT trigger consecutive loss breaker, and should return a strategy
            strat, risk = self.engine.analyze_and_select()
            self.assertEqual(strat, "MOMENTUM")

        # Case B: Recent trades (higher IDs) are losses, oldest trades (lower IDs) are wins/not losses.
        # This SHOULD trigger the consecutive loss circuit breaker (3 losses in a row) and halt.
        trades_recent_losses = [
            {"id": 3, "status": "CLOSED", "pnl": -500.0},  # Recent loss
            {"id": 2, "status": "CLOSED", "pnl": -200.0},  # Recent loss
            {"id": 1, "status": "CLOSED", "pnl": -100.0},  # Recent loss
        ]
        
        with patch('bot.core.trade_repo.trade_repo.get_today_trades', return_value=trades_recent_losses):
            with patch('bot.core.kill_switch.activate_kill_switch') as mock_kill:
                strat, risk = self.engine.analyze_and_select()
                self.assertIsNone(strat)
                mock_kill.assert_called_once()

        # 2. Verify Session Stress ADX Boost
        # In MEDIUM tier (capital 150,000, max_consecutive_losses = 3), 2 recent losses triggers ADX boost (+5).
        # Normal min_adx_to_trade for MEDIUM is 25.0. With boost, it needs 30.0.
        # If we have ADX = 28.0, it should select MOMENTUM normally, but fail if boost is active.
        
        # Scenario A: 2 recent losses. Consecutive loss circuit breaker threshold is 3 for MEDIUM tier.
        # With session ADX boost removed to prevent delayed entries, ADX = 28.0 selects MOMENTUM.
        with patch('bot.core.trade_repo.trade_repo.get_today_trades', return_value=trades_recent_losses[:2]):
            strat, risk = self.engine.analyze_and_select()
            self.assertEqual(strat, "MOMENTUM")
            
        # Scenario B: 2 oldest losses, recent are wins. Boost is NOT active.
        # ADX = 28.0 (which is >= 20). Should select MOMENTUM.
        with patch('bot.core.trade_repo.trade_repo.get_today_trades', return_value=trades_recent_wins):
            strat, risk = self.engine.analyze_and_select()
            self.assertEqual(strat, "MOMENTUM")

if __name__ == '__main__':
    unittest.main()

