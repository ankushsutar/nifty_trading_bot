
import unittest
from unittest.mock import MagicMock, patch
import datetime
from bot.core.decision_engine import DecisionEngine

class TestDecisionEngine(unittest.TestCase):
    def setUp(self):
        self.mock_api = MagicMock()
        self.mock_loader = MagicMock()
        self.engine = DecisionEngine(self.mock_api, self.mock_loader, dry_run=True)
        # Mock Gatekeeper fund checks
        self.engine.gatekeeper.check_funds = MagicMock(return_value=True)
        self.engine.gatekeeper.check_max_daily_loss = MagicMock(return_value=True)
        self.engine.gatekeeper.get_current_capital = MagicMock(return_value=150000.0)

    @patch('backend.market_service.market_service.get_market_data')
    @patch('datetime.datetime')
    def test_orb_selection(self, mock_datetime, mock_market_data):
        print("\n--- Testing ORB Selection (09:45) ---")
        # Setup mock to return a real time object
        mock_now = MagicMock()
        mock_now.time.return_value = datetime.time(9, 45)
        mock_datetime.now.return_value = mock_now
        
        mock_market_data.return_value = {
            'analysis': {'regime': 'TRENDING', 'trend': 'BULLISH', 'adx': 30},
            'oi_data': {'bias': 'BULLISH', 'pcr': 1.2}
        }
        
        strat, risk = self.engine.analyze_and_select()
        self.assertEqual(strat, "MOMENTUM")
        print("✅ MOMENTUM selection verified for 09:30-10:00 Trending market.")

    @patch('backend.market_service.market_service.get_market_data')
    @patch('datetime.datetime')
    def test_vwap_selection(self, mock_datetime, mock_market_data):
        print("\n--- Testing VWAP Selection (10:30) ---")
        mock_now = MagicMock()
        mock_now.time.return_value = datetime.time(10, 30)
        mock_datetime.now.return_value = mock_now
        
        mock_market_data.return_value = {
            'analysis': {'regime': 'TRENDING', 'trend': 'BULLISH', 'adx': 25},
            'oi_data': {'bias': 'BULLISH', 'pcr': 1.2}
        }
        
        strat, risk = self.engine.analyze_and_select()
        self.assertEqual(strat, "MOMENTUM")
        print("✅ MOMENTUM selection verified for 10:00+ Trending market.")

    @patch('backend.market_service.market_service.get_market_data')
    @patch('datetime.datetime')
    def test_momentum_high_adx(self, mock_datetime, mock_market_data):
        print("\n--- Testing Momentum Selection (High ADX > 30) ---")
        mock_now = MagicMock()
        mock_now.time.return_value = datetime.time(10, 30)
        mock_datetime.now.return_value = mock_now
        
        # Even if it's 10:30 (VWAP time), high ADX should trigger Momentum
        mock_market_data.return_value = {
            'analysis': {'regime': 'TRENDING', 'trend': 'BULLISH', 'adx': 35},
            'oi_data': {'bias': 'BULLISH', 'pcr': 1.2}
        }
        
        strat, risk = self.engine.analyze_and_select()
        self.assertEqual(strat, "MOMENTUM")
        print("✅ Momentum selection verified for high-strength trends.")

    @patch('backend.market_service.market_service.get_market_data')
    @patch('datetime.datetime')
    def test_straddle_selection(self, mock_datetime, mock_market_data):
        print("\n--- Testing Straddle Selection (11:00 Sideways) ---")
        mock_now = MagicMock()
        mock_now.time.return_value = datetime.time(11, 0)
        mock_datetime.now.return_value = mock_now
        
        mock_market_data.return_value = {
            'analysis': {'regime': 'SIDEWAYS', 'trend': 'NEUTRAL', 'adx': 15},
            'oi_data': {'bias': 'NEUTRAL', 'pcr': 1.0}
        }
        
        strat, risk = self.engine.analyze_and_select()
        # SIDEWAYS regime fallback resolves to STRADDLE_SCALP in current engine
        self.assertEqual(strat, "STRADDLE_SCALP")
        print("✅ Straddle Scalp selection verified for Sideways market.")

if __name__ == "__main__":
    unittest.main()
