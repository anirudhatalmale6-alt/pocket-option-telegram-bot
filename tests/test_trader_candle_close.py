"""
Tests for acting on candle CLOSE, not on the candle that is still forming.

The client spotted this from the panel: "it doesnt start a new candlestick it
just goes off the one already in place". He was right. The loop polls once a
second, and Pocket Option's newest candle keeps changing until its clock runs
out — so the bot was reading indicators off a half-built bar and could enter on
a "cross" that had not happened yet and might never happen. It could also judge
the same bar over and over.

Two rules, tested here:
  1. If the broker's last candle is still forming, discard it.
  2. One candle produces at most one decision, however often we poll.
"""

import asyncio
from typing import List

import pytest

from core.broker import Broker, TradeResult
from core.config import BotConfig
from core.strategy import Candle, Direction, Signal
from core.trader import Trader


class _FakeBroker(Broker):
    """Serves a fixed candle list; the last one is 'live' if the flag says so."""

    def __init__(self, candles: List[Candle], partial: bool):
        self.LAST_CANDLE_IS_PARTIAL = partial
        self._candles = candles
        self.seen: List[List[Candle]] = []
        self.trades = 0

    async def connect(self): return None
    async def close(self): return None
    async def balance(self): return 1000.0

    async def get_candles(self, asset, timeframe, count):
        return list(self._candles)

    async def place_trade(self, asset, amount, direction, expiry_seconds):
        self.trades += 1
        return TradeResult("t", direction, amount, "win", amount * 0.8)


class _AlwaysBuy:
    """Fires on every evaluation, so any gating failure shows up as extra trades."""

    def __init__(self):
        self.calls = 0

    def evaluate(self, candles):
        self.calls += 1
        return Signal(Direction.CALL, "test")


def _candles(n: int) -> List[Candle]:
    return [Candle(float(i), 1.0, 1.1, 0.9, 1.0 + i * 0.001) for i in range(n)]


def _trader(broker) -> Trader:
    cfg = BotConfig()
    cfg.running = True
    cfg.poll_interval = 0.001
    t = Trader(cfg, broker)
    t.strategy = _AlwaysBuy()
    return t


async def _run_briefly(trader, seconds=0.15):
    task = asyncio.create_task(trader._loop())
    await asyncio.sleep(seconds)
    trader.stop()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        task.cancel()


def test_forming_candle_is_discarded():
    series = _candles(10)
    broker = _FakeBroker(series, partial=True)
    trader = _trader(broker)
    kept = trader._closed_candles(series)
    assert len(kept) == len(series) - 1
    assert kept[-1] is series[-2], "must judge the last CLOSED bar, not the live one"


def test_offline_broker_keeps_every_candle():
    series = _candles(10)
    trader = _trader(_FakeBroker(series, partial=False))
    assert trader._closed_candles(series) == series


def test_a_single_candle_produces_a_single_trade_however_fast_we_poll():
    """The heart of it: polling faster must not mean trading more."""
    broker = _FakeBroker(_candles(10), partial=True)
    trader = _trader(broker)
    asyncio.run(_run_briefly(trader))
    assert broker.trades == 1, (
        f"the same candle was traded {broker.trades} times — polling frequency "
        "must not change how often the bot trades")


def test_a_new_candle_is_traded_again():
    broker = _FakeBroker(_candles(10), partial=True)
    trader = _trader(broker)

    async def scenario():
        task = asyncio.create_task(trader._loop())
        await asyncio.sleep(0.05)
        broker._candles = _candles(11)      # a new bar closed
        await asyncio.sleep(0.05)
        trader.stop()
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()

    asyncio.run(scenario())
    assert broker.trades == 2


def test_empty_candle_list_does_not_crash_the_loop():
    broker = _FakeBroker([], partial=True)
    trader = _trader(broker)
    asyncio.run(_run_briefly(trader, 0.05))
    assert broker.trades == 0


# ------------------------------------------------------- proof of life
class _NeverTrades:
    def evaluate(self, candles):
        return Signal(Direction.NONE, "0/3 agree (none agree) — need 2")


def test_a_silent_strategy_still_reports_that_it_is_working():
    """
    The client watched confluence sit out for a minute and reasonably asked why
    nothing had happened. A picky strategy and a crashed one look identical on a
    blank screen, so the trader must publish what it checked and why it passed.
    """
    broker = _FakeBroker(_candles(10), partial=True)
    trader = _trader(broker)
    trader.strategy = _NeverTrades()

    assert trader.checks == 0
    asyncio.run(_run_briefly(trader, 0.1))

    assert broker.trades == 0
    assert trader.checks >= 1, "the bot must record that it looked at the candle"
    assert trader.last_check_ts > 0
    assert "need 2" in trader.last_reason, "the reason must say what was missing"


def test_confluence_explains_itself_when_it_sits_out():
    """'not enough agreement' tells you nothing; the count and who voted do."""
    from core.confluence_strategy import ConfluenceSettings, ConfluenceStrategy

    flat = [Candle(float(i), 1.0, 1.0, 1.0, 1.0) for i in range(200)]
    sig = ConfluenceStrategy(ConfluenceSettings(min_agree=2)).evaluate(flat)
    assert sig.direction is Direction.NONE
    assert "/3 agree" in sig.reason
    assert "need 2" in sig.reason
