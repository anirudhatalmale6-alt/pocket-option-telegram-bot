"""
The trader: the main orchestration loop.

Each cycle it:
  1. pulls the latest candles from the broker,
  2. asks the strategy for a signal,
  3. runs the signal past the risk manager (daily caps + stake sizing),
  4. places the trade, waits for it to settle,
  5. updates martingale/PnL and notifies Telegram.

It is asyncio-based and cooperative: /start and /stop simply flip
`config.running`, and any parameter change from Telegram is picked up on the
next cycle because everything reads from the shared config object.
"""

from __future__ import annotations

import asyncio
import time
import traceback
from typing import Awaitable, Callable, Optional

from .broker import Broker
from .config import BotConfig
from .risk import RiskManager
from .strategy import Direction, Strategy
from .trend_strategy import TrendStrategy
from .custom_strategy import CustomStrategy
from .alligator_strategy import AlligatorStrategy
from .rsi_strategy import RsiStrategy
from .confluence_strategy import ConfluenceStrategy
from . import plugins


def build_evaluator(config: BotConfig):
    """
    Return the strategy evaluator selected by config.strategy_mode. Every
    evaluator exposes evaluate(candles) -> Signal, so the trader treats them
    interchangeably. Switchable live from Telegram.
    """
    mode = config.strategy_mode
    if mode.startswith(plugins.PREFIX):
        # A strategy file someone dropped into strategies/. If the file has been
        # deleted since it was picked, fall through to the built-ins rather than
        # trading on nothing.
        built = plugins.build(mode)
        if built is not None:
            return built
    if mode == "custom":
        return CustomStrategy(config.custom)
    if mode == "alligator":
        return AlligatorStrategy(config.alligator)
    if mode == "rsi":
        return RsiStrategy(config.rsi)
    if mode == "confluence":
        return ConfluenceStrategy(config.confluence)
    if mode in ("linreg", "ema", "donchian"):
        config.trend.mode = mode
        return TrendStrategy(config.trend)
    return Strategy(config.strategy)  # default: pull-back

# A notifier is any async function taking a string (wired to Telegram/web in main).
Notifier = Callable[[str], Awaitable[None]]

# A status callback reports connection state to the UI: (connected, balance).
StatusCb = Callable[[bool, Optional[float]], None]


