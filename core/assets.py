"""
Live asset + payout scanner.

Why this matters more than it sounds
------------------------------------
On binary options the payout decides the win rate you need just to break even:

    break-even win rate = 100 / (100 + payout)

At a 92% payout you need 52.1% of trades to win. At 68% you need 59.5%. That is
an enormous difference — a strategy that is comfortably profitable on one pair
can be unprofitable on another *with exactly the same signals*. Pocket Option's
payouts differ per asset and change through the day, so picking the pair by
habit ("EUR/USD OTC") is one of the easiest ways to lose money with a perfectly
good strategy.

This module reads Pocket Option's live asset table straight off their chart
WebSocket and reports, per asset: payout, whether it is currently open, and the
expiries it offers. `best_asset()` then picks the highest-paying open pair.

It needs no account at all. Pocket Option serves the asset feed to any client
that completes the socket handshake, so this never touches — and cannot touch —
your balance or your trades. Verified by control test: a deliberately invalid
session gets exactly the same asset table, while a genuine one additionally
gets a `successauth` event that this module neither needs nor uses.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import List, Optional

# `websockets` is a light pure-python dependency (see requirements.txt).
try:
    import websockets
    _HAVE_WS = True
except Exception:  # pragma: no cover - optional at import time
    websockets = None  # type: ignore
    _HAVE_WS = False

DEMO_URL = "wss://demo-api-eu.po.market/socket.io/?EIO=4&transport=websocket"
LIVE_URL = "wss://api-eu.po.market/socket.io/?EIO=4&transport=websocket"

_HEADERS = {
    "Origin": "https://pocketoption.com",
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
}

# Sent when no SSID is supplied. The server drops a socket that never
# authenticates at all, but accepts any well-formed frame for the public asset
# feed — so this is what lets scan_assets.py run with no account.
_ANON_AUTH = ('42["auth",{"session":"anonymous","isDemo":1,"uid":0,"platform":2}]')


@dataclass
class AssetInfo:
    symbol: str            # e.g. "EURUSD_otc" — what you put in PO_ASSET
    name: str              # e.g. "EUR/USD OTC"
    kind: str              # "currency", "stock", "commodity", "cryptocurrency"...
    payout: int            # percent, e.g. 92
    is_open: bool          # tradable right now
    expiries: List[int]    # available expiry lengths, seconds

    @property
    def breakeven_win_rate(self) -> float:
        """Win rate needed just to break even at this payout, in percent."""
        return 100.0 * 100.0 / (100.0 + self.payout) if self.payout else 100.0

    @property
    def min_expiry(self) -> Optional[int]:
        return min(self.expiries) if self.expiries else None


def _parse(rows: list) -> List[AssetInfo]:
    """Turn PO's positional asset array into something readable."""
    out: List[AssetInfo] = []
    for r in rows:
        if not isinstance(r, list) or len(r) < 15:
            continue
        try:
            expiries = [int(e["time"]) for e in r[15]] if len(r) > 15 and isinstance(r[15], list) else []
            out.append(AssetInfo(
                symbol=str(r[1]),
                name=str(r[2]),
                kind=str(r[3]),
                payout=int(r[5]),
                is_open=bool(r[14]),
                expiries=sorted(expiries),
            ))
        except (KeyError, TypeError, ValueError):
            continue  # PO adds fields over time; skip anything we can't read
    return out


async def fetch_assets(ssid: str = "", demo: bool = True,
                       timeout: float = 45.0) -> List[AssetInfo]:
    """
    Connect, authenticate, and return the live asset table.

    `ssid` is optional — leave it empty and an anonymous frame is sent instead.
    The asset table is identical either way.
    """
    if not _HAVE_WS:
        raise RuntimeError("The `websockets` package is required: pip install websockets")

    url = DEMO_URL if demo else LIVE_URL
    pending: Optional[str] = None
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout

    async with websockets.connect(url, additional_headers=_HEADERS,
                                  open_timeout=20, ping_interval=None,
                                  max_size=20_000_000) as ws:
        while loop.time() < deadline:
            remaining = max(1.0, deadline - loop.time())
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break

            if isinstance(msg, bytes):
                # Binary frames carry the payload for the event announced just
                # before them by a "451-[...]" line.
                if pending == "updateAssets":
                    return _parse(json.loads(msg.decode()))
                pending = None
            elif msg.startswith("0{"):
                await ws.send("40")
            elif msg.startswith("40"):
                await ws.send(ssid or _ANON_AUTH)
            elif msg == "2":
                await ws.send("3")          # engine.io keepalive
            elif msg.startswith("451-"):
                try:
                    pending = json.loads(msg[4:])[0]
                except (json.JSONDecodeError, IndexError):
                    pending = None
    raise TimeoutError("Pocket Option did not send the asset table in time — "
                       "check the token, or try again in a minute.")


def best_asset(assets: List[AssetInfo], kind: str = "currency", otc: bool = True,
               expiry: Optional[int] = None) -> Optional[AssetInfo]:
    """Highest-paying asset that is open now and offers `expiry` (if given)."""
    pool = [a for a in assets
            if a.kind == kind
            and a.is_open
            and (("_otc" in a.symbol) == otc)
            and (expiry is None or expiry in a.expiries)]
    return max(pool, key=lambda a: a.payout) if pool else None
