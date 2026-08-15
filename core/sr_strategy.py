"""
Support and resistance — the client's own idea, traded mechanically.

He said he tried support and resistance by hand and did well with it. This is
that, written down precisely enough for a machine to follow, because "trade off
support and resistance" is not yet a rule: it leaves out how a level is found,
how close counts as touching it, and what the entry actually is.

How a level is found
--------------------
A level is a price the market has already turned at, more than once.

  1. Find the SWING POINTS — a bar whose high is the highest of the `pivot_span`
     bars either side of it (a swing high), or whose low is the lowest (a swing
     low).
  2. Cluster swings that sit within `tolerance` of each other. Two turns at
     1.09140 and 1.09147 are the same level, not two.
  3. Keep a cluster only once it has `min_touches` turns in it. One turn is a
     coincidence; the whole premise of the idea is that the level is being
     respected repeatedly.

★ A swing point is only counted once `pivot_span` bars have CLOSED after it.
That is not a detail — it is the difference between this and the ZigZag
indicator, which redraws its swings when new bars arrive and would let a
backtest place trades using prices that had not happened yet. Nothing in here
ever reads a bar to the right of the one being judged. See docs/RESULTS.md for
why every number on this project is quoted with a sample size and an interval:
a strategy that quietly peeks at the future looks superb and earns nothing.

The two entries
---------------
These are opposite trades and the difference matters more than any setting:

  bounce  price reaches the level and is REJECTED — it pierces support and
          closes back above it. Bet the level holds. CALL at support, PUT at
          resistance.

  break   price closes decisively THROUGH the level after having been on the
          other side. Bet the level has failed. CALL through resistance, PUT
          through support.

A market that respects its levels pays for `bounce`. A market that runs in
trends pays for `break`. They cannot both be right at the same time on the same
pair, and which one wins is a question for the data, not for me — see
sr_backtest.py, which measures both.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .strategy import Candle, Direction, Signal


@dataclass
class SrSettings:
    # How far back to look for levels. 120 one-minute bars is two hours — long
    # enough for a level to have been tested more than once, short enough that
    # it is still the level today's market is trading against.
    lookback: int = 120

    # Bars either side of a swing that must be lower (for a high) before it
    # counts. Bigger = fewer, more serious levels. This also sets how long a
    # swing waits to be confirmed, so it is a lag as well as a filter.
    pivot_span: int = 3

    # How close two prices must be to be the same level, as a fraction of price.
    # 0.0006 on EUR/USD at 1.09 is about 6.5 pips. Also used as "near enough to
    # be touching the level".
    tolerance: float = 0.0006

    # Turns needed before a cluster is called a level.
    min_touches: int = 2

    # bounce = bet the level holds. break = bet it fails.
    mode: str = "bounce"

    # For `bounce`: require the bar to have actually pierced the level and
    # closed back. With this off, merely trading near the level is enough, which
    # is a much weaker claim and fires far more often.
    require_rejection: bool = True

    # Take the opposite side of whatever the rule above says.
    #
    # This exists because it keeps winning on this project. Three of the
    # client's own strategies backtested well below break-even and well above it
    # when reversed, and the same is true here on 5-minute EUR/USD. It is worth
    # being suspicious of: reversing a losing rule is the first thing anyone
    # tries, and a strategy that is 43% is not automatically 57% the other way
    # once the broker's cut is taken. It is reported as its own row with its own
    # sample size and interval, never merged into the honest one.
    fade: bool = False


@dataclass
class Level:
    price: float
    touches: int
    kind: str          # "support" | "resistance"


def _pivots(candles: List[Candle], span: int) -> Tuple[List[float], List[float]]:
    """
    Confirmed swing highs and swing lows.

    Only bars with `span` bars on BOTH sides are considered, so the newest
    `span` bars can never produce a pivot. That is deliberate: a swing that has
    not been confirmed yet is a swing that might not turn out to be one, and
    treating it as fact is how a backtest starts reading the future.

    ★ The comparison is STRICT on the left and loose on the right. That looks
    fussy and is not. With `>=` on both sides, a dead flat stretch of price makes
    every single bar simultaneously a swing high AND a swing low — so a market
    that has never turned once reports a level with fifty touches, and the
    strategy trades hardest exactly where there is nothing to trade. Caught by
    the flat-market test, and it is not hypothetical: data_eurusd_1m.csv is 100%
    flat OHLC, and Pocket Option's OTC pairs go flat between ticks. Strict on one
    side means a swing must be a real turn; loose on the other keeps a genuine
    two-bar plateau top, which is a real shape.
    """
    highs: List[float] = []
    lows: List[float] = []
    if span < 1 or len(candles) < span * 2 + 1:
        return highs, lows
    for i in range(span, len(candles) - span):
        left = candles[i - span: i]
        right = candles[i + 1: i + span + 1]
        c = candles[i]
        if c.high > max(w.high for w in left) and c.high >= max(w.high for w in right):
            highs.append(c.high)
        if c.low < min(w.low for w in left) and c.low <= min(w.low for w in right):
            lows.append(c.low)
    return highs, lows


def _cluster(prices: List[float], tolerance: float, min_touches: int,
             kind: str) -> List[Level]:
    """
    Group prices that are within `tolerance` of each other into levels.

    Walks the sorted list and starts a new cluster whenever the next price is
    further from the CLUSTER'S OWN MEAN than the tolerance allows. Comparing
    against the mean rather than the previous price stops a long ladder of
    barely-separated prices from drifting into one enormous "level" that spans
    far more than the tolerance it was built from.
    """
    levels: List[Level] = []
    if not prices:
        return levels
    group = [prices[0]]
    for p in sorted(prices)[1:]:
        mean = sum(group) / len(group)
        if abs(p - mean) <= tolerance * mean:
            group.append(p)
        else:
            if len(group) >= min_touches:
                levels.append(Level(sum(group) / len(group), len(group), kind))
            group = [p]
    if len(group) >= min_touches:
        levels.append(Level(sum(group) / len(group), len(group), kind))
    return levels


def find_levels(candles: List[Candle], settings: SrSettings) -> List[Level]:
    """Every support and resistance level currently visible. Exposed for tests."""
    window = candles[-settings.lookback:] if settings.lookback else candles
    highs, lows = _pivots(window, settings.pivot_span)
    return (_cluster(highs, settings.tolerance, settings.min_touches, "resistance") +
            _cluster(lows, settings.tolerance, settings.min_touches, "support"))


class SrStrategy:
    """Stateless evaluator — call `evaluate(candles)` on each closed candle."""

    def __init__(self, settings: SrSettings):
        self.settings = settings

    def evaluate(self, candles: List[Candle]) -> Signal:
        s = self.settings
        need = max(s.pivot_span * 2 + 1, 20)
        if len(candles) < need:
            return Signal(Direction.NONE, f"warming up ({len(candles)}/{need} candles)")

        levels = find_levels(candles, s)
        if not levels:
            return Signal(Direction.NONE,
                          f"no level with {s.min_touches}+ touches in the last "
                          f"{min(s.lookback, len(candles))} candles yet")

        now = candles[-1]
        prev = candles[-2]
        band = s.tolerance * now.close

        if s.mode == "break":
            sig = self._break(now, prev, levels, band)
        else:
            sig = self._bounce(now, prev, levels, band)

        if s.fade and sig.direction is not Direction.NONE:
            flipped = (Direction.PUT if sig.direction is Direction.CALL
                       else Direction.CALL)
            return Signal(flipped, "REVERSED: " + sig.reason)
        return sig

    # ------------------------------------------------------------- bounce
    def _bounce(self, now: Candle, prev: Candle, levels: List[Level],
                band: float) -> Signal:
        """Price reached a level and was pushed back. Bet the level holds."""
        s = self.settings
        best: Optional[Tuple[float, Direction, str]] = None

        for lv in levels:
            if lv.kind == "support":
                if s.require_rejection:
                    # Pierced it and closed back above: the rejection is the
                    # signal, and it is a fact about a CLOSED bar rather than a
                    # guess about where this one will finish.
                    hit = now.low <= lv.price and now.close > lv.price
                else:
                    hit = abs(now.low - lv.price) <= band and now.close >= now.open
                if hit:
                    dist = abs(now.close - lv.price)
                    reason = (f"bounced off support {lv.price:.5f} "
                              f"({lv.touches} touches) — low {now.low:.5f}, "
                              f"closed back at {now.close:.5f}")
                    if best is None or dist < best[0]:
                        best = (dist, Direction.CALL, reason)
            else:
                if s.require_rejection:
                    hit = now.high >= lv.price and now.close < lv.price
                else:
                    hit = abs(now.high - lv.price) <= band and now.close <= now.open
                if hit:
                    dist = abs(now.close - lv.price)
                    reason = (f"rejected at resistance {lv.price:.5f} "
                              f"({lv.touches} touches) — high {now.high:.5f}, "
                              f"closed back at {now.close:.5f}")
                    if best is None or dist < best[0]:
                        best = (dist, Direction.PUT, reason)

        if best is None:
            near = min(levels, key=lambda lv: abs(now.close - lv.price))
            return Signal(Direction.NONE,
                          f"price {now.close:.5f} is not at a level — nearest is "
                          f"{near.kind} {near.price:.5f} ({near.touches} touches)")
        return Signal(best[1], best[2])

    # -------------------------------------------------------------- break
    def _break(self, now: Candle, prev: Candle, levels: List[Level],
               band: float) -> Signal:
        """Price closed through a level it had been respecting. Bet it failed."""
        best: Optional[Tuple[float, Direction, str]] = None

        for lv in levels:
            through_up = prev.close <= lv.price and now.close > lv.price + band
            through_down = prev.close >= lv.price and now.close < lv.price - band
            if lv.kind == "resistance" and through_up:
                dist = now.close - lv.price
                reason = (f"broke UP through resistance {lv.price:.5f} "
                          f"({lv.touches} touches) — closed {now.close:.5f}")
                if best is None or dist < best[0]:
                    best = (dist, Direction.CALL, reason)
            elif lv.kind == "support" and through_down:
                dist = lv.price - now.close
                reason = (f"broke DOWN through support {lv.price:.5f} "
                          f"({lv.touches} touches) — closed {now.close:.5f}")
                if best is None or dist < best[0]:
                    best = (dist, Direction.PUT, reason)

        if best is None:
            near = min(levels, key=lambda lv: abs(now.close - lv.price))
            return Signal(Direction.NONE,
                          f"no level broken — price {now.close:.5f}, nearest is "
                          f"{near.kind} {near.price:.5f} ({near.touches} touches)")
        return Signal(best[1], best[2])
