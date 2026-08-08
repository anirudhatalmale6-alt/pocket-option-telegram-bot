"""
Backtest the confluence strategy vs the best single strategy on real EUR/USD 1m.

Confluence only trades when >= min_agree of the three best (faded) setups agree.
Expectation: fewer trades, higher win rate. We show min_agree 2 and 3 next to
the best single strategy so the quality-vs-quantity trade-off is explicit.

Real EUR/USD 1m (proxy; NOT 30s, NOT OTC). Break-even at 80% payout = 55.6%.
"""

from __future__ import annotations

from typing import List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yfinance as yf

from core.confluence_strategy import ConfluenceSettings, ConfluenceStrategy
from core.custom_strategy import CustomSettings, CustomStrategy
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


def backtest(candles: List[Candle], strat, expiry_candles: int, warm: int = 60):
    wins = losses = 0
    pnl = 0.0
    equity = [0.0]
    for i in range(warm, len(candles) - expiry_candles):
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

    contenders = [
        ("single: custom(fade)", CustomStrategy(CustomSettings(fade=True))),
        ("confluence min2",      ConfluenceStrategy(ConfluenceSettings(min_agree=2))),
        ("confluence min3",      ConfluenceStrategy(ConfluenceSettings(min_agree=3))),
    ]
    expiries = [1, 2]

    fig, ax = plt.subplots(figsize=(9, 5.5), dpi=110)
    print(f"{'Strategy':>22}{'Expiry':>8}{'Trades':>8}{'WinRate':>9}{'PnL/100':>9}")
    print("-" * 64)
    for name, strat in contenders:
        for exp in expiries:
            r = backtest(candles, strat, exp)
            print(f"{name:>22}{str(exp)+'m':>8}{r['total']:>8}{r['wr']:>8.1f}%{r['pnl100']:>+8.1f}")
            if r["total"] >= 10:
                ax.plot(r["equity"], linewidth=1.3, label=f"{name}, {exp}m: {r['total']}tr WR{r['wr']:.0f}%")

    ax.axhline(0, color="#333", linewidth=0.8)
    ax.set_title("Confluence vs single strategy — real EUR/USD 1m (proxy)", fontsize=10, fontweight="bold")
    ax.set_xlabel("Trade number"); ax.set_ylabel("Cumulative PnL ($1 stake)")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig("confluence_backtest_report.png")
    print("\nSaved chart -> confluence_backtest_report.png")
    print("Higher agreement = fewer trades, usually higher win rate. Demo settles the real number.")


if __name__ == "__main__":
    main()
