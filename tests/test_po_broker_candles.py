"""
The argument order on Pocket Option's get_candles, pinned.

This is the bug that cost the client two days. The library's signature reads

    get_candles(asset, period, offset)

and its own docstring defines them as:

    period = how many SECONDS OF HISTORY to fetch
    offset = the CANDLE SIZE in seconds

Both are integers in seconds, both names are wrong-ish for what they do, and
swapping them is silent: you get an empty list rather than an error. The bot
then reported "waiting for the first candle to close" indefinitely while looking
completely healthy — connected, no errors, no data.

So the order is asserted here rather than trusted to a comment.
"""

from __future__ import annotations

import asyncio

import pytest

from core.po_broker import PocketOptionBroker

# A syntactically valid auth frame so the constructor's SSID parsing passes.
SSID = ('42["auth",{"session":"a%3A4%3A%7Bs%3A10%3A%22session_id%22%3B%7D",'
        '"isDemo":1,"uid":138033625,"platform":2}]')


class FakeClient:
    """Stands in for PocketOptionAsync, recording exactly how it was called."""

    def __init__(self):
        self.calls = []

    async def get_candles(self, asset, period, offset):
        self.calls.append({"asset": asset, "period": period, "offset": offset})
        # Mimic the real thing: a request for a sane period/offset returns bars.
        if offset not in PocketOptionBroker.SUPPORTED_TIMEFRAMES or period < offset:
            return []
        n = max(1, period // offset)
        return [{"time": float(i * offset), "open": 1.0, "high": 1.2,
                 "low": 0.9, "close": 1.1} for i in range(n)]


def _broker():
    b = PocketOptionBroker(SSID, demo=True, uid=138033625)
    b._client = FakeClient()
    return b


def test_history_length_and_candle_size_are_not_swapped():
    broker = _broker()
    asyncio.run(broker.get_candles("EURUSD_otc", 60, 200))
    call = broker._client.calls[0]
    # 200 candles of 60s each = 12,000 seconds of history, in 60s buckets.
    assert call["period"] == 12000, "period must be the span of history"
    assert call["offset"] == 60, "offset must be the candle size"


def test_the_swapped_order_would_have_returned_nothing():
    """Proves the old call really did come back empty, not merely wrong."""
    client = FakeClient()
    # The old code: get_candles(asset, timeframe, count * timeframe)
    assert asyncio.run(client.get_candles("EURUSD_otc", 60, 200 * 60)) == []


def test_candles_come_back_parsed():
    broker = _broker()
    candles = asyncio.run(broker.get_candles("EURUSD_otc", 60, 200))
    assert candles
    assert candles[-1].close == 1.1
    assert candles[-1].high >= candles[-1].low


def test_no_more_than_the_requested_count_is_returned():
    broker = _broker()
    assert len(asyncio.run(broker.get_candles("EURUSD_otc", 60, 20))) <= 20


def test_the_last_candle_is_kept_because_it_is_already_closed():
    # get_candles returns closed candles only. Discarding the final one — which
    # is right for a live feed — would throw away the freshest close and leave
    # every decision a whole candle late.
    assert PocketOptionBroker.LAST_CANDLE_IS_PARTIAL is False


@pytest.mark.parametrize("tf", [5, 15, 30, 60, 300])
def test_every_supported_candle_size_is_accepted(tf):
    broker = _broker()
    assert asyncio.run(broker.get_candles("EURUSD_otc", tf, 50))


@pytest.mark.parametrize("tf", [7, 45, 90, 120, 3600])
def test_an_unsupported_candle_size_is_refused_with_an_explanation(tf):
    # Pocket Option answers these with an empty list. Silence is the worst
    # possible failure here, so turn it into a sentence naming the valid sizes.
    broker = _broker()
    with pytest.raises(ValueError) as err:
        asyncio.run(broker.get_candles("EURUSD_otc", tf, 50))
    assert "60" in str(err.value)
