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
  2. Open DevTools -> Network -> filter "socket" -> click the ws connection.
  3. In the first outgoing frame find the 42["auth",{...}] message; the whole
     42["auth",{"session":"...","isDemo":1,...}] string is your SSID payload.
  Paste it into PO_SSID in your .env. Full walkthrough in docs/SETUP.md.
"""

from __future__ import annotations

import asyncio
from typing import List

from .broker import Broker, TradeResult
from .strategy import Candle

try:
    # Community async client. Import guarded so the module is always importable.
    from BinaryOptionsToolsV2.pocketoption import PocketOptionAsync  # type: ignore
    _HAVE_LIB = True
except Exception:  # pragma: no cover - depends on optional install
    PocketOptionAsync = None  # type: ignore
    _HAVE_LIB = False


class PocketOptionBroker(Broker):
    def __init__(self, ssid: str, demo: bool = True):
        if not ssid:
            raise ValueError("PO_SSID is empty — paste your browser session token (see docs/SETUP.md)")
        self._ssid = ssid
        self._demo = demo
        self._client = None

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
        # The underlying client manages its own socket lifecycle; nothing strictly
        # required here, but we null the reference so reuse fails loudly.
        self._client = None

    def _require(self):
        if self._client is None:
            raise RuntimeError("Broker not connected — call connect() first")
        return self._client

    async def get_candles(self, asset: str, timeframe: int, count: int) -> List[Candle]:
        client = self._require()
        # Returns a list of dicts with time/open/high/low/close keys.
        raw = await client.get_candles(asset, timeframe, count * timeframe)
        candles: List[Candle] = []
        for c in raw[-count:]:
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
