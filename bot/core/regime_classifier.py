import pandas as pd
import numpy as np
from bot.utils.logger import logger


class RegimeClassifier:
    def __init__(self, period_adx=14, period_rsi=14, period_atr=14, period_bbw=20):
        self.period_adx = period_adx
        self.period_rsi = period_rsi
        self.period_atr = period_atr
        self.period_bbw = period_bbw

    def classify(self, df: pd.DataFrame):
        """
        Classifies the market regime based on technical indicators.
        Returns a dict with regime and metadata.
        Degrades gracefully for early-session low candle counts (< 22).
        """
        if df is None or len(df) < 5:
            logger.warning(f"[Regime] Insufficient candles ({len(df) if df is not None else 0}). Returning UNKNOWN.")
            return {
                "regime": "UNKNOWN", "trend": "NEUTRAL",
                "adx": 0, "rsi": 50, "atr": 0, "bbw": 0, "ema9": 0, "ema21": 0
            }

        df = df.copy()
        n = len(df)

        # 1. Calculate Indicators (period auto-clamped to available candles)
        df['RSI'] = self._calculate_rsi(df)
        df['ATR'] = self._calculate_atr(df)
        df['EMA9'] = df['close'].ewm(span=min(9, n), adjust=False).mean()
        df['EMA21'] = df['close'].ewm(span=min(21, n), adjust=False).mean()

        # ADX and BBW need more data — use simplified logic if not enough bars
        has_full_data = n >= 22
        if has_full_data:
            df['ADX'] = self._calculate_adx(df)
            df['BBW'] = self._calculate_bbw(df)
        else:
            df['ADX'] = 0.0
            df['BBW'] = 0.0

        last = df.iloc[-1]
        adx  = last['ADX']
        rsi  = last['RSI']
        atr  = last['ATR']
        bbw  = last['BBW']
        ema9 = last['EMA9']
        ema21= last['EMA21']

        # 2. Early-Session Simplified Regime (< 22 candles, ~110 mins after open)
        # We have enough price action to determine basic trend via EMA + RSI,
        # but not enough for reliable ADX. Default to TRENDING when EMAs diverge.
        if not has_full_data:
            ema_spread = abs(ema9 - ema21) / (ema21 + 1e-10)
            if ema_spread > 0.002:  # 0.2% divergence → trending
                regime = "TRENDING"
            else:
                regime = "SIDEWAYS"
            logger.info(
                f"[Regime] Early-Session Mode ({n} candles). "
                f"EMA spread={ema_spread:.4f} → {regime}"
            )
        else:
            # 3. Full Classification Logic (22+ candles)
            if adx > 20:
                regime = "TRENDING"
            elif adx >= 15 and bbw > 0.02:
                regime = "VOLATILE"
            elif adx < 15:
                regime = "CHOP"
            else:
                regime = "SIDEWAYS"

        # 4. Trend Direction (works at all candle counts)
        trend = "NEUTRAL"
        if regime in ("TRENDING", "SIDEWAYS"):
            if ema9 > ema21 and rsi > 50:
                trend = "BULLISH"
            elif ema9 < ema21 and rsi < 50:
                trend = "BEARISH"
            elif ema9 > ema21:
                trend = "BULLISH"
            elif ema9 < ema21:
                trend = "BEARISH"

        logger.debug(
            f"[Regime] n={n} ADX={adx:.1f} RSI={rsi:.1f} BBW={bbw:.4f} "
            f"EMA9={ema9:.1f} EMA21={ema21:.1f} → {regime}/{trend}"
        )

        # 5. Volume Spike Detection (Institutional Activity Filter)
        volume_spike = self._calculate_volume_spike(df)
        if volume_spike:
            logger.info(f"[Regime] 🔥 VOLUME SPIKE DETECTED! (High institutional confidence)")

        return {
            "regime": regime,
            "trend": trend,
            "adx": round(adx, 2),
            "rsi": round(rsi, 2),
            "atr": round(atr, 2),
            "bbw": round(bbw, 4),
            "ema9": round(ema9, 2),
            "ema21": round(ema21, 2),
            "volume_spike": volume_spike
        }

    def _calculate_rsi(self, df, period=14):
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/period, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/period, adjust=False).mean()
        # FIX Issue 5: Avoid division by zero in pure uptrend (loss=0 → RSI should be 100)
        rs = gain / loss.replace(0, 1e-10)
        return (100 - (100 / (1 + rs))).fillna(50)

    def _calculate_atr(self, df, period=14):
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift()).fillna(0)
        low_close = np.abs(df['low'] - df['close'].shift()).fillna(0)
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        # Use EWM for smoothing (Standard ATR)
        return true_range.ewm(alpha=1/period, adjust=False).mean().fillna(true_range)

    def _calculate_adx(self, df, period=14):
        # FIX Issue 1: Use .where() instead of in-place boolean masking (deprecated in pandas)
        # Also added sign check: +DM only when high.diff() > 0, -DM only when low.diff() < 0
        high_diff = df['high'].diff()
        low_diff = df['low'].diff()

        plus_dm = high_diff.where((high_diff > low_diff.abs()) & (high_diff > 0), 0)
        minus_dm = (-low_diff).where((low_diff.abs() > high_diff) & (low_diff < 0), 0)

        tr = self._calculate_atr(df, period=1)  # TR is ATR(1)
        atr_smooth = tr.ewm(alpha=1/period, adjust=False).mean()

        plus_di = 100 * (plus_dm.ewm(alpha=1/period, adjust=False).mean() / atr_smooth)
        minus_di = 100 * (minus_dm.ewm(alpha=1/period, adjust=False).mean() / atr_smooth)

        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, 1e-10)
        adx = dx.ewm(alpha=1/period, adjust=False).mean()
        return adx.fillna(0)

    def _calculate_bbw(self, df, period=20, std=2):
        ma = df['close'].rolling(window=period).mean()
        sd = df['close'].rolling(window=period).std()
        upper = ma + (std * sd)
        lower = ma - (std * sd)
        return ((upper - lower) / ma).fillna(0)

    def _calculate_volume_spike(self, df, window=20, multiplier=2.5):
        """
        Detects if current volume is significantly higher than recent average.
        Institutional breakouts are usually accompanied by a volume spike.
        """
        if len(df) < window + 1:
            return False
            
        recent_avg_vol = df['volume'].iloc[-(window+1):-1].mean()
        current_vol = df['volume'].iloc[-1]
        
        if recent_avg_vol == 0: return False
        
        spike_ratio = current_vol / recent_avg_vol
        return spike_ratio >= multiplier
