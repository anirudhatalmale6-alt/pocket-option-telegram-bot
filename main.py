"""
Entry point: wires the Telegram interface and the trader together and runs both
concurrently on one asyncio event loop.

  python main.py            # live/demo trading (needs PO_SSID + TELEGRAM_TOKEN)
  python main.py --paper    # run with the offline PaperBroker (no credentials)

Choose broker by whether PO_SSID is set; --paper forces the simulator so you can
watch the whole pipeline (signals -> trades -> Telegram) without an account.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from core.broker import PaperBroker
from core.config import BotConfig
from core.telegram_bot import TelegramInterface
from core.trader import Trader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bot")


async def run(paper: bool) -> None:
    config = BotConfig.from_env()

    # Pick the broker.
    if paper or not config.po_ssid:
        if not paper:
            log.warning("PO_SSID not set — falling back to PaperBroker (offline simulator).")
        broker = PaperBroker()
        config.po_demo = True
    else:
        # Imported lazily so the optional dependency is only needed for real trading.
        from core.po_broker import PocketOptionBroker
        broker = PocketOptionBroker(config.po_ssid, demo=config.po_demo)

    tg = TelegramInterface(config)
    trader = Trader(config, broker, notify=tg.send)
    app = tg.build(reset_cb=trader.risk.reset_day)

    # Start the Telegram polling app and the trading loop side by side.
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    log.info("Telegram bot started. Send /help in your chat.")

    trader_task = asyncio.create_task(trader.run())
    try:
        await trader_task
    finally:
        trader.stop()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="Pocket Option Telegram trading bot")
    parser.add_argument("--paper", action="store_true", help="use the offline paper broker")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.paper))
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down.")


if __name__ == "__main__":
    main()
