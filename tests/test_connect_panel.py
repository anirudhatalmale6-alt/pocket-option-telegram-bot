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
    # Saving a cookie normally kicks off a background check against Pocket
    # Option to confirm which account it opens. Tests must not open sockets.
    web.auto_discover = False
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
    # Name a script that still exists. This used to say "run.sh", which was
    # replaced months ago — the assertion passed the whole time because it only
    # checked for the substring, and "run.sh" is inside "open_panel.sh" too.
    # Instructions that name a missing file are worse than none: they read as a
    # broken install. So check the repo really ships what the message says.
    named = [w.strip(".,") for w in res["message"].split() if w.endswith(".sh")]
    assert named
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for script in named:
        assert os.path.exists(os.path.join(here, script)), f"{script} does not exist"


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


def test_a_missing_uid_is_now_discovered_rather_than_refused(panel):
    # It used to be an error, which meant a trip into DevTools to read a
    # WebSocket frame. The panel finds it instead. Nothing is written until it
    # knows the answer — saving uid 0 would be refused silently by Pocket Option.
    res = panel.command({"action": "connect", "session": GOOD, "uid": ""})
    assert res["ok"] is True
    assert not os.path.exists(panel.env_path)


def test_the_supplied_uid_is_saved_immediately_then_checked(panel):
    # Saved first so a restart is never left with nothing, and verified after.
    calls = []
    panel.auto_discover = True
    panel._discover_async = lambda *a: calls.append(a)

    panel.command({"action": "connect", "session": GOOD, "uid": "555", "demo": True})

    assert _env(panel)["PO_UID"] == "555"
    assert calls == [(GOOD, 555, True, [])]


def test_an_account_with_no_id_is_saved_in_a_form_that_survives_a_restart(panel):
    # If discovery finds Pocket Option accepts the session without an account id,
    # the cookie-plus-uid pair cannot express that: on the next start the broker
    # would refuse to build a frame with uid 0. Store the finished frame instead.
    from core.ssid import normalise

    res = panel._save_account(GOOD, 0, True)
    assert res["ok"] is True
    saved = _env(panel)
    assert saved["PO_SSID"].startswith('42["auth"')
    assert '"uid":0' in saved["PO_SSID"]
    # The value written must be one the broker will accept on the next start.
    assert normalise(saved["PO_SSID"])


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


# ------------------------------------------------- a token startup already refused
def test_a_rejected_cookie_does_not_count_as_having_a_token(panel):
    """
    When main.py cannot build a broker from the saved cookie it falls back to
    practice so the panel stays reachable — the panel is the only way to deliver
    a replacement. But the config still holds that dead cookie, and the panel
    used to read "has a token" straight off it and show "Connecting…" against an
    account it already knew it could never reach. Waiting for a connection that
    cannot arrive is indistinguishable from a slow one; there is nothing on
    screen to tell you to send a new cookie.
    """
    panel.config.po_ssid = "a-cookie-that-was-refused"
    panel.paper = False
    panel.token_error = "That does not look like a Pocket Option session cookie."

    st = panel.state()
    assert st["has_token"] is False
    assert st["token_error"]


def test_sending_a_new_cookie_retires_the_old_complaint(panel):
    panel.token_error = "the previous one was rubbish"
    panel.command({"action": "connect", "session": GOOD, "uid": "1", "demo": True})
    st = panel.state()
    assert st["token_error"] == ""
    assert st["has_token"] is True


def test_practice_can_still_press_start_after_a_rejected_cookie(panel):
    # The fallback's whole purpose is a usable panel. Practice needs no token,
    # so START must not be disabled by the dead one sitting in the config.
    panel.config.po_ssid = "a-cookie-that-was-refused"
    panel.paper = True
    panel.token_error = "no good"
    assert panel.state()["has_token"] is True


