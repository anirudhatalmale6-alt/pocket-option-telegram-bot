"""
Asking Pocket Option's website which account a cookie belongs to.

This is the step that had been missing for weeks: the bot had a perfectly good
session cookie and no account id, and the only two ways to get the id — reading
a WebSocket frame in DevTools, or the bookmarklet walking the page's JavaScript
— had both failed on the client's machine. The cookie is a login, so the site
itself can be asked.

Nothing here touches the network: `fetch` is injected. What is covered is the
handling around it, which is where a mistake would be expensive — sending the
cookie in a form that gets cut in half, sending it anywhere other than Pocket
Option, or reporting an unreachable site as a dead cookie.
"""

from __future__ import annotations

from core import uid_lookup
from core.uid_lookup import Lookup, account_ids, cookie_header, harvest, top_ids

# The cookie as the browser stores it: percent-encoded.
ENCODED = ("a%3A4%3A%7Bs%3A10%3A%22session_id%22%3Bs%3A32%3A%22"
           "bc01274a27165376b8124c84bd7ec930%22%3B%7D")
DECODED = 'a:4:{s:10:"session_id";s:32:"bc01274a27165376b8124c84bd7ec930";}'

CABINET = """
<!doctype html><html><body class="cabinet">
<script>window.settings={"uid":138033625,"isDemo":0,"balance":12.5};
window.profile={"user_id":"138033625","demo_uid":99887766};</script>
<a href="/en/logout">Log out</a>
</body></html>
"""

LOGGED_OUT = """
<!doctype html><html><body>
<form action="/en/login" method="post"><input name="email"></form>
<a href="/en/registration/">Sign up</a>
</body></html>
"""


def _fetch_from(pages: dict, seen: list):
    """A fake fetch that serves canned bodies and records what was asked for."""
    def fetch(url, session):
        seen.append((url, session))
        body = pages.get(url, "")
        return (200 if body else 404), url, body
    return fetch


# ------------------------------------------------------------- the cookie header
def test_a_decoded_blob_is_re_encoded_before_it_goes_in_a_header():
    # A decoded session contains ';' and '"'. A ';' inside a Cookie header ends
    # the cookie, so the site would receive half a login and answer as a logged
    # out visitor — indistinguishable from an expired cookie.
    header = cookie_header(DECODED)
    assert header.startswith("ci_session=")
    assert ";" not in header[len("ci_session="):]
    assert '"' not in header


def test_an_already_encoded_cookie_is_sent_untouched():
    assert cookie_header(ENCODED) == "ci_session=" + ENCODED


# ------------------------------------------------------------------- harvesting
def test_ids_are_found_in_every_shape_the_page_uses():
    found = harvest(CABINET)
    assert 138033625 in found
    assert 99887766 in found


def test_the_most_repeated_id_is_tried_first():
    # Each id costs a connect-and-wait and only the first few get tried, so the
    # one the page keeps mentioning has to come first.
    text = '{"uid":111111111} {"user_id":"222222222"} {"account_id":"222222222"}'
    assert harvest(text)[0] == 222222222


def test_numbers_that_cannot_be_account_ids_are_ignored():
    # A price, a year, a timestamp in milliseconds. The range matches the one
    # the bookmarklet and discover.py use, so nothing is accepted here that is
    # rejected there.
    assert harvest('{"uid":1234}') == []
    assert harvest('{"user_id":"99999"}') == []
    assert harvest('{"uid":17569876543210}') == []


def test_a_page_with_no_ids_yields_nothing_rather_than_guessing():
    assert harvest(LOGGED_OUT) == []
    assert harvest("") == []


# -------------------------------------------------------------------- the lookup
def test_the_cookie_only_ever_goes_to_pocket_option():
    seen = []
    account_ids(ENCODED, fetch=_fetch_from({}, seen))
    assert seen, "nothing was requested at all"
    for url, _ in seen:
        assert url.startswith("https://pocketoption.com/"), url


def test_the_ids_from_the_cabinet_page_come_back():
    seen = []
    res = account_ids(ENCODED, fetch=_fetch_from({uid_lookup.PAGES[0]: CABINET}, seen))
    assert res.ids[0] == 138033625
    assert res.logged_in is True
    assert res.error == ""


def test_it_stops_asking_once_a_page_has_answered():
    # Three requests to somebody's broker for a question already answered is
    # three chances to be rate-limited.
    seen = []
    account_ids(ENCODED, fetch=_fetch_from({uid_lookup.PAGES[0]: CABINET}, seen))
    assert len(seen) == 1


def test_a_logged_out_answer_is_reported_as_a_dead_cookie():
    # The one diagnosis nothing in this program could make before: an expired
    # cookie and a cookie whose id we cannot find both ended as "practice mode",
    # and they need opposite things from the user.
    lines = []
    pages = {url: LOGGED_OUT for url in uid_lookup.PAGES}
    res = account_ids(ENCODED, log=lines.append, fetch=_fetch_from(pages, []))
    assert res.ids == []
    assert res.logged_in is False
    assert res.error == ""            # the site answered; it just said no
    assert any("does not recognise" in line for line in lines)


