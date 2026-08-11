"""
Tests for reading settings out of the environment.

The comment-as-value case below is not hypothetical: it locked a client out of
his own control panel, because `WEB_PASSWORD=   # set this on a VPS` was read as
a password of "# set this on a VPS". Anything starting with '#' is a comment
somebody forgot to move, never a value.
"""

import os

import pytest

from core.config import BotConfig, _s


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("PO_SSID", "PO_SESSION", "PO_ASSET", "WEB_PASSWORD", "WEB_HOST",
              "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "STRATEGY_MODE"):
        monkeypatch.delenv(k, raising=False)


def test_plain_value(monkeypatch):
    monkeypatch.setenv("PO_ASSET", "AUDNZD_otc")
    assert _s("PO_ASSET", "EURUSD_otc") == "AUDNZD_otc"


def test_unset_falls_back_to_default():
    assert _s("PO_ASSET", "EURUSD_otc") == "EURUSD_otc"
    assert _s("WEB_PASSWORD") == ""


def test_trailing_comment_on_empty_value_is_not_a_value(monkeypatch):
    monkeypatch.setenv("WEB_PASSWORD", "# SET THIS on a public VPS")
    assert _s("WEB_PASSWORD") == ""


def test_comment_does_not_override_a_default(monkeypatch):
    monkeypatch.setenv("PO_ASSET", "  # pick a pair  ")
    assert _s("PO_ASSET", "EURUSD_otc") == "EURUSD_otc"


def test_whitespace_is_trimmed(monkeypatch):
    monkeypatch.setenv("PO_ASSET", "  EURJPY_otc  ")
    assert _s("PO_ASSET") == "EURJPY_otc"


def test_hash_inside_a_value_is_kept(monkeypatch):
    # Only a LEADING '#' means "comment" — a password may legitimately contain one.
    monkeypatch.setenv("WEB_PASSWORD", "hunter#2")
    assert _s("WEB_PASSWORD") == "hunter#2"


def test_panel_stays_open_when_password_is_only_a_comment(monkeypatch):
    """The end-to-end version of the bug: no password set => no password asked."""
    monkeypatch.setenv("WEB_PASSWORD", "# SET THIS on a public VPS")
    assert BotConfig.from_env().web_password == ""


def test_shipped_env_example_has_no_inline_comments():
    """Guard the file itself, so this class of bug cannot come back."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, ".env.example"), encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.rstrip("\n")
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            value = line.split("=", 1)[1]
            assert "#" not in value, f".env.example line {n} has an inline comment: {line!r}"
