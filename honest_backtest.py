"""
Backtest every strategy across every real data file, WITH sample sizes and
confidence intervals — the version that can tell you it does not know.

Why this replaced my earlier numbers
------------------------------------
The first backtests I ran reported figures like "confluence 72.7%". That number
was real, and it was worthless: it came from ELEVEN trades. Eleven coin flips
land on 8 heads about 5% of the time, so 72.7% from 11 trades is entirely
consistent with a strategy that has no edge at all. Quoting it as evidence was
my mistake, and this script exists so that mistake cannot be repeated silently.

Every row here prints the number of decided trades and a 95% Wilson confidence
interval for the true win rate, then compares that interval to the break-even
line (100 / (100 + payout)):

  edge          the whole interval is above break-even  -> genuinely profitable
  no edge       the whole interval is below break-even  -> genuinely losing
  unproven      the interval straddles break-even       -> the data cannot say
  inconclusive  fewer than 100 decided trades           -> not enough evidence

Ties (entry price == expiry price) are excluded from the win rate, because
Pocket Option refunds them; they are counted and shown separately so you can see
how much of the sample they ate.

Run:  python honest_backtest.py
"""

from __future__ import annotations

import csv
import math
import os
from typing import Callable, Dict, List, Tuple

from core.alligator_strategy import AlligatorSettings, AlligatorStrategy
from core.confluence_strategy import ConfluenceSettings, ConfluenceStrategy
from core.custom_strategy import CustomSettings, CustomStrategy
from core.momentum_strategy import MomentumSettings, MomentumStrategy
from core.momentum_sr_strategy import MomentumSrSettings, MomentumSrStrategy
from core.rsi_strategy import RsiSettings, RsiStrategy
from core.sr_strategy import SrSettings, SrStrategy
from core.strategy import Candle, Direction, Strategy, StrategySettings
from core.trend_strategy import TrendSettings, TrendStrategy

HERE = os.path.dirname(os.path.abspath(__file__))

# Real EUR/USD history. The 1-minute file is excluded on purpose: free 1m FX
# data has open == high == low == close on every bar and ~60% of consecutive
# closes identical, so indicators using the bar range are meaningless on it and
# most "trades" are ties. Testing on it would flatter the results, not test them.
FILES: List[Tuple[str, str]] = [
    ("5m", "data_eurusd_5m.csv"),
    ("15m", "data_eurusd_15m_60d.csv"),
    ("30m", "data_eurusd_30m_60d.csv"),
    ("1h", "data_eurusd_1h_6mo.csv"),
]

STRATEGIES: Dict[str, Callable[[], object]] = {
    "confluence": lambda: ConfluenceStrategy(ConfluenceSettings(min_agree=2)),
    "custom": lambda: CustomStrategy(CustomSettings(fade=True)),
    "alligator": lambda: AlligatorStrategy(AlligatorSettings()),
    "rsi": lambda: RsiStrategy(RsiSettings()),
    # Momentum(10) at the top/bottom of its own recent range — the client's
    # latest request. Both readings, for the same reason as sr_bounce/sr_fade:
    # they are opposite bets and picking one for him would hide the choice.
    "momentum_turn": lambda: MomentumStrategy(MomentumSettings()),
    "momentum_follow": lambda: MomentumStrategy(MomentumSettings(fade=True)),
    # The client's own combination. Both strictnesses are measured, because
    # how OFTEN it fires decides whether the win rate can ever mean anything.
    "mom_sr_all3": lambda: MomentumSrStrategy(MomentumSrSettings()),
    "mom_sr_either": lambda: MomentumSrStrategy(MomentumSrSettings(require_all=False)),
    # The client's own idea, both readings of it. They are opposite trades, so
    # they are listed separately rather than one being picked for him.
    "sr_bounce": lambda: SrStrategy(SrSettings(mode="bounce")),
    "sr_break": lambda: SrStrategy(SrSettings(mode="break")),
    "sr_fade": lambda: SrStrategy(SrSettings(mode="bounce", fade=True)),
    "pullback": lambda: Strategy(StrategySettings(
        require_both=False, ema_fast=5, ema_slow=20,
        rsi_oversold=40, rsi_overbought=60,
        stoch_oversold=30, stoch_overbought=70)),
    "linreg": lambda: TrendStrategy(TrendSettings(mode="linreg")),
    "donchian": lambda: TrendStrategy(TrendSettings(mode="donchian")),
}

