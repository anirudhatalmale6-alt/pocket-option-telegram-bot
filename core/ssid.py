"""
Turn whatever you managed to copy out of the browser into a valid Pocket Option
SSID auth payload.

Why this file exists
--------------------
Getting the session token out of Pocket Option is the single fiddliest step in
setting this bot up, and it is easy to copy the *wrong* thing. Pocket Option's
web app opens more than one WebSocket to the same server:

  * a CHART socket, which authenticates with
        42["auth",{"sessionToken":"<32 hex>","uid":"...","isChart":1}]
    and only streams prices and the asset list, and

  * a TRADING socket, which authenticates with
        42["auth",{"session":"<long PHP session blob>","isDemo":1,"uid":123,"platform":2}]
    and answers with 451-["successauth",...]. This is the one that can read your
    balance and place orders.

They look almost identical in DevTools, so the chart one gets copied by mistake
all the time. Rather than fail with a vague "not connected", `normalise()`
recognises that case and says exactly what to grab instead.

It also accepts the easier route: the value of the `ci_session` cookie
(DevTools -> Application -> Cookies), which is the same session blob, plus your
numeric uid. Give it those two and it builds the auth line for you.

Everything here is pure string handling — no network, no dependencies — so it is
fully unit-tested in tests/test_ssid.py.
"""

from __future__ import annotations

import json
import re
from typing import Optional
from urllib.parse import unquote


class SsidError(ValueError):
    """Raised with a plain-English explanation of what to copy instead."""


# A PHP-serialised session looks like: a:4:{s:10:"session_id";s:32:"...";...}
_PHP_SESSION_RE = re.compile(r'^a:\d+:\{')
# The chart socket's token is a bare 32-character hex string.
_HEX32_RE = re.compile(r'^[0-9a-f]{32}$', re.I)

_CHART_HELP = (
    "That is the CHART socket's token, not the trading one — it can stream "
    "prices but it cannot read your balance or place trades.\n"
    "In DevTools -> Network -> WS there is more than one connection. Open each "
    "one's Messages tab and find the one that contains a line reading\n"
    '  451-["successauth",...]\n'
    "The green outgoing 42[\"auth\",...] line just above it is the one to copy. "
    "Its session value is long and full of backslashes and contains "
    "'session_id'.\n"
    "Easier alternative: DevTools -> Application -> Cookies -> "
    "https://pocketoption.com -> copy the value of the 'ci_session' cookie, and "
    "set PO_SESSION to that plus PO_UID to your account id."
)


def _has_session_shape(value: str) -> bool:
    """True if `value` really is a PHP session blob, encoded or not."""
    return bool(_PHP_SESSION_RE.match(unquote(value or "")))


def _looks_truncated(value: str) -> bool:
    """
    True if a PHP session blob has been cut off partway through.

    Chrome shows the cookie in a single-line cell in Application -> Cookies. A
    double-click there selects a *word*, not the whole value, so copying half a
    cookie is easy — and half a cookie still starts with 'a:4:{', so every shape
    check above it passes. Pocket Option then refuses it, and the only symptom is
    the balance tile reading "not logged in" with nothing to say why.

    A complete blob always ends with the '}' that closes the a:N:{ it opened
    with. That is cheap to check and cannot be faked by a partial copy.
    """
    return not unquote(value or "").strip().endswith("}")


_TRUNCATED_HELP = (
    "That cookie is cut off — it starts correctly but the end is missing, so "
    "only part of it was copied.\n"
    "In DevTools -> Application -> Cookies, do not double-click the value "
    "(that selects one word). Right-click the ci_session row and choose "
    "'Copy value', or click once in the value cell and press Ctrl+A then Ctrl+C.\n"
    "A whole one is several hundred characters long and finishes with a '}'."
)


# Matches the cookie as it appears in a whole Cookie header, e.g.
#   lang=en; ci_session=a%3A4%3A%7B...%7D; po_uuid=...
_COOKIE_FIELD_RE = re.compile(r'ci_session=([^;\s]+)')


def clean_session_input(raw: str) -> str:
    """
    Pull the ci_session value out of whatever got pasted.

    Copying exactly one cookie value out of DevTools turns out to be the hardest
    manual step in this whole setup, so accept the easier copies too: the whole
    Cookie header, or 'ci_session=...' on its own. Anything without that marker
    — an auth frame, a bare blob — is returned untouched.
    """
    text = (raw or "").strip().strip('"').strip("'")
    found = _COOKIE_FIELD_RE.search(text)
    if not found:
        return text
    value = found.group(1)
    if _looks_truncated(value):
        # A Cookie header separates its fields with ';', but a session blob in
        # its *decoded* form contains ';' inside itself — so stopping at the
        # first one would cut a decoded paste to pieces. When that happens, the
        # rest of the line is the better reading.
        rest = text[found.end(1) - len(value):].strip()
        if not _looks_truncated(rest):
            return rest
    return value


