"""
Fine-tuning grid search for the client's custom strategy.

Honest constraints, stated up front:
  * Free 1-minute history is only ~5 days, which gives too few trades from such a
    selective setup to trust. So we ALSO test on 5-minute data over 60 days to get
    a statistically meaningful sample for the ENTRY LOGIC (expiry there is 5-10 min,
    not the client's 1-2 min — it's for validating the logic, not the exact expiry).
  * Optimising hard on a small window overfits. We therefore prefer settings that
    hold up on the LARGE 5m sample, and only report small-sample 1m results as a
    directional check. Final truth is the client's Pocket Option demo.

Knobs swept: Keltner EMA period, midline confirmation candles, how many of the
two confirmations must agree, fade (mean-reversion) on/off, ZigZag deviation,
and expiry length. Break-even at 80% payout = 55.6%.
"""

from __future__ import annotations

from itertools import product
from typing import List

import yfinance as yf

from core.custom_strategy import CustomSettings, CustomStrategy
from core.strategy import Candle, Direction

PAYOUT = 0.8
BREAKEVEN = 1.0 / (1.0 + PAYOUT) * 100.0


def fetch(interval: str, period: str) -> List[Candle]:
    df = yf.download("EURUSD=X", interval=interval, period=period,
                     progress=False, auto_adjust=True).dropna()
    out, prev = [], None
    for ts, row in df.iterrows():
        def g(k):
            v = row[k]
            return float(v.iloc[0] if hasattr(v, "iloc") else v)
        close = g("Close")
        open_ = prev if prev is not None else g("Open")
        out.append(Candle(ts.timestamp(), open_, g("High"), g("Low"), close))
        prev = close
    return out


def run(candles: List[Candle], s: CustomSettings, expiry: int):
    strat = CustomStrategy(s)
    need = s.keltner_ema + s.stoch_k + s.stoch_d + 10
    wins = losses = 0
    for i in range(need, len(candles) - expiry):
        # Bounded lookback window (constant work per candle) instead of the whole
        # history — the strategy only needs the last `need` candles.
        sig = strat.evaluate(candles[i - need: i + 1])
        if sig.direction is Direction.NONE:
            continue
        entry, exit_ = candles[i].close, candles[i + expiry].close
        if (sig.direction is Direction.CALL and exit_ > entry) or \
           (sig.direction is Direction.PUT and exit_ < entry):
            wins += 1
        elif entry != exit_:
            losses += 1
    total = wins + losses
    wr = (wins / total * 100.0) if total else 0.0
    pnl100 = (wr / 100.0 * PAYOUT - (1 - wr / 100.0)) * 100.0 if total else 0.0
    return total, wr, pnl100


def grid(dev_scale: float):
    """Yield (label, CustomSettings, expiry) combos. dev_scale sets pip size per TF."""
    for ema, conf, minc, fade, dev_pips, expiry in product(
        [14, 21, 34], [1, 2], [1, 2], [False, True], [1.0, 2.0, 4.0], [1, 2]
    ):
        s = CustomSettings(
            keltner_ema=ema, confirm_candles=conf, require_all=False,
            min_confirmations=minc, fade=fade,
            zigzag_deviation=dev_pips * dev_scale,
        )
        label = (f"ema{ema} conf{conf} min{minc} "
                 f"{'FADE' if fade else 'trend'} zz{dev_pips:.0f}p exp{expiry}")
        yield label, s, expiry


def evaluate_dataset(name: str, candles: List[Candle], dev_scale: float, min_sample: int):
    print(f"\n=== {name}  ({len(candles)} candles, min sample {min_sample}) ===")
    results = []
    for label, s, expiry in grid(dev_scale):
        total, wr, pnl100 = run(candles, s, expiry)
        if total >= min_sample:
            results.append((wr, pnl100, total, label, s, expiry))
    results.sort(reverse=True)
    print(f"{'WinRate':>8}{'PnL/100':>9}{'Trades':>8}  Config")
    for wr, pnl100, total, label, _s, _e in results[:8]:
        mark = "  <= clears break-even" if wr >= BREAKEVEN else ""
        print(f"{wr:>7.1f}%{pnl100:>+8.1f}{total:>8}  {label}{mark}")
    if not results:
        print("  (no config reached the minimum sample)")
    return results


def main() -> None:
    print(f"Break-even win rate at {int(PAYOUT*100)}% payout: {BREAKEVEN:.1f}%")

    # 5m over 60 days = large sample -> statistically meaningful for the LOGIC.
    c5 = fetch("5m", "60d")
    big = evaluate_dataset("EUR/USD 5m / 60d (logic validation)", c5, dev_scale=0.0001, min_sample=80)

    # 1m over 5 days = the client's real expiry, but small sample (directional).
    c1 = fetch("1m", "5d")
    small = evaluate_dataset("EUR/USD 1m / 5d (real 1-2 min expiry, small sample)", c1,
                             dev_scale=0.00003, min_sample=15)

    # Recommend the top config from the LARGE sample (least overfit).
    if big:
        wr, pnl100, total, label, s, expiry = big[0]
        print("\n---------------------------------------------")
        print(f"RECOMMENDED (from large 5m sample): {label}")
        print(f"  {wr:.1f}% win rate over {total} trades, {pnl100:+.1f}/100")
        print(f"  settings: keltner_ema={s.keltner_ema}, confirm_candles={s.confirm_candles}, "
              f"min_confirmations={s.min_confirmations}, fade={s.fade}, "
              f"zigzag_deviation={s.zigzag_deviation:.5f}")
        print("  -> I'll set these as the custom-strategy defaults; confirm on your demo.")
    print("\nHonest note: best-on-history != guaranteed live. The demo is the real test.")


if __name__ == "__main__":
    main()
