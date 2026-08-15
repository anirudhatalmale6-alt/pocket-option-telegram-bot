"""
Practice broker that replays REAL market data.

Why this exists
---------------
The original PaperBroker (broker.py) invents prices with a random walk. That is
fine for proving the plumbing works — signal, entry, settlement, martingale,
PnL, panel — but it is useless for judging a strategy, and actively misleading
if you read its win rate as a result. A random walk has no structure to detect,
so ANY strategy lands near 50% there, and at an 80% payout 50% is a slow bleed.
A "48%" in practice mode says nothing about the strategy; it is the expected
outcome of betting on a coin toss.

This broker replays genuine historical EUR/USD candles from the CSVs shipped in
the repo — the same data the backtests use. Every price the strategy sees really
happened, and every trade is settled against the price that really came next.

A limit worth stating plainly rather than hiding: free 1-minute FX history has
no intrabar detail (open = high = low = close on every bar, and ~60% of
consecutive closes are identical), so replaying it at 60s would produce mostly
ties and a meaningless win rate. So the replay uses 5-minute candles and coarser,
where the data is real, and says so when it has rounded your setting up.

And even at its best this is interbank EUR/USD, while Pocket Option's OTC pairs
are synthetic and behave differently. This is a rehearsal on real ground, not a
forecast. The only honest read on OTC performance is your own demo account.

Several pairs at once
---------------------
There is only one instrument in the shipped data, so a watchlist in practice
mode cannot replay four genuinely different markets. What it does instead is
give each pair its own position in the history, far apart from the others, so
four pairs replay four different WEEKS of real EUR/USD rather than four copies
of the same one.

That distinction is the whole point. With a single shared position the pairs
would hand the strategy consecutive slices of one series, and the trade count
would rise without the evidence rising with it — ten pairs, ten times the
trades, and still one market's worth of information. Watching several pairs is
sold on giving a verdict sooner; a practice mode that inflated the count without
inflating the evidence would be quietly lying about exactly that.
"""

from __future__ import annotations

import csv
import os
from typing import Dict, List, Optional

from .broker import Broker, TradeResult
from .strategy import Candle

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Native timeframe -> shipped file. 1-minute data is deliberately absent: see
# the module docstring. Ordered so we can pick the best fit for a request.
SOURCES: Dict[int, str] = {
    300: "data_eurusd_5m.csv",
    900: "data_eurusd_15m.csv",
    1800: "data_eurusd_30m_60d.csv",
    3600: "data_eurusd_1h_6mo.csv",
}

FINEST = min(SOURCES)


