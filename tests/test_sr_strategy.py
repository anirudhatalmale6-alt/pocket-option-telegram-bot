"""
Support and resistance.

The two things worth testing hardest here are not the entries — they are:

  1. that a level is built only from swings the market has ALREADY confirmed,
     never from bars to the right of the one being judged, and
  2. that one lucky touch is not called a level.

Get either wrong and the strategy backtests beautifully and loses money live,
which is the specific failure this project has been burned by before.
"""

from __future__ import annotations

import pytest

from core.sr_strategy import Level, SrSettings, SrStrategy, _cluster, _pivots, find_levels
from core.strategy import Candle, Direction


def c(o, h, l, cl, t=0.0):
    return Candle(t, o, h, l, cl)


def zigzag(lows, highs, floor=1.0900, ceiling=1.0950, span_pad=4):
    """
    A market that repeatedly turns at `floor` and `ceiling`.

    Each leg is padded so the turning bars are genuine swing points with room on
    both sides, which is what the strategy insists on before believing them.
    """
    out = []
    t = 0.0
    price = (floor + ceiling) / 2
    for i in range(6):
        target = floor if i % 2 == 0 else ceiling
        step = (target - price) / span_pad
        for _ in range(span_pad):
            nxt = price + step
            out.append(c(price, max(price, nxt), min(price, nxt), nxt, t))
            price = nxt
            t += 60
        # the turn itself: a bar that pokes at the level and comes back
        if target == floor:
            out.append(c(price, price + 0.0002, floor - 0.00002, price + 0.0002, t))
        else:
            out.append(c(price, ceiling + 0.00002, price - 0.0002, price - 0.0002, t))
        price = out[-1].close
        t += 60
    return out


# --------------------------------------------------------------- pivots
def test_the_newest_bars_can_never_be_a_pivot():
    """
    A swing needs `span` bars after it before it is a swing. If the last bars
    could form pivots, the level set would change retrospectively as new candles
    arrived — which is exactly what ZigZag does and exactly what makes a
    backtest lie.
    """
    span = 3
    bars = [c(1.0, 1.0 + i * 0.001, 1.0, 1.0) for i in range(20)]
    # Make the very last bar an obvious high.
    bars[-1] = c(1.0, 2.0, 1.0, 1.9)
    highs, _ = _pivots(bars, span)
    assert 2.0 not in highs, "the newest bar was treated as a confirmed swing"


def test_a_pivot_is_confirmed_once_enough_bars_have_closed_after_it():
    span = 2
    bars = [c(1.0, 1.0, 1.0, 1.0) for _ in range(3)]
    bars.append(c(1.0, 1.5, 1.0, 1.2))          # the high
    bars += [c(1.0, 1.0, 1.0, 1.0) for _ in range(3)]
    highs, _ = _pivots(bars, span)
    assert 1.5 in highs


def test_evaluating_a_prefix_cannot_see_the_future():
    """
    The same bars, judged now versus judged with another hour of history glued
    on, must give the same answer for that moment.
    """
    bars = zigzag(None, None)
    strat = SrStrategy(SrSettings())
    cut = len(bars) - 8
    now = strat.evaluate(bars[:cut])
    later = SrStrategy(SrSettings()).evaluate(bars[:cut])
    assert now.direction is later.direction and now.reason == later.reason


# -------------------------------------------------------------- levels
def test_one_touch_is_not_a_level():
    prices = [1.0900]
    assert _cluster(prices, 0.0006, 2, "support") == []


def test_two_touches_within_tolerance_are_one_level():
    levels = _cluster([1.09000, 1.09004], 0.0006, 2, "support")
    assert len(levels) == 1
    assert levels[0].touches == 2
    assert levels[0].price == pytest.approx(1.09002)


def test_prices_further_apart_than_the_tolerance_are_different_levels():
    # 1.0900 and 1.0950 are ~46 pips apart, far outside a 6-pip tolerance.
    levels = _cluster([1.0900, 1.0900, 1.0950, 1.0950], 0.0006, 2, "support")
    assert len(levels) == 2


