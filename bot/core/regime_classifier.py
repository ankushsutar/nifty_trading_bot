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
        """
        if df is None or len(df) < 22:
            return {"regime": "UNKNOWN", "adx": 0, "rsi": 50, "atr": 0, "bbw": 0}

        df = df.copy()
        
        # 1. Calculate Indicators
        df['RSI'] = self._calculate_rsi(df)
        df['ADX'] = self._calculate_adx(df)
        df['ATR'] = self._calculate_atr(df)
        df['BBW'] = self._calculate_bbw(df)
        df['EMA9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['EMA21'] = df['close'].ewm(span=21, adjust=False).mean()

        last = df.iloc[-1] # Use the most recent data (even if candle is forming) or -2 for closed?
        # Typically for "Current Regime", -1 is better. Strategies use -2 for signals.
        
        adx = last['ADX']
        rsi = last['RSI']
        atr = last['ATR']
        bbw = last['BBW']
        ema9 = last['EMA9']
        ema21 = last['EMA21']

        # 2. Classification Logic
        regime = "SIDEWAYS" # Default
        
        if adx > 25:
            regime = "TRENDING"
        elif bbw > 0.0015: # BBW threshold for high volatility
            regime = "VOLATILE"
        elif adx < 20:
            regime = "CHOP"
        
        # Trend Direction
        trend = "NEUTRAL"
        if regime == "TRENDING":
            if ema9 > ema21: trend = "BULLISH"
            elif ema9 < ema21: trend = "BEARISH"

        return {
            "regime": regime,
            "trend": trend,
            "adx": round(adx, 2),
            "rsi": round(rsi, 2),
            "atr": round(atr, 2),
            "bbw": round(bbw, 4),
            "ema9": round(ema9, 2),
            "ema21": round(ema21, 2)
        }

    def _calculate_rsi(self, df, period=14):
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/period, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/period, adjust=False).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs)).fillna(50)

    def _calculate_atr(self, df, period=14):
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        return true_range.ewm(alpha=1/period, adjust=False).mean().fillna(0)

    def _calculate_adx(self, df, period=14):
        plus_dm = df['high'].diff()
        minus_dm = df['low'].diff().multiply(-1)
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        
        # Apply logic: only use the larger DM
        mask = plus_dm > minus_dm
        plus_dm[~mask] = 0
        minus_dm[mask] = 0
        
        tr = self._calculate_atr(df, period=1) # TR is ATR(1)
        atr_smooth = tr.ewm(alpha=1/period, adjust=False).mean()
        
        plus_di = 100 * (plus_dm.ewm(alpha=1/period, adjust=False).mean() / atr_smooth)
        minus_di = 100 * (minus_dm.ewm(alpha=1/period, adjust=False).mean() / atr_smooth)
        
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
        adx = dx.ewm(alpha=1/period, adjust=False).mean()
        return adx.fillna(0)

    def _calculate_bbw(self, df, period=20, std=2):
        ma = df['close'].rolling(window=period).mean()
        sd = df['close'].rolling(window=period).std()
        upper = ma + (std * sd)
        lower = ma - (std * sd)
        return ((upper - lower) / ma).fillna(0)
