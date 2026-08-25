"""
Risk & money management: stake sizing, martingale, and daily caps.

This is the safety layer that sits between "the strategy wants to trade" and
"actually place the trade". It decides the stake and can veto trading entirely
when a daily cap is hit. Kept separate from the strategy so risk rules can be
reasoned about (and tested) on their own.
"""

from __future__ import annotations

import time
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
    # Wall-clock time the trade settled. Used by the web dashboard to show a
    # timestamped trade log; defaults so existing callers stay unchanged.
    ts: float = field(default_factory=time.time)
    # Which pair it was. Only interesting once more than one is being watched,
    # but without it a losing streak on one bad pair is invisible inside a
    # combined record — and finding that is half the point of watching several.
    asset: str = ""


@dataclass
class RiskManager:
    risk: RiskSettings
    martingale: MartingaleSettings

    _mg_step: int = 0                 # current martingale step (0 = base stake)
    daily_pnl: float = 0.0            # net profit/loss today
    wins: int = 0
    losses: int = 0
    history: List[TradeRecord] = field(default_factory=list)

    # Profit already banked by earlier runs TODAY. The profit target is measured
    # from here; the loss cap never is. That one asymmetry is the whole design of
    # auto-restart, so it is worth saying why:
    #
    # Restarting after a win does not improve the next trade's odds — the coin
    # has no memory, and "it gets to $3 fast" is exactly what break-even looks
    # like from the inside. What restarting DOES change is the brake. If a
    # restart cleared the day's running total, the loss cap would start counting
    # from zero every time the target was hit, and a bot that banked $3 four
    # times could then lose the full cap on top — the cap would be a cap on
    # nothing. So daily_pnl keeps counting for the whole day and only the target
    # gets a fresh line drawn under it.
    target_base: float = 0.0
    restarts: int = 0

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
        # The loss cap is checked FIRST and against the whole day, so it wins
        # over everything below it including a restart that is about to happen.
        if self.risk.daily_loss_cap > 0 and self.daily_pnl <= -abs(self.risk.daily_loss_cap):
            return False, f"daily loss cap hit ({self.daily_pnl:.2f})"
        if self.risk.daily_profit_target > 0 and self.run_pnl >= self.risk.daily_profit_target:
            return False, f"daily profit target reached ({self.daily_pnl:.2f})"
        return True, "ok"

    @property
    def run_pnl(self) -> float:
        """Profit since the last restart. What the profit target measures."""
        return self.daily_pnl - self.target_base

    def can_bank(self) -> bool:
        """
        True when trading stopped on the PROFIT target and the user asked for it
        to carry on afterwards.

        Deliberately narrow. It must never be true because of the loss cap: that
        is the one stop nothing in this program is allowed to talk its way past.
        """
        if not self.risk.auto_restart or self.risk.daily_profit_target <= 0:
            return False
        if self.risk.daily_loss_cap > 0 and self.daily_pnl <= -abs(self.risk.daily_loss_cap):
            return False
        return self.run_pnl >= self.risk.daily_profit_target

    def bank_and_restart(self) -> str:
        """
        Lock in this run's profit and start the target again from here.

        Returns the line to show. It says the day's total as well as the run's,
        because the day's total is the number the loss cap is still counting and
        the one that decides when everything stops.
        """
        banked = self.run_pnl
        self.target_base = self.daily_pnl
        self.restarts += 1
        self._mg_step = 0            # a fresh run starts on the base stake
        room = ""
        if self.risk.daily_loss_cap > 0:
            left = abs(self.risk.daily_loss_cap) + self.daily_pnl
            room = (f" The day's loss limit has not moved: it stops everything "
                    f"if today ever falls to {-abs(self.risk.daily_loss_cap):.2f}, "
                    f"which is {left:.2f} away from here.")
        return (f"✓ Target reached — banked {banked:+.2f} and starting again "
                f"(restart {self.restarts} today, {self.daily_pnl:+.2f} in total)."
                + room)

    # ----- record a settled trade ---------------------------------------
    def record_result(self, direction: str, stake: float, result: str,
                      profit: float, asset: str = "") -> TradeRecord:
        """
        Update counters and advance/reset martingale.

        `profit` is the net change to balance: e.g. on an 80% payout win of a $1
        stake, profit = +0.80; on a loss, profit = -1.00; on a draw, 0.
        """
        rec = TradeRecord(direction, stake, result, profit, self._mg_step,
                          asset=asset)
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
        self.target_base = 0.0
        self.restarts = 0
        self.history.clear()

    def summary(self) -> str:
        return (f"PnL today: {self.daily_pnl:+.2f} | "
                f"W/L: {self.wins}/{self.losses} ({self.win_rate():.0f}%) | "
                f"martingale step: {self._mg_step}")