def test_an_unreachable_site_is_not_reported_as_a_dead_cookie():
    # No internet must never turn into "your cookie has expired, go and get a
    # new one" — that sends someone off to redo the one step that was fine.
    lines = []
    def dead(url, session):
        return 0, url, ""
    res = account_ids(ENCODED, log=lines.append, fetch=dead)
    assert res.error
    assert res.reached_site is False
    assert not any("does not recognise" in line for line in lines)


def test_a_logged_in_page_with_no_id_says_so_plainly():
    lines = []
    pages = {url: "<html><body>cabinet balance</body></html>"
             for url in uid_lookup.PAGES}
    res = account_ids(ENCODED, log=lines.append, fetch=_fetch_from(pages, []))
    assert res.logged_in is True and res.ids == []
    assert any("did not put an account id" in line for line in lines)


def test_no_cookie_is_a_refusal_not_a_request():
    seen = []
    res = account_ids("", fetch=_fetch_from({}, seen))
    assert res.error and seen == []


def test_the_number_of_ids_tried_is_capped():
    # Every extra id is another connect-and-wait on somebody's afternoon.
    many = " ".join('{"uid":%d}' % (100000000 + n) for n in range(20))
    ids = top_ids(ENCODED, fetch=_fetch_from({uid_lookup.PAGES[0]: many}, []))
    assert len(ids) == uid_lookup.MAX_IDS


def test_the_cookie_is_never_written_to_the_log():
    lines = []
    pages = {url: LOGGED_OUT for url in uid_lookup.PAGES}
    account_ids(ENCODED, log=lines.append, fetch=_fetch_from(pages, []))
    blob = "\n".join(lines)
    assert ENCODED not in blob and DECODED not in blob


def test_a_lookup_with_no_error_is_the_only_one_that_proves_anything():
    assert Lookup(error="no internet").reached_site is False
    assert Lookup().reached_site is True


# ------------------------------------------- a URL that is simply not there
#
# Pocket Option answers an unknown path with 404 and a full marketing page —
# 126KB of it, saying "balance" and "cabinet" throughout. Every logged-IN marker
# matches it. One dead URL in PAGES was therefore enough to set logged_in on
# every lookup that ever ran, which made the "your cookie has expired" branch
# unreachable and left the client being told his login was fine.
NOT_FOUND = """
<!doctype html><html><body>
<h1>The Most Innovative Trading Platform</h1>
<p>Trade with a $10,000 demo balance. Open your cabinet and start today.</p>
</body></html>
"""


def _fetch_mixed(bodies: dict, statuses: dict, seen: list):
    """Like _fetch_from, but the status is chosen per URL rather than implied."""
    def fetch(url, session):
        seen.append((url, session))
        return statuses.get(url, 200), url, bodies.get(url, "")
    return fetch


def test_a_404_marketing_page_is_not_mistaken_for_being_logged_in():
    seen = []
    res = account_ids(ENCODED, fetch=_fetch_mixed(
        {p: NOT_FOUND for p in uid_lookup.PAGES},
        {p: 404 for p in uid_lookup.PAGES}, seen))
    assert res.logged_in is False, \
        "a page that does not exist was read as proof the cookie still works"
    # And it must not be reported as an unreachable site either — the site
    # answered, and "no internet" sends him somewhere else entirely.
    assert res.error == ""


def test_a_dead_url_alongside_a_login_redirect_still_reports_a_dead_cookie():
    # The live arrangement as it actually was: two real pages that bounce a
    # logged-out visitor to /en/login, and one URL that no longer exists. The
    # dead one used to outvote the other two.
    seen = []
    said = []
    pages = list(uid_lookup.PAGES)
    res = account_ids(ENCODED, log=said.append, fetch=_fetch_mixed(
        {pages[0]: LOGGED_OUT, pages[1]: LOGGED_OUT, pages[2]: NOT_FOUND},
        {pages[2]: 404}, seen))
    assert res.logged_in is False
    assert any("does not recognise the saved cookie" in m for m in said), said


def test_nothing_is_harvested_from_a_page_that_does_not_exist():
    # A 404 body can contain any number at all. Ids off it would burn the whole
    # five-attempt budget on numbers that were never account ids.
    seen = []
    body = NOT_FOUND + '<script>window.x={"uid":138033625}</script>'
    res = account_ids(ENCODED, fetch=_fetch_mixed(
        {p: body for p in uid_lookup.PAGES},
        {p: 404 for p in uid_lookup.PAGES}, seen))
    assert res.ids == []


def test_every_page_asked_for_lives_under_the_cabinet():
    # The dead URL was /en/profile/, a public path. Everything here has to be a
    # page only a logged-in user can see, because that is what makes a redirect
    # to the login page mean something.
    for url in uid_lookup.PAGES:
        assert "/cabinet/" in url, url