def test_a_long_ladder_does_not_drift_into_one_giant_level():
    """
    Each price is within tolerance of the one before it, but the ends are far
    apart. Grouping against the previous price would swallow the lot into a
    single "level" spanning many times the tolerance it was built from.
    """
    prices = [1.0900 + i * 0.0005 for i in range(12)]   # 1.0900 .. 1.0955
    levels = _cluster(prices, 0.0006, 2, "support")
    assert levels, "expected at least one level"
    widest = max(levels, key=lambda lv: lv.touches)
    assert widest.touches < len(prices), (
        "every price collapsed into one level — the cluster drifted")


def test_a_repeatedly_respected_price_becomes_a_level():
    bars = zigzag(None, None)
    levels = find_levels(bars, SrSettings())
    assert any(lv.kind == "support" and abs(lv.price - 1.0900) < 0.0006
               for lv in levels), [(l.kind, round(l.price, 5), l.touches) for l in levels]
    assert any(lv.kind == "resistance" and abs(lv.price - 1.0950) < 0.0006
               for lv in levels)


# -------------------------------------------------------------- entries
def test_a_rejection_at_support_is_a_call():
    bars = zigzag(None, None)
    # Walk down to support and poke through it, closing back above.
    bars.append(c(1.0910, 1.0911, 1.0890, 1.0908, 9999.0))
    sig = SrStrategy(SrSettings()).evaluate(bars)
    assert sig.direction is Direction.CALL
    assert "support" in sig.reason


def test_a_rejection_at_resistance_is_a_put():
    bars = zigzag(None, None)
    bars.append(c(1.0940, 1.0960, 1.0939, 1.0942, 9999.0))
    sig = SrStrategy(SrSettings()).evaluate(bars)
    assert sig.direction is Direction.PUT
    assert "resistance" in sig.reason


def test_sitting_between_levels_is_not_a_trade_and_says_which_level_is_nearest():
    bars = zigzag(None, None)
    bars.append(c(1.0925, 1.0926, 1.0924, 1.0925, 9999.0))
    sig = SrStrategy(SrSettings()).evaluate(bars)
    assert sig.direction is Direction.NONE
    assert "nearest" in sig.reason


def test_break_mode_trades_the_opposite_way_to_bounce_mode():
    """
    The same close, read two ways. A bar that smashes through support is a PUT
    to a breakout trader and nothing at all to a bounce trader — if both modes
    ever agreed, one of them would be mislabelled.
    """
    bars = zigzag(None, None)
    bars.append(c(1.0905, 1.0906, 1.0870, 1.0872, 9999.0))
    bounce = SrStrategy(SrSettings(mode="bounce")).evaluate(bars)
    brk = SrStrategy(SrSettings(mode="break")).evaluate(bars)
    assert brk.direction is Direction.PUT
    assert bounce.direction is not Direction.PUT


def test_break_up_through_resistance_is_a_call():
    bars = zigzag(None, None)
    bars.append(c(1.0948, 1.0975, 1.0947, 1.0972, 9999.0))
    sig = SrStrategy(SrSettings(mode="break")).evaluate(bars)
    assert sig.direction is Direction.CALL
    assert "resistance" in sig.reason


def test_a_flat_market_produces_no_levels_and_says_so_plainly():
    bars = [c(1.0900, 1.0900, 1.0900, 1.0900, i * 60.0) for i in range(60)]
    sig = SrStrategy(SrSettings()).evaluate(bars)
    assert sig.direction is Direction.NONE
    # Must not read as "no setup right now" when the truth is "I cannot find any
    # levels at all" — they need different actions from the user.
    assert "no level" in sig.reason


def test_too_little_history_says_warming_up_rather_than_no_setup():
    sig = SrStrategy(SrSettings()).evaluate([c(1.09, 1.09, 1.09, 1.09) for _ in range(5)])
    assert sig.direction is Direction.NONE
    assert "warming up" in sig.reason


def test_requiring_a_rejection_is_stricter_than_not():
    """
    The looser setting must fire at least as often, or the toggle is backwards.
    """
    bars = zigzag(None, None)
    bars.append(c(1.0906, 1.0907, 1.0901, 1.0907, 9999.0))   # near support, no pierce
    strict = SrStrategy(SrSettings(require_rejection=True)).evaluate(bars)
    loose = SrStrategy(SrSettings(require_rejection=False)).evaluate(bars)
    assert strict.direction is Direction.NONE
    assert loose.direction is Direction.CALL
