"""
Banking the profit target and starting again — without unlocking the brake.

The client asked for this twice: "it gets to 3 dollars fast! so I would love to
just keep restarting". The feature is his call and it is built. What is NOT
negotiable is the asymmetry underneath it, and that is what these tests pin
down: the profit target restarts, the daily loss cap never does.

Why that matters. If a restart cleared the day's running total, then a bot that
banked $3 four times could go on to lose the full $20 cap on top — the cap would
be a cap on the last run, not on the day, and the number on screen would mean
something different from what it says. The only thing standing between "stops
after losing $20" and "stops after losing $20, repeatedly" is that daily_pnl
keeps counting across restarts.
"""

from __future__ import annotations

import asyncio

from core.broker import Broker
from core.config import BotConfig, MartingaleSettings, RiskSettings
from core.risk import RiskManager
from core.strategy import Candle
from core.trader import Trader
from core.web_ui import WebInterface


def _risk(target=3.0, cap=20.0, restart=True) -> RiskManager:
    return RiskManager(RiskSettings(base_stake=1.0, daily_loss_cap=cap,
                                    daily_profit_target=target,
                                    auto_restart=restart),
                       MartingaleSettings())


# ---------------------------------------------------------------- the feature
def test_off_by_default():
    assert RiskSettings().auto_restart is False


def test_reaching_the_target_still_stops_when_restart_is_off():
    r = _risk(restart=False)
    r.daily_pnl = 3.0
    assert r.can_trade()[0] is False
    assert r.can_bank() is False


def test_banking_lets_trading_carry_on():
    r = _risk()
    r.daily_pnl = 3.2
    assert r.can_trade()[0] is False
    assert r.can_bank() is True
    r.bank_and_restart()
    assert r.can_trade()[0] is True
    assert r.restarts == 1


def test_the_next_target_is_measured_from_where_the_last_one_ended():
    r = _risk()
    r.daily_pnl = 3.0
    r.bank_and_restart()
    r.daily_pnl = 5.0                 # +2.00 into the new run
    assert r.run_pnl == 2.0
    assert r.can_trade()[0] is True   # not another $3 yet
    r.daily_pnl = 6.0
    assert r.can_trade()[0] is False
    assert r.can_bank() is True


def test_the_days_total_keeps_counting_across_restarts():
    r = _risk()
    r.daily_pnl = 3.0
    r.bank_and_restart()
    r.daily_pnl = 6.0
    r.bank_and_restart()
    assert r.daily_pnl == 6.0         # the day's number, untouched
    assert r.restarts == 2


# ------------------------------------------------------------------ the brake
def test_the_loss_cap_is_not_reset_by_a_restart():
    # The whole point. Bank $3 three times, then lose the day's cap: it must
    # stop, and it must not be allowed to bank its way out of stopping.
    r = _risk()
    for target in (3.0, 6.0, 9.0):
        r.daily_pnl = target
        assert r.can_bank() is True
        r.bank_and_restart()
    r.daily_pnl = -20.0
    allowed, reason = r.can_trade()
    assert allowed is False
    assert "loss cap" in reason
    assert r.can_bank() is False


def test_a_losing_day_can_never_bank():
    r = _risk()
    r.daily_pnl = -25.0
    assert r.can_bank() is False


def test_the_loss_cap_is_reported_against_the_whole_day():
    # The line shown after each restart has to name the day's total, because the
    # day's total is what decides when everything stops.
    r = _risk()
    r.daily_pnl = 3.0
    r.bank_and_restart()
    r.daily_pnl = 6.0
    line = r.bank_and_restart()
    assert "+6.00" in line
    assert "-20.00" in line


def test_a_restart_does_not_carry_a_martingale_ladder_over():
    # A new run starts on the base stake. Carrying a doubled stake into a run
    # that has just been declared a fresh start is the opposite of what the
    # word means, and it is the stake that does the damage.
    r = _risk()
    r.martingale.enabled = True
    r.record_result("call", 1.0, "loss", -1.0)
    assert r.martingale_step == 1
    r.daily_pnl = 3.0
    r.bank_and_restart()
    assert r.martingale_step == 0
    assert r.next_stake() == 1.0


