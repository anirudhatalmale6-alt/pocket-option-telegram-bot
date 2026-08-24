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
    # The address is baked in once, as `var O=…`, and every route back to the
    # panel is built from it: the /hook tab, the account-id beacon, and the
    # target postMessage is aimed at.
    assert "JSON.stringify(location.origin)" in PAGE
    assert "'/hook#'" in PAGE


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
        # The address and the path are joined at run time now, so they are two
        # separate pieces of the source rather than one literal.
        "if (BOOKMARKLET.indexOf('http://127.0.0.1:8080') === -1)"
        " throw new Error('address missing');\n"
        "if (BOOKMARKLET.indexOf(\"/hook#\") === -1)"
        " throw new Error('hook path missing');\n"
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


# ------------------------------------------------- the 427-character refusal
#
# What actually happened: the bookmarklet worked perfectly, handed over a whole
# 427-character cookie, and the panel refused it as "cut off". The cookie was
# fine — the truncation check was wrong. These lock the fix in place, because
# the failure was invisible from the outside: everything looked like the client
# had mis-copied something again, and he had not.

def test_the_hook_page_declares_where_the_cookie_came_from(server):
    # This flag is what tells the connect handler no human clipboard was
    # involved. Lose it and the 427-character refusal comes straight back.
    _, body = _get(server, "/hook")
    assert "via:'bookmarklet'" in body.replace(" ", "").replace("\n", "")


def test_a_bookmarklet_cookie_is_never_refused_as_truncated(server):
    # A value the truncation guard hates, arriving by the one route on which a
    # half-copy is impossible. It must get as far as Pocket Option.
    half = "a%3A4%3A%7Bs%3A10%3A%22session_id%22%3Bs%3A32%3A%22bc01274a2716"
    res = server.command({"action": "connect", "session": half,
                          "uid": "", "demo": True, "via": "bookmarklet"})
    assert res["ok"] is True, res.get("message")


def test_the_same_value_pasted_by_hand_is_still_refused(server):
    # The control: without the flag the guard still does its job, so the manual
    # box keeps catching the half-copies that really do happen there.
    half = "a%3A4%3A%7Bs%3A10%3A%22session_id%22%3Bs%3A32%3A%22bc01274a2716"
    res = server.command({"action": "connect", "session": half,
                          "uid": "", "demo": True})
    assert res["ok"] is False
    assert "cut off" in res["message"]


def test_a_real_shaped_cookie_with_a_trailing_hash_is_accepted(server):
    # The actual shape behind the bug: CodeIgniter's hash after the brace.
    cookie = ("a%3A4%3A%7Bs%3A10%3A%22session_id%22%3Bs%3A32%3A%22"
              "bc01274a27165376b8124c84bd7ec930%22%3B%7D"
              "9f86d081884c7d659a2feaa0c55ad015")
    res = server.command({"action": "connect", "session": cookie,
                          "uid": "", "demo": True})
    assert res["ok"] is True, res.get("message")


def _ping(web, path):
    """Like _get, but the answer is a GIF — bytes, not text."""
    port = web._server.server_address[1]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return r.status, r.read()


# ------------------------------------------- the account id that arrives late
#
# Pocket Option only ever hands over the id of the balance you are CURRENTLY
# looking at. So a scrape taken while the real balance is selected finds the
# real id and nothing else, and the practice id only appears later, at the
# moment the account switcher is used. /uid is where that late id lands.
def test_a_late_account_id_starts_a_practice_search(server):
    server._last_session = "a%3A4%3A%7Bs%3A10%3A%22session_id%22%3B%7D"
    tried = []
    server._discover_async = lambda *a, **k: tried.append((a, k))

    status, _ = _ping(server, "/uid?id=987654321&demo=1")
    assert status == 200
    assert tried, "the id arrived and nothing was done with it"
    session, uid, demo = tried[0][0][:3]
    assert uid == 987654321
    assert demo is True


