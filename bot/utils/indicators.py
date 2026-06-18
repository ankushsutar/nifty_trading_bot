"""
bot/utils/indicators.py — Hot-Path Indicator Calculations

Provides EMA, RSI, and ADX optimized with Numba @njit where available,
falling back to pure NumPy when Numba is not installed.

Usage:
    from bot.utils.indicators import ema, rsi, adx

    closes = np.array([...], dtype=np.float64)
    ema9   = ema(closes, 9)
    rsi14  = rsi(closes, 14)
    adx_val, plus_di, minus_di = adx(highs, lows, closes, 14)
"""

import numpy as np

# ---- Numba optional import ------------------------------------------------
try:
    from numba import njit  # type: ignore
    _NUMBA = True
except ImportError:
    _NUMBA = False
    def njit(*args, **kwargs):         # no-op shim so decorators work without numba
        def _decorator(fn):
            return fn
        return _decorator if args and callable(args[0]) else _decorator


# ==========================================================================
#  EMA — Exponential Moving Average
# ==========================================================================

@njit(cache=True)
def _ema_numba(arr: np.ndarray, period: int) -> np.ndarray:
    """Wilder EMA (same as pandas ewm alpha=1/period, adjust=False)."""
    n      = len(arr)
    result = np.empty(n, dtype=np.float64)
    alpha  = 1.0 / period
    result[0] = arr[0]
    for i in range(1, n):
        result[i] = alpha * arr[i] + (1.0 - alpha) * result[i - 1]
    return result


def ema(closes: np.ndarray, period: int) -> np.ndarray:
    """
    Compute EMA of `closes` over `period` bars.

    Returns np.ndarray of same length. Uses Numba @njit when available
    (sub-microsecond per element), falls back to pure NumPy otherwise.
    """
    closes = np.asarray(closes, dtype=np.float64)
    return _ema_numba(closes, period)


# ==========================================================================
#  RSI — Relative Strength Index (Wilder smoothing)
# ==========================================================================

