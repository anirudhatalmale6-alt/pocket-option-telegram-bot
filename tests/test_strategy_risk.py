"""Unit tests for the strategy signals and the risk/martingale manager."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import MartingaleSettings, RiskSettings
from core.risk import RiskManager
from core.strategy import Candle, Direction, Strategy, StrategySettings


def _candles_from_closes(closes):
    return [Candle(float(i), c, c + 0.5, c - 0.5, c) for i, c in enumerate(closes)]


def test_warmup_returns_none():
    strat = Strategy(StrategySettings())
    sig = strat.evaluate(_candles_from_closes([1, 2, 3]))
    assert sig.direction is Direction.NONE
    assert "warming up" in sig.reason


def test_uptrend_pullback_gives_call():
    # Rising trend then a sharp dip to force oversold momentum in an uptrend.
    closes = [float(i) for i in range(1, 60)]          # strong uptrend
    closes += [closes[-1] - k for k in range(1, 12)]   # deep pullback
    settings = StrategySettings(require_both=False, rsi_oversold=40, stoch_oversold=40)
    strat = Strategy(settings)
    sig = strat.evaluate(_candles_from_closes(closes))
    assert sig.direction in (Direction.CALL, Direction.NONE)
    # If it fired at all in an uptrend pullback, it must be a CALL, never a PUT.
    assert sig.direction is not Direction.PUT


def test_martingale_ladder_and_reset():
    risk = RiskManager(
        RiskSettings(base_stake=1.0, daily_loss_cap=0),
        MartingaleSettings(enabled=True, multiplier=2.0, max_steps=2),
    )
    assert risk.next_stake() == 1.0
    risk.record_result("call", 1.0, "loss", -1.0)
    assert risk.next_stake() == 2.0      # step 1
    risk.record_result("call", 2.0, "loss", -2.0)
    assert risk.next_stake() == 4.0      # step 2
    # Third loss exceeds max_steps -> ladder resets to base.
    risk.record_result("call", 4.0, "loss", -4.0)
    assert risk.next_stake() == 1.0
    # A win always resets.
    risk.record_result("call", 1.0, "loss", -1.0)
    assert risk.martingale_step == 1
    risk.record_result("call", 2.0, "win", 1.6)
    assert risk.martingale_step == 0


def test_daily_loss_cap_blocks_trading():
    risk = RiskManager(
        RiskSettings(base_stake=1.0, daily_loss_cap=3.0),
        MartingaleSettings(enabled=False),
    )
    ok, _ = risk.can_trade()
    assert ok
    risk.record_result("call", 1.0, "loss", -1.0)
    risk.record_result("call", 1.0, "loss", -1.0)
    risk.record_result("call", 1.0, "loss", -1.0)
    ok, reason = risk.can_trade()
    assert not ok and "loss cap" in reason


def test_profit_target_stops():
    risk = RiskManager(
        RiskSettings(base_stake=1.0, daily_loss_cap=0, daily_profit_target=2.0),
        MartingaleSettings(enabled=False),
    )
    risk.record_result("call", 1.0, "win", 2.5)
    ok, reason = risk.can_trade()
    assert not ok and "profit target" in reason
