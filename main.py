"""
Entry point: starts the trading loop plus whichever control interfaces are
configured, all on one asyncio event loop.

  python main.py            # browser control panel + trading
  python main.py --paper    # same, but with the offline simulator (no account)

Control interfaces:
  * Browser panel  — always on unless WEB_ENABLED=false. Open the printed URL,
                     press START. Nothing to install.
  * Telegram       — only starts if TELEGRAM_TOKEN is set. Entirely optional.

Both interfaces read and write the SAME config object the trader uses, so you
can run either, both, or neither.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Optional

from core.broker import PaperBroker
from core.config import BotConfig
from core.trader import Trader
from core.web_ui import WebInterface

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bot")


async def run(paper: bool) -> None:
    config = BotConfig.from_env()

    # ---------------------------------------------------------- broker
    if paper or not config.po_ssid:
        if not paper:
            log.warning("PO_SSID not set — falling back to PaperBroker (offline simulator).")
        broker = PaperBroker()
        config.po_demo = True
    else:
        # Imported lazily so the optional dependency is only needed for real trading.
        from core.po_broker import PocketOptionBroker
        from core.ssid import SsidError
        try:
            broker = PocketOptionBroker(config.po_ssid, demo=config.po_demo,
                                        uid=config.po_uid)
        except SsidError as exc:
            # The token is the one thing nobody gets right first time, so say
            # exactly what is wrong instead of dying inside the socket layer.
            log.error("Pocket Option token problem:\n%s", exc)
            return

    # ------------------------------------------------ control interfaces
    web = WebInterface(config, config.web_host, config.web_port, config.web_password) \
        if config.web_enabled else None

    tg = None
    if config.telegram_token:
        from core.telegram_bot import TelegramInterface
        tg = TelegramInterface(config)

    if web is None and tg is None:
        log.warning("No control interface enabled — the bot cannot be started or stopped.")

    async def notify(msg: str) -> None:
        """Fan a single event out to every interface that is running."""
        if web:
            await web.send(msg)
        if tg:
            await tg.send(msg)

    def status(connected: bool, balance: Optional[float]) -> None:
        if web:
            web.connected = connected
            if balance is not None:
                web.balance = balance

    trader = Trader(config, broker, notify=notify, status_cb=status)

    if web:
        # The panel needs the live risk manager to show PnL and the trade list.
        web.risk = trader.risk
        web.reset_cb = trader.risk.reset_day
        web.paper = isinstance(broker, PaperBroker)
        web.start()
        shown = "localhost" if config.web_host in ("0.0.0.0", "") else config.web_host
        log.info("Control panel: http://%s:%s", shown, config.web_port)
        if not config.web_password and config.web_host == "0.0.0.0":
            log.warning("WEB_PASSWORD is not set — anyone who can reach this port "
                        "can start/stop trading. Set one before exposing a VPS.")

    tg_app = None
    if tg:
        tg_app = tg.build(reset_cb=trader.risk.reset_day)
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling()
        log.info("Telegram bot started. Send /help in your chat.")

    # ------------------------------------------------------------- run
    trader_task = asyncio.create_task(trader.run())
    try:
        await trader_task
    finally:
        trader.stop()
        if web:
            web.stop()
        if tg_app:
            await tg_app.updater.stop()
            await tg_app.stop()
            await tg_app.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="Pocket Option trading bot")
    parser.add_argument("--paper", action="store_true", help="use the offline paper broker")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.paper))
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down.")


if __name__ == "__main__":
    main()
