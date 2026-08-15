"""
Let an AI read the chart and decide — the "can it work like ChatGPT" strategy.

Read the money first
--------------------
This is the part that decides whether the idea is usable at all, so it goes
above the code rather than in a footnote.

The AI has to be asked on every candle it might trade. At 30-second candles that
is 120 questions an hour, per pair. Each question carries about 120 candles of
price history — roughly 2,000 tokens in, 100 out:

    Claude Opus 5   ~$0.0125 a question  ->  ~$1.50 an hour, per pair
    Claude Haiku    ~$0.0025 a question  ->  ~$0.30 an hour, per pair

Against a $1 stake at a 92% payout, a winning trade earns $0.92. A strategy that
fires on 7% of candles makes about 8 trades an hour, so even at a 60% win rate —
which nothing in this project has demonstrated — the hour earns well under a
dollar. Asking on every candle costs more than the trading makes, by a wide
margin, and it costs it whether the answers are any good or not.

So the AI is NOT asked on every candle. A cheap local strategy runs first and
the AI is only asked to confirm the setups that strategy already likes. That
turns 120 questions an hour into about 8 — a few cents rather than a few
dollars — and it also asks a better question: "is this specific setup any
good?" beats "what do you think of the market?".

The gate is not a workaround. A model that only sees numbers has no news, no
order book, and no feed — it is reading the same candles the indicators read.
Its value, if it has any, is judgement about a setup, not clairvoyance about a
market. None of that is proven; that is what sr_backtest and a demo run are for.

Nothing here can run without an API key that the client supplies and pays for.
No key means the strategy sits out and says so — it must never look like "no
setup found" when the truth is "nobody is paying for the answers".
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .strategy import Candle, Direction, Signal

# What one question costs, in dollars per million tokens (input, output).
# Quoted so the panel can show a running estimate rather than a surprise at the
# end of the month. Prices move; this is a estimate, not a bill.
PRICES: Dict[str, tuple] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Rough token cost of one question: the candle table plus the instructions.
EST_INPUT_TOKENS = 2000
EST_OUTPUT_TOKENS = 100


def cost_per_call(model: str) -> float:
    """Estimated dollars for one question. Unknown model -> the dearest guess."""
    inp, out = PRICES.get(model, (5.00, 25.00))
    return (EST_INPUT_TOKENS * inp + EST_OUTPUT_TOKENS * out) / 1_000_000.0


ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "direction": {"type": "string", "enum": ["up", "down", "none"]},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "reason": {"type": "string"},
    },
    "required": ["direction", "confidence", "reason"],
    "additionalProperties": False,
}

SYSTEM = """You judge short-term binary-options setups on foreign-exchange and \
synthetic OTC pairs. You will be shown recent candles and a setup that a \
mechanical strategy has already flagged.

Answer only whether THIS setup is worth taking, and which way.

What you are being judged on: over hundreds of trades, the fraction that win. \
At the payouts involved, {breakeven:.1f}% of trades must win just to break even, \
so a coin-flip answer is a loss. Saying "none" costs nothing. Saying "up" or \
"down" on a setup you are not genuinely confident about is worse than useless.

