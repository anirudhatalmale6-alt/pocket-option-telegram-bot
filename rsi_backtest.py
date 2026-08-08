"""
Backtest the simple RSI(10) reversal strategy.

IMPORTANT HONESTY NOTE: the client asked for 30-SECOND candles on an OTC pair.
Neither is available to backtest publicly:
  * 30s candles: no free data source goes below 1 minute for FX.
  * OTC pairs: they are Pocket Option's own synthetic instruments — the data
    only exists inside PO. There is no historical feed for them anywhere.
So this runs on the closest proxy I have: REAL EUR/USD at 1-minute candles. It
shows the LOGIC works and is directionally useful, but the true 30s-OTC answer
can only come from the demo. Break-even at 80% payout = 55.6%.
"""

from __future__ import annotations

from typing import List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yfinance as yf

from core.rsi_strategy import RsiSettings, RsiStrategy
from core.strategy import Candle, Direction

PAYOUT = 0.8
BREAKEVEN = 1.0 / (1.0 + PAYOUT) * 100.0


def fetch_1m() -> List[Candle]:
    df = yf.download("EURUSD=X", interval="1m", period="5d", progress=False, auto_adjust=True).dropna()
    out, prev_close = [], None
    for ts, row in df.iterrows():
        def g(k):
            v = row[k]
            return float(v.iloc[0] if hasattr(v, "iloc") else v)
        close = g("Close")
        open_ = prev_close if prev_close is not None else g("Open")
        out.append(Candle(ts.timestamp(), open_, g("High"), g("Low"), close))
        prev_close = close
    return out


def backtest(candles: List[Candle], settings: RsiSettings, expiry_candles: int):
    strat = RsiStrategy(settings)
    need = settings.period + 5
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
    print(f"Real EUR/USD 1m candles: {len(candles)} (proxy; NOT 30s, NOT OTC)")
    print(f"Break-even win rate at {int(PAYOUT*100)}% payout: {BREAKEVEN:.1f}%\n")

    variants = [
        ("RSI10 30/70 reversal", dict(oversold=30, overbought=70, fade=False)),
        ("RSI10 20/80 reversal", dict(oversold=20, overbought=80, fade=False)),
        ("RSI10 30/70 fade",     dict(oversold=30, overbought=70, fade=True)),
    ]
    expiries = [1, 2]

    fig, ax = plt.subplots(figsize=(9, 5.5), dpi=110)
    print(f"{'Variant':>24}{'Expiry':>8}{'Trades':>8}{'WinRate':>9}{'PnL/100':>9}")
    print("-" * 66)
    best = None
    for name, kw in variants:
        for exp in expiries:
            s = RsiSettings(period=10, **kw)
            r = backtest(candles, s, exp)
            print(f"{name:>24}{str(exp)+'m':>8}{r['total']:>8}{r['wr']:>8.1f}%{r['pnl100']:>+8.1f}")
            if r["total"] >= 20:
                ax.plot(r["equity"], linewidth=1.2, label=f"{name}, {exp}m: {r['total']}tr WR{r['wr']:.0f}%")
                if best is None or r["pnl100"] > best["pnl100"]:
                    best = {**r, "name": name, "exp": exp}

    ax.axhline(0, color="#333", linewidth=0.8)
    ax.set_title("Simple RSI(10) reversal — real EUR/USD 1m (proxy, not 30s/OTC)", fontsize=10, fontweight="bold")
    ax.set_xlabel("Trade number"); ax.set_ylabel("Cumulative PnL ($1 stake)")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig("rsi_backtest_report.png")

    print()
    if best:
        print(f"Best proxy variant: {best['name']}, {best['exp']}m -> "
              f"{best['wr']:.1f}% WR, {best['pnl100']:+.1f}/100 ({best['total']} trades)")
    print("Saved chart -> rsi_backtest_report.png")
    print("Real 30s/OTC answer needs the Pocket Option demo — no public data exists for it.")


if __name__ == "__main__":
    main()
