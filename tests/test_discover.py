"""
The account search must find the right combination without anyone opening
DevTools — and must not claim success when the cookie itself is dead.

These are the two outcomes that matter: a wrong account id is recoverable and
should be fixed silently, while an expired cookie is not recoverable and has to
be said out loud. Confusing the two is what cost this project two days.
"""

import asyncio

import pytest

from core import discover
from core.discover import Account, find_account, _candidates
from core.ssid import _canonical, normalise, session_value

BLOB = 'a:4:{s:10:"session_id";s:32:"' + "b" * 32 + '";s:10:"ip_address";s:7:"1.2.3.4";}'


def run(coro):
    return asyncio.run(coro)


def fake_broker(accepted):
    """
    Stand in for Pocket Option: a balance for the accepted (uid, demo) pair,
    -1.0 — 'never authenticated' — for everything else.
    """
    calls = []

    async def _try_one(session, uid, demo):
        calls.append((uid, demo))
        for (want_uid, want_demo), bal in accepted.items():
            if (uid, demo) == (want_uid, want_demo):
                return bal
        return None

    return _try_one, calls


# ------------------------------------------------------------------ ordering

def test_saved_details_are_tried_first():
    # The common case — already correct — must cost exactly one connection.
    assert _candidates(555, True)[0] == (555, True)


def test_every_combination_is_covered_once():
    got = _candidates(555, True)
    assert set(got) == {(555, True), (555, False), (0, True), (0, False)}
    assert len(got) == len(set(got))


def test_no_hint_still_tries_both_flags():
    assert _candidates(0, True) == [(0, True), (0, False)]


# -------------------------------------------------------------------- search

def test_finds_the_real_account_when_the_demo_flag_was_wrong(monkeypatch):
    _try, calls = fake_broker({(555, False): 1234.0})
    monkeypatch.setattr(discover, "_try_one", _try)

    found = run(find_account(BLOB, uid_hint=555, demo_hint=True))

    assert found == Account(uid=555, demo=False, balance=1234.0)
    assert calls[0] == (555, True)      # tried the saved one first


def test_stops_as_soon_as_one_is_accepted(monkeypatch):
    _try, calls = fake_broker({(555, True): 50.0})
    monkeypatch.setattr(discover, "_try_one", _try)

    run(find_account(BLOB, uid_hint=555, demo_hint=True))
    assert calls == [(555, True)]       # no pointless extra connections


def test_dead_cookie_returns_nothing(monkeypatch):
    # Nothing accepted anywhere. This must NOT be reported as a wrong uid —
    # it means the session has expired and only a fresh one will help.
    _try, calls = fake_broker({})
    monkeypatch.setattr(discover, "_try_one", _try)

    assert run(find_account(BLOB, uid_hint=555, demo_hint=True)) is None
    assert len(calls) == 4             # exhausted every option before giving up


def test_a_funded_account_beats_an_empty_one(monkeypatch):
    # An accepted-but-empty balance is ambiguous, so keep looking; a real
    # balance elsewhere is the better answer.
    _try, _calls = fake_broker({(555, True): 0.0, (555, False): 900.0})
    monkeypatch.setattr(discover, "_try_one", _try)

    found = run(find_account(BLOB, uid_hint=555, demo_hint=True))
    assert (found.demo, found.balance) == (False, 900.0)


def test_empty_account_is_used_when_it_is_all_there_is(monkeypatch):
    _try, _calls = fake_broker({(555, True): 0.0})
    monkeypatch.setattr(discover, "_try_one", _try)

    found = run(find_account(BLOB, uid_hint=555, demo_hint=True))
    assert found is not None and found.balance == 0.0


def test_progress_is_reported_for_every_attempt(monkeypatch):
    _try, _calls = fake_broker({(555, False): 10.0})
    monkeypatch.setattr(discover, "_try_one", _try)
    lines = []

    run(find_account(BLOB, uid_hint=555, demo_hint=True, log=lines.append))

    assert any("practice" in l for l in lines)
    assert any("real money" in l for l in lines)
    # The token is a password and this log is on screen and in the log file.
    assert not any(BLOB[:20] in l for l in lines)


# -------------------------------------------------------- input tolerance

def test_a_whole_auth_frame_is_unwrapped_not_nested(monkeypatch):
    # PO_SSID may hold a finished frame. Rebuilding around it would refuse
    # every combination, and look identical to an expired cookie.
    seen = {}

    async def _try(session, uid, demo):
        seen["session"] = session
        return 5.0

    monkeypatch.setattr(discover, "_try_one", _try)
    frame = _canonical(BLOB, 777, True)

    run(find_account(frame, uid_hint=777, demo_hint=True))
    assert seen["session"] == BLOB


def test_session_value_handles_both_forms():
    assert session_value(BLOB) == BLOB
    assert session_value(_canonical(BLOB, 1, True)) == BLOB


def test_machine_built_frame_without_a_uid_is_accepted():
    # discover deliberately tests uid 0; normalise must not reject its own
    # frame. A human copying the chart socket's line still gets told.
    assert normalise(_canonical(BLOB, 0, True))
    with pytest.raises(Exception):
        normalise('42["auth",{"sessionToken":"' + "a" * 32 + '","isChart":1}]')
