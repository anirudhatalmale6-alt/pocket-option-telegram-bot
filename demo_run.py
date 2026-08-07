"""
Offline demo of the live trading loop — no Pocket Option account, no Telegram.

Runs the REAL Trader against the PaperBroker with a console notifier, so you can
watch the exact pipeline the live bot uses (candles -> signal -> trade -> result
-> martingale/PnL) print to your terminal. Great for a first look and for
sanity-checking parameter changes.

  python demo_run.py            # ~20 trades then summary
  python demo_run.py --trades 50
"""

from __future__ import annotations

import argparse
import asyncio

from core.broker import PaperBroker
from core.config import BotConfig
from core.strategy import StrategySettings
from core.trader import Trader


async def main(target_trades: int) -> None:
    cfg = BotConfig.from_env()
    cfg.po_demo = True
    cfg.running = True
    cfg.poll_interval = 0.0
    # Looser settings so the demo actually generates trades on synthetic data.
    cfg.strategy = StrategySettings(
        ema_fast=5, ema_slow=20, require_both=False,
        rsi_oversold=45, rsi_overbought=55,
        stoch_oversold=35, stoch_overbought=65,
    )
    cfg.risk.daily_loss_cap = 0  # don't stop the demo early

    async def console(msg: str) -> None:
        print(msg)

    broker = PaperBroker(seed=3)
    trader = Trader(cfg, broker, notify=console)

    await broker.connect()
    print(f"Connected (DEMO). Balance: {await broker.balance():.2f}\n")

    # Drive the loop manually so we can stop after N trades.
    while trader.risk.wins + trader.risk.losses < target_trades:
        candles = await broker.get_candles(cfg.asset, cfg.candle_timeframe, 200)
        sig = trader.strategy.evaluate(candles)
        if sig.direction.value == "none":
            continue
        await trader._execute(sig)  # noqa: SLF001 - demo drives the loop directly

    print("\n=== DEMO SUMMARY ===")
    print(trader.risk.summary())
    print(f"Final paper balance: {await broker.balance():.2f}")
    print("\nReminder: synthetic data + demo only. Not a profit guarantee.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", type=int, default=20)
    args = ap.parse_args()
    asyncio.run(main(args.trades))
