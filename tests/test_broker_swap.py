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


# ---------------------------------- a refused login and an empty account differ
class UnauthorisedBroker(FakeBroker):
    """-1.00 is what the library returns when the session was never authorised."""
    async def balance(self):
        return -1.00


class EmptyBroker(FakeBroker):
    """0.00 is a real account that genuinely has no money in it."""
    async def balance(self):
        return 0.00


def _said_on_connect(broker_cls):
    said = []
    cfg = BotConfig()
    cfg.poll_interval = 0.01
    cfg.running = False

    async def notify(msg):
        said.append(msg)

    trader = Trader(cfg, broker_cls("b", []), notify=notify)
    trader.balance_attempts, trader.balance_delay = 2, 0.01   # do not sleep for real
    asyncio.run(_run_briefly(trader, 0.2))
    return said


def test_a_negative_balance_is_reported_as_a_refused_login():
    """
    Reproduced with a deliberately invalid token: connect() succeeds, balance
    returns -1.00 for ever, and no candle arrives. Calling that "no money in the
    account" sent the client hunting for a top-up button that would not have
    helped — the session simply was not authorised.
    """
    said = _said_on_connect(UnauthorisedBroker)
    assert any("NOT accepting your login" in m for m in said)
    assert any("do NOT log out" in m for m in said), "name the usual cause"


def test_a_zero_balance_is_reported_as_an_empty_account():
    said = _said_on_connect(EmptyBroker)
    assert any("no money in it" in m for m in said)
    assert not any("NOT accepting your login" in m for m in said)


def test_a_funded_account_gets_no_such_warning():
    events = []
    said = []
    cfg = BotConfig()
    cfg.poll_interval = 0.01
    cfg.running = False

    async def notify(msg):
        said.append(msg)

    trader = Trader(cfg, FakeBroker("funded", events), notify=notify)
    asyncio.run(_run_briefly(trader, 0.1))

    assert not any("no money in it" in m for m in said)


# ------------------------------------------------ balance is read until it lands
class SlowBalanceBroker(FakeBroker):
    """Mimics Pocket Option: the balance arrives a moment after the handshake."""

    def __init__(self, name, events, real=673000.0, blank_reads=3):
        super().__init__(name, events)
        self.real = real
        self.blank_reads = blank_reads
        self.reads = 0

    async def balance(self):
        self.reads += 1
        if self.reads <= self.blank_reads:
            return -1.0          # the sentinel the client actually saw
        return self.real


def test_a_late_arriving_balance_is_waited_for_not_cached_as_minus_one():
    """
    The client's demo held 673,000 on Pocket Option's own site while the panel
    showed -1.00 for 23 minutes. The balance arrives asynchronously after auth;
    reading it once, immediately, caught the value before it landed.
    """
    events = []
    broker = SlowBalanceBroker("po", events)
    cfg = BotConfig()
    trader = Trader(cfg, broker)
    got = asyncio.run(trader._settled_balance(broker, attempts=6, delay=0.01))
    assert got == 673000.0


def test_a_genuinely_empty_account_is_reported_not_retried_forever():
    """An account really at zero must still return, and be reported honestly."""
    events = []
    broker = SlowBalanceBroker("po", events, real=0.0, blank_reads=99)
    cfg = BotConfig()
    trader = Trader(cfg, broker)
    got = asyncio.run(trader._settled_balance(broker, attempts=3, delay=0.01))
    assert got <= 0
    assert broker.reads == 3          # bounded, does not spin


def test_the_balance_refreshes_while_idle_so_a_bad_read_self_corrects():
    # It used to be read only at connect and after a settled trade, so with no
    # trades a wrong value stayed on screen indefinitely.
    events = []
    broker = SlowBalanceBroker("po", events, blank_reads=0)
    cfg = BotConfig()
    cfg.poll_interval = 0.01
    cfg.running = False
    seen = []
    trader = Trader(cfg, broker, status_cb=lambda c, b: seen.append(b))
    trader._balance_at = 0.0
    asyncio.run(_run_briefly(trader, 0.2))
    assert any(b == 673000.0 for b in seen if b is not None)


# ------------------------------------------- silence with no data is not silence
class NoDataBroker(FakeBroker):
    async def get_candles(self, asset, timeframe, count):
        return []


def test_no_price_data_is_named_rather_than_looking_like_no_signal():
    """
    "hasn't done anything in 23 min" has two very different causes: a picky
    strategy, or no candles arriving at all. They must not read the same.
    """
    events = []
    cfg = BotConfig()
    cfg.poll_interval = 0.01
    cfg.running = True
    cfg.asset = "NONSENSE_otc"
    trader = Trader(cfg, NoDataBroker("nodata", events))
    asyncio.run(_run_briefly(trader, 0.2))
    assert trader.empty_candles > 0
    assert "no price data" in trader.last_reason
    assert "NONSENSE_otc" in trader.last_reason


def test_the_empty_counter_resets_once_data_returns():
    events = []
    cfg = BotConfig()
    cfg.poll_interval = 0.01
    cfg.running = True
    trader = Trader(cfg, FakeBroker("ok", events))
    trader.empty_candles = 5
    asyncio.run(_run_briefly(trader, 0.15))
    assert trader.empty_candles == 0
