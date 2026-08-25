"""
Recovering a half-saved account on start-up, with nobody clicking anything.

The trap this closes, in the client's own words: "it still says practice
account". His .env held a perfectly good 427-character cookie and no account
id — the state left behind whenever a search fails and clears an id it has
proved wrong. Every start-up after that ended identically: the token is refused
for having no uid, the bot comes up in practice, and the panel prints "also set
PO_UID", which is DevTools homework for somebody with no text editor.

The account search existed and worked. It just only ever ran when a cookie
ARRIVED, so a restart could not recover no matter how good the saved cookie
was, and nothing on screen said another click was needed.
"""

from __future__ import annotations

import threading
import time

import pytest

from core import discover, envfile, uid_lookup
from core.config import BotConfig
from core.web_ui import WebInterface

COOKIE = ("a%3A4%3A%7Bs%3A10%3A%22session_id%22%3Bs%3A32%3A%22"
          "bc01274a27165376b8124c84bd7ec930%22%3B%7D")


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(envfile, "ENV_PATH", str(tmp_path / ".env"))
    web = WebInterface(BotConfig(), "127.0.0.1", 0, "")
    web.env_path = str(tmp_path / ".env")
    web.paper = True
    web.auto_discover = False        # tests must never open a socket to PO
    return web


def _wait_for_the_search() -> None:
    for thread in threading.enumerate():
        if thread.name == "po-account-discovery":
            thread.join(timeout=10)


# ------------------------------------------------------- when it should search
def test_a_saved_cookie_with_no_account_id_is_looked_up_on_startup(panel):
    tried = []
    panel._discover_async = lambda *a, **k: tried.append((a, k))
    panel.config.po_ssid = COOKIE
    panel.config.po_uid = 0
    panel.token_error = "Got the session blob but no account id"

    assert panel.connect_saved() is True
    assert tried, "the cookie in .env was never tried"
    assert tried[0][0][0] == COOKIE
    assert tried[0][1]["via"] == "startup"


def test_the_cookie_is_remembered_so_a_late_account_id_has_something_to_use(panel):
    # An id caught by the listener minutes later needs a cookie to be tried
    # with, and before this there was none held anywhere after a restart.
    panel._discover_async = lambda *a, **k: None
    panel.config.po_ssid = COOKIE
    panel.token_error = "no account id"
    panel.connect_saved()
    assert panel._last_session == COOKIE


# --------------------------------------------------- when it should NOT search
def test_no_saved_cookie_means_there_is_nothing_to_look_up(panel):
    tried = []
    panel._discover_async = lambda *a, **k: tried.append(a)
    panel.config.po_ssid = ""
    assert panel.connect_saved() is False
    assert tried == []


def test_a_token_the_check_accepted_is_left_alone(panel):
    # No complaint from the token check means a connected bot. Searching would
    # spend half a minute re-proving what already works — and a probe that
    # happened to be refused would then clear a perfectly good account id.
    tried = []
    panel._discover_async = lambda *a, **k: tried.append(a)
    panel.config.po_ssid = COOKIE
    panel.config.po_uid = 138033625
    panel.token_error = ""
    assert panel.connect_saved() is False
    assert tried == []


def test_a_saved_auth_frame_with_no_id_is_left_alone_too(panel):
    # The form saved when Pocket Option accepts a session with no account id at
    # all. There is no id to find, nothing was refused, and it is connected.
    panel._discover_async = lambda *a, **k: tried.append(a)
    tried = []
    panel.config.po_ssid = '42["auth",{"session":"%s","isDemo":1,"uid":0}]' % COOKIE
    panel.config.po_uid = 0
    panel.token_error = ""
    assert panel.connect_saved() is False
    assert tried == []


# ------------------------------------------ the website supplies the missing id
def test_ids_from_the_website_are_added_to_the_search(panel, monkeypatch):
    asked = {}

    def fake_lookup(session, log=None, fetch=None):
        asked["session"] = session
        return uid_lookup.Lookup(ids=[555555555, 666666666], logged_in=True)

    async def fake_find(session, uid_hint=0, demo_hint=True, log=None,
                        candidates=None):
        asked["candidates"] = list(candidates or [])
        return None

    monkeypatch.setattr(uid_lookup, "account_ids", fake_lookup)
    monkeypatch.setattr(discover, "find_account", fake_find)
    panel.auto_discover = True
    panel._discover_async(COOKIE, 0, True, [], via="startup")
    _wait_for_the_search()

    assert asked.get("session") == COOKIE
    # These are the ids the search would otherwise never have had. It proves
    # each one by connecting — the lookup only supplies guesses.
    assert asked.get("candidates") == [555555555, 666666666]


def test_a_broken_lookup_never_stops_the_old_routes(panel, monkeypatch):
    reached = {}

    def exploding(session, log=None, fetch=None):
        raise RuntimeError("site changed shape")

    async def fake_find(session, uid_hint=0, demo_hint=True, log=None,
                        candidates=None):
        reached["ran"] = True
        return None

    monkeypatch.setattr(uid_lookup, "account_ids", exploding)
    monkeypatch.setattr(discover, "find_account", fake_find)
    panel.auto_discover = True
    panel._discover_async(COOKIE, 0, True, [777777777], via="bookmarklet")
    _wait_for_the_search()

    assert reached.get("ran") is True
    assert any("Could not ask the website" in line["text"] for line in panel.state()["log"])


def test_a_failed_startup_search_does_not_blame_the_bookmark(panel, monkeypatch):
    # The bookmarklet's failure message tells you to go back to the Pocket
    # Option tab and click it again. After a restart nobody clicked anything,
    # so that text would be describing a step that never happened.
    panel.config.po_ssid = COOKIE
    panel._apply_discovery(None, COOKIE, True, ids_tried=0, via="startup")
    text = " ".join(line["text"] for line in panel.state()["log"])
    assert "saved in your settings" in text
    assert "click the bookmark again" not in text


# --------------------------------------------------------------- the wiring up
def test_startup_actually_calls_it():
    # The method is useless unless main.py runs it, and that path only executes
    # with a real broker and a real event loop. Check the wiring is there.
    with open("main.py", encoding="utf-8") as handle:
        source = handle.read()
    assert "web.connect_saved()" in source
    # And that the old "go and click the button" line is now the fallback for
    # when there is nothing to search, not the first thing printed.
    before = source.index("web.connect_saved()")
    after = source.index("Send a fresh cookie with the one-click button")
    assert before < after
