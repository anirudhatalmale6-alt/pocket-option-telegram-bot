"""
The real-money guard on the control panel's START button.

Context, because it explains why this is worth testing at all: the client ran
this bot on a funded account and lost $200 while no strategy in the project had
yet beaten its break-even line on real data (docs/RESULTS.md — 0 of 28
combinations). Practice, demo and live all looked and felt identical at the
moment of pressing START. That is a defect in the panel, not in his judgement.

So: starting a LIVE account takes one extra, informed press. Practice and demo
are unchanged — friction there would just teach him to click through it.
"""

from __future__ import annotations

from core.config import BotConfig
from core.web_ui import WebInterface


def _panel(*, paper: bool, demo: bool, payout: float = 80.0) -> WebInterface:
    cfg = BotConfig()
    cfg.po_ssid = 'x' if not paper else ''
    cfg.po_demo = demo
    cfg.payout_percent = payout
    web = WebInterface(cfg, "127.0.0.1", 0, "")
    web.paper = paper
    return web


# ------------------------------------------------------- who counts as live
def test_practice_is_not_live():
    assert _panel(paper=True, demo=True)._is_live() is False


def test_demo_account_is_not_live():
    assert _panel(paper=False, demo=True)._is_live() is False


def test_funded_account_is_live():
    assert _panel(paper=False, demo=False)._is_live() is True


# ------------------------------------------------------------- the gate
def test_practice_starts_with_one_press():
    web = _panel(paper=True, demo=True)
    assert web.command({"action": "start"})["ok"] is True
    assert web.config.running is True


def test_demo_starts_with_one_press():
    web = _panel(paper=False, demo=True)
    assert web.command({"action": "start"})["ok"] is True
    assert web.config.running is True


def test_live_does_not_start_on_the_first_press():
    web = _panel(paper=False, demo=False)
    res = web.command({"action": "start"})
    assert res["ok"] is False
    assert res["needs_live_confirm"] is True
    # The important assertion: nothing was set running.
    assert web.config.running is False


def test_live_starts_once_confirmed():
    web = _panel(paper=False, demo=False)
    res = web.command({"action": "start", "confirm_live": True})
    assert res["ok"] is True
    assert web.config.running is True


def test_the_warning_states_the_break_even_rate_not_just_a_scare():
    # A warning that only says "are you sure?" trains people to click yes. This
    # one has to carry the number that decides whether the bot can win at all.
    web = _panel(paper=False, demo=False, payout=92.0)
    msg = web.command({"action": "start"})["message"]
    assert "52.1%" in msg          # 100 / (100 + 92)
    assert "92%" in msg


def test_the_warning_tracks_the_payout():
    web = _panel(paper=False, demo=False, payout=80.0)
    assert "55.6%" in web.command({"action": "start"})["message"]


def test_the_warning_calls_out_martingale_when_it_is_armed():
    # Martingale plus an unproven edge is how a $5 stake becomes a $200 loss.
    web = _panel(paper=False, demo=False)
    web.config.martingale.enabled = True
    assert "MARTINGALE IS ON" in web.command({"action": "start"})["message"]


def test_the_warning_stays_quiet_about_martingale_when_it_is_off():
    web = _panel(paper=False, demo=False)
    web.config.martingale.enabled = False
    assert "MARTINGALE" not in web.command({"action": "start"})["message"]


def test_stop_never_needs_confirming():
    """Getting out must never be harder than getting in."""
    web = _panel(paper=False, demo=False)
    web.config.running = True
    assert web.command({"action": "stop"})["ok"] is True
    assert web.config.running is False


def test_state_exposes_is_live_for_the_standing_banner():
    assert _panel(paper=False, demo=False).state()["is_live"] is True
    assert _panel(paper=False, demo=True).state()["is_live"] is False


# ------------------------------------------ plugins reachable from the panel
def test_plugin_strategies_appear_in_the_dropdown():
    ids = [s["id"] for s in _panel(paper=True, demo=True).state()["strategies"]]
    assert "confluence" in ids                      # built-ins still there
    assert "plugin:example_ema_cross" in ids        # and the drop-in folder


def test_a_plugin_can_be_selected_from_the_panel():
    web = _panel(paper=True, demo=True)
    res = web.command({"action": "settings", "strategy": "plugin:example_ema_cross"})
    assert res["ok"] is True
    assert web.config.strategy_mode == "plugin:example_ema_cross"


def test_an_unknown_strategy_is_still_rejected():
    web = _panel(paper=True, demo=True)
    assert web.command({"action": "settings", "strategy": "plugin:nope"})["ok"] is False


# ----------------------------------------- whose money is on the balance tile
def test_practice_demo_and_live_are_three_distinct_modes():
    # These drive the label under the balance. The client watched a simulated
    # $997.20 for an hour believing it was his real Pocket Option demo money,
    # so the three cases must never collapse into each other.
    assert _panel(paper=True, demo=True).state()["mode"] == "PRACTICE"
    assert _panel(paper=False, demo=True).state()["mode"] == "DEMO"
    assert _panel(paper=False, demo=False).state()["mode"] == "LIVE"


def test_connecting_an_account_stops_the_mode_reading_practice():
    """The badge flipping PRACTICE -> DEMO is the signal that it went real."""
    web = _panel(paper=True, demo=True)
    assert web.state()["mode"] == "PRACTICE"
    web.paper = False
    assert web.state()["mode"] == "DEMO"
