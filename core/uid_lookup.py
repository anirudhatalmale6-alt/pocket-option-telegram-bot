"""
Ask Pocket Option's own website which account a session cookie belongs to.

Why this exists
---------------
The trading socket needs TWO things: the session cookie and a numeric account
id (uid). The cookie is easy — the one-click bookmark reads it out of
`document.cookie` in one piece. The uid has been the wall this project has been
stuck against for weeks:

  * DevTools -> Network -> WS -> find the right socket -> read a green frame.
    Asking a non-technical person to do that has failed every time it was tried.
  * The bookmarklet walks the page's own JavaScript state looking for the id.
    That works when the app happens to keep it somewhere reachable, and this
    client's browser is one where it does not. It has never once found it.

Both routes are attempts to get the number out of the BROWSER. But the session
cookie is a login — so the same cookie can simply ask pocketoption.com, from
the bot, and read the answer off the page the site returns to a logged-in user.
No DevTools, no bookmark, nothing for anybody to click.

What this deliberately does NOT do
----------------------------------
It does not decide anything. Pocket Option's markup is not a documented API and
may change without warning, so guessing which of the numbers on a cabinet page
is "the" uid would just be a new way to be silently wrong — and a wrong uid is
refused in complete silence (see core/discover.py). Instead it harvests every
number that could plausibly be an account id and hands the list to
find_account(), which already PROVES each one by connecting with it and reading
a balance. A useless candidate costs a few seconds; a wrong guess accepted as
fact costs another week.

It sends the cookie to pocketoption.com and nowhere else, and it logs lengths
and counts — never the cookie itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple
from urllib.parse import quote

# Pages a logged-in user can open that carry their own account details. More
# than one because a single URL that changes name takes the whole feature with
# it, and each is cheap.
PAGES = (
    "https://pocketoption.com/en/cabinet/",
    "https://pocketoption.com/en/cabinet/demo-quick-high-low/",
    "https://pocketoption.com/en/profile/",
)

TIMEOUT = 12.0
# Enough of the page to contain the app's bootstrap state, without pulling a
# megabyte of chart bundle through somebody's home connection.
MAX_BYTES = 600_000
# discover.py tries each id by connecting, and each attempt can take up to
# CONNECT_TIMEOUT seconds. Five is already the most a person will sit through.
MAX_IDS = 5

_UA = ("Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36")

# Every shape an account id has been seen in, on a page or in embedded JSON.
# Deliberately generous: this list feeds a verifier, not a decision.
#
# Two details that are easy to get wrong and expensive to get wrong:
#
#   * ANY key ending in "uid", not just "uid" itself. The demo and the real
#     balance have different ids, and the demo one — the only one this bot is
#     ever pointed at — turns up under names like `demo_uid`. A pattern that
#     insisted on the bare word would find the real-money id and miss the one
#     that matters.
#   * (?!\d) on the end. Without it, `\d{6,12}` happily matches the FIRST TWELVE
#     digits of a fourteen-digit number and returns a number that never existed.
#     A wrong id is not refused with a message, it is refused in silence.
_ID_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r'\b[a-z_]*uid"?\s*[:=]\s*"?(\d{6,12})(?!\d)', re.I),
    re.compile(r'\b(?:user_?id|account_?id|profile_?id|trader_?id)"?\s*[:=]\s*"?(\d{6,12})(?!\d)', re.I),
    re.compile(r'data-(?:uid|user-?id|account-?id)\s*=\s*"(\d{6,12})"', re.I),
    re.compile(r'/(?:profile|user|trader|cabinet)/(\d{6,12})(?!\d)', re.I),
)

# Markers that say "this page was served to somebody who is logged IN".
_IN_MARKERS = ("cabinet", "logout", "sign-out", "balance", "demo-quick-high-low")
# ...and that it was not: the site bounces an expired cookie to its front page.
_OUT_MARKERS = ("/login", "sign-in", "registration", "auth/login")


@dataclass
class Lookup:
    """What the site said. `ids` is the only part anything acts on."""
    ids: List[int] = field(default_factory=list)
    logged_in: bool = False
    # Set when nothing could be asked at all (no network, DNS, timeout). An
    # empty result with no error means the pages loaded and had no ids in them,
    # which is a completely different thing and must not be reported the same.
    error: str = ""

    @property
    def reached_site(self) -> bool:
        return not self.error


def cookie_header(session: str) -> str:
    """
    Build the Cookie header for a request to pocketoption.com.

    The value must go out PERCENT-ENCODED, which is how the browser stores it
    and the only form that survives a header: a decoded blob contains literal
    double quotes, semicolons and newlinable characters, and a semicolon inside
    a Cookie header ends the cookie early — handing the site half a login and
    getting a logged-out page back, which reads exactly like an expired cookie.
    """
    value = (session or "").strip()
    if value.startswith("a:"):          # decoded blob — put it back
        value = quote(value, safe="")
    return "ci_session=" + value


def _fetch(url: str, session: str) -> Tuple[int, str, str]:
    """
    GET one page with the cookie attached. Returns (status, final_url, body).

    Never raises: every failure is a status of 0 and an empty body, because a
    lookup that cannot reach the site must degrade into "we learned nothing",
    not into a crash on a background thread nobody is watching.
    """
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers={
        "Cookie": cookie_header(session),
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        # Ask for it uncompressed. urllib does not decompress, and a gzipped
        # body would come back as bytes no regex here can read — silently
        # finding nothing on a page that had the answer in it.
        "Accept-Encoding": "identity",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read(MAX_BYTES)
            return (getattr(resp, "status", 200) or 200,
                    resp.geturl(),
                    body.decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:            # 4xx/5xx still have a body
        try:
            body = exc.read(MAX_BYTES).decode("utf-8", "replace")
        except Exception:
            body = ""
        return exc.code, url, body
    except Exception:
        return 0, url, ""


def harvest(text: str) -> List[int]:
    """
    Every plausible account id in a page, most-repeated first.

    Ordering matters more than it looks: each id costs a connect-and-wait, and
    only the first few get tried. A page mentions the id of the account it is
    drawing many times over and an unrelated number once, so frequency is the
    best cheap ranking available. Ties keep the order they appeared in, which
    puts the bootstrap state ahead of the page furniture.
    """
    counts: dict = {}
    order: dict = {}
    for pattern in _ID_PATTERNS:
        for found in pattern.finditer(text or ""):
            try:
                value = int(found.group(1))
            except (TypeError, ValueError):
                continue
            # Same range the bookmarklet and discover.py use, so an id rejected
            # here is not one accepted somewhere else.
            if not (99999 < value < 10 ** 13):
                continue
            counts[value] = counts.get(value, 0) + 1
            order.setdefault(value, len(order))
    return sorted(counts, key=lambda v: (-counts[v], order[v]))


def _logged_in(final_url: str, body: str) -> bool:
    """
    Whether the page came back to a logged-IN visitor.

    Worth knowing on its own. A dead cookie and a cookie whose uid we cannot
    find produce the same end state today — "practice mode, no account" — and
    they need opposite actions from the user: fetch a fresh cookie, versus
    nothing at all. This is the only place the difference is visible.
    """
    low = (body or "").lower()
    url = (final_url or "").lower()
    if any(m in url for m in _OUT_MARKERS) and "cabinet" not in url:
        return False
    return any(m in low for m in _IN_MARKERS)


def account_ids(session: str, log: Optional[Callable[[str], None]] = None,
                fetch: Optional[Callable[[str, str], Tuple[int, str, str]]] = None
                ) -> Lookup:
    """
    Ask pocketoption.com for the account ids this cookie can see.

    `fetch` is injectable so the whole thing is testable without a network or a
    real login; the default is the urllib one above.
    """
    def say(msg: str) -> None:
        if log:
            log(msg)

    get = fetch or _fetch
    session = (session or "").strip()
    if not session:
        return Lookup(error="no cookie to ask with")

    result = Lookup()
    reached = False
    for url in PAGES:
        status, final_url, body = get(url, session)
        if status == 0 and not body:
            continue                     # could not reach this one; try the next
        reached = True
        if _logged_in(final_url, body):
            result.logged_in = True
        for value in harvest(body):
            if value not in result.ids:
                result.ids.append(value)
        # Stop as soon as one page has answered properly. The others exist for
        # when it does not, and three requests to somebody's broker for a
        # question already answered is three chances to be rate-limited.
        if result.ids:
            break

    if not reached:
        result.error = "could not reach pocketoption.com"
        say("Could not reach pocketoption.com to look up your account id — "
            "no internet, or the site is blocking the request.")
        return result

    if result.ids:
        say(f"Pocket Option's website gave up {len(result.ids)} possible "
            f"account id(s) for this cookie. Trying them.")
    elif result.logged_in:
        say("Pocket Option's website answered as a logged-in user but did not "
            "put an account id anywhere this can read.")
    else:
        # Said plainly, because this is the one answer that means the cookie
        # itself is finished — and it is the answer nothing else in this
        # program has ever been able to give.
        say("Pocket Option does not recognise the saved cookie any more — it "
            "answered as if nobody is logged in. Open pocketoption.com, log in, "
            "and click the blue bookmark again (and do not log out afterwards, "
            "logging out kills the cookie).")
    return result


def top_ids(session: str, log: Optional[Callable[[str], None]] = None,
            fetch: Optional[Callable[[str, str], Tuple[int, str, str]]] = None
            ) -> List[int]:
    """The ids worth trying, capped. The convenience form callers actually use."""
    return account_ids(session, log=log, fetch=fetch).ids[:MAX_IDS]
