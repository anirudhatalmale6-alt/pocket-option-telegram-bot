"""
Backtest the pure TREND strategies on real EUR/USD, on LONGER windows.

Per the client's steer: no pull-back/scalping, trade the trend, and avoid the
shortest expiry. So we test three trend methods (linreg trend line / EMA trend /
Donchian breakout) on 15m, 30m and 1h candles — expiry = one candle, i.e. 15m to
1h binaries. It then prints a table, picks the most optimal method per timeframe,
and charts the winners.

Break-even at 80% payout is 55.6% — every result is judged against that.
Real interbank EUR/USD; Pocket Option OTC pairs/payouts differ, so confirm on
demo. Not a profit guarantee.
"""

from __future__ import annotations

from typing import List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yfinance as yf

from core.strategy import Candle, Direction
from core.trend_strategy import TrendSettings, TrendStrategy

PAYOUT = 0.8
BREAKEVEN = 1.0 / (1.0 + PAYOUT) * 100.0
MIN_SAMPLE = 30

MODES = [
    ("Trend line (linreg)", TrendSettings(mode="linreg", lookback=20)),
    ("EMA trend", TrendSettings(mode="ema", ema_fast=9, ema_slow=21)),
    ("Breakout (Donchian)", TrendSettings(mode="donchian", donchian_period=20)),
]

DATASETS = [
    ("EUR/USD 15m (60 days)", "EURUSD=X", "15m", "60d", "data_eurusd_15m_60d.csv"),
    ("EUR/USD 30m (60 days)", "EURUSD=X", "30m", "60d", "data_eurusd_30m_60d.csv"),
    ("EUR/USD 1h (6 months)", "EURUSD=X", "60m", "6mo", "data_eurusd_1h_6mo.csv"),
]


def fetch(symbol, interval, period, csv_path) -> List[Candle]:
    df = yf.download(symbol, interval=interval, period=period, progress=False, auto_adjust=True).dropna()
    candles, rows = [], []
    for ts, row in df.iterrows():
        def g(k):
            v = row[k]
            return float(v.iloc[0] if hasattr(v, "iloc") else v)
        o, h, l, c = g("Open"), g("High"), g("Low"), g("Close")
        candles.append(Candle(ts.timestamp(), o, h, l, c))
        rows.append(f"{ts.timestamp()},{o},{h},{l},{c}")
    with open(csv_path, "w") as f:
        f.write("time,open,high,low,close\n" + "\n".join(rows))
    return candles


def backtest(candles: List[Candle], settings: TrendSettings):
    strat = TrendStrategy(settings)
    need = max(settings.lookback, settings.ema_slow + 2, settings.donchian_period + 1) + 2
    wins = losses = 0
    pnl = 0.0
    equity = [0.0]
    for i in range(need, len(candles) - 1):
        sig = strat.evaluate(candles[: i + 1])
        if sig.direction is Direction.NONE:
            continue
        entry, exit_ = candles[i].close, candles[i + 1].close
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
    pnl100 = (pnl / total * 100.0) if total else 0.0
    return dict(total=total, wr=wr, pnl=pnl, pnl100=pnl100, equity=equity)


def main() -> None:
    print(f"Break-even win rate at {int(PAYOUT*100)}% payout: {BREAKEVEN:.1f}%\n")
    fig, axes = plt.subplots(len(DATASETS), 1, figsize=(9, 11), dpi=100)
    best_overall = None

    for ax, (label, sym, interval, period, csv_path) in zip(axes, DATASETS):
        candles = fetch(sym, interval, period, csv_path)
        print(f"{label}  ({len(candles)} real candles)")
        print(f"  {'Method':<24}{'Trades':>8}{'WinRate':>9}{'PnL/100':>9}")
        print("  " + "-" * 50)
        best = None
        for name, settings in MODES:
            r = backtest(candles, settings)
            flag = "" if r["total"] >= MIN_SAMPLE else "  (too few - ignore)"
            print(f"  {name:<24}{r['total']:>8}{r['wr']:>8.1f}%{r['pnl100']:>+8.1f}{flag}")
            if r["total"] >= MIN_SAMPLE and r["wr"] > 0:
                ax.plot(r["equity"], linewidth=1.4, label=f"{name}: {r['total']} tr, WR {r['wr']:.1f}%")
                if best is None or r["pnl100"] > best[1]["pnl100"]:
                    best = (name, r)
        if best:
            print(f"  -> best here: {best[0]} ({best[1]['wr']:.1f}% WR, {best[1]['pnl100']:+.1f}/100)")
            if best_overall is None or best[1]["pnl100"] > best_overall[2]["pnl100"]:
                best_overall = (label, best[0], best[1])
        ax.axhline(0, color="#333", linewidth=0.8)
        ax.set_title(f"{label} — cumulative PnL ($1 stake, {int(PAYOUT*100)}% payout)", fontsize=10)
        ax.legend(fontsize=8)
        ax.set_ylabel("PnL $")
        print()

    axes[-1].set_xlabel("Trade number")
    fig.suptitle("Pocket Option Bot — TREND strategies on real EUR/USD",
                 fontsize=13, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.98])
    fig.savefig("trend_backtest_report.png")

    if best_overall:
        lbl, method, r = best_overall
        print(f"MOST OPTIMAL: {method} on {lbl} — "
              f"{r['wr']:.1f}% win rate, {r['pnl100']:+.1f} per 100 trades ({r['total']} trades)")
    print("Saved chart -> trend_backtest_report.png")


if __name__ == "__main__":
    main()
