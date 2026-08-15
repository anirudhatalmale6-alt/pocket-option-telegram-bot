"""
Is the bot actually winning, or does it just look like it?

Why this exists
---------------
The panel used to say "100+ trades before it means anything". That number was
invented, and it is badly wrong in the direction that costs money. At a 92%
payout you need 52.08% of trades just to stand still, so the question is never
"is the win rate above 50%" but "is it far enough above 52.08% that a hundred
coin flips could not have produced it by luck".

100 settled trades at 53% gives a 95% confidence interval of 43.3% to 62.5%.
Break-even sits comfortably inside that. The honest reading is "no idea yet" —
but the screen said 53% in green next to "100 trades", which reads as proof.

This is not a theoretical worry on this project. The client has already lost
real money to a small sample read as a result, and the whole reason the bot is
on a practice account is to stop that happening twice.

The other half of the answer matters just as much: HOW MANY more trades would
settle it. A thin edge is not just unproven, it is unprovable in any reasonable
time — 53% against a 52.08% break-even needs something like eleven thousand
trades. A 58% edge needs about three hundred. Telling someone "keep going" is
useless without saying whether "keep going" means an afternoon or a year.
"""

from __future__ import annotations

from math import sqrt
from typing import Optional, Tuple

# 95%. Two-sided, so 1.96.
Z = 1.96

# No decisive word — "ahead" or "behind" — below this many settled trades,
# whatever the interval says.
#
# The interval alone would call 20 straight wins "ahead of break-even", and it
# would be right about the win rate: the odds of that from a break-even bot are
# tiny. But "ahead" on this screen is read as "this works, put money in it", and
# twenty trades says nothing at all about how the strategy behaves in a
# different market hour, or how deep a losing run gets. A number that is
# technically about one thing and read as a verdict on another has to be held
# to the reading.
#
# 50 is a judgement, not a derivation, and it is written here rather than
# buried so it can be argued with.
MIN_DECISIVE = 50


def wilson(wins: int, n: int, z: float = Z) -> Tuple[float, float]:
    """
    Confidence interval for a win rate, as fractions 0..1.

    Wilson rather than the textbook normal interval: at the sample sizes this
    bot produces, and at rates near 50%, the normal one is both too narrow and
    capable of extending past 100%. This one behaves at small n, which is the
    only n that ever matters here.
    """
    if n <= 0:
        return (0.0, 1.0)
    p = wins / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def trades_needed(rate: float, breakeven: float, cap: int = 200000) -> Optional[int]:
    """
    Roughly how many settled trades before `rate` could be called a real edge.

    Assumes the observed rate keeps up exactly, which is the optimistic case —
    so this is a floor, not an estimate. None means "not within `cap`", which
    for anything under about 53% is the truthful answer.
    """
    if rate <= breakeven:
        return None
    n = 50
    while n <= cap:
        if wilson(round(rate * n), n)[0] > breakeven:
            return n
        # Coarse steps on purpose. This is quoted to a human as "about N more
        # trades"; precision here would be false precision.
        n += 50 if n < 2000 else 500
    return None


def verdict(wins: int, losses: int, breakeven_pct: float) -> dict:
    """
    What the panel should say about the record so far.

    `state` is one of:
      none    — nothing settled yet
      unknown — the interval straddles break-even; this is the usual answer
      ahead   — even the pessimistic end of the interval clears break-even
      behind  — even the optimistic end falls short of it

    "unknown" is the important one, and it is deliberately not dressed up as
    encouraging. It is the state a losing bot spends its entire life in.
    """
    n = wins + losses
    be = breakeven_pct / 100.0
    if n <= 0:
        return {"state": "none", "n": 0, "rate": 0.0, "lo": 0.0, "hi": 100.0,
                "breakeven": breakeven_pct, "need": None}

    lo, hi = wilson(wins, n)
    rate = wins / n
    if n < MIN_DECISIVE:
        state = "unknown"
    elif lo > be:
        state = "ahead"
    elif hi < be:
        state = "behind"
    else:
        state = "unknown"

    return {
        "state": state,
        "n": n,
        "rate": round(100.0 * rate, 1),
        "lo": round(100.0 * lo, 1),
        "hi": round(100.0 * hi, 1),
        "breakeven": round(breakeven_pct, 1),
        # Only worth quoting while the answer is still open. Once it is decided
        # either way, "how many more" is not the question any more.
        "need": trades_needed(rate, be) if state == "unknown" else None,
    }
