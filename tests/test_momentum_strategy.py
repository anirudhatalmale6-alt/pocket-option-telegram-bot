"""
Momentum(10) at the top and bottom of its own range.

The client asked for "momentum, 10, line and every time it hits the top or
bottom it gives a 1 min trade". Two things in that are not settings but
decisions, and both are pinned down here:

  * WHERE the top is. Momentum has no fixed 70/30 scale, so the lines are read
    off the indicator's own recent history. These tests prove that works
    unchanged on a pair whose numbers are a hundred times bigger — which a
    fixed threshold could not do, and which is what "it never trades on that
    pair" would have looked like.

  * WHICH WAY the trade goes, which the sentence never says. Reversal is the
    default and `momentum_follow` is the opposite bet; the two must never be
    silently swapped, so the direction is asserted explicitly.
"""

from __future__ import annotations

from core.config import BotConfig
from core.indicators import momentum, momentum_series, percentile
from core.momentum_strategy import (MIN_HISTORY, MomentumSettings,
                                    MomentumStrategy, bands)
from core.strategy import Candle, Direction
from core.trader import build_evaluator


def _candles(closes):
    return [Candle(time=float(i), open=c, high=c, low=c, close=c)
            for i, c in enumerate(closes)]


def _settle(base=1.1, n=140, wobble=0.0002, seed=7):
    """A calm, repeatable market: enough history to place a band in."""
    out, price = [], base
    state = seed
    for _ in range(n):
        state = (state * 1103515245 + 12345) % 2147483648
        step = ((state / 2147483648.0) - 0.5) * wobble
        price += step
        out.append(price)
    return out


# ------------------------------------------------------------------ indicator
def test_momentum_is_100_when_price_has_not_moved():
    assert momentum([1.1] * 30, 10) == 100.0


def test_above_100_after_a_rise_and_below_after_a_fall():
    rising = [1.0 + i * 0.001 for i in range(30)]
    assert momentum(rising, 10) > 100.0
    assert momentum(list(reversed(rising)), 10) < 100.0


def test_it_compares_against_exactly_period_bars_ago():
    closes = [1.0] * 20 + [1.5]
    # 1.5 against the 1.0 ten bars back = 150.
    assert momentum(closes, 10) == 150.0


def test_a_zero_reference_price_does_not_divide_by_zero():
    # A data gap must not kill the trading loop. 100 = "no change known".
    assert momentum_series([0.0] * 11 + [1.0], 10)[-1] == 100.0


def test_there_is_one_value_per_bar_after_the_first_period():
    assert len(momentum_series(list(range(1, 51)), 10)) == 40
    assert momentum_series([1.0] * 5, 10) == []


def test_percentile_interpolates_rather_than_snapping_to_a_sample():
    assert percentile([0.0, 10.0], 50.0) == 5.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.0) == 1.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 100.0) == 4.0
    assert percentile([], 50.0) is None


# ---------------------------------------------------------------- the bands
def test_the_band_is_measured_from_history_only():
    # The value being judged must not help define the line it is judged
    # against, or a new extreme could never reach the edge.
    history = [100.0 + i * 0.01 for i in range(100)]
    b = bands(history, MomentumSettings())
    assert b is not None
    assert b.upper < max(history) + 1e-9
    assert b.values == 100


def test_a_flat_market_produces_no_band_at_all():
    # PO's OTC pairs go flat between ticks. A zero-width band would make every
    # bar simultaneously a top AND a bottom — trading hardest where there is
    # nothing to trade.
    assert bands([100.0] * 100, MomentumSettings()) is None


def test_a_short_history_is_refused_rather_than_guessed_at():
    assert bands([100.0 + i * 0.01 for i in range(MIN_HISTORY - 1)],
                 MomentumSettings()) is None


def test_only_the_lookback_window_is_used():
    old = [100.0 + i for i in range(200)]          # a huge, ancient range
    recent = [100.0 + i * 0.001 for i in range(100)]
    b = bands(old + recent, MomentumSettings(band_lookback=100))
    assert b is not None
    assert b.upper < 101.0, "the band was still measuring the old, wide range"


# ------------------------------------------------------------- the signals
def _fire(closes, **kw):
    return MomentumStrategy(MomentumSettings(**kw)).evaluate(_candles(closes))


def test_it_says_it_is_warming_up_rather_than_no_signal():
    # "No trade" and "not enough data yet" look identical on the panel unless
    # the reason says so, and the client reads that line to decide whether the
    # bot is working at all.
    sig = _fire([1.1] * 20)
    assert sig.direction is Direction.NONE
    assert "warming up" in sig.reason


def test_reaching_the_top_bets_on_the_turn():
    closes = _settle() + [1.1 + 0.02]     # a jump far above anything recent
    sig = _fire(closes)
    assert sig.direction is Direction.PUT
    assert "top of its range" in sig.reason


def test_reaching_the_bottom_bets_on_the_turn():
    closes = _settle() + [1.1 - 0.02]
    sig = _fire(closes)
    assert sig.direction is Direction.CALL
    assert "bottom of its range" in sig.reason


def test_the_follow_reading_takes_the_opposite_side():
    closes = _settle() + [1.1 + 0.02]
    sig = _fire(closes, fade=True)
    assert sig.direction is Direction.CALL
    assert "FOLLOW" in sig.reason


def test_sitting_in_the_middle_is_no_trade():
    # A close exactly equal to the one ten bars back is Momentum 100 — dead
    # centre for a market that has gone nowhere. Nothing to trade, and the
    # reason must say where in the range it is rather than just "no signal",
    # because that line is how the client tells a working bot from a stuck one.
    closes = _settle()
    sig = _fire(closes + [closes[-10]])
    assert sig.direction is Direction.NONE
    assert "through its range" in sig.reason


