"""
Momentum + Stochastic, taken at support and resistance.

The client's own idea, in his words: "the oscillator added to momentum and when
they both hit snr i could buy/sell". Three things must line up before anything
is bought or sold — the level, the momentum, and the oscillator.

What triggers and what merely agrees
------------------------------------
This is the one real decision in here, and it is not what the sentence says on
first reading. If all three had to HAPPEN on the same candle — momentum crossing
into its zone, the oscillator crossing into its zone, and price arriving at a
level, all within the same sixty seconds — the strategy would fire perhaps once
a week. Rare is the point; never is not.

So the LEVEL is the trigger and the other two are confirmations:

  trigger    price reaches a support or resistance level and is rejected by it
             (the existing sr `bounce` rule, levels and all).
  confirm    momentum is sitting in the top or bottom of its own recent range.
  confirm    Stochastic is overbought or oversold.

All three must point the SAME WAY. Price bouncing off support is a CALL, so
momentum must be at the bottom of its range and Stochastic oversold; a bounce
off support while momentum sits at the top of its range is the market
disagreeing with itself and is exactly the trade this is meant to skip.

Everything here is composed from the modules that already exist rather than
re-implemented: SrStrategy finds and tests the levels, MomentumStrategy places
the momentum bands. A second copy of "where is support" would drift away from
the first one the moment either is tuned.

★ Momentum is used in its `require_cross=False` form on purpose. As its own
strategy it fires only on the candle that ARRIVES at the edge, so that a value
hanging above the line for six bars is not six overlapping bets. Here it is not
the trigger — it is being asked "is momentum stretched right now?", which is a
state, not an event. Demanding the cross as well would put us back to needing
three coincidences on one candle.

Two entries on the dropdown
---------------------------
  momentum_sr      all three must agree. The strictest, and the rarest.
  momentum_sr_any  the level plus EITHER confirmation. Fires several times more
                   often, on a weaker claim.

Both are offered because "how often does it trade" is the thing that decides
whether a strategy can ever be judged. A rule that produces nine trades a month
cannot be told apart from luck, however good the nine look — see docs/RESULTS.md
for why every number on this project carries its sample size.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .indicators import stochastic
from .momentum_strategy import MomentumSettings, MomentumStrategy
from .sr_strategy import SrSettings, SrStrategy
from .strategy import Candle, Direction, Signal


@dataclass
class MomentumSrSettings:
    sr: SrSettings = field(
        default_factory=lambda: SrSettings(mode="bounce"))

    # See the module note: state, not event.
    momentum: MomentumSettings = field(
        default_factory=lambda: MomentumSettings(require_cross=False))

    # Stochastic, the oscillator from his own custom strategy. 14/3 is the
    # platform default and what his chart will be drawing.
    stoch_k: int = 14
    stoch_d: int = 3
    oversold: float = 20.0
    overbought: float = 80.0

    # True  = level + momentum + oscillator.
    # False = level + at least one of the two.
    require_all: bool = True


def _stoch_direction(candles: List[Candle],
                     s: MomentumSrSettings) -> Tuple[Optional[Direction], str]:
    """Which way the oscillator is leaning, and how to say so in the log."""
    values = stochastic([c.high for c in candles], [c.low for c in candles],
                        [c.close for c in candles], s.stoch_k, s.stoch_d)
    if values is None:
        return None, "stochastic not ready"
    k, d = values
    if k <= s.oversold:
        return Direction.CALL, f"stochastic {k:.0f} oversold"
    if k >= s.overbought:
        return Direction.PUT, f"stochastic {k:.0f} overbought"
    return None, f"stochastic {k:.0f} is mid-range"


class MomentumSrStrategy:
    """Stateless evaluator — call `evaluate(candles)` on each closed candle."""

    def __init__(self, settings: MomentumSrSettings):
        self.settings = settings
        self._sr = SrStrategy(settings.sr)
        self._momentum = MomentumStrategy(settings.momentum)

    def evaluate(self, candles: List[Candle]) -> Signal:
        s = self.settings

        # The level is the trigger. No level touched, nothing to confirm — and
        # this is the common case, so it must be cheap and it must say why.
        sr_sig = self._sr.evaluate(candles)
        if sr_sig.direction is Direction.NONE:
            return Signal(Direction.NONE, "no level: " + sr_sig.reason)

        want = sr_sig.direction
        side = "CALL" if want is Direction.CALL else "PUT"

        mom_sig = self._momentum.evaluate(candles)
        mom_agrees = mom_sig.direction is want

        stoch_dir, stoch_note = _stoch_direction(candles, s)
        stoch_agrees = stoch_dir is want

        agree = int(mom_agrees) + int(stoch_agrees)
        need = 2 if s.require_all else 1

        # Named parts rather than a count. "2/3" on the panel does not tell him
        # which one is holding it back, and that is the thing he can act on —
        # it is the difference between "the oscillator never gets there on this
        # pair" and "momentum disagrees with the level".
        parts = [
            f"level {side}",
            ("momentum agrees" if mom_agrees else
             "momentum " + ("disagrees" if mom_sig.direction is not Direction.NONE
                            else "not stretched")),
            (stoch_note if stoch_agrees else "no — " + stoch_note),
        ]

        if agree < need:
            return Signal(Direction.NONE,
                          f"{sr_sig.reason.split(' —')[0]}, but only "
                          f"{agree + 1}/3 line up ({'; '.join(parts)})")

        return Signal(want, f"{agree + 1}/3 agree — {'; '.join(parts)}. "
                            f"{sr_sig.reason}")
