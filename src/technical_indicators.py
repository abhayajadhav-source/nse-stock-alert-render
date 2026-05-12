"""
Pure-pandas technical indicators for trend reversal detection.

Why hand-rolled? Avoids heavy TA libraries (talib, pandas-ta) that
either need C compilation or have heavy dependencies. These functions
are short, well-tested, and use only pandas/numpy.

All inputs are pandas Series of daily closing prices (or volumes).
All outputs are pandas Series aligned to the input index.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Moving averages
# ---------------------------------------------------------------------------
def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average — recursive, weighted toward recent values."""
    return series.ewm(span=period, adjust=False).mean()


# ---------------------------------------------------------------------------
# RSI — Relative Strength Index
# ---------------------------------------------------------------------------
def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    RSI using Wilder's smoothing (standard implementation).

    RSI > 70 → typically overbought
    RSI < 30 → typically oversold
    Range: 0 to 100.
    """
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)

    # Wilder's smoothing = EMA with alpha = 1/period
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

    # Avoid division by zero; rs goes to inf where avg_loss is 0 → RSI = 100
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


# ---------------------------------------------------------------------------
# MACD — Moving Average Convergence Divergence
# ---------------------------------------------------------------------------
def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Returns (macd_line, signal_line, histogram).

    Bullish: macd_line crosses ABOVE signal_line.
    Bearish: macd_line crosses BELOW signal_line.
    Histogram = macd_line - signal_line (visual momentum gauge).
    """
    macd_line   = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    histogram   = macd_line - signal_line
    return macd_line, signal_line, histogram


# ---------------------------------------------------------------------------
# Crossover detection
# ---------------------------------------------------------------------------
def has_crossed_above(
    fast: pd.Series, slow: pd.Series, within_days: int
) -> bool:
    """
    True if `fast` crossed above `slow` at any point within the last N rows.

    A crossover is detected by checking each row in the window: is
    fast > slow now, but fast <= slow on the previous row?
    """
    if len(fast) < within_days + 1 or len(slow) < within_days + 1:
        return False

    tail_fast = fast.iloc[-(within_days + 1):]
    tail_slow = slow.iloc[-(within_days + 1):]

    for i in range(1, len(tail_fast)):
        prev_diff = tail_fast.iloc[i - 1] - tail_slow.iloc[i - 1]
        curr_diff = tail_fast.iloc[i]     - tail_slow.iloc[i]
        if pd.notna(prev_diff) and pd.notna(curr_diff):
            if prev_diff <= 0 and curr_diff > 0:
                return True
    return False


def has_crossed_below(
    fast: pd.Series, slow: pd.Series, within_days: int
) -> bool:
    """Mirror of `has_crossed_above`."""
    if len(fast) < within_days + 1 or len(slow) < within_days + 1:
        return False

    tail_fast = fast.iloc[-(within_days + 1):]
    tail_slow = slow.iloc[-(within_days + 1):]

    for i in range(1, len(tail_fast)):
        prev_diff = tail_fast.iloc[i - 1] - tail_slow.iloc[i - 1]
        curr_diff = tail_fast.iloc[i]     - tail_slow.iloc[i]
        if pd.notna(prev_diff) and pd.notna(curr_diff):
            if prev_diff >= 0 and curr_diff < 0:
                return True
    return False


# ---------------------------------------------------------------------------
# Pivot points (swing highs/lows)
# ---------------------------------------------------------------------------
def find_pivot_lows(series: pd.Series, radius: int = 3) -> list[tuple[int, float]]:
    """
    Find local-minimum pivot points. A point is a pivot low if its value
    is the minimum within +/- `radius` neighbors.

    Returns list of (index_position, value) tuples ordered by time.
    """
    pivots = []
    n = len(series)
    if n < 2 * radius + 1:
        return pivots

    for i in range(radius, n - radius):
        window = series.iloc[i - radius : i + radius + 1]
        if pd.isna(series.iloc[i]):
            continue
        if series.iloc[i] == window.min():
            pivots.append((i, float(series.iloc[i])))
    return pivots


def find_pivot_highs(series: pd.Series, radius: int = 3) -> list[tuple[int, float]]:
    """Mirror of `find_pivot_lows`."""
    pivots = []
    n = len(series)
    if n < 2 * radius + 1:
        return pivots

    for i in range(radius, n - radius):
        window = series.iloc[i - radius : i + radius + 1]
        if pd.isna(series.iloc[i]):
            continue
        if series.iloc[i] == window.max():
            pivots.append((i, float(series.iloc[i])))
    return pivots


def has_higher_lows(lows: list[tuple[int, float]], min_count: int = 3) -> bool:
    """True if the last `min_count` pivot lows are strictly increasing."""
    if len(lows) < min_count:
        return False
    recent = [v for _, v in lows[-min_count:]]
    return all(recent[i] > recent[i - 1] for i in range(1, len(recent)))


def has_lower_highs(highs: list[tuple[int, float]], min_count: int = 3) -> bool:
    """True if the last `min_count` pivot highs are strictly decreasing."""
    if len(highs) < min_count:
        return False
    recent = [v for _, v in highs[-min_count:]]
    return all(recent[i] < recent[i - 1] for i in range(1, len(recent)))
