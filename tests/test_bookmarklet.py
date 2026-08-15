"""
Run the real bookmarklet in a real browser.

This is the one piece of the program that cannot be exercised by importing it:
it is JavaScript, built by string concatenation inside a Python template, and
it runs on somebody else's website. A typo in it does not raise anything — the
bookmark just silently stops working, and the only symptom reaches me as "it
won't connect", days later.

It is also the piece that reads other people's data, so the last part of this
file is about what must NOT come back.

Skipped when Playwright is not installed, so the ordinary suite still runs
anywhere.
"""
from __future__ import annotations

import re

import pytest

pytest.importorskip("playwright.sync_api",
                    reason="Playwright not installed; browser test skipped")
from playwright.sync_api import sync_playwright        # noqa: E402

SRC = open("core/web_ui.py").read()

# A page shaped like Pocket Option's: the account ids are not at the top level
# and not under a key anybody promised us, which is the whole reason the
# bookmarklet walks parsed objects instead of reading a known field.
PAGE = """<!doctype html><title>fake PO</title><script>
document.cookie = 'ci_session=THECOOKIEVALUE';
localStorage.setItem('junk', 'not json at all');
localStorage.setItem('settings', JSON.stringify({theme:'dark', lang:'en'}));
localStorage.setItem('profile', JSON.stringify({
   user:{uid: 138033625, name:'Donnie', token:'SECRETTOKENMUSTNOTLEAK'},
   balances:[{id: 138033625, isDemo:0}, {account_id: 987654321, isDemo:1}],
   demo_account_id: '555444333'
}));
sessionStorage.setItem('accountId', '111222333');
localStorage.setItem('small_uid', '42');
</script>"""

# The page that actually broke it. Storage holds nothing useful at all — which
# is what the real site turned out to look like — and the logged-in profile is
# only in the framework's in-memory state, four levels down, next to a token.
# The first version of the scrape read localStorage and sessionStorage and
# nothing else, came back with no ids whatsoever, and the panel then announced
# that his cookie had expired.
#
# It also carries the two things that make walking `window` dangerous: a
# reference cycle, and the DOM.
IN_MEMORY_PAGE = """<!doctype html><title>fake PO</title><script>
document.cookie = 'ci_session=THECOOKIEVALUE';
document.cookie = 'user_account=444555666';
localStorage.setItem('theme', 'dark');
window.__NUXT__ = {state: {auth: {profile: {uid: 138033625,
                                            email: 'SECRETEMAIL@x.com'}}}};
window.__NUXT__.state.self = window.__NUXT__;      // a cycle
window.appBody = document.body;                    // the DOM, reachable
</script>"""


def _run(html, after=""):
    """Run the real bookmarklet against `html` and return (fragment, extra)."""
    expr = re.search(r"const BOOKMARKLET =\n(.*?);\n\nfunction bmClick",
                     SRC, re.S).group(1)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            # Playwright present but no browser installed, or a version skew
            # between the two. That is a missing tool, not a failing bookmarklet
            # — erroring here would report a bug that is not there.
            pytest.skip(f"no usable browser: {exc}")
        page = browser.new_page()
        page.route("http://po.test/**", lambda r: r.fulfill(
            status=200, content_type="text/html", body=html))
        page.goto("http://po.test/")

        js = page.evaluate("() => { const BOOKMARKLET = " + expr + "; return BOOKMARKLET; }")
        assert js.startswith("javascript:")
        body = js[len("javascript:"):]

        urls: list[str] = []
        page.on("framenavigated", lambda f: urls.append(f.url))
        # `after` runs in the same synchronous turn as the bookmarklet, before
        # the navigation it queued has committed — the only moment at which the
        # things it installed on this page are still there to inspect.
        extra = page.evaluate("() => { " + body + ";\n" + (after or "return null") + " }")
        page.wait_for_timeout(800)
        browser.close()

    url = next((u for u in urls if "/hook#" in u), None)
    assert url, f"the bookmarklet never navigated to the panel; saw {urls}"
    return url, extra


@pytest.fixture(scope="module")
def fragment():
    """The '#...' the bookmarklet hands to the panel, from a real browser run."""
    return _run(PAGE)[0]


