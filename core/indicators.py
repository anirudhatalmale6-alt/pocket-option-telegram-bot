"""
Technical indicators used by the strategy engine.

These are deliberately dependency-free (pure Python) so they can be unit-tested
in isolation and run on any VPS without a heavy numeric stack. Every function
takes a plain list of floats (oldest first, newest last) and returns either a
single latest value or a list aligned to the input.

Author: Anirudha
"""

from __future__ import annotations

from typing import List, Optional, Tuple


def sma(values: List[float], period: int) -> Optional[float]:
    """Simple moving average of the last `period` values. None if not enough data."""
    if period <= 0 or len(values) < period:
        return None
    window = values[-period:]
    return sum(window) / period


def ema_series(values: List[float], period: int) -> List[float]:
    """
    Exponential moving average over the whole series.

    Returns a list the same length as `values`. The first `period-1` entries are
    seeded with the running SMA so early values are still usable; from `period`
    onward it is a true EMA. Empty input -> empty list.
    """
    if period <= 0 or not values:
        return []

    k = 2.0 / (period + 1.0)
    out: List[float] = []
    ema_prev: Optional[float] = None

    for i, v in enumerate(values):
        if i < period:
            # Seed with a simple average of what we have so far.
            seed = sum(values[: i + 1]) / (i + 1)
            ema_prev = seed
        else:
            ema_prev = (v - ema_prev) * k + ema_prev  # type: ignore[operator]
        out.append(ema_prev)  # type: ignore[arg-type]
    return out


def ema(values: List[float], period: int) -> Optional[float]:
    """Latest EMA value, or None if there isn't enough data."""
    series = ema_series(values, period)
    return series[-1] if series else None


def rsi(values: List[float], period: int = 14) -> Optional[float]:
    """
    Wilder's Relative Strength Index (0-100) on closing prices.

    Returns None until there are at least `period + 1` closes. A value near 30 is
    conventionally "oversold" (possible up-pullback), near 70 "overbought".
    """
    if len(values) < period + 1:
        return None

    gains = 0.0
    losses = 0.0
    # Seed the first average gain/loss over the initial window.
    for i in range(1, period + 1):
        delta = values[i] - values[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period

    # Wilder smoothing over the remaining data.
    for i in range(period + 1, len(values)):
        delta = values[i] - values[i - 1]
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def stochastic(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    k_period: int = 14,
    d_period: int = 3,
) -> Optional[Tuple[float, float]]:
    """
    Stochastic oscillator, returning (%K, %D) for the latest bar.

    %K = position of close within the recent high/low range (0-100).
    %D = SMA of %K over `d_period`. None until enough bars exist.
    """
    n = len(closes)
    if n < k_period + d_period - 1 or len(highs) != n or len(lows) != n:
        return None

    k_values: List[float] = []
    # Compute %K for the last `d_period` bars so we can average into %D.
    for offset in range(d_period):
        end = n - offset
        start = end - k_period
        if start < 0:
            return None
        window_high = max(highs[start:end])
        window_low = min(lows[start:end])
        close = closes[end - 1]
        if window_high == window_low:
            k = 50.0  # Flat range -> treat as neutral.
        else:
            k = (close - window_low) / (window_high - window_low) * 100.0
        k_values.append(k)

    k_now = k_values[0]
    d_now = sum(k_values) / len(k_values)
    return k_now, d_now
