"""
The Pocket Option candle feed: one long-lived stream, not one per poll.

Two mistakes are pinned here, both of which cost the client real time.

1. `client.get_candles()` opens a brand-new live subscription and backfill on
   every call. The trading loop calls the broker once per second, so every poll
   tore the feed down and rebuilt it, and the first batch never had time to
   arrive. The panel sat on "waiting for the first candle to close" forever
   while looking perfectly healthy — connected, no errors, no data.

2. That helper's docstring contradicts its own body. It documents

       period = seconds of history,  offset = candle size

   while the body does `hours = offset / 3600` and passes `period` through to
   get_candles_live as the candle size. Trusting the docstring over the body
   sent me the wrong way once already, so the broker no longer uses it at all.

The broker now consumes get_candles_live() in a background task and serves the
trading loop from memory.
"""

from __future__ import annotations

import asyncio

import pytest

from core.po_broker import PocketOptionBroker

SSID = ('42["auth",{"session":"a%3A4%3A%7Bs%3A10%3A%22session_id%22%3B%7D",'
        '"isDemo":1,"uid":138033625,"platform":2}]')


def _bars(n, size=60):
    return [{"time": float(i * size), "open": 1.0, "high": 1.2,
             "low": 0.9, "close": 1.0 + i * 0.01} for i in range(n)]


class FakeClient:
    """Stands in for PocketOptionAsync, recording how the stream was opened."""

    def __init__(self, batches=None, fail=None):
        self.calls = []
        self.subscriptions = 0
        self._batches = batches if batches is not None else [_bars(120)]
        self._fail = fail

    def get_candles_live(self, asset, period, hours=2.0, max_rows=100):
        self.subscriptions += 1
        self.calls.append({"asset": asset, "period": period,
                           "hours": hours, "max_rows": max_rows})
        fail = self._fail
        batches = self._batches

        async def gen():
            if fail:
                raise fail
            for closed in batches:
                yield closed, {"time": 999.0, "open": 1, "high": 1, "low": 1, "close": 1}
            # A real stream stays open; hold it so the task does not "finish".
            while True:
                await asyncio.sleep(0.01)

        return gen()

    async def get_candles(self, *a, **k):     # must never be reached
        raise AssertionError("the per-poll helper must not be used")


def _broker(client=None):
    """Returns (broker, client). The client is handed back separately because
    close() nulls broker._client, and the assertions run after close()."""
    b = PocketOptionBroker(SSID, demo=True, uid=138033625)
    client = client or FakeClient()
    b._client = client
    return b, client


async def _read(broker, asset="EURUSD_otc", tf=60, count=200, settle=0.05):
    first = await broker.get_candles(asset, tf, count)
    await asyncio.sleep(settle)               # let the stream deliver
    second = await broker.get_candles(asset, tf, count)
    return first, second


# ------------------------------------------------------- the stream is opened once
def test_polling_repeatedly_does_not_reopen_the_subscription():
    """The bug: a new subscription per poll, so the feed never settled."""
    broker, client = _broker()

    async def scenario():
        for _ in range(10):
            await broker.get_candles("EURUSD_otc", 60, 200)
            await asyncio.sleep(0.01)
        await broker.close()

    asyncio.run(scenario())
    assert client.subscriptions == 1


def test_the_candle_size_is_passed_as_the_period():
    broker, client = _broker()

    async def scenario():
        await broker.get_candles("EURUSD_otc", 60, 200)
        await asyncio.sleep(0.02)          # let the pump task actually start
        await broker.close()

    asyncio.run(scenario())
    call = client.calls[0]
    assert call["period"] == 60, "period is the candle size, per the library's body"
    assert call["asset"] == "EURUSD_otc"
    assert call["max_rows"] >= 200, "must retain enough history for the indicators"


def test_candles_arrive_once_the_stream_delivers():
    broker, client = _broker()

    async def scenario():
        first, second = await _read(broker)
        await broker.close()
        return first, second

    _first, second = asyncio.run(scenario())
    assert second, "no candles came through the stream"
    assert second[-1].close > 0


def test_changing_the_asset_restarts_the_stream():
    broker, client = _broker()

    async def scenario():
        await broker.get_candles("EURUSD_otc", 60, 200)
        await asyncio.sleep(0.02)
        await broker.get_candles("GBPUSD_otc", 60, 200)
        await asyncio.sleep(0.02)          # let the replacement pump start
        await broker.close()

    asyncio.run(scenario())
    assert client.subscriptions == 2
    assert client.calls[-1]["asset"] == "GBPUSD_otc"


