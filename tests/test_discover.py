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
from core.discover import Account, find_account, _uids
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
    assert _uids(555)[0] == 555


def test_uid_zero_is_always_worth_a_try():
    # Pocket Option may derive the account from the session alone.
    assert _uids(555) == [555, 0]
    assert _uids(0) == [0]


# -------------------------------------------------------------------- search

def test_a_real_account_is_never_returned_as_the_answer_to_a_demo_request(monkeypatch):
    """
    THE ONE THAT MATTERS.

    Only the real-money account answers, and practice was what was asked for.
    It may be reported — knowing the cookie works at all is the whole diagnosis
    — but it must come back flagged, so no caller can mistake it for the
    account that was requested and connect a self-trading bot to real money.
    """
    _try, calls = fake_broker({(555, False): 1234.0})
    monkeypatch.setattr(discover, "_try_one", _try)

    found = run(find_account(BLOB, uid_hint=555, demo_hint=True))

    assert found.matches_request is False
    assert found.demo is False
    assert calls[0] == (555, True)      # tried what was asked for, first


def test_both_demo_uids_are_tried_before_real_money_is_touched(monkeypatch):
    # Order is the safety property here: every practice combination must be
    # exhausted before a real-money frame is sent at all.
    _try, calls = fake_broker({(0, False): 1234.0})
    monkeypatch.setattr(discover, "_try_one", _try)

    run(find_account(BLOB, uid_hint=555, demo_hint=True))

    demo_tries = [i for i, (_u, d) in enumerate(calls) if d]
    real_tries = [i for i, (_u, d) in enumerate(calls) if not d]
    assert max(demo_tries) < min(real_tries)


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


def test_an_empty_demo_beats_a_funded_real_account(monkeypatch):
    """
    This assertion used to say the opposite, and that was the bug.

    An empty demo and a funded live account: "keep the funded one, it looks
    like the better answer" reads as sensible right up to the moment it hands
    an automated trader someone's actual money because their practice balance
    happened to be zero. The kind of account is not an optimisation target.
    """
    _try, _calls = fake_broker({(555, True): 0.0, (555, False): 900.0})
    monkeypatch.setattr(discover, "_try_one", _try)

    found = run(find_account(BLOB, uid_hint=555, demo_hint=True))
    assert (found.demo, found.balance) == (True, 0.0)
    assert found.matches_request is True


def test_a_funded_account_of_the_requested_kind_beats_an_empty_one(monkeypatch):
    # Within one kind, the original preference still holds: an accepted-but-
    # empty balance is ambiguous, so a funded uid is the better answer.
    _try, _calls = fake_broker({(555, True): 0.0, (0, True): 900.0})
    monkeypatch.setattr(discover, "_try_one", _try)

    found = run(find_account(BLOB, uid_hint=555, demo_hint=True))
    assert (found.uid, found.balance) == (0, 900.0)


def test_asking_for_real_money_still_gets_real_money(monkeypatch):
    # The guard is about not SUBSTITUTING one for the other. Someone who
    # deliberately ticks real money must still be able to connect to it.
    _try, _calls = fake_broker({(555, False): 900.0})
    monkeypatch.setattr(discover, "_try_one", _try)

    found = run(find_account(BLOB, uid_hint=555, demo_hint=False))
    assert (found.demo, found.matches_request) == (False, True)


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
