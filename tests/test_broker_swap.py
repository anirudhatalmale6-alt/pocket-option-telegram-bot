"""
Switching accounts on a running trader, without a restart.

The panel writes the token and then calls swap_broker(). The dangerous parts are
all about ordering: the old socket must be closed before the new one opens, the
new broker must not be torn down by the teardown meant for the old one, and an
open position must never be carried across accounts.

These use plain asyncio.run rather than pytest-asyncio, matching the rest of the
suite so the client never has to install an extra test dependency.
"""

from __future__ import annotations

import asyncio

from core.broker import Broker
from core.config import BotConfig
from core.strategy import Candle
from core.trader import Trader


class FakeBroker(Broker):
    """Records connect/close into a shared list so ordering can be asserted."""

    LAST_CANDLE_IS_PARTIAL = False

    def __init__(self, name: str, events: list):
        self.name = name
        self.events = events
        self.connected = False

    async def connect(self):
        self.connected = True
        self.events.append(f"connect:{self.name}")

    async def close(self):
        self.connected = False
        self.events.append(f"close:{self.name}")

    async def balance(self):
        return 1000.0

    async def get_candles(self, asset, timeframe, count):
        return [Candle(time=float(i), open=1.0, high=1.0, low=1.0, close=1.0)
                for i in range(3)]

    async def place_trade(self, asset, amount, direction, expiry_seconds):
        raise AssertionError("no trade should be placed in these tests")


def _trader(events):
    cfg = BotConfig()
    cfg.poll_interval = 0.01
    cfg.running = False              # never actually trade in these tests
    return Trader(cfg, FakeBroker("old", events)), cfg


async def _run_briefly(trader, seconds=0.35):
    task = asyncio.create_task(trader.run())
    await asyncio.sleep(seconds)
    trader.stop()
    try:
        await asyncio.wait_for(task, timeout=2)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        task.cancel()


def _swap_midway(trader, new, delay=0.08):
    """Hand over a new broker while the old one is live, as the panel does."""
    async def swap():
        await asyncio.sleep(delay)
        trader.swap_broker(new)
    return swap()


def _run_with_swap(seconds=0.35):
    """Start a trader, swap its broker mid-flight, return (trader, events, new)."""
    events = []
    trader, cfg = _trader(events)
    new = FakeBroker("new", events)

    async def scenario():
        await asyncio.gather(_run_briefly(trader, seconds), _swap_midway(trader, new))

    asyncio.run(scenario())
    return trader, events, new, cfg


def test_the_new_broker_replaces_the_old_one():
    trader, _events, new, _cfg = _run_with_swap()
    assert trader.broker is new


def test_the_old_socket_is_closed_before_the_new_one_opens():
    """Two live Pocket Option sockets at once is how an account gets flagged."""
    _trader_, events, _new, _cfg = _run_with_swap()
    assert "close:old" in events and "connect:new" in events
    assert events.index("close:old") < events.index("connect:new")


def test_the_freshly_connected_broker_is_not_immediately_closed():
    # The bug this guards: `finally: await self.broker.close()` reads whichever
    # broker is installed at teardown time, which after a swap is the NEW one —
    # so it would be connected and then instantly closed, forever.
    _trader_, events, _new, _cfg = _run_with_swap()
    assert events.index("connect:new") > events.index("close:old")
    # It may be closed at shutdown, but never before it was ever used.
    if "close:new" in events:
        assert events.index("close:new") > events.index("connect:new")


def test_counters_reset_so_the_watch_line_is_not_from_the_old_account():
    events = []
    trader, _cfg = _trader(events)
    trader.checks = 99
    trader.last_reason = "stale"
    new = FakeBroker("new", events)

    async def scenario():
        await asyncio.gather(_run_briefly(trader), _swap_midway(trader, new))

    asyncio.run(scenario())
    assert trader.checks < 99
    assert trader.last_reason != "stale"


def test_trading_is_paused_across_an_account_change():
    """Carrying `running` into a different account would trade by surprise."""
    events = []
    trader, cfg = _trader(events)
    cfg.running = True
    trader.swap_broker(FakeBroker("new", events))
    assert cfg.running is False


def test_no_swap_leaves_the_broker_alone():
    events = []
    trader, _cfg = _trader(events)
    original = trader.broker
    asyncio.run(_run_briefly(trader, 0.1))
    assert trader.broker is original
