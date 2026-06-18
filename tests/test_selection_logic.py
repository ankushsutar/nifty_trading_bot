import datetime
import unittest
from unittest.mock import MagicMock, patch
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
    @patch('bot.core.decision_engine.datetime')
    def test_logic(self, mock_dt, mock_market, mock_trades):
        # Configure mock datetime module
        mock_dt.time = datetime.time
        mock_dt.timedelta = datetime.timedelta
        
        # Case 1: 9:45 AM, ADX 35 (Should be ORB)
        mock_dt.datetime.now.return_value = datetime.datetime(2026, 2, 27, 9, 45)
        mock_market.return_value = {
            'nifty': 22000,
            'analysis': {'regime': 'TRENDING', 'trend': 'BULLISH', 'adx': 35},
            'oi_data': {'bias': 'BULLISH', 'pcr': 1.2},
            'levels': {}
        }
        strat, risk = self.engine.analyze_and_select()
        print(f"9:45 AM, ADX 35 -> Expected: MOMENTUM, Got: {strat}")
        self.assertEqual(strat, "MOMENTUM")

        # Case 2: 11:00 AM, ADX 35 (Should be MOMENTUM)
        mock_dt.datetime.now.return_value = datetime.datetime(2026, 2, 27, 11, 0)
        mock_market.return_value = {
            'nifty': 22000,
            'analysis': {'regime': 'TRENDING', 'trend': 'BULLISH', 'adx': 35},
            'oi_data': {'bias': 'BULLISH', 'pcr': 1.2},
            'levels': {}
        }
        strat, risk = self.engine.analyze_and_select()
        print(f"11:00 AM, ADX 35 -> Expected: MOMENTUM, Got: {strat}")
        self.assertEqual(strat, "MOMENTUM")

        # Case 3: 11:00 AM, ADX 50 (Should be GAMMA_BLAST)
        mock_dt.datetime.now.return_value = datetime.datetime(2026, 2, 27, 11, 0)
        mock_market.return_value = {
            'nifty': 22000,
            'analysis': {'regime': 'TRENDING', 'trend': 'BULLISH', 'adx': 50},
            'oi_data': {'bias': 'NEUTRAL', 'pcr': 1.0},
            'levels': {}
        }
        strat, risk = self.engine.analyze_and_select()
        print(f"11:00 AM, ADX 50 -> Expected: GAMMA_BLAST, Got: {strat}")
        self.assertEqual(strat, "GAMMA_BLAST")

        # Case 4: 11:00 AM, ADX 20 (Should be VWAP)
        mock_dt.datetime.now.return_value = datetime.datetime(2026, 2, 27, 11, 0)
        mock_market.return_value = {
            'nifty': 22000,
            'analysis': {'regime': 'SIDEWAYS', 'trend': 'BULLISH', 'adx': 20},
            'oi_data': {'bias': 'BULLISH', 'pcr': 1.2},
            'levels': {}
        }
        strat, risk = self.engine.analyze_and_select()
        print(f"11:00 AM, ADX 20 -> Expected: STRADDLE_SCALP, Got: {strat}")
        self.assertEqual(strat, "STRADDLE_SCALP")

if __name__ == '__main__':
    unittest.main()
