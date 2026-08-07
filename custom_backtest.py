"""
Backtest the client's CUSTOM strategy (ZigZag + Stochastic + Keltner) on real
EUR/USD 1-minute data, at 1-minute and 2-minute expiry (per the client's 1-2 min).

Because the ZigZag "deviation" scale is platform-dependent, we sweep a few
deviation values to find one that produces a sensible number of signals and to
show how sensitive the setup is. Break-even at 80% payout = 55.6%.

Real interbank EUR/USD; Pocket Option OTC pairs/payouts differ — confirm on demo.
Not a profit guarantee.
"""

from __future__ import annotations

from typing import List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yfinance as yf

from core.custom_strategy import CustomSettings, CustomStrategy
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
        # Yahoo's FX feed reports Open==Close (no candle bodies). In a continuous
        # market a candle opens where the previous one closed, so reconstruct the
        # open that way to get realistic green/red bodies (matches how Pocket
        # Option candles behave too).
        open_ = prev_close if prev_close is not None else g("Open")
        out.append(Candle(ts.timestamp(), open_, g("High"), g("Low"), close))
        prev_close = close
    return out


def backtest(candles: List[Candle], settings: CustomSettings, expiry_candles: int):
    strat = CustomStrategy(settings)
    need = settings.keltner_ema + settings.stoch_k + settings.stoch_d + 10
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

    # ZigZag deviation values to try (price units on EUR/USD; 0.0001 = 1 pip).
    deviations = [0.00008, 0.00013, 0.00020, 0.00030]
    expiries = [1, 2]  # 1-min and 2-min

    fig, ax = plt.subplots(figsize=(9, 5.5), dpi=110)
    print(f"{'ZZ deviation':>13}{'Expiry':>8}{'Trades':>8}{'WinRate':>9}{'PnL/100':>9}")
    print("-" * 55)
    best = None
    for dev in deviations:
        for exp in expiries:
            s = CustomSettings(zigzag_deviation=dev)
            r = backtest(candles, s, exp)
            tag = f"{dev*10000:.1f}pip"
            print(f"{tag:>13}{str(exp)+'m':>8}{r['total']:>8}{r['wr']:>8.1f}%{r['pnl100']:>+8.1f}")
            if r["total"] >= 20:
                ax.plot(r["equity"], linewidth=1.2, label=f"{tag}, {exp}m: {r['total']}tr WR{r['wr']:.0f}%")
                if best is None or (r["total"] >= 20 and r["pnl100"] > best["pnl100"]):
                    best = {**r, "dev": dev, "exp": exp}

    ax.axhline(0, color="#333", linewidth=0.8)
    ax.set_title("Custom strategy (ZigZag+Stoch+Keltner) — real EUR/USD 1m", fontsize=11, fontweight="bold")
    ax.set_xlabel("Trade number"); ax.set_ylabel("Cumulative PnL ($1 stake)")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig("custom_backtest_report.png")

    print()
    if best:
        print(f"Best variant: ZigZag {best['dev']*10000:.1f}pip, {best['exp']}m expiry -> "
              f"{best['wr']:.1f}% win rate, {best['pnl100']:+.1f}/100 ({best['total']} trades)")
    print("Saved chart -> custom_backtest_report.png")
    print("Note: ZigZag deviation is calibratable to your chart; final tuning on your demo.")


if __name__ == "__main__":
    main()