@njit(cache=True)
def _rsi_numba(closes: np.ndarray, period: int) -> np.ndarray:
    n      = len(closes)
    result = np.full(n, 50.0, dtype=np.float64)
    if n < period + 1:
        return result

    alpha = 1.0 / period

    # Seed first smoothed gain/loss with simple average of first `period` deltas
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        if delta > 0:
            gains  += delta
        else:
            losses -= delta
    avg_gain = gains  / period
    avg_loss = losses / period

    if avg_loss < 1e-10:
        result[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        result[period] = 100.0 - 100.0 / (1.0 + rs)

    # Wilder smoothing for remaining bars
    for i in range(period + 1, n):
        delta = closes[i] - closes[i - 1]
        gain  = delta if delta > 0 else 0.0
        loss  = -delta if delta < 0 else 0.0
        avg_gain = alpha * gain  + (1.0 - alpha) * avg_gain
        avg_loss = alpha * loss  + (1.0 - alpha) * avg_loss
        if avg_loss < 1e-10:
            result[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i] = 100.0 - 100.0 / (1.0 + rs)

    return result


def rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """
    Compute RSI of `closes`.

    Returns np.ndarray in [0, 100], seeded to 50.0 for the warm-up bars.
    """
    closes = np.asarray(closes, dtype=np.float64)
    return _rsi_numba(closes, period)


# ==========================================================================
#  ADX — Average Directional Index
# ==========================================================================

@njit(cache=True)
def _adx_numba(
    highs: np.ndarray,
    lows:  np.ndarray,
    closes: np.ndarray,
    period: int,
) -> tuple:
    """
    Returns (adx, plus_di, minus_di) arrays, all length n.

    Uses Wilder smoothing (alpha = 1/period) — same convention as the
    existing RegimeClassifier so values are directly comparable.
    """
    n     = len(closes)
    alpha = 1.0 / period

    adx_arr    = np.zeros(n, dtype=np.float64)
    plus_di_arr = np.zeros(n, dtype=np.float64)
    minus_di_arr = np.zeros(n, dtype=np.float64)

    if n < period + 1:
        return adx_arr, plus_di_arr, minus_di_arr

    # Accumulate directional movement and true range
    sm_plus_dm  = 0.0
    sm_minus_dm = 0.0
    sm_tr       = 0.0

    for i in range(1, period + 1):
        h_diff = highs[i]  - highs[i - 1]
        l_diff = lows[i - 1] - lows[i]
        plus_dm  = h_diff if (h_diff > l_diff and h_diff > 0) else 0.0
        minus_dm = l_diff if (l_diff > h_diff and l_diff > 0) else 0.0

        hl = highs[i] - lows[i]
        hc = abs(highs[i]  - closes[i - 1])
        lc = abs(lows[i]   - closes[i - 1])
        tr = max(hl, hc, lc)

        sm_plus_dm  += plus_dm
        sm_minus_dm += minus_dm
        sm_tr       += tr

    # Seed first DI values
    if sm_tr > 1e-10:
        plus_di_arr[period]  = 100.0 * sm_plus_dm  / sm_tr
        minus_di_arr[period] = 100.0 * sm_minus_dm / sm_tr
    dx_sum = 0.0
    prev_adx = 0.0

    for i in range(period + 1, n):
        h_diff = highs[i]  - highs[i - 1]
        l_diff = lows[i - 1] - lows[i]
        plus_dm  = h_diff if (h_diff > l_diff and h_diff > 0) else 0.0
        minus_dm = l_diff if (l_diff > h_diff and l_diff > 0) else 0.0

        hl = highs[i] - lows[i]
        hc = abs(highs[i]  - closes[i - 1])
        lc = abs(lows[i]   - closes[i - 1])
        tr = max(hl, hc, lc)

        sm_plus_dm  = (1 - alpha) * sm_plus_dm  + alpha * plus_dm
        sm_minus_dm = (1 - alpha) * sm_minus_dm + alpha * minus_dm
        sm_tr       = (1 - alpha) * sm_tr       + alpha * tr

        if sm_tr > 1e-10:
            pdi = 100.0 * sm_plus_dm  / sm_tr
            mdi = 100.0 * sm_minus_dm / sm_tr
        else:
            pdi = 0.0
            mdi = 0.0

        plus_di_arr[i]  = pdi
        minus_di_arr[i] = mdi

        denom = pdi + mdi
        dx = 100.0 * abs(pdi - mdi) / denom if denom > 1e-10 else 0.0

        if i == period + 1:
            prev_adx = dx
        else:
            prev_adx = (1 - alpha) * prev_adx + alpha * dx

        adx_arr[i] = prev_adx

    return adx_arr, plus_di_arr, minus_di_arr


def adx(
    highs:  np.ndarray,
    lows:   np.ndarray,
    closes: np.ndarray,
    period: int = 14,
) -> tuple:
    """
    Compute (ADX, +DI, -DI) arrays.

    Args:
        highs, lows, closes: Equal-length 1-D arrays of price data.
        period: smoothing period (default 14).

    Returns:
        (adx_arr, plus_di_arr, minus_di_arr): np.ndarray, same length as input.
    """
    highs  = np.asarray(highs,  dtype=np.float64)
    lows   = np.asarray(lows,   dtype=np.float64)
    closes = np.asarray(closes, dtype=np.float64)
    return _adx_numba(highs, lows, closes, period)


# ==========================================================================
#  ATR — Average True Range
# ==========================================================================

@njit(cache=True)
def _atr_numba(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> np.ndarray:
    n      = len(closes)
    result = np.zeros(n, dtype=np.float64)
    alpha  = 1.0 / period

    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i]  - closes[i - 1])
        lc = abs(lows[i]   - closes[i - 1])
        tr = max(hl, hc, lc)
        if i == 1:
            result[i] = tr
        else:
            result[i] = alpha * tr + (1.0 - alpha) * result[i - 1]

    return result


def atr(
    highs:  np.ndarray,
    lows:   np.ndarray,
    closes: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    """Compute ATR (Wilder smoothing). Returns np.ndarray."""
    highs  = np.asarray(highs,  dtype=np.float64)
    lows   = np.asarray(lows,   dtype=np.float64)
    closes = np.asarray(closes, dtype=np.float64)
    return _atr_numba(highs, lows, closes, period)


# ==========================================================================
#  Convenience: compute all indicators at once from a DataFrame
# ==========================================================================

def compute_all(df, ema_fast: int = 9, ema_slow: int = 21, adx_period: int = 14):
    """
    Compute EMA fast/slow, RSI, ATR, ADX from a OHLCV DataFrame.

    Args:
        df: pandas DataFrame with columns [open, high, low, close, volume].

    Returns:
        dict with keys: ema_fast, ema_slow, rsi, atr, adx, plus_di, minus_di
        (each a np.ndarray aligned to df's index)
    """
    closes = df["close"].to_numpy(dtype=np.float64)
    highs  = df["high"].to_numpy(dtype=np.float64)
    lows   = df["low"].to_numpy(dtype=np.float64)

    adx_arr, plus_di, minus_di = adx(highs, lows, closes, adx_period)

    return {
        "ema_fast":  ema(closes, ema_fast),
        "ema_slow":  ema(closes, ema_slow),
        "rsi":       rsi(closes, 14),
        "atr":       atr(highs, lows, closes, 14),
        "adx":       adx_arr,
        "plus_di":   plus_di,
        "minus_di":  minus_di,
        "numba":     _NUMBA,
    }
