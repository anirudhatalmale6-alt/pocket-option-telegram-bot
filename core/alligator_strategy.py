"""
The client's second CUSTOM strategy: Bill Williams Alligator + RSI.

Client's settings (as described):
  Alligator:
    * Jaw  : period 10, shift 5   -> this is the "yellow jaws line" we trade off
    * Teeth: period 8,  shift 5   -> UNCHECKED (not used in the signal)
    * Lips : period 3,  shift 1
    (Bill Williams' Alligator lines are SMMA / smoothed moving averages of the
     median price (high+low)/2, plotted forward by their shift.)
  RSI:
    * default period 14; client "unchecked the first and third box", i.e. only
      the middle reference matters -> we use the RSI 50 midline as the trigger,
      not the 70/30 overbought/oversold bands.

Client's entry rules (as described):
  1. Wait until the SECOND candle crosses BOTH the RSI (midline) and the yellow
     jaws line, in the direction the momentum is going.
  2. Confirm the momentum is going the same way on the 2nd, 4th and 6th candle.

Reading of the rules, made explicit and tunable:
  * "second candle crosses the jaws line"  -> two consecutive candles close on
    the same side of the jaw, and the candle before that run was on the other
    side (a fresh cross that has held for the second candle).
  * "crosses the RSI"                       -> RSI is on the same side of its 50
    midline as the price is of the jaw.
  * "momentum on the 2nd/4th/6th candle"    -> those alternate candles are moving
    the same way (close rising for a CALL, falling for a PUT). How many of the
    three must agree is exposed as `min_momentum`.

Everything scale/threshold dependent is a setting so it can be calibrated against
the client's real demo chart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .indicators import rsi as rsi_latest
from .strategy import Candle, Direction, Signal


@dataclass
class AlligatorSettings:
    # --- Alligator (SMMA of median price, shifted forward) ---
    jaw_period: int = 10
    jaw_shift: int = 5
    lips_period: int = 3
    lips_shift: int = 1
    # Teeth is "unchecked" per the client, so it is not part of the signal.
    # Kept here only so the numbers from the brief are recorded.
    teeth_period: int = 8
    teeth_shift: int = 5
    use_lips_filter: bool = False   # optionally also require price beyond the lips

    # --- RSI ---
    rsi_period: int = 14
    rsi_midline: float = 50.0

    # --- Entry shaping ---
    confirm_candles: int = 2                       # "the second candle" holds the cross
    momentum_positions: Tuple[int, ...] = (2, 4, 6)  # candles to check momentum on
    min_momentum: int = 2                          # how many of them must agree

    # Fade mode: take the OPPOSITE side of the trigger. On real short-term forex
    # the breakout reading tended to mean-revert (see the ZigZag strategy tuning),
    # so this is exposed as a one-toggle test. Default off = client's intent.
    fade: bool = False


# ---------------------------------------------------------------------------
# Smoothed moving average (Wilder / Bill Williams SMMA)
# ---------------------------------------------------------------------------
def smma(values: List[float], period: int) -> List[float]:
    """
    Smoothed moving average aligned to `values` (oldest first).

    Entries before enough data is available are None. The seed at index
    period-1 is a simple average; from then on it is the SMMA recursion:
        SMMA_i = (SMMA_{i-1} * (period - 1) + value_i) / period
    """
    n = len(values)
    out: List[Optional[float]] = [None] * n
    if period <= 0 or n < period:
        return out  # type: ignore[return-value]
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out  # type: ignore[return-value]


class AlligatorStrategy:
    def __init__(self, settings: AlligatorSettings):
        self.settings = settings

    def evaluate(self, candles: List[Candle]) -> Signal:
        s = self.settings
        n = len(candles)
        closes = [c.close for c in candles]
        medians = [(c.high + c.low) / 2.0 for c in candles]

        max_pos = max(s.momentum_positions) if s.momentum_positions else 0
        need = max(
            s.jaw_period + s.jaw_shift + s.confirm_candles + 1,
            s.lips_period + s.lips_shift + 1,
            s.rsi_period + 3,
            max_pos + 2,
        ) + 2
        if n < need:
            return Signal(Direction.NONE, f"warming up ({n}/{need})")

        jaw = smma(medians, s.jaw_period)
        lips = smma(medians, s.lips_period)
        last = n - 1

        # Shifted line value visible AT candle index j (line plotted forward).
        def jaw_at(j: int) -> Optional[float]:
            k = j - s.jaw_shift
            return jaw[k] if 0 <= k < n else None

        def lips_at(j: int) -> Optional[float]:
            k = j - s.lips_shift
            return lips[k] if 0 <= k < n else None

        c = s.confirm_candles
        # Need the jaw value at every candle in the confirmation window + the one
        # just before it (to prove a fresh cross).
        jaws = [jaw_at(last - k) for k in range(0, c + 1)]
        if any(v is None for v in jaws):
            return Signal(Direction.NONE, "alligator not ready")

        above = all(closes[last - k] > jaws[k] for k in range(0, c))
        below = all(closes[last - k] < jaws[k] for k in range(0, c))
        fresh_up = above and closes[last - c] <= jaws[c]     # candle before the run was below
        fresh_down = below and closes[last - c] >= jaws[c]

        # --- RSI midline: current side, and that it recently crossed it ---
        r_now = rsi_latest(closes[: last + 1], s.rsi_period)
        r_prev = rsi_latest(closes[:last], s.rsi_period)
        if r_now is None or r_prev is None:
            return Signal(Direction.NONE, "rsi not ready")
        rsi_up = r_now > s.rsi_midline
        rsi_down = r_now < s.rsi_midline

        # --- momentum on the 2nd / 4th / 6th candle (alternate candles) ---
        def momentum(sign: int) -> int:
            agree = 0
            for p in s.momentum_positions:
                a = last - (p - 1)   # the p-th candle back (2nd candle = last-1)
                b = a - 1
                if b < 0:
                    continue
                diff = closes[a] - closes[b]
                if (sign > 0 and diff > 0) or (sign < 0 and diff < 0):
                    agree += 1
            return agree

        snap = dict(rsi=r_now, ema_fast=jaws[0])

        # Optional lips filter: price also beyond the (faster) lips line.
        lips_now = lips_at(last)
        lips_ok_up = (not s.use_lips_filter) or (lips_now is not None and closes[last] > lips_now)
        lips_ok_down = (not s.use_lips_filter) or (lips_now is not None and closes[last] < lips_now)

        base: Optional[Direction] = None
        detail = ""
        if fresh_up and rsi_up and lips_ok_up:
            mom = momentum(+1)
            if mom >= s.min_momentum:
                base = Direction.CALL
                detail = f"jaws cross up + RSI {r_now:.0f}>50 + mom {mom}/{len(s.momentum_positions)}"
        elif fresh_down and rsi_down and lips_ok_down:
            mom = momentum(-1)
            if mom >= s.min_momentum:
                base = Direction.PUT
                detail = f"jaws cross down + RSI {r_now:.0f}<50 + mom {mom}/{len(s.momentum_positions)}"

        if base is None:
            return Signal(Direction.NONE, "no aligned Alligator/RSI trigger", **snap)

        if s.fade:
            base = Direction.PUT if base is Direction.CALL else Direction.CALL
            detail = "FADE " + detail
        return Signal(base, detail, **snap)