def _canonical(session: str, uid: int, demo: bool) -> str:
    """Build the exact auth frame the trading socket expects."""
    payload = {
        "session": session,
        "isDemo": 1 if demo else 0,
        "uid": uid,
        "platform": 2,
    }
    # separators=(",", ":") keeps the frame byte-identical to the browser's.
    return '42["auth",' + json.dumps(payload, separators=(",", ":")) + "]"


def _extract_json(raw: str) -> Optional[dict]:
    """Pull the {...} object out of a 42["auth",{...}] frame, or a bare object."""
    raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        obj = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def normalise(raw: str, uid: int = 0, demo: bool = True) -> str:
    """
    Return a valid `42["auth",{...}]` string, or raise SsidError explaining why not.

    `raw` may be:
      * a full 42["auth",{"session":...}] line copied from the trading socket,
      * the bare {"session":...} object,
      * just the session blob / ci_session cookie value (then `uid` is required).

    `uid` and `demo` are only used when the input does not already carry them.
    """
    if raw is None:
        raise SsidError("No Pocket Option token supplied — set PO_SSID in your .env.")
    raw = clean_session_input(raw)
    if not raw:
        raise SsidError("No Pocket Option token supplied — set PO_SSID in your .env.")

    obj = _extract_json(raw)
    if obj is not None:
        # Copied a frame or an object. Decide which socket it came from.
        session = obj.get("session")
        if not session:
            if obj.get("sessionToken") or obj.get("isChart"):
                raise SsidError(_CHART_HELP)
            raise SsidError(
                "That line has no 'session' field, so it is not the trading "
                "auth frame.\n" + _CHART_HELP
            )
        # uid may arrive as a string ("138033625") — normalise it to an int.
        raw_uid = obj.get("uid", uid)
        try:
            uid_val = int(str(raw_uid).strip() or 0)
        except ValueError:
            uid_val = uid
        if not uid_val and not _has_session_shape(str(session)):
            # A frame carrying a real session blob but no uid is one core/discover.py
            # built on purpose, to test whether Pocket Option derives the account
            # from the session alone. Trust it. Anything else with a missing uid is
            # a human copying the wrong line, and still gets told so.
            raise SsidError("The auth line has no uid — set PO_UID to your account id.")
        if _has_session_shape(str(session)) and _looks_truncated(str(session)):
            raise SsidError(_TRUNCATED_HELP)
        demo_val = bool(obj.get("isDemo", 1 if demo else 0))
        return _canonical(str(session), uid_val, demo_val)

    # Not JSON: treat it as a bare session value (e.g. the ci_session cookie).
    candidate = unquote(raw)
    if _HEX32_RE.match(candidate):
        raise SsidError(_CHART_HELP)
    if not _PHP_SESSION_RE.match(candidate):
        raise SsidError(
            "That does not look like a Pocket Option session.\n"
            "Expected either the whole 42[\"auth\",{...}] line from the trading "
            "socket, or the ci_session cookie value (it starts with 'a:4:{').\n"
            + _CHART_HELP
        )
    if _looks_truncated(candidate):
        raise SsidError(_TRUNCATED_HELP)
    if not uid:
        raise SsidError(
            "Got the session blob but no account id — also set PO_UID "
            "(the numeric uid shown in the auth frame / your PO profile)."
        )
    return _canonical(candidate, uid, demo)


def session_value(raw: str) -> str:
    """
    Return just the session blob, whether `raw` is a whole auth frame or the
    bare ci_session cookie.

    core/discover.py rebuilds the auth frame with different uid/isDemo values, so
    it needs the session on its own. Feeding it a frame by mistake would nest one
    inside the other and every combination would be refused for the wrong reason.
    """
    raw = (raw or "").strip().strip('"').strip("'")
    obj = _extract_json(raw)
    if obj is not None and obj.get("session"):
        return str(obj["session"])
    return raw


def looks_like_chart_token(raw: str) -> bool:
    """True if `raw` is the chart socket's token. Used for friendlier warnings."""
    if not raw:
        return False
    obj = _extract_json(raw)
    if obj is not None:
        return not obj.get("session") and bool(obj.get("sessionToken") or obj.get("isChart"))
    return bool(_HEX32_RE.match(unquote(raw.strip())))
