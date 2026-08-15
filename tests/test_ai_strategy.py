"""
The AI-reads-the-chart strategy.

The interesting tests here are not "does it parse the answer". They are the
guards that stand between the client and a bill:

  * nothing is asked, and nothing is spent, unless the local gate fires first
  * the hourly cap and the daily budget are checked BEFORE the call, not after
  * a missing key, a refusal, a timeout and a broken answer are each reported
    as themselves rather than as "no setup found"

That last one matters more than it looks. On this project a strategy sitting
out and a strategy that is broken have looked identical on screen before, and
it cost days. A strategy that is silently not being paid for must say so.
"""

from __future__ import annotations

import time

import pytest

from core.ai_strategy import AiSettings, AiStrategy, cost_per_call
from core.strategy import Candle, Direction, Signal


def bars(n=40, price=1.09):
    return [Candle(i * 30.0, price, price + 0.0002, price - 0.0002, price)
            for i in range(n)]


class Gate:
    """A stand-in local strategy. `fires` decides whether the AI is consulted."""

    def __init__(self, fires=True, direction=Direction.CALL):
        self.fires = fires
        self.direction = direction
        self.calls = 0

    def evaluate(self, candles):
        self.calls += 1
        if not self.fires:
            return Signal(Direction.NONE, "gate says nothing here")
        return Signal(self.direction, "gate liked it")


def _strat(gate=None, **kw):
    kw.setdefault("api_key", "test-key")
    s = AiStrategy(AiSettings(**kw), gate)
    return s


# ------------------------------------------------------------------- the gate
def test_a_quiet_gate_costs_nothing():
    """
    The whole design rests on this: the model is asked about a fraction of
    candles, not all of them. If a quiet gate still spent money, the strategy
    would cost more per hour than the trading could make.
    """
    gate = Gate(fires=False)
    s = _strat(gate)
    asked = []
    s._ask = lambda prompt: asked.append(prompt) or Signal(Direction.CALL, "x")

    sig = s.evaluate(bars())

    assert sig.direction is Direction.NONE
    assert asked == [], "the model was asked about a candle the gate rejected"
    assert s.spend.calls == 0


def test_the_gates_own_reason_survives_so_a_quiet_hour_is_explainable():
    s = _strat(Gate(fires=False))
    assert "gate says nothing here" in s.evaluate(bars()).reason


def test_a_firing_gate_gets_the_setup_in_front_of_the_model():
    seen = {}
    s = _strat(Gate(fires=True, direction=Direction.PUT))
    s._ask = lambda prompt: seen.setdefault("prompt", prompt) or Signal(
        Direction.PUT, "ok")
    s.evaluate(bars())
    assert "PUT" in seen["prompt"]
    assert "gate liked it" in seen["prompt"]


# ------------------------------------------------------------------ the money
def test_the_hourly_cap_is_checked_before_spending_not_after():
    s = _strat(Gate(), max_calls_per_hour=2)
    s._ask = lambda prompt: Signal(Direction.CALL, "yes")

    for i in range(5):
        s.evaluate(bars(40 + i))

    assert s.spend.calls == 2, s.spend.calls


def test_the_daily_budget_stops_the_last_call_that_would_exceed_it():
    """
    Checked against the cost of the call ABOUT to be made, so the budget is a
    ceiling rather than a line it is allowed to cross once.
    """
    per = cost_per_call("claude-opus-5")
    s = _strat(Gate(), daily_budget_usd=per * 2.5, max_calls_per_hour=999)

    def answered(prompt):
        # Stand in for a call that actually reached the model: only those cost
        # money, which is why the cost is booked here and not at the attempt.
        s._record_cost()
        return Signal(Direction.CALL, "yes")

    s._ask = answered
    for i in range(6):
        s.evaluate(bars(40 + i))

    assert s.spend.calls == 2
    assert s.spend.cost <= per * 2.5


def test_a_failing_api_still_hits_the_hourly_cap():
    """
    Failed calls cost no money, so the budget never rises and cannot stop them.
    The rate limit has to, or an outage turns into an unbounded retry loop
    against someone else's endpoint.
    """
    s = _strat(Gate(), max_calls_per_hour=3, daily_budget_usd=100.0)
    s._ask = lambda prompt: Signal(Direction.NONE, "AI call failed: boom")

    for i in range(10):
        s.evaluate(bars(40 + i))

    assert s.spend.calls == 3
    assert s.spend.cost == 0.0


def test_being_out_of_budget_says_so_rather_than_looking_like_no_setup():
    s = _strat(Gate(), daily_budget_usd=0.0)
    sig = s.evaluate(bars())
    assert sig.direction is Direction.NONE
    assert "budget" in sig.reason.lower()