def load_csv(path: str) -> List[Candle]:
    """Read time,open,high,low,close rows into Candles."""
    out: List[Candle] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                out.append(Candle(
                    time=float(row["time"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                ))
            except (KeyError, TypeError, ValueError):
                continue  # skip a malformed row rather than refuse to start
    return out


def aggregate(candles: List[Candle], factor: int) -> List[Candle]:
    """Merge every `factor` consecutive candles into one (5m -> 15m etc.)."""
    if factor <= 1:
        return list(candles)
    out: List[Candle] = []
    for i in range(0, len(candles) - factor + 1, factor):
        chunk = candles[i:i + factor]
        out.append(Candle(
            time=chunk[0].time,
            open=chunk[0].open,
            high=max(c.high for c in chunk),
            low=min(c.low for c in chunk),
            close=chunk[-1].close,
        ))
    return out


def pick_source(timeframe: int) -> int:
    """The best native series for a requested candle size."""
    usable = [tf for tf in sorted(SOURCES) if tf <= timeframe]
    return usable[-1] if usable else FINEST


class ReplayBroker(Broker):
    """
    Walks forward through real candles, one per get_candles() call.

    Trades settle against the candle `expiry_seconds` later in the same real
    series, so a win is a price move that genuinely happened. When the data runs
    out it wraps back to the start, so an overnight practice run keeps going.
    """

    IS_PRACTICE = True

    # The replayed series contains only closed candles, so the trader has no
    # half-built bar to discard (unlike the live Pocket Option feed).
    LAST_CANDLE_IS_PARTIAL = False

    def __init__(self, timeframe: int = 300, payout: float = 0.8,
                 warmup: int = 250, starting_balance: float = 1000.0,
                 data_dir: str = _HERE):
        self._dir = data_dir
        self._payout = payout
        self._balance = starting_balance
        self._warmup = warmup
        self._cache: Dict[int, List[Candle]] = {}
        self._series: List[Candle] = []
        self._timeframe = FINEST
        self.requested_timeframe = timeframe
        self.wrapped = 0                       # times we looped the history
        self.set_timeframe(timeframe)

    # ------------------------------------------------------------- timeframe
    def _native(self, tf: int) -> List[Candle]:
        if tf not in self._cache:
            self._cache[tf] = load_csv(os.path.join(self._dir, SOURCES[tf]))
        return self._cache[tf]

    def set_timeframe(self, timeframe: int) -> None:
        """
        Rebuild the series at the closest candle size the real data supports.

        Coarser than a shipped file is built by aggregation. Finer than 5m we
        cannot invent, so we stay at 5m and record what was asked for — the
        caller reports that out loud rather than silently trading a different
        timeframe than the panel shows.
        """
        self.requested_timeframe = timeframe
        src = pick_source(timeframe)
        base = self._native(src)
        factor = max(1, timeframe // src)
        series = aggregate(base, factor)
        if len(series) < self._warmup + 50:
            # Aggregated too far for the file we have; drop back to the source.
            series, factor = list(base), 1
        self._series = series
        self._timeframe = max(src, factor * src)
        # Positions are per pair and are rebuilt lazily on first use, so a
        # timeframe change cannot leave one pair reading an index that belongs
        # to a series of a different length.
        self._cursors: Dict[str, int] = {}
        self._cursor = min(self._warmup, len(self._series) - 10)

    def _start_for(self, asset: str) -> int:
        """
        Where in the history this pair begins.

        Spread deterministically by name across the usable span, so the pairs in
        a watchlist replay different stretches of the market and each restart
        replays the same ones. crc32 rather than hash(): Python randomises hash()
        per process, which would make a practice run unreproducible and every
        comparison between two runs meaningless.
        """
        import zlib

        usable = len(self._series) - self._warmup - 10
        if usable <= 1:
            return self._warmup
        return self._warmup + zlib.crc32(asset.encode("utf-8")) % usable

    def _at(self, asset: str) -> int:
        if asset not in self._cursors:
            self._cursors[asset] = self._start_for(asset)
        return self._cursors[asset]

    @property
    def effective_timeframe(self) -> int:
        """The candle size actually being replayed."""
        return self._timeframe

    @property
    def timeframe_was_rounded(self) -> bool:
        return self.requested_timeframe != self._timeframe

    # ----------------------------------------------------------- broker API
    async def connect(self) -> None:
        return

    async def close(self) -> None:
        return

    async def get_candles(self, asset: str, timeframe: int, count: int) -> List[Candle]:
        if timeframe != self.requested_timeframe:
            self._retimeframe(timeframe)      # panel changed candle size live
        pos = self._at(asset) + 1
        if pos >= len(self._series) - 5:
            pos = self._warmup
            self.wrapped += 1
        self._cursors[asset] = pos
        self._cursor = pos                    # the last pair looked at
        return self._series[max(0, pos - count):pos]

    def _retimeframe(self, timeframe: int) -> None:
        """set_timeframe() while keeping roughly our place in history."""
        progress = self._cursor / max(1, len(self._series))
        self.set_timeframe(timeframe)
        self._cursor = max(self._warmup,
                           min(int(progress * len(self._series)), len(self._series) - 10))

    async def balance(self) -> float:
        return self._balance

    async def place_trade(self, asset: str, amount: float, direction: str,
                          expiry_seconds: int) -> TradeResult:
        entry_idx = min(self._at(asset), len(self._series) - 1)
        entry = self._series[entry_idx].close
        # How many replayed candles the option spans, at least one.
        span = max(1, round(expiry_seconds / self._timeframe))
        exit_idx = min(entry_idx + span, len(self._series) - 1)
        exit_price = self._series[exit_idx].close

        if (direction == "call" and exit_price > entry) or \
           (direction == "put" and exit_price < entry):
            result, profit = "win", amount * self._payout
        elif exit_price == entry:
            result, profit = "draw", 0.0
        else:
            result, profit = "loss", -amount

        # Do not replay the same stretch of history twice: jump past the expiry.
        # Only for THIS pair — moving every pair on because one of them traded
        # would skip history none of the others had seen.
        self._cursors[asset] = exit_idx
        self._cursor = exit_idx
        self._balance += profit
        return TradeResult(f"replay-{entry_idx}", direction, amount, result, profit)
