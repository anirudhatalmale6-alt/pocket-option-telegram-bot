"""
Parameter sweep / robustness backtest.

A single backtest is easy to cherry-pick, so this runs several strategy
configurations across MANY independent simulated markets (different random
seeds) and reports the AVERAGE behaviour plus the spread. That's a far more
honest picture than one lucky run.

It also draws the key reference line every binary trader should know: the
break-even win rate. At an 80% payout you must win 1/(1+0.8) = 55.6% of trades
just to break even. Everything is measured against that.

Outputs:
  * a console table, and
  * backtest_report.png (win-rate-vs-breakeven chart + a sample equity curve).

IMPORTANT: this uses a synthetic price model, not live Pocket Option data. The
numbers show how each SETTING behaves and how much it varies — they are a tuning
tool, not a profit forecast. Real tuning happens on your connected demo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt

from core.broker import PaperBroker
from core.config import BotConfig
from core.risk import RiskManager
from core.strategy import Candle, Direction, Strategy, StrategySettings

PAYOUT = 0.8
BREAKEVEN = 1.0 / (1.0 + PAYOUT) * 100.0  # ~55.6%


@dataclass
class Config:
    name: str
    settings: StrategySettings


CONFIGS: List[Config] = [
    Config("Selective (few, high-conviction)",
           StrategySettings(require_both=True, rsi_oversold=30, rsi_overbought=70,
                            stoch_oversold=20, stoch_overbought=80)),
    Config("Balanced",
           StrategySettings(require_both=True, rsi_oversold=35, rsi_overbought=65,
                            stoch_oversold=25, stoch_overbought=75)),
    Config("Active",
           StrategySettings(require_both=False, ema_fast=5, ema_slow=20,
                            rsi_oversold=40, rsi_overbought=60,
                            stoch_oversold=30, stoch_overbought=70)),
    Config("Very active (many trades)",
           StrategySettings(require_both=False, ema_fast=5, ema_slow=20,
                            rsi_oversold=45, rsi_overbought=55,
                            stoch_oversold=35, stoch_overbought=65)),
]


def gen_market(seed: int, n: int = 2500) -> List[Candle]:
    pb = PaperBroker(seed=seed)
    candles: List[Candle] = list(pb._series)  # noqa: SLF001
    while len(candles) < n:
        o = pb._price
        c = pb._step_price()
        candles.append(Candle(float(pb._t), o, max(o, c) + 0.0001, min(o, c) - 0.0001, c))
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
            profit = 1.0 * PAYOUT
            result = "win"
        elif entry == exit_:
            profit, result = 0.0, "draw"
        else:
            profit, result = -1.0, "loss"
        risk.record_result(sig.direction.value, 1.0, result, profit)
        equity.append(risk.daily_pnl)
    return risk, equity


def main() -> None:
    seeds = list(range(1, 21))  # 20 independent simulated markets
    rows = []
    sample_curves = {}

    for conf in CONFIGS:
        trades_list, wr_list, pnl_list, positive = [], [], [], 0
        for s in seeds:
            candles = gen_market(s)
            risk, equity = backtest(candles, conf.settings)
            total = risk.wins + risk.losses
            if total == 0:
                continue
            trades_list.append(total)
            wr_list.append(risk.win_rate())
            pnl_list.append(risk.daily_pnl / total * 100.0)  # PnL per 100 trades
            if risk.daily_pnl > 0:
                positive += 1
            if conf.name not in sample_curves:
                sample_curves[conf.name] = equity
        n = len(wr_list)
        rows.append(dict(
            name=conf.name,
            avg_trades=sum(trades_list) / n if n else 0,
            avg_wr=sum(wr_list) / n if n else 0,
            wr_min=min(wr_list) if wr_list else 0,
            wr_max=max(wr_list) if wr_list else 0,
            avg_pnl100=sum(pnl_list) / n if n else 0,
            pct_positive=positive / len(seeds) * 100.0,
        ))

    # ---- console table ----
    print(f"\nBreak-even win rate at {int(PAYOUT*100)}% payout: {BREAKEVEN:.1f}%\n")
    print(f"{'Config':<34}{'Trades':>8}{'WinRate':>9}{'(range)':>13}{'PnL/100':>9}{'MktsUp':>8}")
    print("-" * 81)
    for r in rows:
        print(f"{r['name']:<34}{r['avg_trades']:>8.0f}{r['avg_wr']:>8.1f}%"
              f"{('%.0f-%.0f%%' % (r['wr_min'], r['wr_max'])):>13}"
              f"{r['avg_pnl100']:>+8.1f}{r['pct_positive']:>7.0f}%")
    print("\n(avg over 20 independent simulated markets; PnL/100 = net $ per 100 "
          "trades at $1 stake; MktsUp = share of markets that finished positive)")

    # ---- chart ----
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 9), dpi=110)

    names = [r["name"] for r in rows]
    wrs = [r["avg_wr"] for r in rows]
    colors = ["#2e7d32" if w >= BREAKEVEN else "#c62828" for w in wrs]
    ax1.bar(range(len(names)), wrs, color=colors)
    ax1.axhline(BREAKEVEN, color="#333", linestyle="--", linewidth=1.5,
                label=f"Break-even {BREAKEVEN:.1f}%")
    for i, (w, r) in enumerate(zip(wrs, rows)):
        ax1.text(i, w + 0.3, f"{w:.1f}%", ha="center", fontsize=9)
    ax1.set_xticks(range(len(names)))
    ax1.set_xticklabels([n.split(" (")[0] for n in names], rotation=15, ha="right", fontsize=9)
    ax1.set_ylabel("Average win rate")
    ax1.set_ylim(45, max(wrs) + 3)
    ax1.set_title("Win rate by strategy setting vs break-even (80% payout)")
    ax1.legend()

    for name, curve in sample_curves.items():
        ax2.plot(curve, label=name.split(" (")[0], linewidth=1.3)
    ax2.axhline(0, color="#333", linewidth=0.8)
    ax2.set_xlabel("Trade number (sample market)")
    ax2.set_ylabel("Cumulative PnL ($, $1 stake)")
    ax2.set_title("Sample equity curves")
    ax2.legend(fontsize=8)

    fig.suptitle("Pocket Option Bot — Strategy Backtest (synthetic data, illustrative)",
                 fontsize=12, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig("backtest_report.png")
    print("\nSaved chart -> backtest_report.png")


if __name__ == "__main__":
    main()