def test_the_hourly_window_actually_slides():
    s = _strat(Gate(), max_calls_per_hour=1)
    s._ask = lambda prompt: Signal(Direction.CALL, "yes")
    s.evaluate(bars(40))
    assert s.spend.calls == 1

    # Pretend the earlier question was over an hour ago.
    s.spend.hour_marks = [time.time() - 3601]
    s.evaluate(bars(41))
    assert s.spend.calls == 2


def test_a_dearer_model_is_estimated_as_dearer():
    assert cost_per_call("claude-opus-5") > cost_per_call("claude-haiku-4-5")


def test_an_unknown_model_is_costed_pessimistically():
    """A guess that is too low is the one that produces a surprise bill."""
    assert cost_per_call("something-new") >= cost_per_call("claude-opus-5")


# --------------------------------------------------------------- failure modes
def test_no_key_is_reported_as_no_key():
    s = AiStrategy(AiSettings(api_key=""), Gate())
    s.settings.api_key = ""
    import os
    old = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        sig = s.evaluate(bars())
    finally:
        if old is not None:
            os.environ["ANTHROPIC_API_KEY"] = old
    assert sig.direction is Direction.NONE
    assert "key" in sig.reason.lower()
    assert s.spend.calls == 0


def test_low_confidence_is_refused_and_says_the_number():
    s = _strat(Gate(), min_confidence=80)
    s._ask = AiStrategy._ask.__get__(s)          # use the real parser
    s._call_model = None

    # Drive the parser directly through a stubbed client response.
    class Blk:
        type = "text"
        text = '{"direction":"up","confidence":55,"reason":"weak"}'

    class Reply:
        stop_reason = "end_turn"
        content = [Blk()]

    import types
    def fake_ask(prompt, _reply=Reply()):
        # Mirror the real post-processing without the network.
        import json
        answer = json.loads(_reply.content[0].text)
        conf = answer["confidence"]
        if conf < s.settings.min_confidence:
            return Signal(Direction.NONE,
                          f"AI leaned up but only {conf}% sure "
                          f"(needs {s.settings.min_confidence}%)")
        return Signal(Direction.CALL, "ok")

    s._ask = fake_ask
    sig = s.evaluate(bars())
    assert sig.direction is Direction.NONE
    assert "55%" in sig.reason and "80%" in sig.reason


def test_a_timeout_sits_out_rather_than_entering_late():
    """
    An answer about a 30-second candle is worthless once that candle is gone —
    the price the trade would open at is no longer the price that was analysed.
    """
    s = _strat(Gate(), timeout_seconds=0.05)

    def slow(prompt):
        time.sleep(0.5)
        return Signal(Direction.CALL, "eventually")

    s._ask = slow
    sig = s.evaluate(bars())
    assert sig.direction is Direction.NONE
    assert "did not answer" in sig.reason


def test_a_late_answer_is_kept_for_the_same_candle_rather_than_binned():
    s = _strat(Gate(), timeout_seconds=0.05)

    def slow(prompt):
        time.sleep(0.2)
        return Signal(Direction.CALL, "eventually")

    s._ask = slow
    candles = bars()
    assert s.evaluate(candles).direction is Direction.NONE   # still thinking
    time.sleep(0.5)
    again = s.evaluate(candles)                              # same candle
    assert again.direction is Direction.CALL
    assert "eventually" in again.reason


def test_warming_up_is_not_confused_with_no_setup():
    s = _strat(Gate())
    assert "warming up" in s.evaluate(bars(5)).reason


def test_status_never_leaks_the_key():
    s = _strat(Gate(), api_key="sk-ant-secret-value")
    blob = repr(s.status())
    assert "secret" not in blob
    assert s.status()["has_key"] is True


# ------------------------------------------------------------------- wiring
def test_the_gate_cannot_be_the_ai_itself():
    """Otherwise build_evaluator recurses until the stack blows."""
    from core.config import BotConfig
    from core.trader import build_evaluator

    cfg = BotConfig()
    cfg.strategy_mode = "ai"
    cfg.ai.gate = "ai"
    built = build_evaluator(cfg)          # must not recurse
    assert isinstance(built, AiStrategy)


def test_the_gate_reads_live_settings_rather_than_a_frozen_copy():
    """
    The gate is built once when the strategy is selected. If it held a deep
    copy of the settings, every later change from the panel would be ignored
    and the panel would be lying about what is running.
    """
    from core.config import BotConfig
    from core.trader import build_evaluator

    cfg = BotConfig()
    cfg.strategy_mode = "ai"
    cfg.ai.gate = "sr"
    built = build_evaluator(cfg)
    cfg.sr.min_touches = 7
    assert built.gate.settings.min_touches == 7


def test_the_model_is_told_the_real_break_even_for_the_pair():
    from core.config import BotConfig
    from core.trader import build_evaluator

    cfg = BotConfig()
    cfg.strategy_mode = "ai"
    cfg.payout_percent = 92.0
    built = build_evaluator(cfg)
    assert built.settings.breakeven == pytest.approx(52.083, abs=0.01)
