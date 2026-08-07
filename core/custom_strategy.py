"""
The client's CUSTOM strategy: ZigZag + Stochastic + Keltner Channel.

Rules as specified by the client:
  * Keltner middle line (EMA) is the trigger: BUY (call) once the SECOND candle
    closes above the middle line and it's a green candle; SELL (put) on the
    mirror — second candle closes below the middle line on a red candle.
  * All THREE indicators must line up (Keltner cross + Stochastic + ZigZag).
  * ZigZag: wait 3 candles to confirm the move (this also avoids ZigZag's
    repaint problem — we read its leg only from confirmed, older pivots).

Settings from the client:
  ZigZag (5, 13, 3)  -> depth 5, deviation 13, backstep 3
  Stochastic (14, 3, 3) -> %K 14, %K slowing 3, %D 3
  Keltner (21, 9, 5) -> EMA 21 midline, ATR 9, band multiplier 5

Anything scale-dependent (the ZigZag deviation in particular) is exposed as a
setting so we can calibrate it against the client's actual chart / demo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .indicators import ema_series
from .strategy import Candle, Direction, Signal


@dataclass
class CustomSettings:
    # Keltner Channel
    keltner_ema: int = 21
    keltner_atr: int = 9
    keltner_mult: float = 5.0
    # Stochastic (with %K slowing)
    stoch_k: int = 14
    stoch_slow: int = 3
    stoch_d: int = 3
    # Midline-cross confirmation: how many consecutive candles beyond the mid.
    confirm_candles: int = 2          # "the second candlestick above the middle line"
    # ZigZag
    zigzag_deviation: float = 0.00013  # price move that defines a swing (~1.3 pips)
    zigzag_confirm: int = 3            # "wait three candlesticks for movement"
    # Gate
    require_all: bool = True           # all three must agree


# ----------------------------------------------------------------------------
# Indicator helpers
# ----------------------------------------------------------------------------
def _atr(highs, lows, closes, period: int) -> Optional[float]:
    """Average True Range over `period` (simple average of true ranges)."""
    n = len(closes)
    if n < period + 1:
        return None
    trs = []
    for i in range(n - period, n):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    return sum(trs) / period


def _stochastic_slow(highs, lows, closes, k_period, slowing, d_period):
    """
    Full stochastic: raw %K smoothed by `slowing`, then %D = SMA of that.
    Returns (%K_slow, %D) or None.
    """
    n = len(closes)
    need = k_period + slowing + d_period
    if n < need:
        return None

    def raw_k(idx):
        window_h = max(highs[idx - k_period + 1: idx + 1])
        window_l = min(lows[idx - k_period + 1: idx + 1])
        if window_h == window_l:
            return 50.0
        return (closes[idx] - window_l) / (window_h - window_l) * 100.0

    # Smoothed %K for the last (d_period) points, each an SMA of `slowing` raw %K.
    k_slow_series = []
    for j in range(d_period):
        idx_end = n - 1 - j
        vals = [raw_k(idx_end - s) for s in range(slowing)]
        k_slow_series.append(sum(vals) / slowing)
    k_now = k_slow_series[0]
    d_now = sum(k_slow_series) / len(k_slow_series)
    return k_now, d_now


def zigzag_direction(highs, lows, deviation: float, confirm: int) -> int:
    """
    Confirmed ZigZag leg direction: +1 up, -1 down, 0 undecided.

    Threshold-reversal ZigZag: track a running extreme; when price reverses from
    it by at least `deviation`, a pivot is set and the leg flips. To avoid
    repaint we evaluate only up to `confirm` candles back, so the current leg is
    already settled and won't change under the live candles.
    """
    n = len(highs)
    end = n - confirm
    if end < 2:
        return 0

    direction = 0          # current leg
    pivot = lows[0]
    for i in range(1, end):
        if direction >= 0:
            # In an up (or unknown) leg: track the high; flip down on a drop.
            if highs[i] > pivot:
                pivot = highs[i]
            elif pivot - lows[i] >= deviation:
                direction = -1
                pivot = lows[i]
        if direction <= 0:
            if lows[i] < pivot:
                pivot = lows[i]
            elif highs[i] - pivot >= deviation:
                direction = 1
                pivot = highs[i]
    return direction


# ----------------------------------------------------------------------------
# Strategy
# ----------------------------------------------------------------------------
class CustomStrategy:
    def __init__(self, settings: CustomSettings):
        self.settings = settings

    def evaluate(self, candles: List[Candle]) -> Signal:
        s = self.settings
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        opens = [c.open for c in candles]

        need = max(s.keltner_ema + 2, s.keltner_atr + 1,
                   s.stoch_k + s.stoch_slow + s.stoch_d,
                   s.zigzag_confirm + 3, s.confirm_candles + 1) + 2
        if len(candles) < need:
            return Signal(Direction.NONE, f"warming up ({len(candles)}/{need})")

        mid_series = ema_series(closes, s.keltner_ema)  # Keltner middle line

        # --- 1. Keltner midline cross confirmation ---
        # Need `confirm_candles` consecutive closes on one side, and the candle
        # just before that run on the other side (a fresh cross), plus the
        # trigger candle's colour must match the direction.
        c = s.confirm_candles
        above = all(closes[-k] > mid_series[-k] for k in range(1, c + 1))
        below = all(closes[-k] < mid_series[-k] for k in range(1, c + 1))
        fresh_up = above and closes[-(c + 1)] <= mid_series[-(c + 1)]
        fresh_down = below and closes[-(c + 1)] >= mid_series[-(c + 1)]
        green = closes[-1] > opens[-1]
        red = closes[-1] < opens[-1]

        keltner_up = fresh_up and green
        keltner_down = fresh_down and red
        if not (keltner_up or keltner_down):
            return Signal(Direction.NONE, "no Keltner midline trigger")

        # --- 2. Stochastic agreement ---
        st = _stochastic_slow(highs, lows, closes, s.stoch_k, s.stoch_slow, s.stoch_d)
        if st is None:
            return Signal(Direction.NONE, "stochastic not ready")
        k_now, d_now = st
        stoch_up = k_now > d_now
        stoch_down = k_now < d_now

        # --- 3. ZigZag confirmed leg ---
        zz = zigzag_direction(highs, lows, s.zigzag_deviation, s.zigzag_confirm)
        zz_up = zz > 0
        zz_down = zz < 0

        snap = dict(stoch_k=k_now, stoch_d=d_now, ema_fast=mid_series[-1])

        # --- combine: all three must line up ---
        if keltner_up and (not s.require_all or (stoch_up and zz_up)):
            return Signal(Direction.CALL,
                          f"Keltner cross up + Stoch {k_now:.0f}/{d_now:.0f} + ZigZag up", **snap)
        if keltner_down and (not s.require_all or (stoch_down and zz_down)):
            return Signal(Direction.PUT,
                          f"Keltner cross down + Stoch {k_now:.0f}/{d_now:.0f} + ZigZag down", **snap)

        return Signal(Direction.NONE, "indicators not all aligned", **snap)
