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
    # It must say how to get a whole one, not just that it failed — and the
    # answer is no longer "copy it more carefully", it is "stop copying it".
    assert "one-click" in str(err.value).lower()


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


# ------------------------------------------------------- sloppier copy routes
#
# "I don't think I can just tap once and copy, tried it and I also tried
# ctrl a+c". Selecting exactly one cookie value in DevTools is the hardest
# manual step in the whole setup, so accept the copies that are easy to make.

def test_the_whole_cookie_header_is_accepted():
    from urllib.parse import quote
    # A real header carries the URL-encoded value — the blob's own semicolons
    # would otherwise end the field early.
    header = 'lang=en; ci_session=%s; po_uuid=1d04110b' % quote(BLOB)
    assert normalise(header, uid=138033625) == normalise(BLOB, uid=138033625)


def test_the_cookie_with_its_name_still_attached_is_accepted():
    assert normalise('ci_session=' + BLOB, uid=138033625) == normalise(BLOB, uid=138033625)


def test_an_auth_frame_is_left_alone_by_the_cookie_cleaner():
    # The control: the cleaner must not go looking for cookies inside a frame
    # that never had one, or it would mangle the DevTools route.
    assert normalise(TRADING_LINE) == normalise(BLOB, uid=138033625)


def test_a_truncated_cookie_header_is_still_caught():
    header = 'lang=en; ci_session=%s' % HALF
    with pytest.raises(SsidError) as err:
        normalise(header, uid=138033625)
    assert "cut off" in str(err.value)


# ------------------------------------------------------ the trailing hash bug
#
# A real 427-character cookie, read out of document.cookie by the bookmarklet
# and therefore complete by construction, was refused as "cut off". The reason
# was this module, not the cookie: CodeIgniter — which Pocket Option's site runs
# — appends a keyed hash of the session data after the closing brace, so a whole
# cookie need not end with '}' at all. Refusing it left the bot no way in.

HASH = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
BLOB_WITH_HASH = BLOB + HASH


def test_a_cookie_with_a_trailing_hash_is_not_called_truncated():
    assert normalise(BLOB_WITH_HASH, uid=138033625).startswith('42["auth",')


def test_the_trailing_hash_survives_url_encoding():
    from urllib.parse import quote
    out = normalise(quote(BLOB_WITH_HASH), uid=138033625)
    assert _payload(out)["session"].endswith(HASH)


def test_the_hash_is_kept_not_stripped():
    # Trimming it to make the shape check happy would hand Pocket Option a
    # cookie it has never issued. The value must go out exactly as it came in.
    assert _payload(normalise(BLOB_WITH_HASH, uid=138033625))["session"] == BLOB_WITH_HASH


def test_rubbish_after_the_brace_is_still_truncation():
    # The control. Only a hex hash counts as a legitimate tail; half a blob that
    # happens to contain a brace must not sneak through on this exemption.
    with pytest.raises(SsidError):
        normalise(BLOB[:60] + "}s:10:\"ip_addre", uid=138033625)


# ------------------------------------------------------------ trusted sources
#
# The truncation check is a guess about someone's clipboard. When the value was
# read by code rather than copied by hand there is no clipboard to guess about,
# and the guess must not be what stands between a good cookie and Pocket Option.

def test_a_trusted_value_skips_the_truncation_guard():
    assert normalise(HALF, uid=138033625, trusted=True).startswith('42["auth",')


def test_trusted_does_not_disable_the_chart_token_check():
    # Coming from the bookmarklet says nothing about *which* token it is, so the
    # checks that are still meaningful have to keep working.
    with pytest.raises(SsidError) as err:
        normalise(CHART_LINE, trusted=True)
    assert "CHART" in str(err.value)


def test_trusted_still_refuses_something_that_is_not_a_session():
    with pytest.raises(SsidError):
        normalise("hello world", uid=138033625, trusted=True)


def test_untrusted_is_the_default():
    # A caller that forgets the argument must get the strict behaviour, not the
    # lenient one — the manual paste box is where half-copies actually happen.
    with pytest.raises(SsidError):
        normalise(HALF, uid=138033625)


# ------------------------------------------------- encoded vs decoded on the wire
#
# The bug that produced "Balance: -1.00" after everything else was fixed. The
# ci_session cookie is stored percent-encoded, and .env keeps it that way on
# purpose (envfile relies on there being no quotes or semicolons to escape). But
# Pocket Option's trading socket wants the DECODED blob — the real auth frame in
# DevTools is full of backslashes, which only happens because the blob contains
# literal double quotes, and an encoded value's quotes are %22.
#
# discover.py built its frames from session_value() + _canonical() and never
# decoded, so every combination it tried was refused. The socket still opens and
# the balance sticks at -1.00, which looks exactly like an expired cookie — so
# the symptom pointed at the client's cookie rather than at this.

from urllib.parse import quote

ENCODED = quote(BLOB, safe="") + "9f86d081884c7d659a2feaa0c55ad015"


def _session_on_the_wire(frame: str) -> str:
    return _payload(frame)["session"]


def test_an_encoded_cookie_is_decoded_before_it_reaches_pocket_option():
    wire = _session_on_the_wire(normalise(ENCODED, uid=138033625))
    assert wire.startswith('a:4:{s:10:"session_id"')
    assert "%3A" not in wire


def test_discovery_sends_the_same_decoded_form_as_a_paste():
    # discover.py takes this route; it was the one that skipped decoding.
    from core.ssid import _canonical, session_value
    theirs = _session_on_the_wire(_canonical(session_value(ENCODED), 138033625, True))
    mine = _session_on_the_wire(normalise(ENCODED, uid=138033625))
    assert theirs == mine
    assert theirs.startswith('a:4:{')


def test_the_trailing_hash_is_not_lost_in_decoding():
    wire = _session_on_the_wire(normalise(ENCODED, uid=138033625))
    assert wire.endswith("9f86d081884c7d659a2feaa0c55ad015")


def test_decoding_is_idempotent():
    # Called from several layers, so it must never matter how many times a value
    # has been through it. A second unquote would eat any literal % in a
    # user-agent string.
    from core.ssid import decode_session
    assert decode_session(decode_session(ENCODED)) == decode_session(ENCODED)
    assert decode_session(BLOB) == BLOB


def test_the_copy_written_to_env_stays_encoded():
    # .env has to keep parsing. A decoded blob carries quotes and semicolons.
    from core.ssid import _canonical
    stored = _canonical(ENCODED, 0, True, decode=False)
    assert "%3A" in stored
    assert 'a:4:{s:10:"session_id"' not in stored


def test_a_frame_restored_from_env_still_reaches_po_decoded():
    # The restart path: encoded frame in .env -> normalise -> wire.
    from core.ssid import _canonical
    stored = _canonical(ENCODED, 0, True, decode=False)
    assert _session_on_the_wire(normalise(stored)).startswith('a:4:{')
