"""
Backtest on REAL EUR/USD market data.

Downloads genuine historical EUR/USD candles (via Yahoo Finance) at a few
timeframes and runs the exact strategy the live bot uses. This is far more
meaningful than synthetic data because real price action contains the trends and
pull-backs the strategy is designed to trade.

Binary model: enter on candle i's close, settle on candle i+1's close = a
one-candle expiry (so 5m data ≈ 5-minute binaries). Win pays `PAYOUT`, loss = -1.

Honesty note kept front and centre: at an 80% payout you must win 55.6% of
trades to break even. Pocket Option's OTC pairs are synthetic and can behave
differently from the interbank EUR/USD used here, and payouts vary — so treat
this as a realistic read on the STRATEGY's behaviour, then confirm/tune on your
own demo. No backtest guarantees future results.
"""

from __future__ import annotations

from typing import List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yfinance as yf

from core.config import BotConfig
from core.risk import RiskManager
from core.strategy import Candle, Direction, Strategy, StrategySettings

PAYOUT = 0.8
BREAKEVEN = 1.0 / (1.0 + PAYOUT) * 100.0

CONFIGS = [
    ("Selective", StrategySettings(require_both=True, rsi_oversold=30, rsi_overbought=70,
                                   stoch_oversold=20, stoch_overbought=80)),
    ("Balanced", StrategySettings(require_both=True, rsi_oversold=35, rsi_overbought=65,
                                  stoch_oversold=25, stoch_overbought=75)),
    ("Active", StrategySettings(require_both=False, ema_fast=5, ema_slow=20,
                                rsi_oversold=40, rsi_overbought=60,
                                stoch_oversold=30, stoch_overbought=70)),
]


def fetch(symbol: str, interval: str, period: str, csv_path: str) -> List[Candle]:
    df = yf.download(symbol, interval=interval, period=period, progress=False, auto_adjust=True)
    df = df.dropna()
    candles: List[Candle] = []
    rows = []
    for ts, row in df.iterrows():
        o = float(row["Open"].iloc[0] if hasattr(row["Open"], "iloc") else row["Open"])
        h = float(row["High"].iloc[0] if hasattr(row["High"], "iloc") else row["High"])
        l = float(row["Low"].iloc[0] if hasattr(row["Low"], "iloc") else row["Low"])
        c = float(row["Close"].iloc[0] if hasattr(row["Close"], "iloc") else row["Close"])
        candles.append(Candle(ts.timestamp(), o, h, l, c))
        rows.append(f"{ts.timestamp()},{o},{h},{l},{c}")
    with open(csv_path, "w") as f:
        f.write("time,open,high,low,close\n")
        f.write("\n".join(rows))
    return candles


def backtest(candles: List[Candle], settings: StrategySettings):
    strat = Strategy(settings)
    cfg = BotConfig()
    cfg.risk.daily_loss_cap = 0
    risk = RiskManager(cfg.risk, cfg.martingale)
    window = max(settings.ema_slow, settings.rsi_period + 1,
                 settings.stoch_k + settings.stoch_d) + 5
    equity = [0.0]
    for i in range(window, len(candles) - 1):
        sig = strat.evaluate(candles[: i + 1])
        if sig.direction is Direction.NONE:
            continue
        entry, exit_ = candles[i].close, candles[i + 1].close
        up, down = exit_ > entry, exit_ < entry
        if (sig.direction is Direction.CALL and up) or (sig.direction is Direction.PUT and down):
            profit, result = PAYOUT, "win"
        elif entry == exit_:
            profit, result = 0.0, "draw"
        else:
            profit, result = -1.0, "loss"
        risk.record_result(sig.direction.value, 1.0, result, profit)
        equity.append(risk.daily_pnl)
    return risk, equity


DATASETS = [
    ("EUR/USD 5m (1 month)", "EURUSD=X", "5m", "1mo", "data_eurusd_5m.csv"),
    ("EUR/USD 15m (1 month)", "EURUSD=X", "15m", "1mo", "data_eurusd_15m.csv"),
    ("EUR/USD 1m (5 days)", "EURUSD=X", "1m", "5d", "data_eurusd_1m.csv"),
]


def main() -> None:
    print(f"Break-even win rate at {int(PAYOUT*100)}% payout: {BREAKEVEN:.1f}%\n")
    fig, axes = plt.subplots(len(DATASETS), 1, figsize=(9, 11), dpi=100)
    header = f"{'Dataset / Config':<40}{'Trades':>8}{'WinRate':>9}{'PnL/100':>9}"

    MIN_SAMPLE = 30  # fewer trades than this is statistical noise, not a result

    for ax, (label, sym, interval, period, csv_path) in zip(axes, DATASETS):
        candles = fetch(sym, interval, period, csv_path)
        print(f"\n{label}  ({len(candles)} real candles)")
        print(header)
        print("-" * 66)
        plotted = False
        for name, settings in CONFIGS:
            risk, equity = backtest(candles, settings)
            total = risk.wins + risk.losses
            wr = risk.win_rate()
            pnl100 = (risk.daily_pnl / total * 100.0) if total else 0.0
            flag = "" if total >= MIN_SAMPLE else "  (too few signals - ignore)"
            print(f"{'  ' + name:<40}{total:>8}{wr:>8.1f}%{pnl100:>+8.1f}{flag}")
            if total >= MIN_SAMPLE:
                ax.plot(equity, linewidth=1.4, label=f"{name}: {total} trades, WR {wr:.1f}%")
                plotted = True
        ax.axhline(0, color="#333", linewidth=0.8)
        ax.set_title(f"{label} — cumulative PnL ($1 stake, {int(PAYOUT*100)}% payout)", fontsize=10)
        if plotted:
            ax.legend(fontsize=8)
        ax.set_ylabel("PnL $")

    axes[-1].set_xlabel("Trade number")
    fig.suptitle("Pocket Option Bot — Backtest on REAL EUR/USD data",
                 fontsize=13, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.98])
    fig.savefig("real_backtest_report.png")
    print("\nSaved chart -> real_backtest_report.png")
    print(f"\nReminder: real interbank EUR/USD, one-candle expiry, {int(PAYOUT*100)}% payout. "
          "PO OTC pairs/payouts differ — confirm on your demo. Not a profit guarantee.")


if __name__ == "__main__":
    main()
