"""Unit tests for the pure indicator functions."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.indicators import ema, ema_series, rsi, sma, stochastic


def test_sma_basic():
    assert sma([1, 2, 3, 4], 2) == 3.5
    assert sma([1, 2], 5) is None


def test_ema_series_length_and_trend():
    values = list(range(1, 21))  # steadily rising
    series = ema_series(values, 5)
    assert len(series) == len(values)
    # On a rising series the EMA should also be rising.
    assert series[-1] > series[0]
    # EMA of a monotonic ramp lags below the latest price.
    assert series[-1] < values[-1]


def test_rsi_all_gains_is_high():
    rising = [float(i) for i in range(1, 30)]
    r = rsi(rising, 14)
    assert r is not None and r > 90  # only gains -> RSI near 100


def test_rsi_all_losses_is_low():
    falling = [float(i) for i in range(30, 1, -1)]
    r = rsi(falling, 14)
    assert r is not None and r < 10


def test_rsi_needs_enough_data():
    assert rsi([1, 2, 3], 14) is None


def test_stochastic_range():
    n = 30
    highs = [10 + (i % 5) for i in range(n)]
    lows = [5 + (i % 3) for i in range(n)]
    closes = [7 + (i % 4) for i in range(n)]
    res = stochastic(highs, lows, closes, 14, 3)
    assert res is not None
    k, d = res
    assert 0 <= k <= 100
    assert 0 <= d <= 100