# ------------------------------------------- demo is a requirement, not a preference
class _Found:
    """Stands in for discover.Account without importing the network module."""
    def __init__(self, uid, demo, balance, matches_request=True):
        self.uid, self.demo, self.balance = uid, demo, balance
        self.matches_request = matches_request

    @property
    def label(self):
        return ("practice" if self.demo else "real money") + f" (account id {self.uid})"


def test_a_real_money_account_is_never_saved_when_demo_was_asked_for(panel):
    """
    The worst thing this program can do on its own.

    The account search used to try all four (uid, isDemo) combinations in one
    list and keep the first FUNDED one — so an empty demo lost to a live
    account with money in it, and the panel silently saved LIVE and flipped its
    own mode tag. Ask for practice, get connected to real money, with no
    decision from anybody.
    """
    panel.config.po_ssid = ""
    panel._apply_discovery(_Found(555, demo=False, balance=1234.0,
                                  matches_request=False), GOOD, demo=True)

    assert panel.config.po_ssid == ""          # nothing saved
    assert panel.config.po_demo is not False    # never flipped to live
    assert not os.path.exists(panel.env_path)   # and nothing written to disk
    said = " ".join(e["text"] for e in panel.state()["log"])
    assert "NOT" in said and "real money" in said


def test_the_matching_account_is_still_saved_normally(panel):
    # The guard must not break the ordinary path.
    panel._apply_discovery(_Found(555, demo=True, balance=50.0), GOOD, demo=True)
    assert panel.config.po_uid == 555
    assert panel.config.po_demo is True


# ------------------------------- a saved account id belongs to ONE kind of account
#
# Reported as "it tries to connect for about 5 min then fails", with a log
# reading:
#
#   Trying your practice (account id 138033625)…
#   refused — not the practice (account id 138033625)
#
# 138033625 is his REAL-money account. It is not the practice account and never
# could be, so that attempt was doomed before it was sent. The bookmarklet posts
# uid:'' on purpose precisely so the panel can search; `int(raw_uid or c.po_uid
# or 0)` threw that away and resurrected the last id saved.
def _hint(panel, monkeypatch, **body):
    """The uid the account search is actually started with."""
    seen = {}
    panel.auto_discover = True
    monkeypatch.setattr(panel, "_discover_async",
                        lambda session, uid, demo, cands=None:
                            seen.update(uid=uid, demo=demo, cands=cands))
    panel.command({"action": "connect", "session": GOOD, **body})
    return seen


def test_a_real_money_id_is_not_used_as_the_hint_for_a_practice_request(panel, monkeypatch):
    panel.config.po_uid = 138033625      # saved when the real account connected
    panel.config.po_demo = False
    assert _hint(panel, monkeypatch, uid="", demo=True)["uid"] == 0


def test_a_saved_id_is_still_reused_when_the_kind_matches(panel, monkeypatch):
    # The fix must not throw away a genuinely useful hint: re-sending a cookie
    # for the same kind of account should not re-run the whole search blind.
    panel.config.po_uid = 138033625
    panel.config.po_demo = False
    assert _hint(panel, monkeypatch, uid="", demo=False)["uid"] == 138033625


def test_a_typed_id_always_wins(panel, monkeypatch):
    panel.config.po_uid = 138033625
    panel.config.po_demo = False
    assert _hint(panel, monkeypatch, uid="999", demo=True)["uid"] == 999


def test_a_leftover_id_is_never_written_against_the_new_flag(panel):
    """
    The pre-save is what produced "Pocket Option is NOT accepting your login"
    one second after a good cookie arrived.

    Saving uid 138033625 (real) with PO_DEMO=true is a pair Pocket Option
    refuses in silence, so the trader reconnected on it, read balance -1.00 and
    blamed the cookie. The panel had invented the broken pair itself.
    """
    panel.config.po_uid = 138033625
    panel.config.po_demo = False
    panel.auto_discover = False
    panel.command({"action": "connect", "session": GOOD, "uid": "", "demo": True})

    assert not os.path.exists(panel.env_path), (
        "an unverified account id was written to .env before discovery ran")


