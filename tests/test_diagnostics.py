"""
The report he pastes to me instead of photographing his screen.

Every fault report on this project has arrived as a photo of a laptop, at an
angle, cropped — and five separate times the one field that would have settled
the question was outside the frame. This route exists so a report is a paste.

Two things must hold, and the second one matters more than the first: it has to
carry enough to diagnose with, and it must NEVER carry the session cookie. It is
written to be pasted into a chat window, so a leak here is a leak of his trading
account to a third party, permanently, in someone else's message history.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from core import envfile
from core.config import BotConfig
from core.web_ui import WebInterface

# Shaped like the real thing — PHP-serialised and URL-encoded — so a scrub that
# only handles plain strings would still be caught.
SECRET = ("a%3A4%3A%7Bs%3A10%3A%22session_id%22%3Bs%3A32%3A"
          "%22DEADBEEFCAFEBABEDEADBEEFCAFEBABE%22%3B%7D")


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(envfile, "ENV_PATH", str(tmp_path / ".env"))
    cfg = BotConfig()
    cfg.po_ssid = SECRET
    web = WebInterface(cfg, "127.0.0.1", 0, "")
    web.paper = True
    web.env_path = str(tmp_path / ".env")
    web.auto_discover = False
    web.start()
    yield web
    web.stop()


def _report(web, password=""):
    port = web._server.server_address[1]
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/diagnostics")
    if password:
        req.add_header("X-Auth", password)
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.read().decode()


# ------------------------------------------------------ what must not be in it
def test_the_session_cookie_is_never_in_the_report(server):
    _, body = _report(server)
    assert SECRET not in body
    assert "DEADBEEFCAFEBABE" not in body


def test_a_log_line_that_leaked_the_cookie_is_scrubbed(server):
    """
    Belt and braces. Every line is written not to contain the session, and this
    does not trust that — one careless f-string in a future exception handler is
    all it would take, and the report is the one thing that leaves the machine.
    """
    server.log("connect failed for " + SECRET)
    _, body = _report(server)
    assert "DEADBEEFCAFEBABE" not in body
    assert "[cookie removed]" in body


def test_it_says_a_cookie_is_set_without_saying_what_it_is(server):
    """The length alone separates 'pasted nothing', 'pasted a truncated value'
    and 'pasted the wrong field', which is most of what I need from it."""
    _, body = _report(server)
    assert "cookie          " in body
    assert "characters" in body


# ------------------------------------------------------ what must be in it
def test_practice_is_named_as_a_simulator_not_an_account(server):
    """
    The single most expensive misunderstanding on this project: he has twice
    watched a practice run believing it was his Pocket Option demo. A report
    that just said 'PRACTICE' would repeat it.
    """
    _, body = _report(server)
    assert "PRACTICE" in body
    assert "SIMULATOR" in body
    assert "nothing sent to Pocket Option" in body


def test_the_settings_i_keep_having_to_ask_for_are_in_it(server):
    _, body = _report(server)
    for field in ("strategy", "stake", "expiry", "candle size",
                  "stop on loss", "stop on profit", "martingale"):
        assert field in body, f"{field!r} missing — that is another day of asking"


def test_the_recent_log_comes_with_it(server):
    server.log("Trying your practice account (account id 138033625)…")
    _, body = _report(server)
    assert "138033625" in body


def test_the_verdict_is_a_sentence_not_a_python_dict(server):
    """It is pasted into a chat window and read by a human, not parsed."""
    _, body = _report(server)
    assert "{'state'" not in body and '{"state"' not in body


# ------------------------------------------------------------------ the lock
def test_the_report_is_behind_the_password_when_one_is_set(tmp_path, monkeypatch):
    """It lists the pairs, the stake and how much is being risked. That is the
    same class of thing as /api/state, and it is locked the same way."""
    monkeypatch.setattr(envfile, "ENV_PATH", str(tmp_path / ".env"))
    cfg = BotConfig()
    cfg.po_ssid = SECRET
    web = WebInterface(cfg, "127.0.0.1", 0, "hunter2")
    web.paper = True
    web.env_path = str(tmp_path / ".env")
    web.auto_discover = False
    web.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _report(web)
        assert exc.value.code == 401
        status, body = _report(web, "hunter2")
        assert status == 200 and "DIAGNOSTICS" in body
    finally:
        web.stop()


# --------------------------------------------------------------- the button
def test_the_panel_offers_the_button_and_does_not_ask_for_a_photo(server):
    from core.web_ui import PAGE

    assert "copyDiag()" in PAGE
    assert "/api/diagnostics" in PAGE
    # The fallback matters more than the clipboard call: the address that works
    # when localhost does not (penguin.linux.test) is not a secure context, so
    # navigator.clipboard is unavailable there and the box is the real path.
    assert "diagbox" in PAGE


# ------------------------------------------------ the connection story survives
#
# The report shows the last 40 log lines. A running bot writes two per trade, so
# on a busy pair those 40 lines cover about ninety seconds — while connecting
# happens once, at start-up, and then writes nothing more. Every report pasted
# so far has arrived with the trading in it and the connection answer gone: the
# search result, the reason the cookie was refused, whether the bookmark was
# even the current one. Those are the only lines that say what to do next, and
# they were the only ones guaranteed to be missing.
def test_the_reason_it_is_not_connected_survives_a_burst_of_trading(server):
    server.log("Pocket Option does not recognise the saved cookie any more.",
               connect=True)
    for i in range(200):                     # ~100 trades' worth of noise
        server.log(f"ENTRY PUT EURUSD_otc stake 1.00 exp 60s ({i})")
        server.log(f"WIN +0.80 ({i})")

    _, body = _report(server)
    assert "does not recognise the saved cookie" in body, \
        "the connection answer was scrolled away by the trading feed"
    # And it is genuinely out of the trading log, not just still inside its 40.
    tail = body.split("LAST 40 LOG LINES")[1]
    assert "does not recognise the saved cookie" not in tail


def test_the_connection_section_is_there_even_before_anything_is_tried(server):
    _, body = _report(server)
    assert "----- CONNECTION" in body
    assert "has not tried to connect at all" in body


def test_trading_lines_do_not_leak_into_the_connection_section(server):
    server.log("Looking the account up now.", connect=True)
    server.log("ENTRY PUT EURUSD_otc — bounced off support, no account id needed")
    story = server.diagnostics().split("----- CONNECTION")[1] \
                                .split("LAST 40 LOG LINES")[0]
    assert "Looking the account up" in story
    assert "ENTRY PUT" not in story, \
        "the connection section is filling up with trades"


def test_the_bookmark_version_is_reported(server):
    # A bookmark saved in Chrome months ago cannot be seen any other way: an old
    # one sends a perfectly good cookie and then silently cannot do the watching
    # step, so from the server's side it is identical to a current one.
    _, body = _report(server)
    assert "no cookie sent on this run" in body

    server.bookmark_note = "OLD version — it cannot watch for your account id."
    _, body = _report(server)
    assert "OLD version" in body


def test_the_connection_section_is_scrubbed_too(server):
    # It is the section most likely to quote something that came off the wire.
    from core.ssid import session_value
    server.log("Cookie not accepted: " + session_value(SECRET), connect=True)
    _, body = _report(server)
    assert "DEADBEEFCAFEBABE" not in body


def test_the_story_does_not_grow_without_limit(server):
    for i in range(200):
        server.log(f"searching {i}", connect=True)
    assert len(server._connect_log) <= 24
    _, body = _report(server)
    assert "searching 199" in body
