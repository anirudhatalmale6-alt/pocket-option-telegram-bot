"""
Confluence strategy — the honest way to aim for a HIGHER win rate.

Instead of one indicator, it runs several of the setups that tested best on
real data (all in their winning / faded configuration) and only takes a trade
when enough of them agree on the same direction at the same time.

The trade-off is deliberate and unavoidable: agreement is rarer than any single
signal, so this trades LESS OFTEN but each trade is higher-conviction. That's
the normal lever for lifting win rate — quality over quantity — not a magic
setting. `min_agree` controls how strict it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .strategy import Candle, Direction, Signal
from .custom_strategy import CustomSettings, CustomStrategy
from .alligator_strategy import AlligatorSettings, AlligatorStrategy
from .rsi_strategy import RsiSettings, RsiStrategy


@dataclass
class ConfluenceSettings:
    # Each sub-strategy in its best (faded) configuration.
    custom: CustomSettings = field(default_factory=lambda: CustomSettings(fade=True))
    alligator: AlligatorSettings = field(default_factory=lambda: AlligatorSettings(fade=True))
    rsi: RsiSettings = field(default_factory=lambda: RsiSettings(period=10, fade=True))
    # How many of the three must point the same way to trade. 2 = majority.
    min_agree: int = 2


class ConfluenceStrategy:
    def __init__(self, settings: ConfluenceSettings):
        self.settings = settings
        self._subs = [
            ("custom", CustomStrategy(settings.custom)),
            ("alligator", AlligatorStrategy(settings.alligator)),
            ("rsi", RsiStrategy(settings.rsi)),
        ]

    def evaluate(self, candles: List[Candle]) -> Signal:
        calls = puts = 0
        who = []
        for name, ev in self._subs:
            sig = ev.evaluate(candles)
            if sig.direction is Direction.CALL:
                calls += 1
                who.append(name + "↑")
            elif sig.direction is Direction.PUT:
                puts += 1
                who.append(name + "↓")

        need = self.settings.min_agree
        if calls >= need and calls > puts:
            return Signal(Direction.CALL, f"confluence {calls}/3 agree ({', '.join(who)})")
        if puts >= need and puts > calls:
            return Signal(Direction.PUT, f"confluence {puts}/3 agree ({', '.join(who)})")
        return Signal(Direction.NONE, "not enough agreement")