def _ids(fragment):
    _, _, ids = fragment.split("#", 1)[1].partition("|")
    return set(ids.split(",")) if ids else set()


def test_the_cookie_still_comes_through(fragment):
    cookie = fragment.split("#", 1)[1].partition("|")[0]
    assert cookie == "THECOOKIEVALUE"


def test_account_ids_are_found_wherever_they_are_buried(fragment):
    # nested object, inside an array, a string value, and sessionStorage.
    assert {"138033625", "987654321", "555444333", "111222333"} <= _ids(fragment)


def test_numbers_too_small_to_be_account_ids_are_ignored(fragment):
    assert "42" not in _ids(fragment)


# ------------------------------------ the id is not always written down at all
@pytest.fixture(scope="module")
def in_memory():
    return _run(IN_MEMORY_PAGE)[0]


def test_an_id_that_only_exists_in_the_page_s_own_state_is_found(in_memory):
    """
    Storage-only scraping returned nothing here, and the failure was then
    reported to him as an expired cookie. A framework keeps the logged-in
    profile in memory and never has to write it anywhere.
    """
    assert "138033625" in _ids(in_memory)


def test_an_id_in_another_cookie_is_found(in_memory):
    assert "444555666" in _ids(in_memory)


def test_walking_the_page_s_own_objects_still_finishes(in_memory):
    # A cycle and a live DOM are both reachable from `window` on any real site.
    # Reaching this assertion at all is the test: without the visited set, the
    # node budget and the DOM guard, the bookmark hangs the tab and never
    # navigates, and _run fails on the missing navigation.
    assert in_memory


# ---------------------------------- when it is nowhere on the page, listen for it
def test_the_listener_catches_the_id_out_of_pocket_option_s_own_auth_frame():
    """
    The id is guaranteed to exist in one place: the auth frame their page sends
    on its own WebSocket. A bookmark cannot see frames sent before it ran, so it
    wraps send and leaves what it catches in storage for the next click.
    """
    _, got = _run(IN_MEMORY_PAGE, after="""
      try{ WebSocket.prototype.send.call({}, '42["auth",{"session":"SECRETSESSIONVALUE","isDemo":1,"uid":138033625,"platform":1}]') }catch(e){}
      try{ WebSocket.prototype.send.call({}, '42["auth",{"session":"SECRETSESSIONVALUE","isDemo":0,"uid":999888777,"platform":1}]') }catch(e){}
      return JSON.stringify(localStorage);
    """)
    assert '"pobot_account_demo":"138033625"' in got
    assert '"pobot_account_real":"999888777"' in got


def test_the_listener_does_not_write_down_the_session():
    """The frame it reads carries the session too. Only digits may be kept."""
    _, got = _run(IN_MEMORY_PAGE, after="""
      try{ WebSocket.prototype.send.call({}, '42["auth",{"session":"SECRETSESSIONVALUE","isDemo":1,"uid":138033625}]') }catch(e){}
      return JSON.stringify(localStorage);
    """)
    assert "SECRETSESSION" not in got


def test_what_the_listener_stored_is_picked_up_on_the_next_click():
    """Storing it is only useful if the scrape then finds it. Same origin, so
    the second run sees what the first one left behind."""
    page = IN_MEMORY_PAGE.replace(
        "</script>",
        "localStorage.setItem('pobot_account_demo', '777666555');</script>")
    assert "777666555" in _ids(_run(page)[0])


# ------------------------------------------------------- what must not come back
def test_nothing_but_integers_ever_leaves_the_page(fragment):
    """
    The walk parses whole objects, so it sees session tokens and names. It must
    keep none of them. This is somebody else's account data on somebody else's
    website; the bar is that the only thing capable of crossing is a number.
    """
    for secret in ("SECRET", "TOKEN", "Donnie", "dark", "theme"):
        assert secret not in fragment, f"{secret!r} left the page"
    assert all(x.isdigit() for x in _ids(fragment))


def test_walking_the_page_s_own_state_leaks_nothing_either(in_memory):
    """The widened search reads more, so it has more it must refuse to keep."""
    for secret in ("SECRET", "@x.com", "dark", "NUXT"):
        assert secret not in in_memory, f"{secret!r} left the page"
    assert all(x.isdigit() for x in _ids(in_memory))
