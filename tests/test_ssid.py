"""Tests for core/ssid.py — the token normaliser.

These matter more than they look: a wrong token is the most common setup
failure, and the whole point of this module is that it fails with a message you
can act on rather than a socket timeout.
"""

import json

import pytest

from core.ssid import SsidError, looks_like_chart_token, normalise

# A realistic PHP-serialised session blob (shortened but same shape).
BLOB = ('a:4:{s:10:"session_id";s:32:"0123456789abcdef0123456789abcdef";'
        's:10:"ip_address";s:7:"1.2.3.4";s:10:"user_agent";s:7:"Mozilla";'
        's:13:"last_activity";i:1700000000;}')

TRADING_LINE = ('42["auth",{"session":"%s","isDemo":1,"uid":138033625,'
                '"platform":2}]' % BLOB.replace('"', '\\"'))

CHART_LINE = ('42["auth",{"sessionToken":"eb29431e026b200e4be64d4061a2901d",'
              '"uid":"138033625","lang":"en",'
              '"currentUrl":"cabinet/demo-quick-high-low","isChart":1}]')


def _payload(ssid: str) -> dict:
    """Parse the {...} back out of a normalised auth frame."""
    return json.loads(ssid[ssid.index("{"):ssid.rindex("}") + 1])


def test_trading_line_round_trips():
    out = normalise(TRADING_LINE)
    assert out.startswith('42["auth",{')
    p = _payload(out)
    assert p["session"] == BLOB
    assert p["uid"] == 138033625
    assert p["isDemo"] == 1
    assert p["platform"] == 2


def test_string_uid_becomes_int():
    line = '42["auth",{"session":"%s","isDemo":1,"uid":"42","platform":2}]' % BLOB.replace('"', '\\"')
    assert _payload(normalise(line))["uid"] == 42


def test_live_flag_is_preserved():
    line = '42["auth",{"session":"%s","isDemo":0,"uid":7,"platform":2}]' % BLOB.replace('"', '\\"')
    assert _payload(normalise(line))["isDemo"] == 0


def test_bare_cookie_plus_uid():
    p = _payload(normalise(BLOB, uid=99, demo=True))
    assert p["session"] == BLOB and p["uid"] == 99


def test_url_encoded_cookie_is_decoded():
    from urllib.parse import quote
    p = _payload(normalise(quote(BLOB), uid=99))
    assert p["session"] == BLOB


def test_cookie_without_uid_is_rejected():
    with pytest.raises(SsidError) as e:
        normalise(BLOB)
    assert "PO_UID" in str(e.value)


def test_chart_token_is_named_and_explained():
    with pytest.raises(SsidError) as e:
        normalise(CHART_LINE)
    msg = str(e.value)
    assert "CHART socket" in msg
    assert "successauth" in msg  # tells them what to look for instead


def test_bare_chart_hex_is_rejected():
    with pytest.raises(SsidError):
        normalise("eb29431e026b200e4be64d4061a2901d")


def test_empty_is_rejected():
    for bad in ("", "   ", None):
        with pytest.raises(SsidError):
            normalise(bad)


def test_garbage_is_rejected():
    with pytest.raises(SsidError):
        normalise("hello there")


def test_looks_like_chart_token():
    assert looks_like_chart_token(CHART_LINE)
    assert looks_like_chart_token("eb29431e026b200e4be64d4061a2901d")
    assert not looks_like_chart_token(TRADING_LINE)
    assert not looks_like_chart_token("")


# --------------------------------------------------------------- truncation
#
# Reported as "it says not logged in": the cookie was the right shape but
# Pocket Option refused it, and nothing on screen could say why. A half-copied
# cookie still begins with 'a:4:{', so every check above this one passes it.

HALF = BLOB[:len(BLOB) // 2]


def test_a_half_copied_cookie_is_rejected_with_a_copying_instruction():
    with pytest.raises(SsidError) as err:
        normalise(HALF, uid=138033625)
    assert "cut off" in str(err.value)
    # It must say how to copy it properly, not just that it failed.
    assert "Copy value" in str(err.value)


def test_truncation_is_caught_inside_a_pasted_auth_frame_too():
    frame = ('42["auth",{"session":"%s","isDemo":1,"uid":138033625,"platform":2}]'
             % HALF.replace('"', '\\"'))
    with pytest.raises(SsidError) as err:
        normalise(frame)
    assert "cut off" in str(err.value)


def test_truncation_is_detected_through_url_encoding():
    from urllib.parse import quote
    with pytest.raises(SsidError):
        normalise(quote(HALF), uid=138033625)


def test_a_whole_cookie_is_still_accepted():
    # The control: same code path, complete value, must pass. Without this the
    # test above would also pass if normalise() had started rejecting everything.
    assert normalise(BLOB, uid=138033625).startswith('42["auth",')
