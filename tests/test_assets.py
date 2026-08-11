"""Tests for core/assets.py — payout maths and PO's positional asset rows.

The break-even numbers are the point of the whole module, so they get pinned
here: get these wrong and the bot will happily trade a pair it cannot win on.
"""

from core.assets import AssetInfo, _parse, best_asset


def _row(idx, symbol, name, kind, payout, is_open, expiries=(60, 120, 180, 300)):
    """Build a row shaped like Pocket Option's updateAssets payload."""
    return [idx, symbol, name, kind, 2, payout, 60, 30, 3, 0, 170, 0, [],
            1786395600, is_open, [{"time": t} for t in expiries], 1786395600, 60, -1]


ROWS = [
    _row(1, "EURUSD_otc", "EUR/USD OTC", "currency", 68, True),
    _row(2, "AUDNZD_otc", "AUD/NZD OTC", "currency", 92, True),
    _row(3, "USDCHF_otc", "USD/CHF OTC", "currency", 95, False),      # closed
    _row(4, "EURUSD", "EUR/USD", "currency", 85, True),               # not OTC
    _row(5, "#AAPL_otc", "Apple OTC", "stock", 90, True),             # not currency
    _row(6, "GBPJPY_otc", "GBP/JPY OTC", "currency", 90, True, (120, 900)),  # no 60s
]


def test_parse_reads_the_fields_we_rely_on():
    a = {x.symbol: x for x in _parse(ROWS)}
    assert a["EURUSD_otc"].payout == 68
    assert a["EURUSD_otc"].name == "EUR/USD OTC"
    assert a["EURUSD_otc"].kind == "currency"
    assert a["USDCHF_otc"].is_open is False
    assert a["GBPJPY_otc"].expiries == [120, 900]
    assert a["EURUSD_otc"].min_expiry == 60


def test_parse_skips_malformed_rows():
    assert _parse([[1, 2], "nonsense", None, ROWS[0]])[0].symbol == "EURUSD_otc"


def test_breakeven_win_rate():
    # 92% payout -> 100/192 = 52.08%; 68% -> 100/168 = 59.52%
    assert round(AssetInfo("x", "x", "currency", 92, True, []).breakeven_win_rate, 1) == 52.1
    assert round(AssetInfo("x", "x", "currency", 68, True, []).breakeven_win_rate, 1) == 59.5
    assert round(AssetInfo("x", "x", "currency", 100, True, []).breakeven_win_rate, 1) == 50.0


def test_zero_payout_is_unwinnable():
    assert AssetInfo("x", "x", "currency", 0, True, []).breakeven_win_rate == 100.0


def test_best_asset_picks_highest_paying_open_otc_currency():
    assets = _parse(ROWS)
    pick = best_asset(assets, kind="currency", otc=True)
    assert pick.symbol == "AUDNZD_otc"          # 95% USDCHF is closed, 90% is a stock


def test_best_asset_respects_expiry_filter():
    assets = _parse(ROWS)
    # AUDNZD pays most and offers 60s, so it wins on payout...
    assert best_asset(assets, expiry=60).symbol == "AUDNZD_otc"
    # ...but at 900s it is not even eligible, so the filter must exclude it.
    assert best_asset(assets, expiry=900).symbol == "GBPJPY_otc"


def test_best_asset_can_ask_for_non_otc():
    assert best_asset(_parse(ROWS), otc=False).symbol == "EURUSD"


def test_best_asset_returns_none_when_nothing_matches():
    assert best_asset(_parse(ROWS), kind="commodity") is None


# --------------------------------------------------------------- panel rows
def _fake_fetch(rows):
    """Stand in for the live socket call, so these tests never touch the network."""
    async def _fetch(*_args, **_kwargs):
        return list(rows)
    return _fetch


def test_panel_payout_rows_are_sorted_and_filtered(monkeypatch):
    """
    The panel's payout list must put the best-paying pairs first and hide the
    closed ones — picking a shut pair from a stale list is a dead bot.
    """
    from core.config import BotConfig
    from core.web_ui import WebInterface

    rows = [
        AssetInfo("LOW_otc", "Low", "currency", 60, True, [60]),
        AssetInfo("SHUT_otc", "Shut", "currency", 95, False, [60]),
        AssetInfo("BEST_otc", "Best", "currency", 92, True, [60]),
        AssetInfo("ZERO_otc", "Zero", "currency", 0, True, [60]),
    ]
    web = WebInterface(BotConfig())
    monkeypatch.setattr("core.assets.fetch_assets", _fake_fetch(rows))

    out = web.payouts()
    assert out["ok"] is True
    symbols = [r["symbol"] for r in out["assets"]]
    assert symbols == ["BEST_otc", "LOW_otc"], "closed and payout-less pairs must be dropped"
    assert out["assets"][0]["breakeven"] == 52.1


def test_panel_payouts_are_cached(monkeypatch):
    """Pocket Option refuses rapid reconnects, so a second click must not refetch."""
    from core.config import BotConfig
    from core.web_ui import WebInterface

    calls = []

    def _counting(rows):
        fetch = _fake_fetch(rows)

        async def wrapper(*a, **kw):
            calls.append(1)
            return await fetch(*a, **kw)
        return wrapper

    web = WebInterface(BotConfig())
    monkeypatch.setattr("core.assets.fetch_assets",
                        _counting([AssetInfo("A_otc", "A", "currency", 92, True, [60])]))
    web.payouts()
    second = web.payouts()
    assert len(calls) == 1
    assert second["cached"] is True


def test_panel_reports_a_failure_instead_of_pretending(monkeypatch):
    from core.config import BotConfig
    from core.web_ui import WebInterface

    async def _boom(*a, **kw):
        raise RuntimeError("network down")

    web = WebInterface(BotConfig())
    monkeypatch.setattr("core.assets.fetch_assets", _boom)
    out = web.payouts()
    assert out["ok"] is False
    assert "network down" in out["message"]
    assert out["assets"] == []