def test_a_typed_id_is_still_saved_immediately(panel):
    # Someone who dug the id out of DevTools should not lose it to a restart.
    panel.command({"action": "connect", "session": GOOD, "uid": "777", "demo": True})
    assert _env(panel)["PO_UID"] == "777"


# ------------------------- a file written by the old bug must heal itself
def test_a_refused_id_is_cleared_so_the_next_attempt_starts_clean(panel):
    """
    His .env was left holding the REAL id with PO_DEMO=true — written by the
    bug above. That pair claims to be the practice account, so the kind-matches
    test in _connect is satisfied by it and hands the same doomed id straight
    back to the search. Without this, the fix does not reach the one file that
    needs it.
    """
    panel.config.po_uid, panel.config.po_demo = 138033625, True
    panel._apply_discovery(_Found(138033625, demo=False, balance=0.52,
                                  matches_request=False), GOOD, demo=True)
    assert panel.config.po_uid == 0
    assert _env(panel)["PO_UID"] == ""


def test_clearing_the_id_leaves_the_cookie_and_the_demo_choice_alone(panel):
    panel.config.po_uid, panel.config.po_demo = 138033625, True
    panel.config.po_ssid = "a-cookie"
    panel._apply_discovery(None, GOOD, demo=True)
    assert panel.config.po_ssid == "a-cookie"
    assert panel.config.po_demo is True


def test_a_confirmed_id_is_never_cleared(panel):
    panel.config.po_uid, panel.config.po_demo = 555, True
    panel._apply_discovery(_Found(555, demo=True, balance=50.0), GOOD, demo=True)
    assert panel.config.po_uid == 555


# ---------------------- ids scraped off the Pocket Option page reach the search
def test_scraped_ids_are_passed_to_the_account_search(panel, monkeypatch):
    seen = _hint(panel, monkeypatch, uid="", demo=True,
                 uids=["987654321", 555444333])
    assert seen["cands"] == [987654321, 555444333]


def test_junk_in_the_scraped_id_list_is_dropped(panel, monkeypatch):
    """It arrives over HTTP and is about to be put into an auth frame."""
    seen = _hint(panel, monkeypatch, uid="", demo=True,
                 uids=["12", "not-a-number", None, "987654321", "987654321",
                       "9" * 20, {"nested": 1}])
    assert seen["cands"] == [987654321]


def test_no_scraped_ids_is_not_an_error(panel, monkeypatch):
    assert _hint(panel, monkeypatch, uid="", demo=True)["cands"] == []


# ------------------------------- an old bookmark is invisible unless we say so
#
# Updating the bot does not update a bookmark: the JavaScript was copied into
# Chrome when it was made. An old one still sends the cookie, so nothing looks
# broken — it just never sends account ids and the search keeps failing for a
# reason nobody can see.
def test_a_bookmark_that_sends_no_ids_at_all_is_called_out(panel):
    # The /hook page is always served by the CURRENT server, so it always posts
    # a uids field and an old bookmark is invisible from here. It has to say so
    # explicitly, and this asserts on the flag it actually sends.
    panel.command({"action": "connect", "session": GOOD, "uid": "", "demo": True,
                   "uids": [], "stale": True, "via": "bookmarklet"})
    said = " ".join(e["text"] for e in panel.state()["log"])
    assert "older version" in said


def test_a_current_bookmark_that_found_no_ids_is_not_nagged(panel):
    """An empty list means the page had none — not that the bookmark is old."""
    panel.command({"action": "connect", "session": GOOD, "uid": "", "demo": True,
                   "uids": [], "stale": False, "via": "bookmarklet"})
    said = " ".join(e["text"] for e in panel.state()["log"])
    assert "older version" not in said


def test_typing_the_cookie_in_by_hand_is_not_called_an_old_bookmark(panel):
    panel.command({"action": "connect", "session": GOOD, "uid": "", "demo": True})
    said = " ".join(e["text"] for e in panel.state()["log"])
    assert "older version" not in said
