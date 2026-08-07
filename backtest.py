"""
Backtest / demo harness.

Runs the exact strategy the live bot uses over a series of candles and reports
win rate, PnL and a martingale-aware equity curve. Two data sources:

  python backtest.py                 # synthetic data from PaperBroker
  python backtest.py --csv data.csv  # your own candles (time,open,high,low,close)

This is the tool we use to fine-tune parameters together before touching live
funds. Reminder: past results on any data set do NOT guarantee future results —
binary options are high-risk. Treat the numbers as a way to compare settings,
not as a profit promise.
"""

from __future__ import annotations

import argparse
import csv
from typing import List

from core.config import BotConfig
from core.risk import RiskManager
from core.strategy import Candle, Direction, Strategy


def load_csv(path: str) -> List[Candle]:
    out: List[Candle] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            out.append(Candle(
                time=float(row.get("time", 0)),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
            ))
    return out


def synthetic(n: int = 3000) -> List[Candle]:
    """Generate candles with the PaperBroker's price model (deterministic)."""
    from core.broker import PaperBroker
    pb = PaperBroker(seed=7)
    candles: List[Candle] = []
    # Prime is 300; pull more one at a time.
    candles.extend(pb._series)  # noqa: SLF001 - intentional for backtest data gen
    while len(candles) < n:
        o = pb._price
        c = pb._step_price()
        hi = max(o, c) + 0.0001
        lo = min(o, c) - 0.0001
        candles.append(Candle(float(pb._t), o, hi, lo, c))
    return candles


def run_backtest(candles: List[Candle], cfg: BotConfig, payout: float = 0.8) -> dict:
    strat = Strategy(cfg.strategy)
    risk = RiskManager(cfg.risk, cfg.martingale)

    window = max(cfg.strategy.ema_slow, cfg.strategy.rsi_period + 1,
                 cfg.strategy.stoch_k + cfg.strategy.stoch_d) + 5
    trades = 0
    equity_curve = [0.0]

    # Walk forward: decide on candle i using candles[:i+1], settle on next candle.
    for i in range(window, len(candles) - 1):
        allowed, _ = risk.can_trade()
        if not allowed:
            break
        sig = strat.evaluate(candles[: i + 1])
        if sig.direction is Direction.NONE:
            continue

        stake = risk.next_stake()
        entry = candles[i].close
        exit_price = candles[i + 1].close  # simple 1-candle expiry model

        up = exit_price > entry
        down = exit_price < entry
        if (sig.direction is Direction.CALL and up) or (sig.direction is Direction.PUT and down):
            result, profit = "win", stake * payout
        elif exit_price == entry:
            result, profit = "draw", 0.0
        else:
            result, profit = "loss", -stake

        risk.record_result(sig.direction.value, stake, result, profit)
        equity_curve.append(risk.daily_pnl)
        trades += 1

    return {
        "trades": trades,
        "wins": risk.wins,
        "losses": risk.losses,
        "win_rate": risk.win_rate(),
        "pnl": risk.daily_pnl,
        "equity_curve": equity_curve,
        "max_drawdown": _max_drawdown(equity_curve),
    }


def _max_drawdown(curve: List[float]) -> float:
    peak = curve[0] if curve else 0.0
    dd = 0.0
    for v in curve:
        peak = max(peak, v)
        dd = min(dd, v - peak)
    return dd


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="candles CSV with time,open,high,low,close")
    ap.add_argument("--payout", type=float, default=0.8)
    args = ap.parse_args()

    cfg = BotConfig.from_env()
    candles = load_csv(args.csv) if args.csv else synthetic()

    res = run_backtest(candles, cfg, payout=args.payout)
    print("=== Backtest results ===")
    print(f"Candles       : {len(candles)}")
    print(f"Trades        : {res['trades']}")
    print(f"Wins / Losses : {res['wins']} / {res['losses']}")
    print(f"Win rate      : {res['win_rate']:.1f}%")
    print(f"Net PnL       : {res['pnl']:+.2f}")
    print(f"Max drawdown  : {res['max_drawdown']:.2f}")
    print("\nNote: numbers depend entirely on data + payout + settings. "
          "Use this to compare parameter sets, not as a profit guarantee.")


if __name__ == "__main__":
    main()
