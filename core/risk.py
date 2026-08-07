"""
Risk & money management: stake sizing, martingale, and daily caps.

This is the safety layer that sits between "the strategy wants to trade" and
"actually place the trade". It decides the stake and can veto trading entirely
when a daily cap is hit. Kept separate from the strategy so risk rules can be
reasoned about (and tested) on their own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .config import MartingaleSettings, RiskSettings


@dataclass
class TradeRecord:
    direction: str
    stake: float
    result: str        # "win" | "loss" | "draw" | "pending"
    profit: float      # net profit for this trade (negative on loss)
    martingale_step: int


@dataclass
class RiskManager:
    risk: RiskSettings
    martingale: MartingaleSettings

    _mg_step: int = 0                 # current martingale step (0 = base stake)
    daily_pnl: float = 0.0            # net profit/loss today
    wins: int = 0
    losses: int = 0
    history: List[TradeRecord] = field(default_factory=list)

    # ----- stake sizing --------------------------------------------------
    def next_stake(self) -> float:
        """Stake for the next trade, applying martingale if enabled."""
        stake = self.risk.base_stake
        if self.martingale.enabled and self._mg_step > 0:
            stake = self.risk.base_stake * (self.martingale.multiplier ** self._mg_step)
        return round(stake, 2)

    @property
    def martingale_step(self) -> int:
        return self._mg_step

    # ----- gate before every trade --------------------------------------
    def can_trade(self) -> tuple[bool, str]:
        """Return (allowed, reason). Enforces daily loss cap / profit target."""
        if self.risk.daily_loss_cap > 0 and self.daily_pnl <= -abs(self.risk.daily_loss_cap):
            return False, f"daily loss cap hit ({self.daily_pnl:.2f})"
        if self.risk.daily_profit_target > 0 and self.daily_pnl >= self.risk.daily_profit_target:
            return False, f"daily profit target reached ({self.daily_pnl:.2f})"
        return True, "ok"

    # ----- record a settled trade ---------------------------------------
    def record_result(self, direction: str, stake: float, result: str, profit: float) -> TradeRecord:
        """
        Update counters and advance/reset martingale.

        `profit` is the net change to balance: e.g. on an 80% payout win of a $1
        stake, profit = +0.80; on a loss, profit = -1.00; on a draw, 0.
        """
        rec = TradeRecord(direction, stake, result, profit, self._mg_step)
        self.history.append(rec)
        self.daily_pnl += profit

        if result == "win":
            self.wins += 1
            self._mg_step = 0  # reset ladder on any win
        elif result == "loss":
            self.losses += 1
            if self.martingale.enabled:
                self._mg_step += 1
                if self._mg_step > self.martingale.max_steps:
                    # Ladder exhausted -> reset and take the loss rather than
                    # chasing further (this is the risk cap on martingale).
                    self._mg_step = 0
            else:
                self._mg_step = 0
        # draws leave the ladder where it is.
        return rec

    def win_rate(self) -> float:
        total = self.wins + self.losses
        return (self.wins / total * 100.0) if total else 0.0

    def reset_day(self) -> None:
        self.daily_pnl = 0.0
        self.wins = 0
        self.losses = 0
        self._mg_step = 0
        self.history.clear()

    def summary(self) -> str:
        return (f"PnL today: {self.daily_pnl:+.2f} | "
                f"W/L: {self.wins}/{self.losses} ({self.win_rate():.0f}%) | "
                f"martingale step: {self._mg_step}")
