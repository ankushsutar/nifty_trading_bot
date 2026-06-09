import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config.settings import Config
from bot.config.instruments import get_instrument
from bot.utils.expiry_calculator import get_next_weekly_expiry
from bot.utils.greeks import select_strike_by_delta
from bot.core.levels_provider import LevelsProvider
from bot.core.oi_analyzer import OIAnalyzer
from bot.core.broker_adapter import SPOT_TOKEN_MAP, REVERSE_SPOT_TOKEN_MAP

class TestMultiIndexRotation(unittest.TestCase):
    def setUp(self):
        self.original_active_symbol = Config.ACTIVE_SYMBOL

    def tearDown(self):
        Config.ACTIVE_SYMBOL = self.original_active_symbol

    def test_instrument_config_resolution(self):
        """Test instrument configurations for supported indices."""
        # 1. NIFTY
        nifty = get_instrument("NIFTY")
        self.assertEqual(nifty.name, "NIFTY")
        self.assertEqual(nifty.strike_step, 50)
        self.assertEqual(nifty.analysis_token, "99926000")
        
        # 2. BANKNIFTY
        banknifty = get_instrument("BANKNIFTY")
        self.assertEqual(banknifty.name, "BANKNIFTY")
        self.assertEqual(banknifty.strike_step, 100)
        self.assertEqual(banknifty.analysis_token, "99926009")

        # 3. FINNIFTY
        finnifty = get_instrument("FINNIFTY")
        self.assertEqual(finnifty.name, "FINNIFTY")
        self.assertEqual(finnifty.strike_step, 50)
        self.assertEqual(finnifty.analysis_token, "99926037")

    def test_expiry_calculator_rotation(self):
        """Test expiry calculator derives the correct expiry day for indices."""
        # Mocking today as a Monday
        import datetime
        monday = datetime.date(2026, 6, 8) # June 8, 2026 is Monday
        
        with patch('datetime.date') as mock_date:
            mock_date.today.return_value = monday
            mock_date.side_effect = lambda *args, **kwargs: datetime.date(*args, **kwargs)
            
            # FINNIFTY Expiry should be next Tuesday (June 9, 2026)
            Config.ACTIVE_SYMBOL = "FINNIFTY"
            fin_expiry = get_next_weekly_expiry()
            self.assertEqual(fin_expiry, "09JUN2026")
            
            # BANKNIFTY Expiry should be next Wednesday (June 10, 2026)
            Config.ACTIVE_SYMBOL = "BANKNIFTY"
            bank_expiry = get_next_weekly_expiry()
            self.assertEqual(bank_expiry, "10JUN2026")
            
            # NIFTY Expiry should be Tuesday (Nifty Expiry on Tuesday in latest settings)
            Config.ACTIVE_SYMBOL = "NIFTY"
            nifty_expiry = get_next_weekly_expiry()
            self.assertEqual(nifty_expiry, "09JUN2026")

    def test_greeks_strike_selection(self):
        """Test delta-based strike selection respects unique index strike steps."""
        mock_loader = MagicMock()
        
        def mock_get_option_bucket(symbol_name, expiry, atm_strike, range_points):
            step = 50 if symbol_name == "NIFTY" else 100
            bucket = {}
            for i in range(-5, 6):
                strike = atm_strike + (i * step)
                bucket[str(strike)] = {
                    "type": "CE",
                    "strike": strike,
                    "token": f"TOK_{strike}",
                    "symbol": f"{symbol_name}_{strike}_CE"
                }
            return bucket

        mock_loader.get_option_bucket.side_effect = mock_get_option_bucket
        
        # Test NIFTY (strike step 50)
        # Spot: 22020. VIX: 15.0. Leg: CE. Target Delta: 0.3.
        # Strike step is 50, so expected strike should be aligned with 50.
        strike_nifty, _, _ = select_strike_by_delta(
            mock_loader, 22020.0, "09JUN2026", 15.0, "CE", 0.30, "NIFTY"
        )
        self.assertIsNotNone(strike_nifty)
        self.assertEqual(strike_nifty % 50, 0)
        
        # Test BANKNIFTY (strike step 100)
        # Spot: 47520. VIX: 15.0. Leg: CE. Target Delta: 0.3.
        # Strike step is 100, so expected strike should be aligned with 100.
        strike_bank, _, _ = select_strike_by_delta(
            mock_loader, 47520.0, "10JUN2026", 15.0, "CE", 0.30, "BANKNIFTY"
        )
        self.assertIsNotNone(strike_bank)
        self.assertEqual(strike_bank % 100, 0)

    def test_namespaced_levels_and_oi_cache(self):
        """Test levels and OI cache file paths are properly namespaced."""
        # 1. Levels Provider
        mock_api = MagicMock()
        lp = LevelsProvider(mock_api)
        
        Config.ACTIVE_SYMBOL = "FINNIFTY"
        self.assertIn("finnifty", lp.levels_file_path)
        
        Config.ACTIVE_SYMBOL = "BANKNIFTY"
        self.assertIn("banknifty", lp.levels_file_path)
        
        # 2. OI Analyzer
        mock_loader = MagicMock()
        oi = OIAnalyzer(mock_api, mock_loader)
        
        Config.ACTIVE_SYMBOL = "FINNIFTY"
        self.assertIn("finnifty", oi.snapshot_file)
        self.assertIn("finnifty", oi.history_file)
        
        Config.ACTIVE_SYMBOL = "BANKNIFTY"
        self.assertIn("banknifty", oi.snapshot_file)
        self.assertIn("banknifty", oi.history_file)

    def test_broker_adapter_token_mappings(self):
        """Test broker adapter maps spot tokens accurately between Angel and Zerodha Kite."""
        # Check mapping of Nifty Spot
        self.assertEqual(SPOT_TOKEN_MAP["99926000"]["kite_token"], 256265)
        self.assertEqual(SPOT_TOKEN_MAP["99926000"]["symbol"], "NIFTY 50")
        
        # Check mapping of BankNifty Spot
        self.assertEqual(SPOT_TOKEN_MAP["99926009"]["kite_token"], 260105)
        self.assertEqual(SPOT_TOKEN_MAP["99926009"]["symbol"], "NIFTY BANK")

        # Check mapping of FinNifty Spot
        self.assertEqual(SPOT_TOKEN_MAP["99926037"]["kite_token"], 257801)
        self.assertEqual(SPOT_TOKEN_MAP["99926037"]["symbol"], "NIFTY FIN SERVICE")

        # Reverse maps
        self.assertEqual(REVERSE_SPOT_TOKEN_MAP["256265"], "99926000")
        self.assertEqual(REVERSE_SPOT_TOKEN_MAP["260105"], "99926009")
        self.assertEqual(REVERSE_SPOT_TOKEN_MAP["257801"], "99926037")

if __name__ == "__main__":
    unittest.main()