You have the candles below and nothing else — no news, no order flow, no data \
past the last candle. Do not pretend otherwise. If the candles do not support a \
view, the honest answer is "none", and it is expected to be the common one."""


@dataclass
class AiSettings:
    # Which model answers. Left at the strongest by default — this is a
    # judgement call about someone's money, and the cheaper models are a
    # deliberate choice to make, not a default to inherit quietly.
    model: str = "claude-opus-5"

    # The local strategy that must fire before the AI is asked anything.
    # "sr" / "sr_fade" / "rsi" / "custom" / "alligator" / "pullback".
    # Set to "" to ask on every candle — see the cost note at the top of this
    # file before doing that.
    gate: str = "sr_fade"

    # How much history the model is shown.
    candles: int = 120

    # Refuse to trade unless the model is at least this sure. It is the only
    # cheap defence against a confident-sounding guess.
    min_confidence: int = 65

    # Hard stops on spending, checked before every question.
    max_calls_per_hour: int = 40
    daily_budget_usd: float = 2.00

    # How long the trading loop may wait for an answer. The loop is blocked
    # while it waits, so this is deliberately short: a 30-second candle that
    # took ten seconds to think about is not the candle that was analysed.
    timeout_seconds: float = 6.0

    # Set from the panel, never committed. Falls back to the environment.
    api_key: str = ""

    # Filled in by the trader so the model is told the real break-even.
    breakeven: float = 52.1


@dataclass
class _Spend:
    """What has been asked and what it is estimated to have cost."""
    calls: int = 0
    cost: float = 0.0
    day: str = ""
    hour_marks: List[float] = field(default_factory=list)


class AiStrategy:
    """
    Gate first, ask second.

    `gate_evaluator` is any object with evaluate(candles) -> Signal. It runs on
    every candle for free; the model is only consulted when it produces a
    direction, and only to confirm or veto that direction.
    """

    def __init__(self, settings: AiSettings, gate_evaluator=None):
        self.settings = settings
        self.gate = gate_evaluator
        self.spend = _Spend()
        self._answers: Dict[float, Signal] = {}     # candle time -> answer
        self._pending: Dict[float, threading.Thread] = {}
        self._lock = threading.Lock()
        self.last_error = ""

    # ------------------------------------------------------------ housekeeping
    def _key(self) -> str:
        return (self.settings.api_key
                or os.getenv("ANTHROPIC_API_KEY", "")).strip()

    def _affordable(self) -> Optional[str]:
        """None if another question is allowed, else the reason it is not."""
        s = self.settings
        today = time.strftime("%Y-%m-%d")
        with self._lock:
            if self.spend.day != today:
                self.spend = _Spend(day=today)
            now = time.time()
            self.spend.hour_marks = [t for t in self.spend.hour_marks
                                     if now - t < 3600]
            if len(self.spend.hour_marks) >= s.max_calls_per_hour:
                return (f"reached {s.max_calls_per_hour} AI questions this hour "
                        f"— waiting rather than spending more")
            if self.spend.cost + cost_per_call(s.model) > s.daily_budget_usd:
                return (f"today's AI budget of ${s.daily_budget_usd:.2f} is spent "
                        f"(${self.spend.cost:.2f} used) — no more questions until "
                        f"tomorrow")
        return None

    def _record_attempt(self) -> None:
        """
        Count a question we have committed to asking.

        Deliberately separate from the cost below, and deliberately counted
        even when the call goes on to fail. The hourly cap is a RATE limit —
        its job is to stop the bot hammering the API, and a failing endpoint is
        exactly when hammering happens. Counting only successes would let an
        error loop retry without limit, which is the one case the cap exists
        for. The budget is the other half and only counts money actually spent.
        """
        with self._lock:
            self.spend.calls += 1
            self.spend.hour_marks.append(time.time())

    def _record_cost(self) -> None:
        """Add the estimated cost of a call that actually reached the model."""
        with self._lock:
            self.spend.cost += cost_per_call(self.settings.model)

    # ------------------------------------------------------------- the question
    def _describe(self, candles: List[Candle], setup: str) -> str:
        rows = candles[-self.settings.candles:]
        lines = [f"{c.open:.5f} {c.high:.5f} {c.low:.5f} {c.close:.5f}"
                 for c in rows]
        return (
            f"Setup flagged by the mechanical strategy: {setup}\n\n"
            f"The last {len(rows)} closed candles, oldest first, as "
            f"open high low close:\n" + "\n".join(lines) +
            "\n\nThe trade would be placed at the close of the last candle and "
            "settled one to two candles later. Is it worth taking, and which "
            "way?"
        )

    def _ask(self, prompt: str) -> Signal:
        """Blocking call to the model. Runs on a worker thread."""
        key = self._key()
        if not key:
            return Signal(Direction.NONE, "no AI key set")
        try:
            import anthropic
        except ImportError:
            return Signal(Direction.NONE,
                          "the anthropic package is not installed — run: "
                          "./.venv/bin/python -m pip install anthropic")

        s = self.settings
        try:
            client = anthropic.Anthropic(api_key=key)
            reply = client.messages.create(
                model=s.model,
                max_tokens=1000,
                system=SYSTEM.format(breakeven=s.breakeven),
                output_config={"format": {"type": "json_schema",
                                          "schema": ANSWER_SCHEMA}},
                messages=[{"role": "user", "content": prompt}],
            )
            self._record_cost()
        except Exception as exc:                    # network, auth, rate limit
            self.last_error = str(exc)
            return Signal(Direction.NONE, f"AI call failed: {exc}")

        # A refusal returns a normal response with no usable content, so check
        # the stop reason before reading the text rather than after.
        if getattr(reply, "stop_reason", "") == "refusal":
            return Signal(Direction.NONE, "the AI declined to answer this one")

        text = next((b.text for b in reply.content
                     if getattr(b, "type", "") == "text"), "")
        try:
            answer = json.loads(text)
        except (TypeError, ValueError):
            return Signal(Direction.NONE, "the AI answer could not be read")

        want = str(answer.get("direction", "none")).lower()
        conf = int(answer.get("confidence", 0))
        why = str(answer.get("reason", ""))[:200]

        if want == "none":
            return Signal(Direction.NONE, f"AI says no trade — {why}")
        if conf < s.min_confidence:
            return Signal(Direction.NONE,
                          f"AI leaned {want} but only {conf}% sure "
                          f"(needs {s.min_confidence}%) — {why}")
        return Signal(Direction.CALL if want == "up" else Direction.PUT,
                      f"AI {conf}% sure: {why}")

    # ------------------------------------------------------------------ evaluate
    def evaluate(self, candles: List[Candle]) -> Signal:
        s = self.settings
        if len(candles) < 25:
            return Signal(Direction.NONE, f"warming up ({len(candles)}/25)")

        # 1. The free opinion first. Nothing is spent unless this fires.
        setup = "none"
        if self.gate is not None:
            local = self.gate.evaluate(candles)
            if local.direction is Direction.NONE:
                return Signal(Direction.NONE, f"gate: {local.reason}")
            setup = f"{local.direction.value.upper()} — {local.reason}"

        stamp = candles[-1].time

        # 2. An answer already in hand for this candle.
        with self._lock:
            done = self._answers.pop(stamp, None)
        if done is not None:
            return done

        if not self._key():
            return Signal(Direction.NONE,
                          "AI strategy selected but no API key has been set — "
                          "paste one into the panel, or pick a strategy that "
                          "does not use the AI")

        blocked = self._affordable()
        if blocked:
            return Signal(Direction.NONE, blocked)

        # 3. Ask, on a worker thread, and wait a bounded moment for it.
        #
        # Waiting at all blocks the trading loop, which is why the gate matters:
        # this path is reached on a small fraction of candles, not all of them.
        # And the wait has to be short — an answer about a 30-second candle is
        # worthless ten seconds after that candle closed, because the price the
        # trade would open at is no longer the price that was analysed.
        prompt = self._describe(candles, setup)
        self._record_attempt()
        result: Dict[str, Signal] = {}

        def work():
            result["signal"] = self._ask(prompt)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        thread.join(s.timeout_seconds)

        if "signal" not in result:
            # Still thinking. Keep the thread alive so the answer is not thrown
            # away — it lands in the cache and is used if this same candle is
            # judged again, and is simply discarded otherwise.
            def stash():
                thread.join()
                if "signal" in result:
                    with self._lock:
                        self._answers[stamp] = result["signal"]
            threading.Thread(target=stash, daemon=True).start()
            return Signal(Direction.NONE,
                          f"AI did not answer within {s.timeout_seconds:.0f}s — "
                          f"sitting this candle out rather than entering late")

        return result["signal"]

    # ---------------------------------------------------------------- reporting
    def status(self) -> dict:
        """What has been spent, for the panel."""
        with self._lock:
            return {
                "model": self.settings.model,
                "calls": self.spend.calls,
                "cost": round(self.spend.cost, 4),
                "budget": self.settings.daily_budget_usd,
                "per_call": round(cost_per_call(self.settings.model), 5),
                "has_key": bool(self._key()),
                "last_error": self.last_error,
            }