# ------------------------------------------------------------- a new day/reset
def test_resetting_the_day_clears_the_restart_count_too():
    r = _risk()
    r.daily_pnl = 3.0
    r.bank_and_restart()
    r.reset_day()
    assert r.restarts == 0 and r.target_base == 0.0 and r.run_pnl == 0.0


def test_no_target_means_nothing_to_restart():
    r = _risk(target=0.0)
    r.daily_pnl = 50.0
    assert r.can_bank() is False


# ------------------------------------------------------- the loop actually does it
class _Quiet(Broker):
    """Connects, has money, never produces a signal."""
    LAST_CANDLE_IS_PARTIAL = False

    async def connect(self): return None
    async def close(self): return None
    async def balance(self): return 1000.0

    async def get_candles(self, asset, timeframe, count):
        return [Candle(time=float(i), open=1.0, high=1.0, low=1.0, close=1.0)
                for i in range(3)]

    async def place_trade(self, asset, amount, direction, expiry_seconds):
        raise AssertionError("these tests never get as far as a trade")


async def _run_briefly(trader, seconds=0.2):
    task = asyncio.create_task(trader.run())
    await asyncio.sleep(seconds)
    trader.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def _loop_with(target_hit: float, restart: bool):
    said = []
    cfg = BotConfig()
    cfg.poll_interval = 0.01
    cfg.running = True
    cfg.risk = RiskSettings(base_stake=1.0, daily_loss_cap=20.0,
                            daily_profit_target=3.0, auto_restart=restart)

    async def notify(msg):
        said.append(msg)

    trader = Trader(cfg, _Quiet(), notify=notify)
    trader.risk.daily_pnl = target_hit
    asyncio.run(_run_briefly(trader))
    return cfg, trader, said


def test_the_loop_banks_and_keeps_running():
    cfg, trader, said = _loop_with(3.0, restart=True)
    assert cfg.running is True, "it stopped instead of restarting"
    assert trader.risk.restarts >= 1
    assert any("banked" in m for m in said)


def test_the_loop_still_stops_dead_on_the_loss_cap():
    # Restarts on, and the day is down past the cap. Nothing may talk its way
    # past this: it is the only automatic stop left once restarts are enabled.
    cfg, trader, said = _loop_with(-20.0, restart=True)
    assert cfg.running is False
    assert trader.risk.restarts == 0
    assert any("loss cap" in m for m in said)


def test_the_loop_stops_at_the_target_when_restart_is_off():
    cfg, trader, said = _loop_with(3.0, restart=False)
    assert cfg.running is False
    assert any("profit target" in m for m in said)


# --------------------------------------------------------------- on the panel
def _panel(restart=False, target=3.0):
    cfg = BotConfig()
    cfg.risk = RiskSettings(daily_profit_target=target, auto_restart=restart)
    web = WebInterface(cfg, "127.0.0.1", 0, "")
    web.paper = True
    web.auto_discover = False
    return web


def test_the_toggle_saves():
    web = _panel()
    res = web.command({"action": "settings", "auto_restart": True})
    assert res["ok"] is True
    assert web.config.risk.auto_restart is True
    assert web.state()["auto_restart"] is True


def test_the_toggle_is_refused_when_there_is_no_target_to_restart():
    # Otherwise it is a tick box that does nothing, and the page would not say
    # so — the bot would simply run until the loss limit stopped it.
    web = _panel(target=0.0)
    res = web.command({"action": "settings", "auto_restart": True})
    assert res["ok"] is False
    assert "stop after winning" in res["message"]
    assert web.config.risk.auto_restart is False, "refused, so nothing changed"


def test_the_report_says_whether_it_restarts():
    web = _panel(restart=True)
    assert "BANK IT AND RESTART" in web.diagnostics()
    assert "after target" in _panel(restart=False).diagnostics()
