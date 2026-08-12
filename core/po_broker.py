"""
Real Pocket Option broker.

IMPORTANT — how the connection works, and the honest caveat:
Pocket Option does not publish an official trading API. Bots connect the same
way the website does: over its WebSocket, authenticated with the SSID session
token copied from a logged-in browser. This wrapper uses the community
`BinaryOptionsToolsV2` package (pip install BinaryOptionsToolsV2), which
maintains that WebSocket for us. Because this is an unofficial route it can
break if Pocket Option changes their protocol, and automated trading is against
their Terms of Service — so we ALWAYS run on the DEMO balance first.

If the library is not installed, importing this module still works; connect()
raises a clear message telling you what to install. That lets the rest of the
bot (backtests, paper trading, tests) run with zero external dependencies.

Getting your SSID (demo):
  1. Log in to Pocket Option in Chrome and switch to the DEMO account.
  2. Open DevTools FIRST (F12) -> Network -> filter WS -> then refresh (Ctrl+R).
     DevTools only records frames from the moment it is open.
  3. Several WebSockets open. Click each and read its Messages tab; the TRADING
     one is the one containing 451-["successauth",...]. The green outgoing
     42["auth",{"session":"...","isDemo":1,...}] line above it is your SSID.
  Paste it into PO_SSID in your .env. If you copied the chart socket's token by
  mistake, core/ssid.py detects it and tells you. Easier alternative: put the
  ci_session cookie value in PO_SESSION and your account id in PO_UID.
  Full walkthrough in docs/SETUP.md.
"""

from __future__ import annotations

import asyncio
import time
from typing import List

from .broker import Broker, TradeResult
from .ssid import normalise
from .strategy import Candle

try:
    # Community async client. Import guarded so the module is always importable.
    from BinaryOptionsToolsV2.pocketoption import PocketOptionAsync  # type: ignore
    _HAVE_LIB = True
except Exception:  # pragma: no cover - depends on optional install
    PocketOptionAsync = None  # type: ignore
    _HAVE_LIB = False


