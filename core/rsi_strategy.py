"""
Simple, fast RSI strategy — the client's "RSI 10, 30-second candles, 1-2 min
expiry, OTC pair" idea, kept deliberately minimal.

One indicator, one rule:
  * CALL when RSI comes back UP out of oversold (a dip that's turning).
  * PUT  when RSI comes back DOWN out of overbought (a rally that's turning).
This is a mean-reversion / scalp entry, which is what short fast candles reward.

The candle SIZE (30s) and EXPIRY (1-2 min) are set on the bot, not here — this
module just reads whatever candles it's given, so point it at 30s candles and
it trades 30s candles. `fade` flips the direction for the breakout reading.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .indicators import rsi as rsi_latest
from .strategy import Candle, Direction, Signal


@dataclass
class RsiSettings:
    period: int = 10          # client asked for RSI 10
    oversold: float = 30.0
    overbought: float = 70.0
    # If True, require the RSI to have actually crossed back through the level
    # (prev candle beyond it, this candle inside). If False, fire while it's
    # simply turning inside the zone.
    require_cross: bool = True
    fade: bool = False        # flip direction (breakout reading) — one toggle


class RsiStrategy:
    def __init__(self, settings: RsiSettings):
        self.settings = settings

    def evaluate(self, candles: List[Candle]) -> Signal:
        s = self.settings
        closes = [c.close for c in candles]
        need = s.period + 3
        if len(closes) < need:
            return Signal(Direction.NONE, f"warming up ({len(closes)}/{need})")

        now = rsi_latest(closes, s.period)
        prev = rsi_latest(closes[:-1], s.period)
        if now is None or prev is None:
            return Signal(Direction.NONE, "rsi not ready")

        snap = dict(rsi=now)
        base: Optional[Direction] = None
        detail = ""

        if s.require_cross:
            # Coming back UP through the oversold line, or DOWN through overbought.
            if prev <= s.oversold and now > s.oversold:
                base, detail = Direction.CALL, f"RSI cross up out of {s.oversold:.0f} ({prev:.0f}->{now:.0f})"
            elif prev >= s.overbought and now < s.overbought:
                base, detail = Direction.PUT, f"RSI cross down out of {s.overbought:.0f} ({prev:.0f}->{now:.0f})"
        else:
            # Turning while still inside the zone.
            if now <= s.oversold and now > prev:
                base, detail = Direction.CALL, f"RSI turning up in oversold ({prev:.0f}->{now:.0f})"
            elif now >= s.overbought and now < prev:
                base, detail = Direction.PUT, f"RSI turning down in overbought ({prev:.0f}->{now:.0f})"

        if base is None:
            return Signal(Direction.NONE, "no RSI trigger", **snap)

        if s.fade:
            base = Direction.PUT if base is Direction.CALL else Direction.CALL
            detail = "FADE " + detail
        return Signal(base, detail, **snap)
