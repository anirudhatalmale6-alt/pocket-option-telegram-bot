"""
Backtest the client's Alligator + RSI strategy on real EUR/USD 1-minute data,
at 1-minute and 2-minute expiry.

Alligator: Jaw SMMA(10) shift 5, Lips SMMA(3) shift 1, Teeth unchecked.
RSI 14, 50 midline trigger. Momentum confirmed on the 2nd/4th/6th candle.

We test the client's intent (fade off) and the mean-reversion variant (fade on),
plus a looser momentum requirement, so the numbers are honest either way.
Break-even at 80% payout = 55.6%. Real interbank EUR/USD; Pocket Option OTC
pairs/payouts differ — confirm on demo. Not a profit guarantee.
"""

from __future__ import annotations

from typing import List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yfinance as yf

from core.alligator_strategy import AlligatorSettings, AlligatorStrategy
from core.strategy import Candle, Direction

PAYOUT = 0.8
BREAKEVEN = 1.0 / (1.0 + PAYOUT) * 100.0


def fetch_1m() -> List[Candle]:
    df = yf.download("EURUSD=X", interval="1m", period="5d", progress=False, auto_adjust=True).dropna()
    out = []
    prev_close = None
    for ts, row in df.iterrows():
        def g(k):
            v = row[k]
            return float(v.iloc[0] if hasattr(v, "iloc") else v)
        close = g("Close")
        # Yahoo's FX feed reports Open==Close; reconstruct open = prev close so
        # candle bodies are realistic (matches Pocket Option behaviour).
        open_ = prev_close if prev_close is not None else g("Open")
        out.append(Candle(ts.timestamp(), open_, g("High"), g("Low"), close))
        prev_close = close
    return out


def backtest(candles: List[Candle], settings: AlligatorSettings, expiry_candles: int):
    strat = AlligatorStrategy(settings)
    need = settings.jaw_period + settings.jaw_shift + settings.rsi_period + 10
    wins = losses = 0
    pnl = 0.0
    equity = [0.0]
    for i in range(need, len(candles) - expiry_candles):
        sig = strat.evaluate(candles[: i + 1])
        if sig.direction is Direction.NONE:
            continue
        entry = candles[i].close
        exit_ = candles[i + expiry_candles].close
        if (sig.direction is Direction.CALL and exit_ > entry) or \
           (sig.direction is Direction.PUT and exit_ < entry):
            pnl += PAYOUT; wins += 1
        elif entry == exit_:
            pass
        else:
            pnl -= 1.0; losses += 1
        equity.append(pnl)
    total = wins + losses
    wr = (wins / total * 100.0) if total else 0.0
    return dict(total=total, wr=wr, pnl=pnl,
                pnl100=(pnl / total * 100.0) if total else 0.0, equity=equity)


def main() -> None:
    candles = fetch_1m()
    print(f"Real EUR/USD 1m candles: {len(candles)}")
    print(f"Break-even win rate at {int(PAYOUT*100)}% payout: {BREAKEVEN:.1f}%\n")

    variants = [
        ("intent  mom>=2 fade off", dict(fade=False, min_momentum=2)),
        ("intent  mom>=1 fade off", dict(fade=False, min_momentum=1)),
        ("reversed mom>=2 fade on", dict(fade=True, min_momentum=2)),
        ("reversed mom>=1 fade on", dict(fade=True, min_momentum=1)),
    ]
    expiries = [1, 2]

    fig, ax = plt.subplots(figsize=(9, 5.5), dpi=110)
    print(f"{'Variant':>26}{'Expiry':>8}{'Trades':>8}{'WinRate':>9}{'PnL/100':>9}")
    print("-" * 68)
    best = None
    for name, kw in variants:
        for exp in expiries:
            s = AlligatorSettings(**kw)
            r = backtest(candles, s, exp)
            print(f"{name:>26}{str(exp)+'m':>8}{r['total']:>8}{r['wr']:>8.1f}%{r['pnl100']:>+8.1f}")
            if r["total"] >= 20:
                ax.plot(r["equity"], linewidth=1.2, label=f"{name}, {exp}m: {r['total']}tr WR{r['wr']:.0f}%")
                if best is None or r["pnl100"] > best["pnl100"]:
                    best = {**r, "name": name, "exp": exp}

    ax.axhline(0, color="#333", linewidth=0.8)
    ax.set_title("Alligator + RSI strategy — real EUR/USD 1m", fontsize=11, fontweight="bold")
    ax.set_xlabel("Trade number"); ax.set_ylabel("Cumulative PnL ($1 stake)")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig("alligator_backtest_report.png")

    print()
    if best:
        print(f"Best variant: {best['name']}, {best['exp']}m expiry -> "
              f"{best['wr']:.1f}% win rate, {best['pnl100']:+.1f}/100 ({best['total']} trades)")
    else:
        print("No variant produced enough trades on this sample.")
    print("Saved chart -> alligator_backtest_report.png")
    print("Note: small sample (5 days). Real proof is the Pocket Option demo.")


if __name__ == "__main__":
    main()
