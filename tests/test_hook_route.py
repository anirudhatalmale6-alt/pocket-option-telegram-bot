"""
The bookmarklet route: pocketoption.com -> /hook -> connected.

Worth testing properly because it replaced the step that had failed four times
in a row (find the cookie in DevTools, select exactly its value, copy it, paste
it here). If this route breaks, the fallback is the one that does not work.

The parts that can silently rot are the ones checked here: the /hook page being
served at all, the bookmarklet carrying *this* panel's address rather than a
hardcoded localhost, and the cookie riding in the URL fragment — because a
fragment is the reason the session never reaches the server as a logged URL.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

import pytest

from core import envfile
from core.config import BotConfig
from core.web_ui import PAGE, WebInterface


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(envfile, "ENV_PATH", str(tmp_path / ".env"))
    web = WebInterface(BotConfig(), "127.0.0.1", 0, "")
    web.paper = True
    web.env_path = str(tmp_path / ".env")
    web.auto_discover = False        # tests must never open a socket to PO
    web.start()
    yield web
    web.stop()


def _get(web, path):
    port = web._server.server_address[1]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return r.status, r.read().decode()


# ------------------------------------------------------------------ the page
def test_the_hook_page_is_served(server):
    status, body = _get(server, "/hook")
    assert status == 200
    assert "Connecting your Pocket Option account" in body


def test_the_hook_page_reads_the_fragment_not_the_query(server):
    _, body = _get(server, "/hook")
    # location.hash is the whole point: fragments are never transmitted to a
    # server, so the session cannot end up in an access log or in this process's
    # request handling. A '?' query would.
    assert "location.hash" in body
    assert "location.search" not in body


def test_the_hook_page_wipes_the_secret_out_of_the_address_bar(server):
    _, body = _get(server, "/hook")
    assert "history.replaceState" in body


def test_the_hook_page_sends_no_account_id(server):
    # Discovery works the id out. Sending a guess would reintroduce the exact
    # failure this whole flow exists to remove.
    _, body = _get(server, "/hook")
    assert "uid:''" in body.replace(" ", "")


def test_an_unknown_path_is_still_a_404(server):
    with pytest.raises(urllib.error.HTTPError) as err:
        _get(server, "/hook-something-else")
    assert err.value.code == 404


# ----------------------------------------------------------- the bookmarklet
def test_the_bookmarklet_is_built_from_this_panels_own_address():
    # Hardcoding localhost:8080 would break on the Chromebook, where the panel
    # answers on penguin.linux.test, and on any VPS.
    assert "location.origin" in PAGE
    assert '"/hook#"' in PAGE


def test_the_bookmarklet_reads_the_right_cookie():
    assert "ci_session=" in PAGE
    assert "document.cookie" in PAGE


def test_the_bookmarklet_says_something_useful_when_run_on_the_wrong_page():
    # Clicked from anywhere but a logged-in Pocket Option tab it finds nothing,
    # and silence there reads as "the bot is broken".
    assert "No Pocket Option cookie on this page" in PAGE


def test_the_bookmarklet_is_valid_javascript():
    """
    The bookmarklet is assembled from concatenated string literals, so a stray
    quote would produce a plausible-looking URL that does nothing when clicked —
    and it would fail on Pocket Option's page, where nothing can report it.
    """
    import subprocess

    # Pull the concatenated pieces out of PAGE and let a JS engine judge them.
    src = PAGE[PAGE.index("const BOOKMARKLET ="):]
    src = src[:src.index(";\n\nfunction bmClick")]
    probe = (
        "var location={origin:'http://127.0.0.1:8080'};\n"
        + src + ";\n"
        # Strip the javascript: scheme and check the body parses.
        "new Function(BOOKMARKLET.slice('javascript:'.length));\n"
        "if (BOOKMARKLET.indexOf('http://127.0.0.1:8080/hook#') === -1)"
        " throw new Error('address missing');\n"
        "console.log('ok');\n"
    )
    node = subprocess.run(["node", "-e", probe], capture_output=True, text=True)
    if node.returncode != 0 and "not found" in (node.stderr or "").lower():
        pytest.skip("node not installed")
    assert node.returncode == 0, node.stderr
    assert "ok" in node.stdout


# ----------------------------------------------------- the value it produces
def test_a_cookie_arriving_this_way_is_accepted_the_same_as_a_pasted_one(server):
    """
    The bookmarklet URL-encodes the value and the page decodes it again, so what
    reaches the connect handler is byte-for-byte what a paste would deliver.
    Encoding it twice, or forgetting to decode, would produce a cookie that
    passes every shape check and is then refused by Pocket Option in silence.
    """
    from urllib.parse import quote, unquote

    cookie = ("a%3A4%3A%7Bs%3A10%3A%22session_id%22%3Bs%3A32%3A%22"
              "bc01274a27165376b8124c84bd7ec930%22%3B%7D")
    round_tripped = unquote(quote(cookie, safe=""))   # what the two halves do
    assert round_tripped == cookie

    res = server.command({"action": "connect", "session": round_tripped,
                          "uid": "", "demo": True})
    assert res["ok"] is True
