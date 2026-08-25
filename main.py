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
from core.strategy import StrategySettings
from core.trader import Trader
from core.web_ui import WebInterface

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bot")


def _prepare_practice(config: BotConfig) -> None:
    """
    Set practice mode up so what it shows is both visible AND meaningful.

    Two separate problems, and it matters that they are separate:

    1. Visibility. The 'pullback' default fires almost never with its shipped
       thresholds — measured across the four real data files: 0, 0, 1, 1 signals
       in thousands of candles. Pressing START on it looks identical to a dead
       bot. The "Active" preset (the one real_backtest.py measures) fires often
       enough to watch, so practice uses it and says so.

    2. Meaning. Practice used to run on the random-walk PaperBroker. A random
       walk has nothing to detect, so any strategy lands near 50% there — and at
       an 80% payout, 50% loses steadily. A win rate off that simulator is not a
       weak result, it is not a result at all. Practice now replays real EUR/USD
       candles instead (core/replay_broker.py), so the prices are genuine and
       the settlement is against what actually happened next.

    What practice still cannot tell you: how the strategy does on Pocket
    Option's OTC pairs, which are synthetic and behave differently. That answer
    only comes from your own demo account.
    """
    if config.strategy_mode == "pullback" and config.strategy == StrategySettings():
        log.info("Practice mode: the default 'pullback' thresholds trigger almost "
                 "never on real data — using the wider 'Active' preset so you can "
                 "actually watch it work.")
        config.strategy = StrategySettings(
            ema_fast=5, ema_slow=20, require_both=False,
            rsi_oversold=40, rsi_overbought=60,
            stoch_oversold=30, stoch_overbought=70,
        )


async def run(paper: bool) -> None:
    config = BotConfig.from_env()
    practice = paper or not config.po_ssid
    practice_note = ""
    token_error = ""

    # ---------------------------------------------------------- broker
    if paper or not config.po_ssid:
        if not paper:
            log.warning("PO_SSID not set — falling back to practice mode (no account).")
        # Prefer replaying real market data; fall back to the synthetic walk only
        # if the data files are missing, and be loud about the difference.
        try:
            from core.replay_broker import ReplayBroker
            broker = ReplayBroker(timeframe=config.candle_timeframe,
                                  payout=config.payout_percent / 100.0)
            practice_note = (f"Practice mode — replaying real EUR/USD history at "
                             f"{broker.effective_timeframe}s candles. No account, no money. "
                             f"Real prices, but NOT Pocket Option's OTC pairs.")
            log.info("Practice mode: replaying REAL EUR/USD history at %ss candles "
                     "(%s%% payout, break-even %.1f%%).",
                     broker.effective_timeframe, config.payout_percent,
                     100.0 * 100.0 / (100.0 + config.payout_percent))
            if broker.timeframe_was_rounded:
                log.warning("Your candle size is %ss, but free EUR/USD history has no "
                            "detail below 5 minutes (every 1m bar is flat), so practice "
                            "replays %ss candles. Your %ss setting still applies on a "
                            "real account.", config.candle_timeframe,
                            broker.effective_timeframe, config.candle_timeframe)
        except Exception as exc:
            log.warning("Real-data replay unavailable (%s) — using the synthetic "
                        "simulator. Its win rate is meaningless: a random walk has "
                        "no pattern to find.", exc)
            broker = PaperBroker(payout=config.payout_percent / 100.0)
            practice_note = ("Practice mode — SYNTHETIC prices. Proves the bot works; "
                             "its win rate means nothing.")
        config.po_demo = True
        _prepare_practice(config)
    else:
        # Imported lazily so the optional dependency is only needed for real trading.
        from core.po_broker import PocketOptionBroker
        from core.ssid import SsidError
        try:
            broker = PocketOptionBroker(config.po_ssid, demo=config.po_demo,
                                        uid=config.po_uid)
        except SsidError as exc:
            # Do NOT exit here. The control panel is the only way to deliver a
            # new cookie, so quitting is a catch-22: the bot refuses to start
            # without a good token, and a good token can only arrive through the
            # thing that refused to start. From the outside it is worse still —
            # nothing is listening, so the browser just says the page cannot be
            # found, which looks like the update broke the whole program.
            #
            # Come up in practice mode instead, with the reason on screen and
            # the one-click connect button reachable. Sending a fresh cookie
            # swaps the real broker in via reconnect() below, no restart needed.
            log.error("Pocket Option token problem:\n%s", exc)
            token_error = str(exc)
            broker = PaperBroker(payout=config.payout_percent / 100.0)
            practice = True
            practice_note = ("The saved Pocket Option cookie was not usable, so this "
                             "is PRETEND money. Send a fresh one with the one-click "
                             "button above and it will switch to your real account.")
            config.po_demo = True
            _prepare_practice(config)

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
        web.trader = trader

        def reconnect() -> None:
            """
            Swap in a broker built from details just entered on the panel.

            Raises on a bad token so the panel can report it honestly instead of
            saying "connecting" to something that will never connect.
            """
            from core.po_broker import PocketOptionBroker
            trader.swap_broker(PocketOptionBroker(config.po_ssid, demo=config.po_demo,
                                                  uid=config.po_uid))
            web.paper = False

        web.reload_cb = reconnect
        web.paper = practice
        web.practice_note = practice_note
        web.token_error = token_error
        web.start()
        if token_error:
            # Into the panel's own activity feed. It was only ever logged to the
            # terminal, and the whole point of the launcher icon is that nobody
            # is watching a terminal any more.
            web.log("The saved Pocket Option cookie could not be used, so the bot "
                    "started in practice mode.", connect=True)
            # First line only. These messages carry several paragraphs of
            # DevTools instructions for whoever is reading the terminal, and
            # dumping all of that into the panel would bury the one thing that
            # actually needs doing under the exact manual procedure the
            # one-click button exists to replace. The full text is in the
            # terminal log above for anyone who wants it.
            web.log(f"Reason: {token_error.strip().splitlines()[0]}", connect=True)
            # Before telling anybody to go and click something: the commonest
            # reason a saved cookie is refused is that it has no account id
            # beside it, and that is a question the bot can answer by itself.
            # Only fall back to instructions when there is nothing to search.
            if not web.connect_saved():
                web.log("Send a fresh cookie with the one-click button and it "
                        "will switch to your account — no restart needed.",
                        connect=True)
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