class PocketOptionBroker(Broker):
    # We consume only the CLOSED candles from the live stream, so there is no
    # forming bar to discard here. Discarding one would throw away the freshest
    # close and leave every decision a full candle late.
    LAST_CANDLE_IS_PARTIAL = False

    # Candle sizes Pocket Option actually serves. Asking for anything else comes
    # back empty, which looks exactly like a dead connection.
    SUPPORTED_TIMEFRAMES = (1, 5, 15, 30, 60, 300)

    # How much history to backfill when the stream starts, and how many closed
    # candles to keep. 200 covers every indicator in the project with room spare.
    BACKFILL_HOURS = 2.0
    MAX_ROWS = 200

    # How long a freshly opened feed may deliver nothing before we call it a
    # fault. Generous: a real backfill takes a few seconds, never a minute.
    STALL_SECONDS = 75.0

    def __init__(self, ssid: str, demo: bool = True, uid: int = 0):
        if not ssid:
            raise ValueError("PO_SSID is empty — paste your browser session token (see docs/SETUP.md)")
        # Accept whatever the user managed to copy (full auth frame, bare object
        # or just the ci_session cookie) and fail early with a readable message
        # if it is the chart socket's token instead of the trading one.
        self._ssid = normalise(ssid, uid=uid, demo=demo)
        self._demo = demo
        self._client = None
        # One long-lived candle stream, not a new one per poll. See _ensure_stream.
        self._stream_task = None
        self._stream_key = None      # (asset, timeframe) the stream is serving
        self._stream_error = ""      # last failure, surfaced instead of silence
        self._stream_started = 0.0   # when the current stream opened
        self._closed_candles: List[dict] = []

    async def connect(self) -> None:
        if not _HAVE_LIB:
            raise RuntimeError(
                "BinaryOptionsToolsV2 is not installed. Run:  pip install BinaryOptionsToolsV2\n"
                "This is the library that maintains the Pocket Option WebSocket session."
            )
        # The library takes the raw SSID auth payload string.
        self._client = PocketOptionAsync(self._ssid)
        # Give the socket a moment to authenticate before first use.
        await asyncio.sleep(2)

    async def close(self) -> None:
        await self._stop_stream()
        # The underlying client manages its own socket lifecycle; nothing strictly
        # required here, but we null the reference so reuse fails loudly.
        self._client = None

    async def _stop_stream(self) -> None:
        task, self._stream_task = self._stream_task, None
        self._stream_key = None
        self._stream_started = 0.0
        self._closed_candles = []
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _pump(self, asset: str, timeframe: int) -> None:
        """
        Hold one live subscription open and keep the newest closed candles.

        get_candles_live() is an async generator: it subscribes to ticks, backfills
        history, then yields (closed, forming) on every update. Consuming it in a
        background task means the trading loop reads a list from memory instead of
        awaiting the network on every tick.
        """
        try:
            gen = self._client.get_candles_live(
                asset, timeframe, hours=self.BACKFILL_HOURS, max_rows=self.MAX_ROWS
            )
            async for closed, _forming in gen:
                self._closed_candles = closed or []
                self._stream_error = ""
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Record it rather than dying quietly: an exception in a background
            # task is invisible, and invisible here means the panel sits on
            # "waiting for the first candle" forever with nothing to explain it.
            self._stream_error = f"{type(exc).__name__}: {exc}"

    async def _ensure_stream(self, asset: str, timeframe: int) -> None:
        """Start the stream, or restart it if the asset or candle size changed."""
        key = (asset, timeframe)
        alive = self._stream_task is not None and not self._stream_task.done()
        if alive and self._stream_key == key:
            return
        if self._stream_task is not None and self._stream_task.done():
            # Surface why it stopped before replacing it.
            exc = self._stream_task.exception() if not self._stream_task.cancelled() else None
            if exc is not None and not self._stream_error:
                self._stream_error = f"{type(exc).__name__}: {exc}"
        await self._stop_stream()
        self._stream_key = key
        self._stream_started = time.time()
        self._stream_task = asyncio.create_task(self._pump(asset, timeframe))

    def _require(self):
        if self._client is None:
            raise RuntimeError("Broker not connected — call connect() first")
        return self._client

    async def get_candles(self, asset: str, timeframe: int, count: int) -> List[Candle]:
        self._require()          # raises a readable error if not connected yet
        if timeframe not in self.SUPPORTED_TIMEFRAMES:
            raise ValueError(
                f"Pocket Option does not serve {timeframe}s candles. Pick one of: "
                f"{', '.join(str(t) for t in self.SUPPORTED_TIMEFRAMES)} "
                f"(change 'Candle size' in the control panel)."
            )
        # Deliberately NOT client.get_candles(). That helper opens a whole new
        # live subscription and backfill on every single call, and the trading
        # loop calls this once per second — so each poll tore down and rebuilt
        # the feed, and the first batch never had time to arrive. That is why the
        # panel sat on "waiting for the first candle to close" indefinitely.
        #
        # (Its docstring is also wrong about its own arguments: it documents
        # period as the history span and offset as the candle size, while the
        # body does `hours = offset / 3600` and passes `period` straight through
        # to get_candles_live as the candle size. Reading the docstring rather
        # than the body is what sent me the wrong way here once already.)
        await self._ensure_stream(asset, timeframe)
        if self._stream_error:
            raise RuntimeError(f"Pocket Option candle feed failed — {self._stream_error}")

        # A stream that opens and then simply never delivers is the failure mode
        # that wasted days here: no exception, no error, just nothing. Measured
        # against a deliberately invalid token, the library prints its own
        # timeout internally and our task keeps waiting, so silence has to be
        # turned into a diagnosis on our side.
        if not self._closed_candles and self._stream_started:
            waited = time.time() - self._stream_started
            if waited > self.STALL_SECONDS:
                raise RuntimeError(
                    f"No price data for {asset} after {waited:.0f}s. Pocket Option "
                    f"usually does this when the session cookie is not being "
                    f"accepted — check the balance: if it shows 'not logged in', "
                    f"paste a fresh cookie and do not log out afterwards."
                )

        candles: List[Candle] = []
        for c in list(self._closed_candles)[-count:]:
            candles.append(Candle(
                time=float(c.get("time", 0)),
                open=float(c["open"]),
                high=float(c["high"]),
                low=float(c["low"]),
                close=float(c["close"]),
            ))
        return candles

    async def balance(self) -> float:
        client = self._require()
        bal = await client.balance()
        return float(bal)

    async def place_trade(self, asset: str, amount: float, direction: str,
                          expiry_seconds: int) -> TradeResult:
        client = self._require()
        # buy() / sell() return an order id we then wait on to settle.
        if direction == "call":
            order_id, _ = await client.buy(asset, amount, expiry_seconds)
        else:
            order_id, _ = await client.sell(asset, amount, expiry_seconds)

        # Block until the option expires and PO reports the outcome.
        outcome = await client.check_win(order_id)
        profit = float(outcome.get("profit", 0.0))
        if profit > 0:
            result = "win"
        elif profit < 0:
            result = "loss"
        else:
            result = "draw"
        return TradeResult(str(order_id), direction, amount, result, profit)
