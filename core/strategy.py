"""
Strategy engine: trend-following filter + scalping pull-back entries.

The idea the client described:
  1. Read the current market DIRECTION with a trend filter (fast vs slow EMA).
  2. Only take trades in the direction of that trend.
  3. Enter on a PULL-BACK against the trend that then shows exhaustion
     (RSI / Stochastic leaving oversold/overbought), i.e. buy the dip in an
     uptrend, sell the rally in a downtrend.
  4. Close within the shortest profitable window -> handled by the configurable
     binary-option expiry (3m .. 1h), not by this module.

This module is pure: it takes candles + settings and returns a Signal. All the
thresholds are configurable so they can be tuned live from Telegram.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from .indicators import ema, rsi, stochastic


class Direction(str, Enum):
    CALL = "call"   # bet price will be HIGHER at expiry
    PUT = "put"     # bet price will be LOWER at expiry
    NONE = "none"   # no trade


@dataclass
class Candle:
    """One OHLC candle. `time` is a unix epoch seconds float."""
    time: float
    open: float
    high: float
    low: float
    close: float


@dataclass
class StrategySettings:
    """All tunables. Every field is adjustable at runtime from Telegram."""
    ema_fast: int = 9
    ema_slow: int = 21
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    stoch_k: int = 14
    stoch_d: int = 3
    stoch_oversold: float = 20.0
    stoch_overbought: float = 80.0
    # Require both RSI and Stochastic to agree before entering. Turning this off
    # gives more (lower-quality) signals; on gives fewer, higher-conviction ones.
    require_both: bool = True
    # Minimum EMA separation (as a fraction of price) to consider a trend "real"
    # and avoid trading a flat, choppy market.
    min_trend_strength: float = 0.0


@dataclass
class Signal:
    direction: Direction
    reason: str
    # Snapshot of the indicator values at decision time, for logging / Telegram.
    ema_fast: Optional[float] = None
    ema_slow: Optional[float] = None
    rsi: Optional[float] = None
    stoch_k: Optional[float] = None
    stoch_d: Optional[float] = None


class Strategy:
    """Stateless evaluator — call `evaluate(candles)` each closed candle."""

    def __init__(self, settings: StrategySettings):
        self.settings = settings

    def evaluate(self, candles: List[Candle]) -> Signal:
        s = self.settings
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]

        # Need enough bars for the slowest indicator.
        need = max(s.ema_slow, s.rsi_period + 1, s.stoch_k + s.stoch_d)
        if len(candles) < need:
            return Signal(Direction.NONE, f"warming up ({len(candles)}/{need} candles)")

        ema_f = ema(closes, s.ema_fast)
        ema_s = ema(closes, s.ema_slow)
        rsi_v = rsi(closes, s.rsi_period)
        stoch = stochastic(highs, lows, closes, s.stoch_k, s.stoch_d)

        if ema_f is None or ema_s is None or rsi_v is None or stoch is None:
            return Signal(Direction.NONE, "indicators not ready")

        stoch_k, stoch_d = stoch
        snap = dict(ema_fast=ema_f, ema_slow=ema_s, rsi=rsi_v,
                    stoch_k=stoch_k, stoch_d=stoch_d)

        # --- 1. Trend filter -------------------------------------------------
        price = closes[-1]
        trend_gap = abs(ema_f - ema_s) / price if price else 0.0
        if trend_gap < s.min_trend_strength:
            return Signal(Direction.NONE, "trend too weak / choppy", **snap)

        uptrend = ema_f > ema_s
        downtrend = ema_f < ema_s

        # --- 2. Pull-back exhaustion in the trend direction ------------------
        rsi_oversold = rsi_v <= s.rsi_oversold
        rsi_overbought = rsi_v >= s.rsi_overbought
        stoch_oversold = stoch_k <= s.stoch_oversold
        stoch_overbought = stoch_k >= s.stoch_overbought

        if uptrend:
            # Buy the dip: price pulled back and momentum is oversold.
            osold = (rsi_oversold and stoch_oversold) if s.require_both else (rsi_oversold or stoch_oversold)
            if osold:
                return Signal(Direction.CALL,
                              f"uptrend pullback (RSI {rsi_v:.1f}, %K {stoch_k:.1f})", **snap)

        if downtrend:
            # Sell the rally: price bounced and momentum is overbought.
            obought = (rsi_overbought and stoch_overbought) if s.require_both else (rsi_overbought or stoch_overbought)
            if obought:
                return Signal(Direction.PUT,
                              f"downtrend rally (RSI {rsi_v:.1f}, %K {stoch_k:.1f})", **snap)

        return Signal(Direction.NONE, "no pullback entry", **snap)
