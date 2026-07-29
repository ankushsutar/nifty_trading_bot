import unittest
from unittest.mock import MagicMock, patch

# Import trade_repo early so pymongo resolves its imports with the un-patched global datetime module
from bot.core.trade_repo import trade_repo

import pandas as pd
# pyrefly: ignore [missing-import]
import numpy as np
import datetime
from datetime import datetime as real_datetime

from bot.core.regime_classifier import RegimeClassifier
from bot.core.decision_engine import DecisionEngine
from bot.core.safety_checks import SafetyGatekeeper


class TestBBWSqueezeAndVixScaling(unittest.TestCase):
    
    def test_calculate_bbw(self):
        classifier = RegimeClassifier()
        # Create a dataframe with constant prices
        df = pd.DataFrame({
            "close": [100.0] * 30,
            "high": [100.0] * 30,
            "low": [100.0] * 30,
            "volume": [100] * 30
        })
        # If standard deviation is 0, BBW should be 0.0
        bbw = classifier._calculate_bbw(df)
        self.assertEqual(bbw.iloc[-1], 0.0)
        
        # Create a dataframe with changing prices to get non-zero standard deviation
        df_varying = pd.DataFrame({
            "close": list(range(100, 130)),
            "high": list(range(100, 130)),
            "low": list(range(100, 130)),
            "volume": [100] * 30
        })
        bbw_varying = classifier._calculate_bbw(df_varying)
        self.assertTrue(bbw_varying.iloc[-1] > 0.0)

    def test_regime_classifier_squeeze_and_expansion(self):
        classifier = RegimeClassifier()
        
        # Build mock dataframe: 120 candles total
        # First 100 candles: volatile, wide bands
        # Next 15 candles: extremely tight (squeeze)
        # Last 5 candles: expanding bands
        
        closes = []
        for i in range(100):
            closes.append(100.0 + (i % 2) * 10.0) # wide oscillation
        for _ in range(15):
            closes.append(100.0) # tight squeeze
        for i in range(5):
            closes.append(100.0 + i * 5.0) # expansion
            
        df = pd.DataFrame({
            "close": closes,
            "high": closes,
            "low": closes,
            "volume": [1000] * len(closes)
        })
        
        # Classify should calculate BBW and find squeeze / expansion
        res = classifier.classify(df)
        
        # Verify the squeeze expansion is detected
        self.assertIn("is_squeeze", res)
        self.assertIn("is_squeeze_expansion", res)
        # Since we are expanding in the last 5 bars after a long squeeze, is_squeeze_expansion should be True
        self.assertTrue(res["is_squeeze_expansion"])

    @patch('bot.core.trade_repo.trade_repo.get_today_trades', return_value=[])
    @patch('backend.market_service.market_service.get_market_data')
    @patch('bot.core.decision_engine.datetime.datetime')
    def test_decision_engine_bbw_squeeze_gate(self, mock_dt, mock_market, mock_trades):
        api = MagicMock()
        loader = MagicMock()
        engine = DecisionEngine(api, loader, dry_run=True)
        
        # Mock Safety checks
        engine.gatekeeper.is_market_open = MagicMock(return_value=True)
        engine.gatekeeper.is_blackout_period = MagicMock(return_value=False)
        engine.gatekeeper.check_max_daily_loss = MagicMock(return_value=True)
        engine.gatekeeper.check_funds = MagicMock(return_value=True)
        engine.gatekeeper.get_current_capital = MagicMock(return_value=50000)
        
        mock_dt.now.return_value = real_datetime(2026, 2, 27, 11, 0)
        
        # CASE 1: Trending conditions, no BBW Squeeze expansion, and BBW too low for override -> Should block entry
        mock_market.return_value = {
            'nifty': 22000,
            'vix': 15.0,
            'analysis': {
                'regime': 'TRENDING', 
                'trend': 'BULLISH', 
                'adx': 35,
                'is_squeeze_expansion': False, # blocked
                'bbw': 0.003  # Below min_bbw_to_trade (0.005) -> no override
            },
            'oi_data': {'bias': 'BULLISH', 'pcr': 1.2},
            'levels': {}
        }
        strat, risk = engine.analyze_and_select()
        self.assertIsNone(strat)
        
        # CASE 2: Trending conditions with BBW Squeeze expansion -> Should allow entry
        mock_market.return_value = {
            'nifty': 22000,
            'vix': 15.0,
            'analysis': {
                'regime': 'TRENDING', 
                'trend': 'BULLISH', 
                'adx': 35,
                'is_squeeze_expansion': True, # allowed
                'bbw': 0.025
            },
            'oi_data': {'bias': 'BULLISH', 'pcr': 1.2},
            'levels': {}
        }
        strat, risk = engine.analyze_and_select()
        self.assertEqual(strat, "MOMENTUM")

        # CASE 3: Trending conditions, no BBW Squeeze expansion, but strong trend override -> Should allow entry
        mock_market.return_value = {
            'nifty': 22000,
            'vix': 15.0,
            'analysis': {
                'regime': 'TRENDING', 
                'trend': 'BULLISH', 
                'adx': 35,
                'is_squeeze_expansion': False, 
                'bbw': 0.015  # >= 0.005 -> triggers override
            },
            'oi_data': {'bias': 'BULLISH', 'pcr': 1.2},
            'levels': {}
        }
        strat, risk = engine.analyze_and_select()
        self.assertEqual(strat, "MOMENTUM")

    @patch('bot.core.trade_repo.trade_repo.get_today_trades', return_value=[])
    @patch('backend.market_service.market_service.get_market_data')
    @patch('bot.core.decision_engine.datetime.datetime')
    def test_decision_engine_dynamic_vix_scaling(self, mock_dt, mock_market, mock_trades):
        api = MagicMock()
        loader = MagicMock()
        engine = DecisionEngine(api, loader, dry_run=True)
        
        # Mock Safety checks
        engine.gatekeeper.is_market_open = MagicMock(return_value=True)
        engine.gatekeeper.is_blackout_period = MagicMock(return_value=False)
        engine.gatekeeper.check_max_daily_loss = MagicMock(return_value=True)
        engine.gatekeeper.check_funds = MagicMock(return_value=True)
        engine.gatekeeper.get_current_capital = MagicMock(return_value=50000)
        
        mock_dt.now.return_value = real_datetime(2026, 2, 27, 11, 0)
        
        # CASE 1: Low VIX (VIX=15.0 <= 18.0) -> Risk multiplier remains 1.0 (or matching confluence scale)
        # Confluence is 5/7 -> Scale = 0.5x
        # VIX scaling = 1.0. Total risk multiplier = 0.5
        mock_market.return_value = {
            'nifty': 22000,
            'vix': 15.0,
            'analysis': {
                'regime': 'TRENDING', 
                'trend': 'BULLISH', 
                'adx': 35,
                'is_squeeze_expansion': True,
                'bbw': 0.025
            },
            'oi_data': {'bias': 'BULLISH', 'pcr': 1.2},
            'levels': {}
        }
        
        # Clean the cached VIX value on the class
        SafetyGatekeeper._vix_cache_time = 0.0
        engine.api.ltpData.return_value = {
            'status': True,
            'data': {'ltp': 15.0}
        }
        
        with patch('os.path.exists', return_value=False):
            strat, risk = engine.analyze_and_select()
            self.assertEqual(strat, "MOMENTUM")
            self.assertAlmostEqual(risk, 0.50, delta=0.01)
                
        # CASE 2: High VIX (VIX=30.0 > 18.0) -> Sizing multiplier = min(1.0, 15.0 / 30.0) = 0.50
        # Confluence is 5/7 -> Scale = 0.5x
        # VIX scaling = 0.50. Total risk multiplier = 0.5 * 0.5 = 0.25
        SafetyGatekeeper._vix_cache_time = 0.0
        engine.api.ltpData.return_value = {
            'status': True,
            'data': {'ltp': 30.0}
        }
        
        with patch('os.path.exists', return_value=False):
            strat, risk = engine.analyze_and_select()
            self.assertEqual(strat, "MOMENTUM")
            self.assertAlmostEqual(risk, 0.25, delta=0.01)


if __name__ == '__main__':
    unittest.main()
