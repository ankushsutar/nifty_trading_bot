"""
bot/core/constituent_tracker.py — Heavyweight Constituent Tracker

Monitors top NIFTY 50 heavyweight equities (HDFC Bank, Reliance, ICICI Bank)
to detect institutional alignment and prevent retail bull/bear traps.

NIFTY 50 Weightage Concentration:
- HDFC Bank (~11-13%)
- Reliance Industries (~9-10%)
- ICICI Bank (~7-8%)
Combined: ~30% of NIFTY 50 index movement.
"""

import time
import datetime
import pandas as pd
from typing import Dict, Tuple, Optional
from bot.utils.logger import logger
from bot.utils.token_lookup import TokenLookup
from bot.core.data_fetcher import DataFetcher
from bot.config.settings import Config


class ConstituentTracker:
    _instance = None

    # Top heavyweights with relative weights within this basket
    CONSTITUENTS = {
        "HDFCBANK": {"name": "HDFCBANK", "weight": 0.40, "symbol_alt": "HDFCBANK-EQ"},
        "RELIANCE": {"name": "RELIANCE", "weight": 0.35, "symbol_alt": "RELIANCE-EQ"},
        "ICICIBANK": {"name": "ICICIBANK", "weight": 0.25, "symbol_alt": "ICICIBANK-EQ"},
    }

    def __new__(cls, api=None):
        if cls._instance is None:
            cls._instance = super(ConstituentTracker, cls).__new__(cls)
            cls._instance.api = api
            cls._instance.data_fetcher = DataFetcher(api)
            cls._instance.token_lookup = TokenLookup()
            cls._instance._token_cache = {}   # symbol -> (token, tradingsymbol)
            cls._instance._last_analysis = {}
            cls._instance._last_analysis_time = 0.0
            cls._instance._open_prices = {}    # symbol -> (date, open_price)
            cls._instance.CACHE_TTL = 15.0    # 15s cache to protect broker rate-limits
        elif api is not None:
            cls._instance.api = api
            cls._instance.data_fetcher = DataFetcher(api)
        return cls._instance

    def _resolve_token(self, symbol: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Resolves the broker-specific token and tradingsymbol for a heavyweight stock on NSE.
        Caches the token in memory.
        """
        if symbol in self._token_cache:
            return self._token_cache[symbol]

        config = self.CONSTITUENTS.get(symbol, {})
        alt_sym = config.get("symbol_alt", f"{symbol}-EQ")

        if self.token_lookup.df is None:
            self.token_lookup.load_scrip_master()

        df = self.token_lookup.df
        if df is not None and not df.empty:
            # Try primary symbol first, then alt symbol
            for candidate in [symbol, alt_sym]:
                mask = (df['symbol'] == candidate) & (df['exch_seg'] == 'NSE')
                matches = df[mask]
                if not matches.empty:
                    token = str(matches.iloc[0]['token'])
                    ts = str(matches.iloc[0]['symbol'])
                    self._token_cache[symbol] = (token, ts)
                    return token, ts

            # Fallback: substring match on symbol
            mask = (df['symbol'].str.startswith(symbol, na=False)) & (df['exch_seg'] == 'NSE')
            matches = df[mask]
            if not matches.empty:
                token = str(matches.iloc[0]['token'])
                ts = str(matches.iloc[0]['symbol'])
                self._token_cache[symbol] = (token, ts)
                return token, ts

        # Fallback to hardcoded standard tokens if lookup fails
        is_zerodha = (Config.BROKER == "ZERODHA")
        hardcoded_map = {
            "HDFCBANK": ("341249" if is_zerodha else "1333", "HDFCBANK" if is_zerodha else "HDFCBANK-EQ"),
            "RELIANCE": ("738561" if is_zerodha else "2885", "RELIANCE" if is_zerodha else "RELIANCE-EQ"),
            "ICICIBANK": ("1270529" if is_zerodha else "4963", "ICICIBANK" if is_zerodha else "ICICIBANK-EQ"),
        }
        fallback = hardcoded_map.get(symbol, (None, None))
        if fallback[0]:
            self._token_cache[symbol] = fallback
            return fallback

        return None, None

    def _get_day_open(self, symbol: str, token: str) -> Optional[float]:
        """
        Gets the day's open price for a stock using 5m candles.
        Cached per day to avoid redundant queries.
        """
        today_str = datetime.date.today().isoformat()
        if symbol in self._open_prices:
            cached_date, open_px = self._open_prices[symbol]
            if cached_date == today_str and open_px > 0:
                return open_px

        try:
            df = self.data_fetcher.fetch_latest_candles(token, interval="FIVE_MINUTE", days=1)
            if df is not None and not df.empty:
                # Find the first candle of today
                if 'timestamp' in df.columns:
                    df_today = df[pd.to_datetime(df['timestamp']).dt.date == datetime.date.today()]
                else:
                    df_today = df
                if not df_today.empty:
                    day_open = float(df_today.iloc[0]['open'])
                    self._open_prices[symbol] = (today_str, day_open)
                    return day_open
        except Exception as e:
            logger.debug(f"[Constituents] Could not fetch day open for {symbol}: {e}")

        return None

    def analyze(self, force_refresh: bool = False) -> Dict:
        """
        Analyzes the real-time status of top heavyweights.
        Returns:
            dict containing individual stock changes, weighted average change,
            composite bias ('BULLISH', 'BEARISH', 'NEUTRAL'), and veto flags.
        """
        now = time.time()
        if not force_refresh and self._last_analysis and (now - self._last_analysis_time) < self.CACHE_TTL:
            return self._last_analysis

        result = {
            "timestamp": datetime.datetime.now().isoformat(),
            "bias": "NEUTRAL",
            "weighted_change": 0.0,
            "is_aligned_ce": True,
            "is_aligned_pe": True,
            "is_veto_ce": False,
            "is_veto_pe": False,
            "veto_reason": "",
            "stocks": {}
        }

        total_weight = 0.0
        weighted_sum = 0.0

        for sym, meta in self.CONSTITUENTS.items():
            weight = meta["weight"]
            token, tradingsymbol = self._resolve_token(sym)
            if not token:
                continue

            ltp = self.data_fetcher.get_ltp(token, exchange="NSE")
            if not ltp or ltp <= 0:
                continue

            day_open = self._get_day_open(sym, token)
            # If day_open is unavailable, compare against LTP itself (0% change)
            if day_open and day_open > 0:
                pct_change = ((ltp - day_open) / day_open) * 100.0
            else:
                pct_change = 0.0

            trend = "BULLISH" if pct_change >= 0.15 else ("BEARISH" if pct_change <= -0.15 else "NEUTRAL")

            result["stocks"][sym] = {
                "token": token,
                "symbol": tradingsymbol or sym,
                "ltp": round(float(ltp), 2),
                "open": round(float(day_open), 2) if day_open else None,
                "change_pct": round(float(pct_change), 2),
                "trend": trend,
                "weight": weight
            }

            weighted_sum += pct_change * weight
            total_weight += weight

        if total_weight > 0:
            weighted_change = round(weighted_sum / total_weight, 3)
            result["weighted_change"] = weighted_change

            # Set composite bias based on weighted change
            if weighted_change >= 0.20:
                result["bias"] = "BULLISH"
            elif weighted_change <= -0.20:
                result["bias"] = "BEARISH"
            else:
                result["bias"] = "NEUTRAL"

            # Check for Trap / Veto conditions
            hdfc = result["stocks"].get("HDFCBANK", {}).get("change_pct", 0.0)
            reliance = result["stocks"].get("RELIANCE", {}).get("change_pct", 0.0)

            # Veto CE: Both HDFCBANK & RELIANCE are negative and overall basket is negative
            if hdfc <= -0.10 and reliance <= -0.10 and weighted_change < -0.25:
                result["is_veto_ce"] = True
                result["is_aligned_ce"] = False
                result["veto_reason"] = (
                    f"Heavyweight Drag: HDFCBANK ({hdfc:.2f}%) and RELIANCE ({reliance:.2f}%) "
                    f"are both negative (basket {weighted_change:.2f}%). CE entry blocked."
                )

            # Veto PE: Both HDFCBANK & RELIANCE are positive and overall basket is positive
            if hdfc >= 0.10 and reliance >= 0.10 and weighted_change > 0.25:
                result["is_veto_pe"] = True
                result["is_aligned_pe"] = False
                result["veto_reason"] = (
                    f"Heavyweight Surge: HDFCBANK (+{hdfc:.2f}%) and RELIANCE (+{reliance:.2f}%) "
                    f"are both positive (basket +{weighted_change:.2f}%). PE entry blocked."
                )

        self._last_analysis = result
        self._last_analysis_time = now
        return result

    def is_aligned_for_entry(self, direction: str) -> Tuple[bool, str, int]:
        """
        Evaluates whether heavyweights support a proposed trade direction.
        Args:
            direction: "CE" / "BULLISH" or "PE" / "BEARISH"
        Returns:
            Tuple of (is_permitted: bool, reason: str, confluence_points: int)
        """
        analysis = self.analyze()
        bias = analysis.get("bias", "NEUTRAL")
        direction_upper = direction.upper()

        is_bullish = direction_upper in ("CE", "BULLISH")
        is_bearish = direction_upper in ("PE", "BEARISH")

        # 1. Veto Check
        if is_bullish and analysis.get("is_veto_ce", False):
            return False, analysis.get("veto_reason", "Heavyweight Drag"), 0

        if is_bearish and analysis.get("is_veto_pe", False):
            return False, analysis.get("veto_reason", "Heavyweight Surge"), 0

        # 2. Confluence Points (+1 if strictly aligned)
        confluence_points = 0
        if is_bullish and bias == "BULLISH":
            confluence_points = 1
            reason = f"Heavyweights Aligned BULLISH (+{analysis.get('weighted_change', 0):.2f}%)"
        elif is_bearish and bias == "BEARISH":
            confluence_points = 1
            reason = f"Heavyweights Aligned BEARISH ({analysis.get('weighted_change', 0):.2f}%)"
        else:
            reason = f"Heavyweights Neutral/Mixed ({analysis.get('weighted_change', 0):.2f}%)"

        return True, reason, confluence_points
