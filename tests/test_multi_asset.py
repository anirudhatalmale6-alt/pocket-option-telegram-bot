"""
Watching several pairs at once.

The point of a watchlist is speed of evidence, not a better strategy — ten pairs
give roughly ten times the trades an hour, so a verdict that would have taken
three months arrives in ten days. What has to be true for that to be honest:

  * every pair in the list actually gets looked at, not just the first one;
  * one candle on one pair produces at most one trade;
  * a dead pair among live ones is reported as a dead PAIR, not a dead feed;
  * the win rate is scored against the hardest break-even in the list, because
    the pairs do not share one.

The last is the one that would quietly lose money: at a 92% payout you need
52.1% wins and at 52% you need 65.8%, so pooling both and judging against 92%
marks a losing week as a winning one.
"""

from __future__ import annotations

import asyncio

import pytest

from core.broker import Broker, TradeResult
from core.config import BotConfig
from core.risk import RiskManager
from core.strategy import Candle, Direction, Signal
from core.trader import Trader


class Recorder(Broker):
    """A broker that says yes to everything and remembers what it was asked."""

    LAST_CANDLE_IS_PARTIAL = False
    IS_PRACTICE = True
    is_practice = True

    def __init__(self, dead=(), stamp=1000.0):
        self.asked = []
        self.traded = []
        self.dead = set(dead)
        self.stamp = stamp

    async def connect(self):
        return None

    async def close(self):
        return None

    async def balance(self):
        return 1000.0

    async def get_candles(self, asset, timeframe, count):
        self.asked.append(asset)
        if asset in self.dead:
            return []
        # A fresh timestamp per call so each poll looks like a new closed candle
        # unless a test pins it.
        return [Candle(self.stamp, 1.0, 1.0, 1.0, 1.0) for _ in range(3)]

    async def place_trade(self, asset, amount, direction, expiry):
        self.traded.append(asset)
        return TradeResult(order_id=f"t{len(self.traded)}", direction=direction,
                           amount=amount, result="win", profit=0.9)


class Always:
    def __init__(self, direction=Direction.NONE):
        self.direction = direction

    def evaluate(self, candles):
        return Signal(self.direction, "test")


def _trader(cfg, broker, strategy):
    t = Trader(cfg, broker)
    t.strategy = strategy
    t.risk = RiskManager(cfg.risk, cfg.martingale)
    return t


async def _run_ticks(trader, ticks):
    """Run the loop for a fixed number of polls, then stop it."""
    count = {"n": 0}
    real_sleep = asyncio.sleep

    async def fake_sleep(_s):
        count["n"] += 1
        if count["n"] >= ticks:
            trader.stop()
        await real_sleep(0)

    asyncio.sleep = fake_sleep
    try:
        await trader._loop()
    finally:
        asyncio.sleep = real_sleep


# ------------------------------------------------------------- watchlist
def test_one_asset_behaves_exactly_as_before():
    cfg = BotConfig()
    cfg.asset = "EURUSD_otc"
    assert cfg.watched() == ["EURUSD_otc"]


def test_the_primary_pair_is_always_first_and_never_duplicated():
    cfg = BotConfig()
    cfg.asset = "EURUSD_otc"
    cfg.assets = ["GBPUSD_otc", "EURUSD_otc", "GBPUSD_otc"]
    assert cfg.watched() == ["EURUSD_otc", "GBPUSD_otc"]


def test_an_empty_watchlist_can_never_mean_watch_nothing():
    """
    A bot watching no pairs looks exactly like a broken connection on the panel,
    and would be reported as one.
    """
    cfg = BotConfig()
    cfg.assets = []
    assert cfg.watched() == [cfg.asset]


# --------------------------------------------------------------- rotation
def test_every_pair_in_the_list_gets_polled():
    cfg = BotConfig()
    cfg.running = True
    cfg.poll_interval = 0
    cfg.asset = "A"
    cfg.assets = ["B", "C"]
    broker = Recorder()
    trader = _trader(cfg, broker, Always())
    asyncio.run(_run_ticks(trader, 9))
    assert set(broker.asked) >= {"A", "B", "C"}, broker.asked


def test_the_pairs_take_turns_rather_than_the_first_one_hogging_it():
    cfg = BotConfig()
    cfg.running = True
    cfg.poll_interval = 0
    cfg.asset = "A"
    cfg.assets = ["B", "C"]
    broker = Recorder()
    trader = _trader(cfg, broker, Always())
    asyncio.run(_run_ticks(trader, 9))
    assert broker.asked[:6] == ["A", "B", "C", "A", "B", "C"], broker.asked


def test_one_candle_on_one_pair_produces_at_most_one_trade():
    """
    The per-candle lock has to be PER PAIR. Keeping one timestamp for all of
    them would let whichever pair polled first silence every other pair for that
    minute — which shows up as "the extra pairs never trade", not as a bug.
    """
    cfg = BotConfig()
    cfg.running = True
    cfg.poll_interval = 0
    cfg.asset = "A"
    cfg.assets = ["B"]
    broker = Recorder(stamp=555.0)          # same candle time every poll
    trader = _trader(cfg, broker, Always(Direction.CALL))
    asyncio.run(_run_ticks(trader, 12))
    assert sorted(broker.traded) == ["A", "B"], broker.traded