class Trader:
    def __init__(self, config: BotConfig, broker: Broker,
                 notify: Optional[Notifier] = None,
                 status_cb: Optional[StatusCb] = None):
        self.config = config
        self.broker = broker
        self.strategy = build_evaluator(config)
        self.risk = RiskManager(config.risk, config.martingale)
        self._notify = notify or self._noop
        self._status_cb = status_cb
        self._active = False           # a trade is currently open
        self._stop = False             # hard stop for shutdown
        self._pending_broker = None    # set by swap_broker(), installed on the
                                       # next cycle once the old one is closed
        # Proof of life for the panel. A selective strategy that is working
        # perfectly looks exactly like a crashed one when the screen is blank,
        # so we publish what was checked, when, and why it passed.
        self.checks = 0                # candles evaluated since start
        self.last_check_ts = 0.0       # when the newest candle was judged
        self.last_reason = ""          # why the last candle was not traded
        self._balance_at = 0.0         # when the balance was last refreshed
        self.empty_candles = 0         # consecutive empty reads from the broker
        # How long to let Pocket Option deliver the opening balance. Only ever
        # waited out when the figure is non-positive, so a healthy account pays
        # nothing for it. Instance attributes so tests need not sleep for real.
        self.balance_attempts = 6
        self.balance_delay = 1.0

    def _status(self, connected: bool, balance: Optional[float] = None) -> None:
        """Tell the UI whether we are connected (never fatal if it fails)."""
        if self._status_cb:
            try:
                self._status_cb(connected, balance)
            except Exception:
                pass

    async def _noop(self, _msg: str) -> None:
        return

    async def notify(self, msg: str) -> None:
        try:
            await self._notify(msg)
        except Exception:
            # Never let a Telegram hiccup kill the trading loop.
            pass

    # ------------------------------------------------------------------ run
    async def _settled_balance(self, broker: Broker, attempts: int = 0,
                               delay: float = 0.0) -> float:
        """
        Read the balance, giving Pocket Option a moment to actually send it.

        The balance arrives asynchronously over the websocket after the auth
        handshake. Asking for it the instant we connect can return whatever the
        client holds before that message lands — the client's real demo account
        showed 673,000 on Pocket Option's own site while this reported -1.00.
        A negative balance is impossible on a binary-options account, so treat a
        non-positive figure as "not arrived yet" and give it a few seconds.

        Returns the last value read either way: if the account really is empty,
        that is the truth and the caller warns about it. Never invents a number.
        """
        attempts = attempts or self.balance_attempts
        delay = delay or self.balance_delay
        bal = 0.0
        for attempt in range(attempts):
            bal = float(await broker.balance())
            if bal > 0:
                return bal
            if attempt < attempts - 1:
                await asyncio.sleep(delay)
        return bal

    def swap_broker(self, broker: Broker) -> None:
        """
        Trade against a different account from the next cycle, no restart.

        The control panel calls this when the Pocket Option token is entered on
        the page. Requiring a restart there would mean sending the client back
        to the terminal for the one step he is most likely to fumble, which is
        the whole reason the panel took over the job.
        """
        self._pending_broker = broker
        self.config.running = False   # never carry an open position across accounts

    async def run(self) -> None:
        """Long-lived loop. Reconnects on error; respects config.running."""
        backoff = 2
        while not self._stop:
            # Bind the broker for this whole attempt. Reading self.broker in the
            # finally would close whichever broker happened to be installed by
            # then — i.e. a freshly swapped one, immediately after connecting it.
            broker = self.broker
            try:
                await broker.connect()
                bal = await self._settled_balance(broker)
                mode = "DEMO" if self.config.po_demo else "LIVE"
                self._status(True, bal)
                await self.notify(f"Connected to Pocket Option ({mode}). Balance: {bal:.2f}")
                if bal < 0:
                    # Reproduced with a deliberately invalid token: the socket
                    # opens, connect() "succeeds", and balance() then returns
                    # -1.0 for ever while no candle ever arrives. A negative
                    # balance is impossible on a real account, so this is the
                    # library telling us the session was never authorised — not
                    # an empty account, which is what this used to claim.
                    await self.notify(
                        "⚠️ Pocket Option is NOT accepting your login. The connection "
                        "opens but nothing is authorised, so there is no balance and "
                        "no price data.\n"
                        "Your session cookie has expired or was cancelled — logging "
                        "out of Pocket Option kills it. Sign in to pocketoption.com, "
                        "grab a fresh ci_session cookie, paste it into the panel, and "
                        "do NOT log out afterwards."
                    )
                elif bal == 0:
                    await self.notify(
                        "⚠️ This account has no money in it. Trades will be rejected "
                        "until it is topped up. On a demo account you can refill it "
                        "from the Pocket Option website."
                    )
                backoff = 2
                await self._loop()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # connection dropped or unexpected error
                self._status(False)
                await self.notify(f"⚠️ Error: {e}. Reconnecting in {backoff}s...")
                traceback.print_exc()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)  # exponential backoff, capped
            finally:
                self._status(False)
                await broker.close()
            # Install a broker handed over while the old one was live. Done here,
            # after the old one is closed, so two sockets are never open at once.
            if self._pending_broker is not None:
                self.broker = self._pending_broker
                self._pending_broker = None
                self.checks = 0            # counters belong to the old account
                self.last_reason = ""
                backoff = 2
                await self.notify("Account details updated — reconnecting.")

    def _closed_candles(self, candles):
        """
        Drop the candle that is still being built.

        Pocket Option's newest candle is live: it keeps changing until its clock
        runs out. Reading indicators off it means judging an unfinished bar —
        a "cross" can appear halfway through and be gone by the close, which is
        both a different signal from the one the backtests measured and a way to
        enter mid-candle on something that never actually happened.
        """
        if getattr(self.broker, "LAST_CANDLE_IS_PARTIAL", False) and len(candles) > 1:
            return candles[:-1]
        return candles

    async def _loop(self) -> None:
        cfg = self.config
        self.risk.risk = cfg.risk
        self.risk.martingale = cfg.martingale
        active_mode = cfg.strategy_mode
        # Timestamp of the last candle we already made a decision on, so one
        # candle produces at most one entry no matter how often we poll.
        judged: Optional[float] = None

        while not self._stop:
            # Hand control back to run() so it can close this account's socket
            # and open the new one.
            if self._pending_broker is not None:
                return

            # Keep the balance tile honest, whether or not trading is running.
            # It used to be read once at connect and then only after a settled
            # trade, so a bad first read stayed on screen indefinitely — exactly
            # what happened: -1.00 shown against a real balance of 673,000. This
            # sits ABOVE the running check on purpose; a stopped bot still shows
            # a balance, and a stale one there is just as misleading.
            now = time.time()
            if now - self._balance_at > 30:
                self._balance_at = now
                try:
                    self._status(True, await self.broker.balance())
                except Exception:
                    pass      # a failed refresh must never stop the loop

            if not cfg.running:
                await asyncio.sleep(cfg.poll_interval)
                continue

            # Rebuild the evaluator if the strategy was switched from Telegram.
            if cfg.strategy_mode != active_mode:
                self.strategy = build_evaluator(cfg)
                active_mode = cfg.strategy_mode
                await self.notify(f"Strategy switched to: {active_mode}")

            # Respect daily caps before doing anything.
            allowed, reason = self.risk.can_trade()
            if not allowed:
                await self.notify(f"⛔ Trading paused: {reason}. Use /reset to clear or /stop.")
                cfg.running = False
                continue

            if self._active:
                await asyncio.sleep(cfg.poll_interval)
                continue

            candles = self._closed_candles(
                await self.broker.get_candles(cfg.asset, cfg.candle_timeframe, 200)
            )
            if not candles:
                # No price data is a completely different problem from "no setup
                # matched", and on a blank screen the two look identical. Say
                # which one it is, naming the asset — a typo'd or closed pair is
                # the usual cause, and neither announces itself.
                self.empty_candles += 1
                self.last_check_ts = time.time()
                self.last_reason = (
                    f"no price data coming back for '{cfg.asset}' at "
                    f"{cfg.candle_timeframe}s — check the asset name is right "
                    f"and that the pair is open (use Show live payouts)"
                )
                if self.empty_candles in (10, 100, 1000):
                    await self.notify(f"⚠️ {self.last_reason}")
                await asyncio.sleep(cfg.poll_interval)
                continue
            self.empty_candles = 0

            # Act once per candle, on the close. Polling faster than the candle
            # only makes us notice the close sooner; it must not re-judge a bar
            # we have already ruled on.
            stamp = candles[-1].time
            if stamp == judged:
                await asyncio.sleep(cfg.poll_interval)
                continue
            judged = stamp

            signal = self.strategy.evaluate(candles)
            self.checks += 1
            self.last_check_ts = time.time()
            self.last_reason = signal.reason

            if signal.direction is Direction.NONE:
                await asyncio.sleep(cfg.poll_interval)
                continue

            await self._execute(signal)

    # -------------------------------------------------------------- execute
    async def _execute(self, signal) -> None:
        cfg = self.config
        stake = self.risk.next_stake()
        step = self.risk.martingale_step
        self._active = True
        try:
            step_txt = f" (martingale step {step})" if step else ""
            await self.notify(
                f"📈 ENTRY {signal.direction.value.upper()} {cfg.asset} "
                f"stake {stake:.2f} exp {cfg.expiry_seconds}s{step_txt}\n{signal.reason}"
            )
            result = await self.broker.place_trade(
                cfg.asset, stake, signal.direction.value, cfg.expiry_seconds
            )
            self.risk.record_result(result.direction, result.amount, result.result, result.profit)

            icon = {"win": "✅", "loss": "❌", "draw": "➖"}.get(result.result, "•")
            await self.notify(
                f"{icon} {result.result.upper()} {result.profit:+.2f}\n{self.risk.summary()}"
            )

            # Keep the dashboard's balance tile honest after every settlement.
            try:
                self._status(True, await self.broker.balance())
            except Exception:
                pass
        finally:
            self._active = False

    def stop(self) -> None:
        self._stop = True
