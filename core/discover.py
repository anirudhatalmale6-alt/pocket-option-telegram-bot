"""
Work out which Pocket Option account a session cookie belongs to.

Why this exists
---------------
Pocket Option's auth frame carries three things: the session blob, an `isDemo`
flag and a numeric `uid`. The demo balance and the real balance have DIFFERENT
uids, and sending a uid that does not match the flag is refused *silently* —
the socket still opens, the balance reads -1.00 forever, and no price data ever
arrives. Nothing anywhere says "wrong account id".

The documented way to get a matching pair is to read the outgoing auth frame in
DevTools. That asks someone to open DevTools, pick the right one of three
WebSockets, scroll a message list and not copy their own password into a chat
window. It cost this project two days.

So: don't ask. There are only four sensible combinations, and each one can be
tested in a few seconds by connecting and reading the balance. Try them, keep
the one Pocket Option accepts.

No trades are placed here. It connects, reads a balance, and disconnects.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from .ssid import _canonical, session_value

# A connect that has not answered in this long is not going to.
CONNECT_TIMEOUT = 25.0
BALANCE_TIMEOUT = 10.0
# Pocket Option sends the balance shortly after auth, not with it. Poll a few
# times before deciding a combination was refused.
BALANCE_READS = 5
BALANCE_GAP = 2.0


@dataclass
class Account:
    """A combination Pocket Option accepted, and what it was worth."""
    uid: int
    demo: bool
    balance: float

    @property
    def label(self) -> str:
        which = "practice" if self.demo else "real money"
        who = f"account id {self.uid}" if self.uid else "no account id"
        return f"{which} ({who})"


def _candidates(uid_hint: int, demo_hint: bool) -> List[Tuple[int, bool]]:
    """
    The combinations worth trying, best guess first.

    Ordered so the common case — the details already saved are simply correct —
    costs one attempt, while a wrong uid still gets found without asking anyone
    to open DevTools. uid 0 is included because Pocket Option may derive the
    account from the session alone; if it does, that combination works and the
    uid never mattered.
    """
    out: List[Tuple[int, bool]] = []
    for uid in ([uid_hint] if uid_hint else []) + [0]:
        for demo in (demo_hint, not demo_hint):
            if (uid, demo) not in out:
                out.append((uid, demo))
    return out


async def _try_one(session: str, uid: int, demo: bool) -> Optional[float]:
    """
    Connect with one combination and return the balance, or None if refused.

    A balance of -1.0 is the library's way of saying "never authenticated", so
    it counts as a refusal rather than an empty account. 0.0 is genuinely
    ambiguous — an accepted login on an empty account looks identical — so it is
    returned as-is and judged by the caller.
    """
    from .po_broker import PocketOptionBroker

    broker = PocketOptionBroker.from_auth_frame(_canonical(session, uid, demo))
    try:
        await asyncio.wait_for(broker.connect(), CONNECT_TIMEOUT)
        for attempt in range(BALANCE_READS):
            try:
                bal = float(await asyncio.wait_for(broker.balance(), BALANCE_TIMEOUT))
            except (asyncio.TimeoutError, Exception):
                bal = -1.0
            if bal > 0:
                return bal
            if bal == 0.0:
                return 0.0
            if attempt < BALANCE_READS - 1:
                await asyncio.sleep(BALANCE_GAP)
        return None
    except (asyncio.TimeoutError, Exception):
        return None
    finally:
        try:
            await broker.close()
        except Exception:
            pass


async def find_account(session: str, uid_hint: int = 0, demo_hint: bool = True,
                       log: Optional[Callable[[str], None]] = None
                       ) -> Optional[Account]:
    """
    Find a combination Pocket Option accepts for this session cookie.

    Returns the first funded account found, falling back to an accepted-but-empty
    one if that is all there is. Returns None when every combination is refused —
    which means the cookie itself is dead, not that the account id is wrong.

    `log` receives one line per attempt so the control panel can show progress;
    an unattended run without it behaves identically.
    """
    def say(msg: str) -> None:
        if log:
            log(msg)

    # Accept either form of input: the saved PO_SSID may already be a whole auth
    # frame, and rebuilding a frame around a frame would refuse everything.
    session = session_value(session)
    empty: Optional[Account] = None

    for uid, demo in _candidates(uid_hint, demo_hint):
        found = Account(uid, demo, 0.0)
        say(f"Trying your {found.label}…")
        bal = await _try_one(session, uid, demo)

        if bal is None:
            say(f"  refused — not the {found.label}")
            continue
        if bal > 0:
            found.balance = bal
            say(f"  accepted — balance {bal:,.2f}")
            return found
        # Accepted, but nothing in it. Keep looking for a funded one; if every
        # other combination is refused outright, this is still the right answer.
        say("  accepted, but the balance is zero")
        if empty is None:
            empty = found

    return empty
