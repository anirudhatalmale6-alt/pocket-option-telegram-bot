"""
Momentum(10) — trade it when it reaches the top or the bottom of its range.

The client's words: "momentum, 10, line and every time it hits the top or
bottom it gives a 1 min trade". The period and the 1-minute expiry are exactly
as asked. Two things in that sentence needed a decision rather than a setting,
and they are what this file is really about.

1. What "the top" IS
--------------------
RSI and Stochastic are drawn on a fixed 0-100 scale, so "overbought" can be a
number everybody agrees on: 70. Momentum has no such scale. In the form Pocket
Option draws it — close divided by the close `period` bars ago, times 100 — it
sits near 100 and wanders a few hundredths either side. How far is "a lot"
depends on the pair, the candle size, and the hour: EUR/USD at 1-minute in the
Asian session barely leaves 99.99-100.01, while GBP/JPY on a news release
swings ten times that. A fixed threshold would therefore trade constantly on
one pair and never on another, which is exactly the failure the client would
see as "it just doesn't do anything".

So the top and the bottom are read off the indicator's OWN recent history: the
highest and lowest `band_percentile` percent of the last `band_lookback` values
(default: the outer 10% of the last 100 bars). That is the same shape as the
line he sees on his chart — the point where the wiggle reaches the edge of the
range it has been keeping to — and it is self-scaling, so it means the same
thing on every pair he watches.

★ The bands are measured from the bars BEFORE the one being judged. If the
current value were included in its own band, a new extreme would define the
edge it is being tested against, and the strategy could never quite reach it.

2. Which WAY the trade goes
---------------------------
"It gives a trade" does not say CALL or PUT, and the two readings are opposite
bets:

  reversal (default)  the push has run out — the market is stretched furthest
                      from where it was ten bars ago, so bet it comes back.
                      Top -> PUT, bottom -> CALL.

  follow              the push is real and continuing. Top -> CALL,
                      bottom -> PUT.

Both are on the dropdown as separate entries, for the same reason `sr` and
`sr_fade` are: they are opposite trades, and one option called "Momentum" would
hide which of them is running. Reversal is the default because that is the
conventional reading of an oscillator at its extreme, and because on this
project the mean-reversion side has consistently been the better half of the
pair. That is a starting point, not a claim — see momentum_backtest.py, which
measures both against the break-even line the payout sets.

A note on the chart drawing: "line" is how the indicator is drawn on his own
screen, line versus histogram. It changes nothing about the numbers, and the
bot computes Momentum itself from the candles rather than reading anything off
the chart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .indicators import momentum_series, percentile
from .strategy import Candle, Direction, Signal


@dataclass
class MomentumSettings:
    # Bars back that Momentum compares against. The client asked for 10.
    period: int = 10

    # How much history places the top and bottom lines. 100 one-minute bars is
    # a little under two hours — long enough to know what this pair's normal
    # range looks like, short enough to still be describing today's market.
    band_lookback: int = 100

    # The outer slice of that history which counts as "the top" / "the bottom",
    # in percent. 10 means the highest and lowest tenth. Smaller = rarer, more
    # extreme signals; larger = more trades, each a weaker claim.
    band_percentile: float = 10.0

    # Fire on the bar that ENTERS the zone, not on every bar it stays there.
    # Off, a Momentum that hangs above the line for six bars is six trades on
    # one move — and with a 1-minute expiry they overlap into what is really a
    # single, six-times-larger bet.
    require_cross: bool = True

    # False = reversal (top -> PUT). True = follow the push (top -> CALL).
    fade: bool = False


# Fewest past Momentum values that may define a top and a bottom. With ten
# values the "outer 10%" is one sample, so a single spike sets the line the next
# bar is judged against and the strategy fires on noise. Thirty is still short
# of the 100 it normally has (the bot fetches 200 candles on every look) — it is
# the floor, not the target.
MIN_HISTORY = 30


@dataclass
class Bands:
    upper: float
    lower: float
    values: int          # how many past values the bands were measured from


def bands(momentum_values: List[float], settings: MomentumSettings) -> Optional[Bands]:
    """
    Where the top and bottom lines sit, from history only.

    `momentum_values` must already EXCLUDE the value being judged. Returns None
    if there is not enough history, or if the market has been so flat that the
    top and the bottom are the same price — on which more below.
    """
    history = momentum_values[-settings.band_lookback:]
    if len(history) < MIN_HISTORY:
        return None
    upper = percentile(history, 100.0 - settings.band_percentile)
    lower = percentile(history, settings.band_percentile)
    if upper is None or lower is None or upper <= lower:
        # Dead flat. Pocket Option's OTC pairs really do go flat between ticks,
        # and a zero-width band would make every single bar simultaneously a
        # top and a bottom — the strategy trading its hardest exactly where
        # there is nothing to trade.
        return None
    return Bands(upper, lower, len(history))


class MomentumStrategy:
    """Stateless evaluator — call `evaluate(candles)` on each closed candle."""

    def __init__(self, settings: MomentumSettings):
        self.settings = settings

    def evaluate(self, candles: List[Candle]) -> Signal:
        s = self.settings
        closes = [c.close for c in candles]
        # `period` bars to produce the first Momentum value, then MIN_HISTORY
        # values to measure a band from, plus the one being judged.
        need = s.period + MIN_HISTORY + 1
        if len(closes) < need:
            return Signal(Direction.NONE, f"warming up ({len(closes)}/{need} candles)")

        series = momentum_series(closes, s.period)
        if len(series) < MIN_HISTORY + 1:
            return Signal(Direction.NONE, "momentum not ready")

        now, prev = series[-1], series[-2]
        band = bands(series[:-1], s)
        if band is None:
            return Signal(Direction.NONE,
                          "momentum is flat — no top or bottom to reach yet")

        # How far through its own range the line is sitting, 0-100. Purely for
        # the log: "momentum 100.02" means nothing on its own, and this is the
        # number that says whether that is high or low FOR THIS PAIR.
        span = band.upper - band.lower
        pos = (now - band.lower) / span * 100.0 if span else 50.0

        at_top = now >= band.upper
        at_bottom = now <= band.lower
        if s.require_cross:
            # Only the bar that arrives at the line, not the ones that linger.
            at_top = at_top and prev < band.upper
            at_bottom = at_bottom and prev > band.lower

        if not at_top and not at_bottom:
            return Signal(Direction.NONE,
                          f"momentum {now:.3f} is {pos:.0f}% through its range "
                          f"({band.lower:.3f}–{band.upper:.3f}, last "
                          f"{band.values} bars)")

        edge = "top" if at_top else "bottom"
        line = band.upper if at_top else band.lower
        base = Direction.PUT if at_top else Direction.CALL      # reversal reading
        detail = (f"momentum {now:.3f} reached the {edge} of its range "
                  f"({line:.3f}, from the last {band.values} bars) — "
                  f"was {prev:.3f}")

        if s.fade:
            base = Direction.CALL if base is Direction.PUT else Direction.PUT
            detail = "FOLLOW: " + detail
        return Signal(base, detail)


def describe(settings: MomentumSettings) -> Tuple[str, str]:
    """(one-line summary, direction it takes) — used by the panel's report."""
    way = ("follows the push (top = CALL)" if settings.fade
           else "bets on the turn (top = PUT)")
    return (f"Momentum {settings.period}, top/bottom = outer "
            f"{settings.band_percentile:.0f}% of the last "
            f"{settings.band_lookback} bars", way)
