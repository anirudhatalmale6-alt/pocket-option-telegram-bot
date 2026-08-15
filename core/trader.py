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
from typing import Awaitable, Callable, Dict, Optional

from .broker import Broker
from .config import BotConfig
from .risk import RiskManager
from .strategy import Direction, Strategy
from .trend_strategy import TrendStrategy
from .custom_strategy import CustomStrategy
from .alligator_strategy import AlligatorStrategy
from .rsi_strategy import RsiStrategy
from .confluence_strategy import ConfluenceStrategy
from .sr_strategy import SrStrategy
from .ai_strategy import AiStrategy
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
    if mode in ("sr", "sr_break", "sr_fade"):
        # One module, three readings of the same idea. Bet the level holds, bet
        # it fails, or take the other side of the bounce — kept as separate
        # dropdown entries because they are opposite trades, and a single
        # "support & resistance" option would hide which one is running.
        config.sr.mode = "break" if mode == "sr_break" else "bounce"
        config.sr.fade = (mode == "sr_fade")
        return SrStrategy(config.sr)
    if mode == "ai":
        # The AI never runs alone. A cheap local strategy decides which candles
        # are worth paying to ask about; without that the model is asked on
        # every candle, which costs more per hour than the trading can make.
        # See core/ai_strategy.py for the arithmetic.
        gate = None
        if config.ai.gate:
            inner = BotConfig_replace_mode(config, config.ai.gate)
            gate = build_evaluator(inner)
        config.ai.breakeven = (100.0 * 100.0 / (100.0 + config.payout_percent)
                               if config.payout_percent else 100.0)
        return AiStrategy(config.ai, gate)
    if mode in ("linreg", "ema", "donchian"):
        config.trend.mode = mode
        return TrendStrategy(config.trend)
    return Strategy(config.strategy)  # default: pull-back


