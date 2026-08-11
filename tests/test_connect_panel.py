"""
Entering the Pocket Option token on the control panel.

This replaced "open the hidden .env file in a text editor and paste a
400-character secret", which was never going to work for a client on a
Chromebook who does not have a text editor and had already lost a day to folder
names. The risky parts are: writing to a real config file, handling a secret,
and swapping accounts on a running trader. All three are covered here.
"""

from __future__ import annotations

import os

import pytest

from core import envfile
from core.config import BotConfig
from core.web_ui import WebInterface

# A structurally valid PHP session blob, URL-encoded exactly as the cookie is.
GOOD = ("a%3A4%3A%7Bs%3A10%3A%22session_id%22%3Bs%3A32%3A%22"
        "bc01274a27165376b8124c84bd7ec930%22%3B%7D")
# The chart socket's token — the thing people copy by mistake.
CHART = "eb29431e026b200e4be64d4061a2901d"


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(envfile, "ENV_PATH", str(tmp_path / ".env"))
    web = WebInterface(BotConfig(), "127.0.0.1", 0, "")
    web.paper = True
    web.env_path = str(tmp_path / ".env")
    return web


def _env(panel):
    return envfile.read(panel.env_path)


# ------------------------------------------------------------- the happy path
def test_a_valid_cookie_is_saved_to_env(panel):
    res = panel.command({"action": "connect", "session": GOOD, "uid": "138033625",
                         "demo": True})
    assert res["ok"] is True
    saved = _env(panel)
    assert saved["PO_SESSION"] == GOOD
    assert saved["PO_UID"] == "138033625"
    assert saved["PO_DEMO"] == "true"


def test_a_leftover_po_ssid_cannot_override_what_was_just_saved(panel):
    # config.from_env reads `PO_SSID or PO_SESSION`, so an old PO_SSID line left
    # in the file would win on the next restart and quietly undo this save.
    envfile.update({"PO_SSID": "stale-old-value"}, panel.env_path)
    panel.command({"action": "connect", "session": GOOD, "uid": "1", "demo": True})
    saved = _env(panel)
    assert saved["PO_SESSION"] == GOOD
    assert saved["PO_SSID"] == ""


def test_saving_preserves_the_rest_of_the_file(panel):
    """The .env is full of explanatory comments; a save must not flatten them."""
    with open(panel.env_path, "w") as fh:
        fh.write("# how much to stake\nSTAKE=5\n\n# panel port\nWEB_PORT=8080\n")
    panel.command({"action": "connect", "session": GOOD, "uid": "1", "demo": True})
    body = open(panel.env_path).read()
    assert "# how much to stake" in body
    assert "STAKE=5" in body
    assert "WEB_PORT=8080" in body
    assert f"PO_SESSION={GOOD}" in body


def test_connecting_updates_the_live_config_not_just_the_file(panel):
    panel.command({"action": "connect", "session": GOOD, "uid": "1", "demo": True})
    assert panel.config.po_ssid == GOOD
    assert panel.config.po_uid == 1
    assert panel.paper is False        # no longer practice data


def test_choosing_live_is_recorded_as_live(panel):
    panel.command({"action": "connect", "session": GOOD, "uid": "1", "demo": False})
    assert _env(panel)["PO_DEMO"] == "false"
    assert panel.config.po_demo is False


def test_the_reload_callback_fires_so_no_restart_is_needed(panel):
    called = []
    panel.reload_cb = lambda: called.append(True)
    res = panel.command({"action": "connect", "session": GOOD, "uid": "1", "demo": True})
    assert called == [True]
    assert "Connecting" in res["message"]


def test_without_a_reload_callback_it_says_how_to_restart(panel):
    panel.reload_cb = None
    res = panel.command({"action": "connect", "session": GOOD, "uid": "1", "demo": True})
    assert res["ok"] is True
    assert "run.sh" in res["message"]


# ------------------------------------------------------------ bad input first
def test_an_empty_paste_is_refused_before_anything_is_written(panel):
    res = panel.command({"action": "connect", "session": "  ", "uid": "1"})
    assert res["ok"] is False
    assert not os.path.exists(panel.env_path)


def test_the_chart_token_is_rejected_with_an_explanation(panel):
    # The single most common wrong copy. It must not be saved and then fail
    # later as a mystery "not connected".
    res = panel.command({"action": "connect", "session": CHART, "uid": "1"})
    assert res["ok"] is False
    assert "trading" in res["message"].lower()
    assert not os.path.exists(panel.env_path)


def test_a_non_numeric_uid_is_refused(panel):
    res = panel.command({"action": "connect", "session": GOOD, "uid": "abc"})
    assert res["ok"] is False
    assert not os.path.exists(panel.env_path)


def test_a_missing_uid_is_refused_rather_than_saved_as_zero(panel):
    res = panel.command({"action": "connect", "session": GOOD, "uid": ""})
    assert res["ok"] is False


def test_an_unwritable_env_reports_failure_instead_of_claiming_success(panel, monkeypatch):
    def boom(*a, **k):
        raise OSError("read-only file system")
    monkeypatch.setattr(envfile, "update", boom)
    res = panel.command({"action": "connect", "session": GOOD, "uid": "1"})
    # Saying "saved" here would be the worst outcome: he restarts and it is
    # still not connected, with nothing to explain why.
    assert res["ok"] is False
    assert "read-only" in res["message"]


# ------------------------------------------------------------ secret handling
def test_the_token_never_comes_back_out_in_the_state_payload(panel):
    panel.command({"action": "connect", "session": GOOD, "uid": "1", "demo": True})
    blob = repr(panel.state())
    assert GOOD not in blob
    assert panel.state()["session_set"] is True
    assert panel.state()["uid"] == 1


def test_the_token_is_never_written_into_the_activity_log(panel):
    panel.command({"action": "connect", "session": GOOD, "uid": "1", "demo": True})
    assert all(GOOD not in entry["text"] for entry in panel.state()["log"])


def test_the_env_file_is_not_world_readable(panel):
    panel.command({"action": "connect", "session": GOOD, "uid": "1", "demo": True})
    assert oct(os.stat(panel.env_path).st_mode)[-3:] == "600"