def test_changing_the_candle_size_restarts_the_stream():
    broker, client = _broker()

    async def scenario():
        await broker.get_candles("EURUSD_otc", 60, 200)
        await asyncio.sleep(0.02)
        await broker.get_candles("EURUSD_otc", 30, 200)
        await asyncio.sleep(0.02)          # let the replacement pump start
        await broker.close()

    asyncio.run(scenario())
    assert client.subscriptions == 2
    assert client.calls[-1]["period"] == 30


def test_no_more_than_the_requested_count_is_returned():
    broker, client = _broker()

    async def scenario():
        _f, second = await _read(broker, count=20)
        await broker.close()
        return second

    assert len(asyncio.run(scenario())) <= 20


# ---------------------------------------------------------- failures are surfaced
def test_a_stream_failure_is_raised_not_swallowed():
    """
    An exception inside a background task is invisible. Invisible here means the
    panel waits for a first candle that is never coming, with nothing to explain
    why — exactly the dead end the client spent two days in.
    """
    broker, _client = _broker(FakeClient(fail=RuntimeError("socket closed by Pocket Option")))

    async def scenario():
        await broker.get_candles("EURUSD_otc", 60, 200)   # starts the stream
        await asyncio.sleep(0.05)                          # it dies
        with pytest.raises(RuntimeError) as err:
            await broker.get_candles("EURUSD_otc", 60, 200)
        await broker.close()
        return str(err.value)

    msg = asyncio.run(scenario())
    assert "socket closed by Pocket Option" in msg


def test_closing_the_broker_stops_the_stream():
    broker, client = _broker()

    async def scenario():
        await broker.get_candles("EURUSD_otc", 60, 200)
        task = broker._stream_task
        await broker.close()
        return task

    task = asyncio.run(scenario())
    assert task.done() or task.cancelled()


# ------------------------------------------------------------- candle size guards
def test_the_last_candle_is_kept_because_it_is_already_closed():
    # We consume only the `closed` half of each (closed, forming) yield, so there
    # is no partial bar to drop. Dropping one would bin the freshest close and
    # leave every decision a full candle late.
    assert PocketOptionBroker.LAST_CANDLE_IS_PARTIAL is False


@pytest.mark.parametrize("tf", [5, 15, 30, 60, 300])
def test_every_supported_candle_size_is_accepted(tf):
    broker, client = _broker()

    async def scenario():
        _f, second = await _read(broker, tf=tf)
        await broker.close()
        return second

    assert asyncio.run(scenario())


@pytest.mark.parametrize("tf", [7, 45, 90, 120, 3600])
def test_an_unsupported_candle_size_is_refused_with_an_explanation(tf):
    # Pocket Option answers these with nothing at all. Silence is the worst
    # failure mode here, so turn it into a sentence naming the valid sizes.
    broker, client = _broker()
    with pytest.raises(ValueError) as err:
        asyncio.run(broker.get_candles("EURUSD_otc", tf, 50))
    assert "60" in str(err.value)


# ------------------------------------------- a feed that opens but never delivers
class SilentClient(FakeClient):
    """Opens fine, then delivers nothing — what an unauthorised session does."""

    def get_candles_live(self, asset, period, hours=2.0, max_rows=100):
        self.subscriptions += 1
        self.calls.append({"asset": asset, "period": period,
                           "hours": hours, "max_rows": max_rows})

        async def gen():
            while True:            # never yields a batch
                await asyncio.sleep(0.01)
            yield [], None         # pragma: no cover - unreachable, keeps it a generator

        return gen()


def test_a_stream_that_never_delivers_is_eventually_called_a_fault():
    """
    Reproduced against a deliberately invalid token: connect() succeeds, the
    balance stays -1.00, and the feed opens but sends nothing, with no exception
    anywhere. Silence has to become a diagnosis or it reads as "still loading"
    forever — which is precisely the dead end this project sat in.
    """
    broker, _client = _broker(SilentClient())

    async def scenario():
        await broker.get_candles("EURUSD_otc", 60, 200)     # opens the stream
        broker._stream_started -= broker.STALL_SECONDS + 1  # pretend time passed
        with pytest.raises(RuntimeError) as err:
            await broker.get_candles("EURUSD_otc", 60, 200)
        await broker.close()
        return str(err.value)

    msg = asyncio.run(scenario())
    assert "No price data" in msg
    assert "cookie" in msg, "must point at the usual cause, not just report silence"


def test_a_slow_but_working_feed_is_not_called_a_fault_too_early():
    """A backfill takes a few seconds; that must not be reported as broken."""
    broker, _client = _broker(SilentClient())

    async def scenario():
        got = await broker.get_candles("EURUSD_otc", 60, 200)
        await broker.close()
        return got

    assert asyncio.run(scenario()) == []       # empty, but no exception raised