def test_it_fires_once_on_arrival_not_on_every_bar_it_stays_up():
    # A 1-minute expiry means overlapping trades: six bars above the line would
    # be six live bets on one move, which is a single six-times-larger stake
    # wearing a disguise.
    base = _settle()
    first = _fire(base + [1.1 + 0.02])
    second = _fire(base + [1.1 + 0.02, 1.1 + 0.021])
    assert first.direction is Direction.PUT
    assert second.direction is Direction.NONE


def test_with_the_cross_off_it_keeps_firing_while_it_stays_up():
    base = _settle()
    sig = _fire(base + [1.1 + 0.02, 1.1 + 0.021], require_cross=False)
    assert sig.direction is Direction.PUT


def test_a_flat_market_never_trades():
    sig = _fire([1.1] * 200)
    assert sig.direction is Direction.NONE
    assert "flat" in sig.reason


# -------------------------------------------------- the same rule on any pair
def test_the_same_move_fires_on_a_pair_a_hundred_times_bigger():
    """
    The reason there is no fixed threshold in this file.

    EUR/USD wobbles by 0.02% over ten 1-minute bars; USD/JPY at 150 moves a
    hundred times as many price units for the same percentage. A fixed
    "momentum above 100.5" line would fire constantly on one and never on the
    other. Scaling the whole series must leave the decision identical.
    """
    small = _settle(base=1.1, wobble=0.0002) + [1.1 * 1.018]
    big = [p * 150 / 1.1 for p in small]
    assert _fire(small).direction is _fire(big).direction is Direction.PUT


def test_a_quiet_market_still_has_a_top():
    # Ten times calmer. The band closes in with it, so the strategy does not
    # simply stop working when the session goes quiet.
    quiet = _settle(wobble=0.00002)
    sig = _fire(quiet + [quiet[-1] * 1.0018])
    assert sig.direction is Direction.PUT


# ------------------------------------------------------------------- wiring
def test_the_panel_can_select_both_readings():
    cfg = BotConfig()
    cfg.strategy_mode = "momentum"
    assert isinstance(build_evaluator(cfg), MomentumStrategy)
    assert cfg.momentum.fade is False

    cfg.strategy_mode = "momentum_follow"
    assert isinstance(build_evaluator(cfg), MomentumStrategy)
    assert cfg.momentum.fade is True, "the reversed entry traded the normal way"


def test_the_client_asked_for_ten():
    assert MomentumSettings().period == 10


# ------------------------------------------------- the panel side of it
def _panel():
    from core.web_ui import WebInterface
    web = WebInterface(BotConfig(), "127.0.0.1", 0, "")
    web.paper = True
    web.auto_discover = False
    return web


def test_both_readings_are_on_the_dropdown():
    from core.web_ui import STRATEGY_CHOICES
    ids = [k for k, _ in STRATEGY_CHOICES]
    assert "momentum" in ids and "momentum_follow" in ids
    labels = dict(STRATEGY_CHOICES)
    assert "REVERSED" in labels["momentum_follow"], \
        "nothing on screen would say which way the reversed entry trades"


def test_the_length_and_the_percentage_save():
    web = _panel()
    res = web.command({"action": "settings", "strategy": "momentum",
                       "momentum_period": 14, "momentum_percentile": 20})
    assert res["ok"] is True
    assert web.config.momentum.period == 14
    assert web.config.momentum.band_percentile == 20
    assert web.state()["momentum_period"] == 14


def test_a_percentage_that_would_collapse_the_band_is_refused():
    # At 50 the top and the bottom meet in the middle: every bar is at both at
    # once. The panel must say so rather than accept a setting that turns the
    # strategy into a coin flip on every candle.
    web = _panel()
    res = web.command({"action": "settings", "momentum_percentile": 50})
    assert res["ok"] is False
    assert web.config.momentum.band_percentile == 10.0, "refused, so nothing changed"


def test_the_report_names_the_momentum_settings_when_momentum_is_running():
    web = _panel()
    web.command({"action": "settings", "strategy": "momentum"})
    assert "momentum        10" in web.diagnostics()
    # ...and stays out of the way otherwise, so the pasted report keeps to what
    # is actually in use.
    web.command({"action": "settings", "strategy": "sr"})
    assert "momentum " not in web.diagnostics()


def test_the_payout_table_says_what_kind_each_asset_is():
    # "USD pairs" cannot be picked out of the symbol alone — AAPL_USD and BTCUSD
    # both contain USD and neither is a currency pair.
    import core.web_ui as web_ui
    source = open(web_ui.__file__, encoding="utf-8").read()
    assert '"kind": a.kind,' in source


def test_the_strategy_dropdown_is_not_overwritten_while_it_holds_a_choice():
    """
    The panel repaints from the saved settings every 2 seconds, and its
    "don't fight the user" rule only protects the field that currently has
    FOCUS. Pick a strategy, click into any other box, and two seconds later the
    dropdown silently returns to the old one — while Save reads the dropdown,
    so it saves the strategy you thought you had just replaced. Found in a
    browser, not by these tests, which is exactly why the guard is asserted
    here now.
    """
    import core.web_ui as web_ui
    source = open(web_ui.__file__, encoding="utf-8").read()
    assert "if (!strategyDirty) setField('f-strategy', s.strategy);" in source
    assert "strategyDirty = false" in source, "nothing ever clears the flag"
