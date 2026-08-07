"""
Pure trend-following strategies.

The client's insight: drop the fiddly pull-back logic and just "trade the trend"
on longer windows. This module offers three simple, robust trend approaches that
all share one idea — figure out which way price is actually moving, then bet it
keeps going over the next (longer) expiry. No martingale, no scalping.

Modes:
  * "linreg"   – fit a straight TREND LINE through the last N closes (least
                 squares) and trade the direction of its slope. This is the
                 literal "trend line" the client asked about.
  * "ema"      – classic trend filter: fast EMA above slow EMA and rising = up.
  * "donchian" – trend BREAKOUT: price closing above the recent high (or below
                 the recent low) signals a new trend leg.

Each returns the same Signal type the pull-back strategy uses, so the trader and
backtester treat them identically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .indicators import ema, ema_series
from .strategy import Candle, Direction, Signal


@dataclass
class TrendSettings:
    mode: str = "linreg"           # linreg | ema | donchian
    lookback: int = 20             # window for the trend line / channel
    ema_fast: int = 9
    ema_slow: int = 21
    # Minimum slope as a fraction of price per candle to call a trend "real"
    # (filters flat, sideways chop). e.g. 0.00005 = 0.005% per candle.
    min_slope: float = 0.00003
    donchian_period: int = 20


def _linreg_slope(values: List[float]) -> float:
    """Least-squares slope of `values` against index 0..n-1 (price units/candle)."""
    n = len(values)
    if n < 2:
        return 0.0
    xbar = (n - 1) / 2.0
    ybar = sum(values) / n
    num = 0.0
    den = 0.0
    for i, y in enumerate(values):
        dx = i - xbar
        num += dx * (y - ybar)
        den += dx * dx
    return num / den if den else 0.0


class TrendStrategy:
    def __init__(self, settings: TrendSettings):
        self.settings = settings

    def evaluate(self, candles: List[Candle]) -> Signal:
        s = self.settings
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]

        need = max(s.lookback, s.ema_slow + 2, s.donchian_period + 1)
        if len(candles) < need:
            return Signal(Direction.NONE, f"warming up ({len(candles)}/{need})")

        if s.mode == "linreg":
            return self._linreg(closes)
        if s.mode == "ema":
            return self._ema(closes)
        if s.mode == "donchian":
            return self._donchian(highs, lows, closes)
        return Signal(Direction.NONE, f"unknown trend mode '{s.mode}'")

    # ---- linear-regression trend line ----
    def _linreg(self, closes: List[float]) -> Signal:
        s = self.settings
        window = closes[-s.lookback:]
        slope = _linreg_slope(window)
        price = closes[-1]
        slope_pct = slope / price if price else 0.0
        if slope_pct > s.min_slope:
            return Signal(Direction.CALL, f"trend line up ({slope_pct*100:.3f}%/candle)")
        if slope_pct < -s.min_slope:
            return Signal(Direction.PUT, f"trend line down ({slope_pct*100:.3f}%/candle)")
        return Signal(Direction.NONE, "trend line flat")

    # ---- EMA trend ----
    def _ema(self, closes: List[float]) -> Signal:
        s = self.settings
        fast_series = ema_series(closes, s.ema_fast)
        ema_f = fast_series[-1]
        ema_s = ema(closes, s.ema_slow)
        rising = fast_series[-1] > fast_series[-2]
        if ema_f > ema_s and rising:
            return Signal(Direction.CALL, "EMA uptrend", ema_fast=ema_f, ema_slow=ema_s)
        if ema_f < ema_s and not rising:
            return Signal(Direction.PUT, "EMA downtrend", ema_fast=ema_f, ema_slow=ema_s)
        return Signal(Direction.NONE, "no clear EMA trend", ema_fast=ema_f, ema_slow=ema_s)

    # ---- Donchian breakout ----
    def _donchian(self, highs: List[float], lows: List[float], closes: List[float]) -> Signal:
        p = self.settings.donchian_period
        prior_high = max(highs[-p - 1:-1])
        prior_low = min(lows[-p - 1:-1])
        close = closes[-1]
        if close > prior_high:
            return Signal(Direction.CALL, f"breakout above {p}-bar high")
        if close < prior_low:
            return Signal(Direction.PUT, f"breakdown below {p}-bar low")
        return Signal(Direction.NONE, "inside channel")
