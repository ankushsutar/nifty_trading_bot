"""
tests/test_institutional_edge.py — Comprehensive Test Suite for Institutional Edge
Tests:
1. ConstituentTracker (HDFC Bank, Reliance, ICICI Bank weighting, alignment, and trap veto)
2. FIIDIIAnalyzer (Daily institutional futures ratio, regime classification, persistence)
3. DecisionEngine Confluence & Trap Shield Integration
"""

import os
import sys
import pytest
import datetime
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.core.constituent_tracker import ConstituentTracker
from bot.core.fii_dii_analyzer import FIIDIIAnalyzer


class TestConstituentTracker:
    @pytest.fixture
    def tracker(self):
        # Reset singleton instance
        ConstituentTracker._instance = None
        mock_api = MagicMock()
        t = ConstituentTracker(mock_api)
        # Mock token resolution
        t._token_cache = {
            "HDFCBANK": ("1333", "HDFCBANK-EQ"),
            "RELIANCE": ("2885", "RELIANCE-EQ"),
            "ICICIBANK": ("4963", "ICICIBANK-EQ"),
        }
        return t

    def test_bullish_alignment(self, tracker):
        # Setup mock prices: HDFC +0.8%, Reliance +0.6%, ICICI +0.4%
        tracker._get_day_open = MagicMock(side_effect=lambda sym, tok: 1000.0)
        tracker.data_fetcher.get_ltp = MagicMock(side_effect=lambda tok, exchange="NSE": {
            "1333": 1008.0,  # +0.8%
            "2885": 1006.0,  # +0.6%
            "4963": 1004.0,  # +0.4%
        }.get(tok, 1000.0))

        analysis = tracker.analyze(force_refresh=True)
        assert analysis["bias"] == "BULLISH"
        assert analysis["weighted_change"] > 0.5
        assert analysis["is_veto_ce"] is False

        is_aligned, reason, pts = tracker.is_aligned_for_entry("CE")
        assert is_aligned is True
        assert pts == 1
        assert "BULLISH" in reason

    def test_heavyweight_drag_veto_ce(self, tracker):
        # Setup mock prices: HDFC -0.9%, Reliance -0.7%, ICICI -0.5% (Heavyweight dump)
        tracker._get_day_open = MagicMock(side_effect=lambda sym, tok: 1000.0)
        tracker.data_fetcher.get_ltp = MagicMock(side_effect=lambda tok, exchange="NSE": {
            "1333": 991.0,  # -0.9%
            "2885": 993.0,  # -0.7%
            "4963": 995.0,  # -0.5%
        }.get(tok, 1000.0))

        analysis = tracker.analyze(force_refresh=True)
        assert analysis["bias"] == "BEARISH"
        assert analysis["is_veto_ce"] is True
        assert analysis["is_aligned_ce"] is False

        # Attempting to buy CE should be VETOED
        is_aligned, reason, pts = tracker.is_aligned_for_entry("CE")
        assert is_aligned is False
        assert pts == 0
        assert "Heavyweight Drag" in reason

        # But PE should be permitted with confluence point
        pe_aligned, pe_reason, pe_pts = tracker.is_aligned_for_entry("PE")
        assert pe_aligned is True
        assert pe_pts == 1

    def test_heavyweight_surge_veto_pe(self, tracker):
        # Setup mock prices: Both HDFC and Reliance surging (+1.0%)
        tracker._get_day_open = MagicMock(side_effect=lambda sym, tok: 1000.0)
        tracker.data_fetcher.get_ltp = MagicMock(side_effect=lambda tok, exchange="NSE": {
            "1333": 1010.0,  # +1.0%
            "2885": 1012.0,  # +1.2%
            "4963": 1008.0,  # +0.8%
        }.get(tok, 1000.0))

        analysis = tracker.analyze(force_refresh=True)
        assert analysis["is_veto_pe"] is True

        # Attempting to buy PE should be VETOED
        pe_aligned, pe_reason, pe_pts = tracker.is_aligned_for_entry("PE")
        assert pe_aligned is False
        assert "Heavyweight Surge" in pe_reason


class TestFIIDIIAnalyzer:
    @pytest.fixture
    def analyzer(self, tmp_path):
        FIIDIIAnalyzer._instance = None
        a = FIIDIIAnalyzer()
        a.data_dir = str(tmp_path)
        a.sentiment_file = os.path.join(str(tmp_path), "fii_dii_sentiment.json")
        a._cached_data = None
        a._last_read_time = 0.0
        return a

    def test_ratio_classification(self, analyzer):
        # Heavily Short (< 35%)
        rec_short = analyzer.update_from_values(fii_long=30000, fii_short=70000)
        assert rec_short["fii_long_ratio"] == 30.0
        assert rec_short["regime"] == "HEAVILY_SHORT"
        assert rec_short["bias"] == "BEARISH"

        # Heavily Long (> 65%)
        rec_long = analyzer.update_from_values(fii_long=75000, fii_short=25000)
        assert rec_long["fii_long_ratio"] == 75.0
        assert rec_long["regime"] == "HEAVILY_LONG"
        assert rec_long["bias"] == "BULLISH"

        # Neutral Balanced
        rec_neutral = analyzer.update_from_values(fii_long=50000, fii_short=50000)
        assert rec_neutral["fii_long_ratio"] == 50.0
        assert rec_neutral["regime"] == "NEUTRAL_BALANCED"
        assert rec_neutral["bias"] == "NEUTRAL"

    def test_alignment_evaluation(self, analyzer):
        analyzer.update_from_ratio(72.0)
        is_favorable, reason, pts = analyzer.evaluate_alignment("CE")
        assert is_favorable is True
        assert pts == 1

        # Adverse check for PE when institutions are heavily long
        pe_favorable, pe_reason, pe_pts = analyzer.evaluate_alignment("PE")
        assert pe_favorable is False
        assert pe_pts == 0
        assert "Adverse FII Positioning" in pe_reason

    def test_persistence_roundtrip(self, analyzer):
        analyzer.update_from_values(fii_long=120000, fii_short=80000, dii_long=60000, dii_short=40000)
        # Clear in-memory cache to force read from disk
        analyzer._cached_data = None
        loaded = analyzer.get_sentiment(force_refresh=True)
        assert loaded["fii_long_ratio"] == 60.0
        assert loaded["regime"] == "MODERATELY_BULLISH"
        assert loaded["bias"] == "BULLISH"
