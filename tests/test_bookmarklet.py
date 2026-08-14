"""
Run the real bookmarklet in a real browser.

This is the one piece of the program that cannot be exercised by importing it:
it is JavaScript, built by string concatenation inside a Python template, and
it runs on somebody else's website. A typo in it does not raise anything — the
bookmark just silently stops working, and the only symptom reaches me as "it
won't connect", days later.

It is also the piece that reads other people's data, so the second half of this
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


@pytest.fixture(scope="module")
def fragment():
    """The '#...' the bookmarklet hands to the panel, from a real browser run."""
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
            status=200, content_type="text/html", body=PAGE))
        page.goto("http://po.test/")

        js = page.evaluate("() => { const BOOKMARKLET = " + expr + "; return BOOKMARKLET; }")
        assert js.startswith("javascript:")

        urls: list[str] = []
        page.on("framenavigated", lambda f: urls.append(f.url))
        page.evaluate(js[len("javascript:"):])
        page.wait_for_timeout(800)
        browser.close()

    url = next((u for u in urls if "/hook#" in u), None)
    assert url, f"the bookmarklet never navigated to the panel; saw {urls}"
    return url


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


def test_nothing_but_integers_ever_leaves_the_page(fragment):
    """
    The walk parses whole objects, so it sees session tokens and names. It must
    keep none of them. This is somebody else's account data on somebody else's
    website; the bar is that the only thing capable of crossing is a number.
    """
    for secret in ("SECRET", "TOKEN", "Donnie", "dark", "theme"):
        assert secret not in fragment, f"{secret!r} left the page"
    assert all(x.isdigit() for x in _ids(fragment))