PAYOUT = 80.0
MIN_SAMPLE = 100          # below this, refuse to draw a conclusion


def breakeven(payout: float) -> float:
    return 100.0 * 100.0 / (100.0 + payout)


def load(path: str) -> List[Candle]:
    out: List[Candle] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out.append(Candle(float(r["time"]), float(r["open"]), float(r["high"]),
                              float(r["low"]), float(r["close"])))
    return out


def wilson(wins: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """95% confidence interval for a proportion. Sane at small n, unlike +-1.96*se."""
    if n == 0:
        return 0.0, 100.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return 100.0 * (centre - margin) / denom, 100.0 * (centre + margin) / denom


def backtest(candles: List[Candle], strat, expiry_candles: int = 1,
             warmup: int = 60) -> dict:
    wins = losses = ties = 0
    for i in range(warmup, len(candles) - expiry_candles):
        sig = strat.evaluate(candles[: i + 1])
        if sig.direction is Direction.NONE:
            continue
        entry = candles[i].close
        exit_ = candles[i + expiry_candles].close
        if entry == exit_:
            ties += 1
        elif (sig.direction is Direction.CALL and exit_ > entry) or \
             (sig.direction is Direction.PUT and exit_ < entry):
            wins += 1
        else:
            losses += 1
    n = wins + losses
    lo, hi = wilson(wins, n)
    return {"wins": wins, "losses": losses, "ties": ties, "n": n,
            "wr": 100.0 * wins / n if n else 0.0, "lo": lo, "hi": hi}


def verdict(res: dict, be: float) -> str:
    if res["n"] < MIN_SAMPLE:
        return "inconclusive (too few trades)"
    if res["lo"] > be:
        return "EDGE"
    if res["hi"] < be:
        return "no edge"
    return "unproven"


def main() -> None:
    be = breakeven(PAYOUT)
    print(f"Real EUR/USD history. Payout assumed {PAYOUT:.0f}% "
          f"-> break-even win rate {be:.1f}%")
    print(f"A verdict needs at least {MIN_SAMPLE} decided trades. "
          f"Ties are refunded, so excluded from the win rate.\n")
    print(f"{'tf':>4} {'strategy':>16} {'trades':>7} {'ties':>6} {'win rate':>9} "
          f"{'95% CI':>14}  verdict")
    print("-" * 74)

    results = []
    for tag, fname in FILES:
        path = os.path.join(HERE, fname)
        if not os.path.exists(path):
            print(f"{tag:>4}  (missing {fname})")
            continue
        candles = load(path)
        for name, make in STRATEGIES.items():
            r = backtest(candles, make())
            results.append((tag, name, r))
            ci = f"{r['lo']:.0f}-{r['hi']:.0f}%"
            print(f"{tag:>4} {name:>16} {r['n']:>7} {r['ties']:>6} "
                  f"{r['wr']:>8.1f}% {ci:>14}  {verdict(r, be)}")
        print()

    print("-" * 74)
    # The same win rates, judged against two different payouts. Asset choice
    # moves the bar you have to clear as much as strategy choice does: the OTC
    # pairs paying 92% need 52.1%, while EURUSD_otc at 68% needs 59.5%.
    for payout in (PAYOUT, 92.0):
        bar = breakeven(payout)
        edges = sum(1 for _, _, r in results if verdict(r, bar) == "EDGE")
        print(f"At a {payout:.0f}% payout (break-even {bar:.1f}%): "
              f"{edges} of {len(results)} combinations show a statistically real edge.")

    if not any(verdict(r, breakeven(92.0)) == "EDGE" for _, _, r in results):
        print(
            "\nRead that plainly: on this data, at either payout, none of these\n"
            "strategies is proven to beat the spread. The automation works —\n"
            "connection, signals, entries, settlement, martingale, risk caps — but\n"
            "automation is not an edge. Anything that looked better than this in a\n"
            "smaller test was sample size, not skill. This is interbank EUR/USD, not\n"
            "Pocket Option's synthetic OTC pairs, so the honest next step is a long\n"
            "run on your own demo account — not real money."
        )


if __name__ == "__main__":
    main()
