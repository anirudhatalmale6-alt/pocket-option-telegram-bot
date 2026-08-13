"""
Broker abstraction.

The trader talks to an interface, not directly to Pocket Option, so we can:
  * run against a PaperBroker for backtests / dry runs with no credentials, and
  * swap in the real PocketOptionBroker (po_broker.py) for demo/live trading,
without changing a line of the strategy or trader code.

A "trade" here is a binary option: you stake `amount`, pick call/put and an
expiry; at expiry you either win the payout, lose the stake, or draw.
"""

from __future__ import annotations

import abc
import math
import random
from dataclasses import dataclass
from typing import List, Optional

from .strategy import Candle


@dataclass
class TradeResult:
    order_id: str
    direction: str
    amount: float
    result: str        # "win" | "loss" | "draw"
    profit: float      # net profit (payout - stake on win, -stake on loss)


class Broker(abc.ABC):
    """Minimal surface the trader needs."""

    # Does get_candles() return a final candle that is still forming?
    # Pocket Option's live feed does: the newest candle updates tick by tick
    # until its clock runs out. Indicators computed on a half-built candle
    # flicker, and a signal read off one can vanish before the candle closes —
    # so the trader discards it and only judges candles that have finished.
    # Offline brokers replay already-closed candles, so they leave this False.
    LAST_CANDLE_IS_PARTIAL = False

    # Is this pretend money? Declared on the base class, defaulting to the
    # DANGEROUS answer, so a new broker has to opt in to being called practice.
    # Getting this backwards would let a real account be described as pretend,
    # which is the mistake that cost this project $200 of someone's money.
    IS_PRACTICE = False

    @property
    def is_practice(self) -> bool:
        return self.IS_PRACTICE

    @abc.abstractmethod
    async def connect(self) -> None: ...

    @abc.abstractmethod
    async def close(self) -> None: ...

    @abc.abstractmethod
    async def get_candles(self, asset: str, timeframe: int, count: int) -> List[Candle]: ...

    @abc.abstractmethod
    async def balance(self) -> float: ...

    @abc.abstractmethod
    async def place_trade(self, asset: str, amount: float, direction: str,
                          expiry_seconds: int) -> TradeResult:
        """Place a binary trade and resolve it, returning the settled result."""


class PaperBroker(Broker):
    """
    Fully offline simulator. Generates a synthetic price series (random walk with
    mild trends) and settles trades against the price move over the expiry. Used
    for backtests, unit tests and dry runs so nothing here touches a real account.

    `seed` makes runs reproducible. `payout` is the assumed win payout fraction
    (0.8 = 80%, typical for OTC pairs on Pocket Option).
    """

    IS_PRACTICE = True

    def __init__(self, seed: int = 42, payout: float = 0.8, start_price: float = 1.1000):
        self._rng = random.Random(seed)
        self._payout = payout
        self._price = start_price
        self._t = 0
        self._balance = 1000.0
        self._series: List[Candle] = []
        self._prime(300)

    def _step_price(self) -> float:
        # Small drift that flips occasionally -> creates trends to trade.
        drift = math.sin(self._t / 40.0) * 0.00003
        shock = self._rng.gauss(0, 0.00025)
        self._price = max(0.0001, self._price + drift + shock)
        self._t += 1
        return self._price

    def _prime(self, n: int) -> None:
        for _ in range(n):
            o = self._price
            c = self._step_price()
            hi = max(o, c) + abs(self._rng.gauss(0, 0.0001))
            lo = min(o, c) - abs(self._rng.gauss(0, 0.0001))
            self._series.append(Candle(float(self._t), o, hi, lo, c))

    async def connect(self) -> None:  # nothing to do offline
        return

    async def close(self) -> None:
        return

    async def get_candles(self, asset: str, timeframe: int, count: int) -> List[Candle]:
        # Advance one new candle each call so a live loop sees fresh data.
        o = self._price
        c = self._step_price()
        hi = max(o, c) + abs(self._rng.gauss(0, 0.0001))
        lo = min(o, c) - abs(self._rng.gauss(0, 0.0001))
        self._series.append(Candle(float(self._t), o, hi, lo, c))
        return self._series[-count:]

    async def balance(self) -> float:
        return self._balance

    async def place_trade(self, asset: str, amount: float, direction: str,
                          expiry_seconds: int) -> TradeResult:
        entry = self._price
        # Simulate the price at expiry.
        steps = max(1, expiry_seconds // 5)
        exit_price = entry
        for _ in range(steps):
            exit_price = self._step_price()

        moved_up = exit_price > entry
        moved_down = exit_price < entry
        if (direction == "call" and moved_up) or (direction == "put" and moved_down):
            result, profit = "win", amount * self._payout
        elif exit_price == entry:
            result, profit = "draw", 0.0
        else:
            result, profit = "loss", -amount
        self._balance += profit
        return TradeResult(f"paper-{self._t}", direction, amount, result, profit)