def test_an_id_the_page_called_real_money_is_still_searched_as_practice(server):
    """
    The flag on the page is a hint, not an instruction, and the one thing this
    route must never do is aim the bot at real money on its own. Trying a
    real-money id AS practice costs a few seconds and is refused; the reverse
    is somebody's actual balance.
    """
    server._last_session = "a%3A4%3A%7Bs%3A10%3A%22session_id%22%3B%7D"
    tried = []
    server._discover_async = lambda *a, **k: tried.append(a)

    _ping(server, "/uid?id=138033625&demo=0")
    assert tried and tried[0][2] is True, "a search was aimed at real money"


def test_the_same_id_twice_does_not_start_two_searches(server):
    # Pocket Option's socket re-authenticates on its own, so the listener fires
    # again and again. Each search takes half a minute and writes over the log.
    server._last_session = "a%3A4%3A%7Bs%3A10%3A%22session_id%22%3B%7D"
    tried = []
    server._discover_async = lambda *a, **k: tried.append(a)

    _ping(server, "/uid?id=987654321&demo=1")
    _ping(server, "/uid?id=987654321&demo=1")
    assert len(tried) == 1


def test_an_id_arriving_before_any_cookie_says_so_rather_than_failing(server):
    server._last_session = ""
    _ping(server, "/uid?id=987654321&demo=1")
    assert any("no cookie has been sent yet" in line["text"]
               for line in server._log)


def test_a_nonsense_id_is_ignored_quietly(server):
    server._last_session = "a%3A4%3A%7Bs%3A10%3A%22session_id%22%3B%7D"
    tried = []
    server._discover_async = lambda *a, **k: tried.append(a)

    for bad in ("0", "42", "banana", "99999999999999999999"):
        status, _ = _ping(server, f"/uid?id={bad}&demo=1")
        assert status == 200          # a beacon must never look like a fault
    assert not tried


def test_the_route_answers_the_browsers_private_network_preflight(server):
    """
    Chrome will not let a page on the public internet touch 127.0.0.1 until the
    thing on 127.0.0.1 has said it expects it. Without these headers the beacon
    fails inside the browser, before it is sent — which is invisible from here
    and looks, on the Pocket Option page, exactly like success.
    """
    port = server._server.server_address[1]
    req = urllib.request.Request(f"http://127.0.0.1:{port}/uid",
                                 method="OPTIONS")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 204
        assert r.headers.get("Access-Control-Allow-Private-Network") == "true"
        assert r.headers.get("Access-Control-Allow-Origin") == "*"


# ------------------------------------------- a tab left open across an update
#
# The page is served once and then polls for numbers for ever. Its JAVASCRIPT
# is whatever it was given, and the blue drag-to-bookmark button's address is
# built by that JavaScript. So a tab open across an update hands out the
# PREVIOUS version's bookmark, silently — which is exactly how an afternoon was
# spent on a bookmark that had already been fixed.
def test_the_page_is_stamped_with_the_version_that_served_it(server):
    from core import version
    _, body = _get(server, "/")
    assert "__BUILD__" not in body, "the placeholder was served unfilled"
    assert f"const PAGE_VERSION = '{version.RUNNING}'" in body


def test_the_page_compares_its_own_version_against_the_running_bot(server):
    _, body = _get(server, "/")
    # Stamp plus poll value plus a visible consequence. Any one of the three
    # missing and the page goes back to looking current while being stale.
    assert "PAGE_VERSION !== s.version" in body
    assert "bm-stale" in body
    assert "This page is older than the bot" in body


def test_the_stamp_does_not_disturb_a_checkout_without_git(server, monkeypatch):
    # An unpacked zip has no .git, so RUNNING is "". That must leave a page that
    # simply never claims to be stale, not one that always does.
    from core import version
    monkeypatch.setattr(version, "RUNNING", "")
    _, body = _get(server, "/")
    assert "const PAGE_VERSION = ''" in body