def test_a_trade_is_placed_on_the_pair_that_signalled():
    cfg = BotConfig()
    cfg.running = True
    cfg.poll_interval = 0
    cfg.asset = "A"
    cfg.assets = ["B", "C"]
    broker = Recorder(stamp=777.0)
    trader = _trader(cfg, broker, Always(Direction.CALL))
    asyncio.run(_run_ticks(trader, 12))
    assert sorted(broker.traded) == ["A", "B", "C"]
    # ...and the record says which pair it was, or a bad pair hides inside the
    # combined figures.
    assert sorted(t.asset for t in trader.risk.history) == ["A", "B", "C"]


def test_a_dead_pair_is_named_and_does_not_condemn_the_healthy_ones():
    cfg = BotConfig()
    cfg.running = True
    cfg.poll_interval = 0
    cfg.asset = "GOOD"
    cfg.assets = ["DEAD"]
    broker = Recorder(dead={"DEAD"})
    trader = _trader(cfg, broker, Always())
    asyncio.run(_run_ticks(trader, 20))
    # The dead pair's own counter climbs; the healthy one keeps resetting it.
    assert trader.empty_by_asset["DEAD"] > 1
    assert trader.empty_by_asset.get("GOOD", 0) == 0


def test_the_reason_line_names_the_pair_when_several_are_watched():
    cfg = BotConfig()
    cfg.running = True
    cfg.poll_interval = 0
    cfg.asset = "A"
    cfg.assets = ["B"]
    trader = _trader(cfg, Recorder(), Always())
    asyncio.run(_run_ticks(trader, 4))
    assert trader.last_reason.split(":")[0] in ("A", "B"), trader.last_reason


def test_the_reason_line_stays_clean_with_a_single_pair():
    """With one pair the pair's name in front of every line is just noise."""
    cfg = BotConfig()
    cfg.running = True
    cfg.poll_interval = 0
    cfg.asset = "A"
    trader = _trader(cfg, Recorder(), Always())
    asyncio.run(_run_ticks(trader, 3))
    assert trader.last_reason == "test"


# ------------------------------------------------------------ break-even
def test_a_mixed_watchlist_is_judged_on_its_worst_payout():
    cfg = BotConfig()
    cfg.asset = "RICH"
    cfg.assets = ["POOR"]
    cfg.payout_percent = 92.0
    cfg.asset_payouts = {"RICH": 92.0, "POOR": 52.0}
    assert cfg.worst_payout() == 52.0


def test_a_pair_with_no_known_payout_falls_back_to_the_configured_one():
    cfg = BotConfig()
    cfg.asset = "KNOWN"
    cfg.assets = ["MYSTERY"]
    cfg.payout_percent = 80.0
    cfg.asset_payouts = {"KNOWN": 92.0}
    # The unknown one must not be assumed generous just because it is unknown.
    assert cfg.worst_payout() == 80.0


def test_one_pair_leaves_the_break_even_exactly_where_it_was():
    cfg = BotConfig()
    cfg.payout_percent = 92.0
    assert cfg.worst_payout() == 92.0


# ---------------------------------------------------------------- panel
def _panel():
    from core.web_ui import WebInterface
    cfg = BotConfig()
    web = WebInterface(cfg, "127.0.0.1", 0, "")
    web.paper = True
    return web


def test_the_box_accepts_whatever_the_user_separated_them_with():
    web = _panel()
    res = web.command({"action": "settings",
                       "pairs": "EURUSD_otc, GBPUSD_otc\nAUDCAD_otc USDCHF_otc"})
    assert res["ok"], res
    assert web.config.watched() == ["EURUSD_otc", "GBPUSD_otc",
                                    "AUDCAD_otc", "USDCHF_otc"]


def test_the_first_pair_typed_becomes_the_main_one():
    web = _panel()
    web.command({"action": "settings", "pairs": "GBPJPY_otc, EURUSD_otc"})
    assert web.config.asset == "GBPJPY_otc"


def test_duplicates_are_dropped_rather_than_polled_twice():
    web = _panel()
    web.command({"action": "settings", "pairs": "A, B, A, B, C"})
    assert web.config.watched() == ["A", "B", "C"]


def test_an_absurd_list_is_refused_with_the_reason():
    web = _panel()
    res = web.command({"action": "settings",
                       "pairs": ", ".join(f"P{i}" for i in range(40))})
    assert not res["ok"]
    assert "40 pairs" in res["message"]
    # ...and nothing was applied, so the working list survives the mistake.
    assert web.config.watched() == ["EURUSD_otc"]


def test_payouts_sent_with_the_list_set_the_break_even():
    web = _panel()
    web.command({"action": "settings", "pairs": "RICH, POOR",
                 "payouts": {"RICH": 92.0, "POOR": 60.0}})
    st = web.state()
    assert st["worst_payout"] == 60.0
    assert st["breakeven"] == pytest.approx(62.5)


def test_the_state_reports_every_pair_being_watched():
    web = _panel()
    web.command({"action": "settings", "pairs": "A, B, C"})
    assert web.state()["pairs"] == ["A", "B", "C"]


def test_starting_with_a_watchlist_warns_that_the_loss_cap_arrives_sooner():
    """
    More trades an hour is the selling point and the risk in the same sentence.
    Announcing only the first half would be advertising.
    """
    web = _panel()
    web.config.risk.daily_loss_cap = 20.0
    web.command({"action": "settings", "pairs": "A, B, C, D"})
    web.command({"action": "start"})
    text = " ".join(x["text"] for x in web._log)
    assert "4x" in text
    assert "loss cap" in text


def test_starting_on_one_pair_does_not_lecture_about_a_watchlist():
    web = _panel()
    web.command({"action": "start"})
    assert "loss cap is reached" not in " ".join(x["text"] for x in web._log)
