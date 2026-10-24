"""
bot/core/fii_dii_analyzer.py — FII / DII Derivatives Positioning Analyzer

Tracks Foreign Institutional Investor (FII) & Domestic Institutional Investor (DII)
Open Interest in Index Futures to establish the daily macro institutional bias.

Core Principle:
- FIIs are the primary institutional trend-setters in NIFTY 50 derivatives.
- When FII Index Futures Long Ratio is extremely depressed (< 35%), the market is heavily shorted
  and bullish breakouts often get sold into.
- When FII Long Ratio is elevated (> 65%), institutional money supports dips and short trades
  suffer frequent short-covering squeezes.
"""

import os
import json
import datetime
from typing import Dict, Tuple
from bot.utils.logger import logger


class FIIDIIAnalyzer:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(FIIDIIAnalyzer, cls).__new__(cls)
            cls._instance.data_dir = os.path.join(os.getcwd(), "data")
            cls._instance.sentiment_file = os.path.join(cls._instance.data_dir, "fii_dii_sentiment.json")
            cls._instance._cached_data = None
            cls._instance._last_read_time = 0.0
        return cls._instance

    def _default_sentiment(self) -> Dict:
        """Returns a neutral fallback sentiment if no data is recorded."""
        today = datetime.date.today().isoformat()
        return {
            "date": today,
            "fii_index_futures_long": 100000,
            "fii_index_futures_short": 100000,
            "fii_long_ratio": 50.0,
            "dii_index_futures_long": 50000,
            "dii_index_futures_short": 50000,
            "dii_long_ratio": 50.0,
            "regime": "NEUTRAL_BALANCED",
            "bias": "NEUTRAL",
            "source": "DEFAULT",
            "last_updated": datetime.datetime.now().isoformat()
        }

    def get_sentiment(self, force_refresh: bool = False) -> Dict:
        """
        Reads today's FII/DII sentiment from disk cache.
        Cached in memory for 30s to prevent excessive file I/O.
        """
        import time
        now = time.time()
        if not force_refresh and self._cached_data and (now - self._last_read_time) < 30:
            return self._cached_data

        if os.path.exists(self.sentiment_file):
            try:
                with open(self.sentiment_file, "r") as f:
                    data = json.load(f)
                    self._cached_data = data
                    self._last_read_time = now
                    return data
            except Exception as e:
                logger.warning(f"[FII/DII] Failed to read sentiment file: {e}")

        # Initialize with default if missing
        default_data = self._default_sentiment()
        self.save_sentiment(default_data)
        self._cached_data = default_data
        self._last_read_time = now
        return default_data

    def save_sentiment(self, data: Dict) -> bool:
        """Atomically saves sentiment to disk."""
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            temp_file = os.path.join(self.data_dir, "fii_dii_sentiment.tmp")
            with open(temp_file, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(temp_file, self.sentiment_file)
            self._cached_data = data
            return True
        except Exception as e:
            logger.error(f"[FII/DII] Failed to write sentiment: {e}")
            return False

    def update_from_values(
        self,
        fii_long: int,
        fii_short: int,
        dii_long: int = 0,
        dii_short: int = 0,
        source: str = "MANUAL",
        date_str: str = None
    ) -> Dict:
        """
        Calculates ratios, assigns institutional regime and bias, and persists to disk.
        """
        today = date_str or datetime.date.today().isoformat()
        total_fii = fii_long + fii_short
        fii_ratio = round((fii_long / total_fii * 100.0), 2) if total_fii > 0 else 50.0

        total_dii = dii_long + dii_short
        dii_ratio = round((dii_long / total_dii * 100.0), 2) if total_dii > 0 else 50.0

        # Classify regime based on FII Long Ratio
        if fii_ratio >= 65.0:
            regime = "HEAVILY_LONG"
            bias = "BULLISH"
        elif fii_ratio >= 55.0:
            regime = "MODERATELY_BULLISH"
            bias = "BULLISH"
        elif fii_ratio <= 35.0:
            regime = "HEAVILY_SHORT"
            bias = "BEARISH"
        elif fii_ratio <= 45.0:
            regime = "MODERATELY_BEARISH"
            bias = "BEARISH"
        else:
            regime = "NEUTRAL_BALANCED"
            bias = "NEUTRAL"

        record = {
            "date": today,
            "fii_index_futures_long": int(fii_long),
            "fii_index_futures_short": int(fii_short),
            "fii_long_ratio": fii_ratio,
            "dii_index_futures_long": int(dii_long),
            "dii_index_futures_short": int(dii_short),
            "dii_long_ratio": dii_ratio,
            "regime": regime,
            "bias": bias,
            "source": source,
            "last_updated": datetime.datetime.now().isoformat()
        }

        self.save_sentiment(record)
        logger.info(
            f">>> [FII/DII] 🏛️ Updated: FII Long Ratio = {fii_ratio:.1f}% "
            f"({regime} | Bias: {bias}) [Source: {source}]"
        )
        return record

    def update_from_ratio(self, fii_long_ratio: float, source: str = "MANUAL") -> Dict:
        """Helper to quickly set FII ratio directly."""
        fii_long = int(fii_long_ratio * 1000)
        fii_short = int((100.0 - fii_long_ratio) * 1000)
        return self.update_from_values(fii_long, fii_short, source=source)

    def evaluate_alignment(self, direction: str) -> Tuple[bool, str, int]:
        """
        Evaluates whether institutional FII derivatives positioning supports a proposed trade.
        Args:
            direction: "CE" / "BULLISH" or "PE" / "BEARISH"
        Returns:
            Tuple of (is_favorable: bool, reason: str, confluence_points: int)
        """
        sentiment = self.get_sentiment()
        fii_ratio = sentiment.get("fii_long_ratio", 50.0)
        bias = sentiment.get("bias", "NEUTRAL")
        regime = sentiment.get("regime", "NEUTRAL_BALANCED")

        dir_upper = direction.upper()
        is_bullish = dir_upper in ("CE", "BULLISH")
        is_bearish = dir_upper in ("PE", "BEARISH")

        confluence_points = 0

        if is_bullish:
            if bias == "BULLISH":
                confluence_points = 1
                reason = f"FII Net Long (Ratio: {fii_ratio:.1f}%)"
                return True, reason, confluence_points
            elif regime == "HEAVILY_SHORT":
                reason = f"Adverse FII Positioning: Institutions Heavily Short (Ratio: {fii_ratio:.1f}%)"
                return False, reason, 0
            else:
                reason = f"FII Neutral/Balanced (Ratio: {fii_ratio:.1f}%)"
                return True, reason, 0

        elif is_bearish:
            if bias == "BEARISH":
                confluence_points = 1
                reason = f"FII Net Short (Ratio: {fii_ratio:.1f}%)"
                return True, reason, confluence_points
            elif regime == "HEAVILY_LONG":
                reason = f"Adverse FII Positioning: Institutions Heavily Long (Ratio: {fii_ratio:.1f}%)"
                return False, reason, 0
            else:
                reason = f"FII Neutral/Balanced (Ratio: {fii_ratio:.1f}%)"
                return True, reason, 0

        return True, "FII Position Neutral", 0
