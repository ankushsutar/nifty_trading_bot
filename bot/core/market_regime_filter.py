"""
bot/core/market_regime_filter.py — Market Regime Filter

Implements:
1. VIX Filter: Fetch India VIX via Kite historical data. If VIX < 15,
   disable all net-short premium strategies (Short Straddles/Strangles)
   and switch execution to directional Vertical Debit Spreads.
2. Trend Detection: Supertrend (10, 3) and 20-EMA / 50-EMA crossover on the
   15-minute Nifty 50 spot chart to determine daily directional bias.
   Strictly aligns Delta exposure with this trend.
3. Dynamic Stop-Loss: Dynamic trailing stop based on 1.5x 5-minute ATR.
4. Risk Protocol: Calculates position size ensuring max risk per trade <= 2% equity.
"""

import time
import datetime
import pandas as pd
import numpy as np
from bot.config.settings import Config
from bot.utils.logger import logger
from bot.utils.indicators import supertrend, ema, atr


class MarketRegimeFilter:
    """
    Evaluates market regime to prevent losses in trending, low-VIX environments.
    """
    VIX_LOW_THRESHOLD = 15.0
    VIX_TOKEN = "99926017"        # India VIX token (Kite 264969)
    NIFTY_SPOT_TOKEN = "99926000"  # Nifty 50 spot token (Kite 256265)

    def __init__(self, api, token_loader=None, data_fetcher=None):
        self.api = api
        self.token_loader = token_loader
        if data_fetcher is None:
            from bot.core.data_fetcher import DataFetcher
            self.data_fetcher = DataFetcher(api)
        else:
            self.data_fetcher = data_fetcher

        self._cached_vix = None
        self._cached_vix_time = 0.0
        self._cached_trend = None
        self._cached_trend_time = 0.0

    def fetch_india_vix(self, force_refresh: bool = False) -> float:
        """
        Fetches India VIX using Kite historical data, with fallback to LTP and cache.
        """
        now = time.time()
        if not force_refresh and self._cached_vix is not None and (now - self._cached_vix_time) < 60:
            return self._cached_vix

        vix_val = None

        # 1. Primary: Kite Connect API historical data
        kite_client = getattr(self.api, "kite", None)
        if kite_client is not None:
            try:
                from bot.utils.rate_limiter import rate_limiter
                rate_limiter.wait()
                today = datetime.date.today()
                from_dt = datetime.datetime.combine(today - datetime.timedelta(days=2), datetime.time(9, 15))
                to_dt = datetime.datetime.now()
                
                # Kite instrument token for India VIX is 264969
                records = kite_client.historical_data(
                    instrument_token=264969,
                    from_date=from_dt,
                    to_date=to_dt,
                    interval="5minute"
                )
                if records and len(records) > 0:
                    vix_val = float(records[-1]["close"])
                    logger.info(f"[MarketRegimeFilter] India VIX fetched from Kite historical data: {vix_val:.2f}")
            except Exception as e:
                logger.warning(f"[MarketRegimeFilter] Kite historical VIX fetch failed: {e}")

        # 2. Secondary: Broker adapter getCandleData
        if vix_val is None:
            try:
                today = datetime.date.today()
                from_str = (today - datetime.timedelta(days=2)).strftime("%Y-%m-%d") + " 09:15"
                to_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                resp = self.api.getCandleData({
                    "exchange": "NSE",
                    "symboltoken": self.VIX_TOKEN,
                    "interval": "FIVE_MINUTE",
                    "fromdate": from_str,
                    "todate": to_str
                })
                if resp and resp.get("status") and resp.get("data"):
                    candles = resp["data"]
                    if len(candles) > 0:
                        vix_val = float(candles[-1][4])  # close
                        logger.info(f"[MarketRegimeFilter] India VIX fetched via getCandleData: {vix_val:.2f}")
            except Exception as e:
                logger.warning(f"[MarketRegimeFilter] getCandleData VIX fetch failed: {e}")

        # 3. Tertiary: LTP quote
        if vix_val is None:
            try:
                ltp = self.data_fetcher.get_ltp(self.VIX_TOKEN, exchange="NSE")
                if ltp and ltp > 0:
                    vix_val = float(ltp)
                    logger.info(f"[MarketRegimeFilter] India VIX fetched via LTP: {vix_val:.2f}")
            except Exception as e:
                logger.warning(f"[MarketRegimeFilter] LTP VIX fetch failed: {e}")

        # 4. Fallback: market_analysis.json / market_service
        if vix_val is None:
            try:
                from backend.market_service import market_service
                analysis = market_service.get_current_analysis()
                if analysis and analysis.get("vix"):
                    vix_val = float(analysis["vix"])
                    logger.info(f"[MarketRegimeFilter] India VIX loaded from market intelligence cache: {vix_val:.2f}")
            except Exception:
                pass

        if vix_val is None or vix_val <= 0:
            vix_val = 14.5  # Safe conservative default below threshold
            logger.warning(f"[MarketRegimeFilter] Could not determine India VIX, defaulting to conservative: {vix_val}")

        self._cached_vix = vix_val
        self._cached_vix_time = now
        return vix_val

    def is_vix_low(self, threshold: float = None) -> bool:
        """
        Returns True if India VIX < 15 (or custom threshold).
        In low-VIX environments, option selling premiums are too thin, and any breakout
        causes heavy losses.
        """
        t = threshold if threshold is not None else self.VIX_LOW_THRESHOLD
        vix = self.fetch_india_vix()
        return vix < t

    def get_15m_trend(self, force_refresh: bool = False) -> dict:
        """
        Detects trend on the 15-minute Nifty 50 spot chart:
        1. Supertrend (10, 3)
        2. 20-EMA / 50-EMA crossover
        Returns directional bias: 'BULLISH', 'BEARISH', or 'NEUTRAL'.
        """
        now = time.time()
        if not force_refresh and self._cached_trend is not None and (now - self._cached_trend_time) < 120:
            return self._cached_trend

        # Fetch 15-minute candles (last 5 trading days to ensure 50+ candles for 50-EMA)
        df = self.data_fetcher.fetch_latest_candles(
            self.NIFTY_SPOT_TOKEN,
            interval="FIFTEEN_MINUTE",
            days=5,
            exchange="NSE"
        )

        default_result = {
            "bias": "NEUTRAL",
            "supertrend_direction": 0,
            "supertrend_value": 0.0,
            "ema20": 0.0,
            "ema50": 0.0,
            "close": 0.0,
            "strength": "WEAK"
        }

        if df is None or len(df) < 15:
            logger.warning(f"[MarketRegimeFilter] Insufficient 15m candles ({len(df) if df is not None else 0})")
            return default_result

        highs = df["high"].to_numpy(dtype=np.float64)
        lows = df["low"].to_numpy(dtype=np.float64)
        closes = df["close"].to_numpy(dtype=np.float64)

        # 1. Supertrend (10, 3)
        st_vals, st_dirs = supertrend(highs, lows, closes, period=10, multiplier=3.0)
        current_st = float(st_vals[-1])
        current_dir = int(st_dirs[-1])  # +1 Bullish, -1 Bearish

        # 2. 20-EMA & 50-EMA
        ema20_arr = ema(closes, 20)
        ema50_arr = ema(closes, min(50, len(closes)))
        current_ema20 = float(ema20_arr[-1])
        current_ema50 = float(ema50_arr[-1])
        last_close = float(closes[-1])

        # Directional Consensus
        is_st_bull = (current_dir == 1)
        is_st_bear = (current_dir == -1)
        is_ema_bull = (current_ema20 > current_ema50)
        is_ema_bear = (current_ema20 < current_ema50)

        if is_st_bull and is_ema_bull:
            bias = "BULLISH"
            strength = "STRONG"
        elif is_st_bear and is_ema_bear:
            bias = "BEARISH"
            strength = "STRONG"
        elif is_st_bull or (last_close > current_st):
            bias = "BULLISH"
            strength = "MODERATE"
        elif is_st_bear or (last_close < current_st):
            bias = "BEARISH"
            strength = "MODERATE"
        else:
            bias = "NEUTRAL"
            strength = "WEAK"

        result = {
            "bias": bias,
            "supertrend_direction": current_dir,
            "supertrend_value": current_st,
            "ema20": current_ema20,
            "ema50": current_ema50,
            "close": last_close,
            "strength": strength
        }

        logger.info(
            f"[MarketRegimeFilter] 15m Trend: {bias} ({strength}) | "
            f"Supertrend={current_st:.1f} (dir={current_dir}) | "
            f"EMA20={current_ema20:.1f}, EMA50={current_ema50:.1f}, Close={last_close:.1f}"
        )

        self._cached_trend = result
        self._cached_trend_time = now
        return result

    def get_5m_atr(self, period: int = 14) -> float:
        """
        Calculates 5-minute ATR on Nifty 50 spot chart.
        Used for dynamic stop-loss sizing (1.5x 5-min ATR).
        """
        df = self.data_fetcher.fetch_latest_candles(
            self.NIFTY_SPOT_TOKEN,
            interval="FIVE_MINUTE",
            days=2,
            exchange="NSE"
        )
        if df is None or len(df) < period + 1:
            logger.warning("[MarketRegimeFilter] Insufficient 5m candles for ATR, defaulting to 25.0 pts")
            return 25.0

        highs = df["high"].to_numpy(dtype=np.float64)
        lows = df["low"].to_numpy(dtype=np.float64)
        closes = df["close"].to_numpy(dtype=np.float64)

        atr_arr = atr(highs, lows, closes, period=period)
        atr_val = float(atr_arr[-1])
        return max(5.0, atr_val)

    def calculate_position_size(
        self,
        account_equity: float,
        net_debit_per_share: float,
        lot_size: int = None,
        atr_5m: float = None,
        max_risk_pct: float = 0.02,
    ) -> dict:
        """
        Execution Protocol Risk Sizer:
        Ensures maximum risk per trade does NOT exceed 2% of total account equity.

        In a vertical debit spread:
        - Max loss per share is strictly capped at net debit paid.
        - Stop loss is dynamically set to 1.5x 5-minute ATR (or net debit, whichever is smaller).
        """
        if lot_size is None:
            lot_size = Config.NIFTY_LOT_SIZE

        max_risk_rupees = account_equity * max_risk_pct

        # Dynamic stop loss distance in points
        if atr_5m is not None and atr_5m > 0:
            sl_distance_points = 1.5 * atr_5m
            # Option delta for ATM/near-ATM is ~0.50, so spread delta is ~0.25 - 0.35.
            # Spread price risk is approximately spread_delta * sl_distance_points or capped by net debit
            spread_risk_estimate = min(net_debit_per_share, max(5.0, sl_distance_points * 0.35))
        else:
            spread_risk_estimate = net_debit_per_share

        risk_per_share = max(1.0, spread_risk_estimate)
        risk_per_lot = risk_per_share * lot_size

        if risk_per_lot <= 0:
            lots = 1
        else:
            lots = int(max_risk_rupees // risk_per_lot)

        # Capital ceiling check (cost of debit spread = lots * lot_size * net_debit)
        max_cost = account_equity * 0.50  # Do not deploy more than 50% equity in one spread
        cost_per_lot = net_debit_per_share * lot_size
        if cost_per_lot > 0:
            lots_by_cost = int(max_cost // cost_per_lot)
            lots = min(lots, lots_by_cost)

        lots = max(0, lots)
        total_risk = lots * risk_per_lot
        actual_risk_pct = (total_risk / account_equity * 100.0) if account_equity > 0 else 0.0

        return {
            "lots": lots,
            "lot_size": lot_size,
            "quantity": lots * lot_size,
            "max_risk_rupees": max_risk_rupees,
            "risk_per_share": risk_per_share,
            "risk_per_lot": risk_per_lot,
            "total_risk": total_risk,
            "actual_risk_pct": actual_risk_pct,
            "within_2pct_limit": actual_risk_pct <= 2.05,
        }

    def filter_regime(self) -> dict:
        """
        Master Filter Check:
        Combines VIX and 15m Trend to determine permitted strategies.
        """
        vix = self.fetch_india_vix()
        is_low_vix = (vix < self.VIX_LOW_THRESHOLD)
        trend_info = self.get_15m_trend()
        bias = trend_info.get("bias", "NEUTRAL")
        atr_5m = self.get_5m_atr()

        # Low VIX Protocol:
        # If VIX < 15, short premium strategies are banned due to negative edge & explosive breakout risk.
        # Switch to directional Vertical Debit Spreads.
        short_strategies_disabled = is_low_vix
        spread_type = None

        if is_low_vix:
            if bias == "BULLISH":
                spread_type = "BULL_CALL_SPREAD"
            elif bias == "BEARISH":
                spread_type = "BEAR_PUT_SPREAD"
            else:
                # If neutral, lean towards supertrend direction
                st_dir = trend_info.get("supertrend_direction", 0)
                spread_type = "BULL_CALL_SPREAD" if st_dir >= 0 else "BEAR_PUT_SPREAD"

        return {
            "vix": vix,
            "is_low_vix": is_low_vix,
            "short_strategies_disabled": short_strategies_disabled,
            "directional_bias": bias,
            "trend_strength": trend_info.get("strength", "MODERATE"),
            "supertrend": trend_info.get("supertrend_value", 0.0),
            "supertrend_direction": trend_info.get("supertrend_direction", 0),
            "ema20": trend_info.get("ema20", 0.0),
            "ema50": trend_info.get("ema50", 0.0),
            "atr_5m": atr_5m,
            "dynamic_sl_distance": 1.5 * atr_5m,
            "recommended_spread": spread_type,
            "deploy_vertical_spread": is_low_vix and (spread_type is not None),
        }
