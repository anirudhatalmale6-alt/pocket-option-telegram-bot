"""
A complete, working strategy in about ten lines — copy this file to start one.

It trades a plain EMA cross: when the fast average crosses above the slow one,
bet up; below, bet down. Deliberately simple, because the point of this file is
to show the SHAPE of a strategy, not to be a good one.

It is not a good one. An EMA cross on 60-second binaries has no measured edge —
run `python honest_backtest.py` and check the confidence interval against the
break-even line before you let anything here near real money.
"""

from core.indicators import ema_series

NAME = "Example — EMA cross (demo of the format)"
ORDER = 900          # sits at the bottom of the dropdown

FAST, SLOW = 9, 21


def evaluate(candles):
    # Need enough history for the slow average, plus one bar back to compare.
    if len(candles) < SLOW + 2:
        return None

    closes = [c.close for c in candles]
    # ema() gives just the newest value; ema_series() gives the whole line,
    # which is what you need to see a CROSS rather than a level.
    fast, slow = ema_series(closes, FAST), ema_series(closes, SLOW)
    if len(fast) < 2 or len(slow) < 2:
        return None

    now_above = fast[-1] > slow[-1]
    was_above = fast[-2] > slow[-2]

    # Only the candle where the relationship FLIPS is a signal. Without this,
    # every candle in a long trend fires again and re-enters the same trade.
    if now_above and not was_above:
        return "call", f"EMA{FAST} crossed above EMA{SLOW}"
    if was_above and not now_above:
        return "put", f"EMA{FAST} crossed below EMA{SLOW}"
    return None
