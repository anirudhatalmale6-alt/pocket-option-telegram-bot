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
    # False when this is NOT the kind of account that was asked for — found by
    # the diagnostic sweep below, and never safe to save without being asked.
    matches_request: bool = True

    @property
    def label(self) -> str:
        which = "practice" if self.demo else "real money"
        who = f"account id {self.uid}" if self.uid else "no account id"
        return f"{which} ({who})"


def _uids(uid_hint: int, candidates: Optional[List[int]] = None) -> List[int]:
    """
    The account ids worth trying, best guess first.

    `candidates` are ids the bookmarklet scraped off Pocket Option's own page.
    The demo balance and the real balance have different ids and the cookie
    carries neither, so when the saved id is the wrong one these are the only
    thing standing between the user and reading a WebSocket frame in DevTools.

    uid 0 is included because Pocket Option may derive the account from the
    session alone; if it does, that combination works and the uid never
    mattered. It goes last: it is the least likely to be accepted, and every
    combination costs up to CONNECT_TIMEOUT seconds of somebody's afternoon.
    """
    out: List[int] = [uid_hint] if uid_hint else []
    for uid in (candidates or []):
        # Bounded deliberately. Each extra id is another connect-and-wait, and
        # a page with a dozen numeric ids in storage would turn one click into
        # a ten-minute stare at a spinner.
        if uid and uid not in out and len(out) < 5:
            out.append(uid)
    return out + [0]


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


async def _search(session: str, uids: List[int], demo: bool,
                  say: Callable[[str], None]) -> Optional[Account]:
    """Try every uid for ONE kind of account. First accepted wins."""
    empty: Optional[Account] = None

    for uid in uids:
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
        # Accepted, but nothing in it. Keep looking for a funded one under the
        # same kind; if every other uid is refused, this is still the answer.
        say("  accepted, but the balance is zero")
        if empty is None:
            empty = found

    return empty


async def find_account(session: str, uid_hint: int = 0, demo_hint: bool = True,
                       log: Optional[Callable[[str], None]] = None,
                       candidates: Optional[List[int]] = None
                       ) -> Optional[Account]:
    """
    Find which account this session cookie opens, for the kind that was ASKED
    FOR — demo or real — and never the other one by accident.

    The kind is not a preference to be optimised away. This used to try all four
    (uid, isDemo) combinations in one flat list and keep the first FUNDED one,
    which meant a demo balance of zero lost to a real-money balance that had
    money in it: ask for practice, get handed a live account, silently, because
    the live one looked like the better answer. On a bot that places trades by
    itself that is not a wrong guess, it is someone's actual money. This project
    has already cost the client $200 to exactly that class of mistake.

    So the requested kind is searched on its own and wins if anything at all
    accepts it — funded or empty. Only when NOTHING of that kind is reachable
    does it look at the other one, purely to be able to say what happened
    ("your real account answers, your demo does not"), and what it finds is
    returned with matches_request=False. A caller must not save that without
    asking a human first.

    Returns None when every combination is refused, which means the cookie
    itself is dead, not that the account id is wrong.

    `log` receives one line per attempt so the control panel can show progress;
    an unattended run without it behaves identically.
    """
    def say(msg: str) -> None:
        if log:
            log(msg)

    # Accept either form of input: the saved PO_SSID may already be a whole auth
    # frame, and rebuilding a frame around a frame would refuse everything.
    session = session_value(session)
    uids = _uids(uid_hint, candidates)
    extra = len(uids) - (2 if uid_hint else 1)
    if extra > 0:
        say(f"Found {extra} account id(s) on the Pocket Option page to try as "
            f"well as the usual ones.")

    wanted = await _search(session, uids, demo_hint, say)
    if wanted is not None:
        return wanted

    # Nothing of the requested kind answered. Look at the other kind ONLY to
    # explain why — a cookie that opens the real account but not the demo is a
    # completely different problem from a dead cookie, and telling them apart
    # is the difference between "paste a fresh cookie" and "your demo needs
    # opening on Pocket Option first".
    other = "real money" if demo_hint else "practice"
    say(f"Nothing answered for that account. Checking whether your {other} "
        f"account responds, so we know which problem this is…")
    found = await _search(session, uids, not demo_hint, say)
    if found is not None:
        found.matches_request = False
    return found
