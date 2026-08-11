"""
Tests for the real-data practice broker.

The point of this broker is that its numbers mean something, so these tests are
about honesty as much as correctness: the replayed prices must be the ones in
the file, settlement must use the price that genuinely came next, and when the
requested candle size cannot be honoured it must SAY so rather than quietly
trade a different timeframe than the panel displays.
"""

import asyncio

import pytest

from core.replay_broker import FINEST, ReplayBroker, aggregate, pick_source
from core.strategy import Candle


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# --------------------------------------------------------------- aggregation
def _series(n):
    return [Candle(float(i), i, i + 2, i - 2, i + 1) for i in range(n)]


def test_aggregate_merges_open_high_low_close_correctly():
    out = aggregate(_series(10), 5)
    assert len(out) == 2
    first = out[0]
    assert first.open == 0            # first open of the chunk
    assert first.close == 5           # last close of the chunk (i=4 -> 5)
    assert first.high == 6            # max high  (i=4 -> 6)
    assert first.low == -2            # min low   (i=0 -> -2)


def test_aggregate_factor_one_is_a_copy():
    src = _series(4)
    assert aggregate(src, 1) == src


def test_aggregate_drops_an_incomplete_trailing_chunk():
    # 7 candles at factor 5 gives one full bar, not one and a stub.
    assert len(aggregate(_series(7), 5)) == 1


# ----------------------------------------------------------- source choosing
def test_sub_five_minute_requests_fall_back_to_the_finest_real_data():
    assert pick_source(30) == FINEST
    assert pick_source(60) == FINEST


def test_coarser_requests_pick_the_matching_native_file():
    assert pick_source(900) == 900
    assert pick_source(3600) == 3600


def test_rounding_is_reported_not_hidden():
    b = ReplayBroker(timeframe=30)
    assert b.effective_timeframe == FINEST
    assert b.timeframe_was_rounded is True, "a silently changed timeframe is a lie"

    exact = ReplayBroker(timeframe=900)
    assert exact.effective_timeframe == 900
    assert exact.timeframe_was_rounded is False


# ------------------------------------------------------------------ replay
def test_candles_come_from_the_file_and_move_forward():
    b = ReplayBroker(timeframe=300)
    first = run(b.get_candles("EURUSD_otc", 300, 50))
    second = run(b.get_candles("EURUSD_otc", 300, 50))
    assert len(first) == 50
    assert second[-1].time > first[-1].time
    # Real prices, not a random walk: values must match the loaded series.
    assert first[-1] in b._series


def test_trade_settles_against_the_price_that_actually_came_next():
    b = ReplayBroker(timeframe=300)
    run(b.get_candles("EURUSD_otc", 300, 50))
    idx = b._cursor
    entry = b._series[idx].close
    exit_ = b._series[idx + 1].close
    res = run(b.place_trade("EURUSD_otc", 1.0, "call", 300))
    expected = "win" if exit_ > entry else ("loss" if exit_ < entry else "draw")
    assert res.result == expected


def test_expiry_longer_than_a_candle_settles_further_ahead():
    b = ReplayBroker(timeframe=300)
    run(b.get_candles("EURUSD_otc", 300, 50))
    start = b._cursor
    run(b.place_trade("EURUSD_otc", 1.0, "call", 900))   # 3 candles
    assert b._cursor == start + 3, "a 15m option must not settle on the 5m bar"


def test_history_is_not_replayed_twice_within_one_trade():
    b = ReplayBroker(timeframe=300)
    run(b.get_candles("EURUSD_otc", 300, 50))
    before = b._cursor
    run(b.place_trade("EURUSD_otc", 1.0, "put", 300))
    assert b._cursor > before


def test_balance_tracks_the_payout():
    b = ReplayBroker(timeframe=300, payout=0.8, starting_balance=100.0)
    run(b.get_candles("EURUSD_otc", 300, 50))
    res = run(b.place_trade("EURUSD_otc", 10.0, "call", 300))
    expected = {"win": 108.0, "loss": 90.0, "draw": 100.0}[res.result]
    assert run(b.balance()) == pytest.approx(expected)


def test_running_off_the_end_wraps_instead_of_stopping():
    b = ReplayBroker(timeframe=3600)
    b._cursor = len(b._series) - 6
    run(b.get_candles("EURUSD_otc", 3600, 10))
    assert b.wrapped == 1
    assert b._cursor == b._warmup


def test_changing_candle_size_live_rebuilds_the_series():
    b = ReplayBroker(timeframe=300)
    run(b.get_candles("EURUSD_otc", 300, 50))
    run(b.get_candles("EURUSD_otc", 900, 50))
    assert b.effective_timeframe == 900


def test_replayed_candles_are_all_closed():
    """The trader trusts this flag to decide whether to discard the last bar."""
    assert ReplayBroker.LAST_CANDLE_IS_PARTIAL is False