def BotConfig_replace_mode(config: BotConfig, mode: str) -> BotConfig:
    """
    A shallow view of the config with a different strategy_mode.

    Used to build the AI's gate by reusing build_evaluator rather than a second
    switch statement that would drift out of step with the first one. `copy`
    rather than `deepcopy` so the gate reads the same live settings objects the
    panel edits — a gate frozen at start-up would ignore every later change.
    ★ Guards against `gate: "ai"`, which would recurse until the stack blew.
    """
    import copy

    view = copy.copy(config)
    view.strategy_mode = "pullback" if mode == "ai" else mode
    return view

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
        self.last_asset = ""           # which pair that reason was about
        self.watching: list = []       # the pairs currently in rotation
        self._balance_at = 0.0         # when the balance was last refreshed
        self.empty_candles = 0         # consecutive empty reads from the broker
        self.empty_by_asset: Dict[str, int] = {}   # ...counted per pair
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
                # Never claim a Pocket Option connection for a practice broker.
                # Nothing here has touched Pocket Option. The one time this
                # project let practice read like the real thing, the client
                # spent an hour watching a simulated $997.20 believing it was
                # his own account — the balance TILE said PRETEND while the log
                # said the opposite three lines below it.
                practice = getattr(broker, "is_practice", False)
                self._status(True, bal)
                if practice:
                    await self.notify(
                        f"Practice mode — no Pocket Option account is connected. "
                        f"Pretend balance: {bal:.2f}")
                else:
                    mode = "DEMO" if self.config.po_demo else "LIVE"
                    await self.notify(
                        f"Connected to Pocket Option ({mode}). Balance: {bal:.2f}")

                # The warnings below are all about a real account: a refused
                # login, or an empty one. Neither can apply to pretend money.
                if practice:
                    pass
                elif bal < 0:
                    # Reproduced with a deliberately invalid token: the socket
                    # opens, connect() "succeeds", and balance() then returns
                    # -1.0 for ever while no candle ever arrives. A negative
                    # balance is impossible on a real account, so this is the
                    # library telling us the session was never authorised — not
                    # an empty account, which is what this used to claim.
                    # Say what is KNOWN, then the causes in order of likelihood.
                    # This used to assert flatly that the cookie had expired,
                    # and the one time it mattered that was wrong — the cookie
                    # was fine and the bot was sending it in the wrong format.
                    # An error message that names the wrong culprit sends
                    # someone off fetching a new cookie for a fault no cookie
                    # can fix, and it does it in my voice.
                    await self.notify(
                        "⚠️ Pocket Option is NOT accepting your login. The connection "
                        "opens but nothing is authorised, so there is no balance and "
                        "no price data.\n"
                        "The usual cause is an expired session cookie — logging out of "
                        "Pocket Option kills it. Sign in to pocketoption.com, send a "
                        "fresh cookie with the one-click button, and do NOT log out "
                        "afterwards.\n"
                        "If you only just sent a fresh one, it is not expiry. Tell me "
                        "and I will look at it from my end."
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

    # Times each pair should be looked at during one candle. Any lower and a
    # close is noticed so late that the price has moved away from the one the
    # signal was computed on; any higher and Pocket Option starts refusing a
    # client that asks too often.
    LOOKS_PER_CANDLE = 6

    # Never poll faster than this, whatever the arithmetic says.
    MIN_TICK = 0.15

    def tick_seconds(self, pairs: int) -> float:
        """
        How long to wait between polls, given how many pairs are in rotation.

        One pair per tick keeps the request rate flat, but it also means each
        pair is only looked at every `pairs * tick` seconds — and THAT is the
        number that matters, because it is how late an entry can be after the
        candle it was decided on closed.

        At the old fixed one-second tick that lag was fine on 60s candles (10
        pairs -> 10s, a sixth of the bar) and bad on the 30s candles the client
        asked for (10 pairs -> 10s, a THIRD of the bar, entered at a price the
        signal never saw). So the tick shortens as pairs are added, keeping the
        per-pair look rate roughly constant instead of the request rate.

        A single pair is left exactly as it was: this must not quietly change
        the behaviour of the setup that has been running all week.
        """
        cfg = self.config
        if pairs <= 1:
            return cfg.poll_interval
        target = cfg.candle_timeframe / float(self.LOOKS_PER_CANDLE) / pairs
        return max(self.MIN_TICK, min(cfg.poll_interval, target))

    def look_interval(self, pairs: int) -> float:
        """Seconds between two looks at the SAME pair — the lag that matters."""
        return self.tick_seconds(pairs) * max(1, pairs)

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
        # Timestamp of the last candle we already made a decision on, PER PAIR,
        # so one candle produces at most one entry no matter how often we poll.
        # Keyed by asset: a shared timestamp would let whichever pair was polled
        # first silence all the others for that minute, and the bug would look
        # like "the extra pairs never trade" rather than like a bug.
        judged: Dict[str, Optional[float]] = {}
        # Round-robin cursor over the watchlist. One pair is checked per tick
        # rather than all of them, which keeps the request rate flat however
        # many pairs are being watched — Pocket Option starts refusing
        # connections when asked too often, and ten pairs polled every second
        # would be ten times the traffic of the version that already worked.
        cursor = 0

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

            watching = cfg.watched()
            asset = watching[cursor % len(watching)]
            cursor += 1
            self.watching = watching
            tick = self.tick_seconds(len(watching))

            candles = self._closed_candles(
                await self.broker.get_candles(asset, cfg.candle_timeframe, 200)
            )
            if not candles:
                # No price data is a completely different problem from "no setup
                # matched", and on a blank screen the two look identical. Say
                # which one it is, naming the asset — a typo'd or closed pair is
                # the usual cause, and neither announces itself.
                #
                # Counted per pair, not globally: with a watchlist, one dead pair
                # among nine healthy ones must not be able to raise an alarm
                # that reads as though the whole feed is down, and nine healthy
                # ones must not reset the counter and hide the dead one forever.
                self.empty_by_asset[asset] = self.empty_by_asset.get(asset, 0) + 1
                self.empty_candles = self.empty_by_asset[asset]
                self.last_check_ts = time.time()
                self.last_reason = (
                    f"no price data coming back for '{asset}' at "
                    f"{cfg.candle_timeframe}s — check the asset name is right "
                    f"and that the pair is open (use Show live payouts)"
                )
                if self.empty_by_asset[asset] in (10, 100, 1000):
                    await self.notify(f"⚠️ {self.last_reason}")
                await asyncio.sleep(tick)
                continue
            self.empty_by_asset[asset] = 0
            self.empty_candles = 0

            # Act once per candle, on the close. Polling faster than the candle
            # only makes us notice the close sooner; it must not re-judge a bar
            # we have already ruled on.
            stamp = candles[-1].time
            if stamp == judged.get(asset):
                await asyncio.sleep(tick)
                continue
            judged[asset] = stamp

            signal = self.strategy.evaluate(candles)
            self.checks += 1
            self.last_check_ts = time.time()
            self.last_asset = asset
            self.last_reason = (f"{asset}: {signal.reason}"
                                if len(watching) > 1 else signal.reason)

            if signal.direction is Direction.NONE:
                await asyncio.sleep(tick)
                continue

            await self._execute(signal, asset)

    # -------------------------------------------------------------- execute
    async def _execute(self, signal, asset: Optional[str] = None) -> None:
        cfg = self.config
        asset = asset or cfg.asset
        stake = self.risk.next_stake()
        step = self.risk.martingale_step
        self._active = True
        try:
            step_txt = f" (martingale step {step})" if step else ""
            await self.notify(
                f"📈 ENTRY {signal.direction.value.upper()} {asset} "
                f"stake {stake:.2f} exp {cfg.expiry_seconds}s{step_txt}\n{signal.reason}"
            )
            result = await self.broker.place_trade(
                asset, stake, signal.direction.value, cfg.expiry_seconds
            )
            self.risk.record_result(result.direction, result.amount, result.result,
                                    result.profit, asset)

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
