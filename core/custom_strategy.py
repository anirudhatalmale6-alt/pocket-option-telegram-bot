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
    # NOTE: defaults below are the values that tested best on real EUR/USD in the
    # tuning pass (see tune_custom.py). The one big change from the client's
    # original spec is `fade=True` — on real forex the entry works far better
    # reversed. It's one toggle away from the original (fade=False). Confirm on
    # the demo, since Pocket Option OTC pairs may prefer the non-faded version.

    # Keltner Channel
    keltner_ema: int = 14              # tuned (client mentioned 21; 14 tested better)
    keltner_atr: int = 9
    keltner_mult: float = 5.0
    # Stochastic (with %K slowing)
    stoch_k: int = 14
    stoch_slow: int = 3
    stoch_d: int = 3
    # Midline-cross confirmation: how many consecutive candles beyond the mid.
    confirm_candles: int = 2          # "the second candlestick above the middle line"
    # ZigZag
    zigzag_deviation: float = 0.00010  # price move that defines a swing; calibratable
    zigzag_confirm: int = 3            # "wait three candlesticks for movement"
    # Gate / tuning knobs
    require_all: bool = False          # use min_confirmations instead of forcing both
    # How many of the two confirmations (Stochastic, ZigZag) must agree with the
    # Keltner midline trigger. 2 = all three line up (client's original), 1 = the
    # Keltner trigger plus either confirmation, 0 = Keltner trigger alone.
    min_confirmations: int = 2
    # Fade mode: enter the OPPOSITE direction of the midline trigger. Short-term
    # forex mean-reverts, so this tested far better than the breakout version.
    # This is the one knob that reverses the client's original entry direction.
    fade: bool = True


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

        # --- combine: Keltner trigger + N confirmations ---
        # `require_all` (legacy) forces both confirmations; otherwise use
        # min_confirmations (count of Stochastic/ZigZag that agree with the trigger).
        need_conf = 2 if s.require_all else s.min_confirmations
        base: Optional[Direction] = None
        detail = ""
        if keltner_up:
            agree = int(stoch_up) + int(zz_up)
            if agree >= need_conf:
                base, detail = Direction.CALL, f"Keltner up + {agree}/2 conf (Stoch {k_now:.0f}/{d_now:.0f})"
        elif keltner_down:
            agree = int(stoch_down) + int(zz_down)
            if agree >= need_conf:
                base, detail = Direction.PUT, f"Keltner down + {agree}/2 conf (Stoch {k_now:.0f}/{d_now:.0f})"

        if base is None:
            return Signal(Direction.NONE, "indicators not aligned", **snap)

        # Fade mode flips the direction (mean-reversion test).
        if s.fade:
            base = Direction.PUT if base is Direction.CALL else Direction.CALL
            detail = "FADE " + detail
        return Signal(base, detail, **snap)
