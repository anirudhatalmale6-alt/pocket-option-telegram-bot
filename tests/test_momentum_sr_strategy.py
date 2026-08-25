"""
The client's own combination: Momentum + Stochastic, taken at support/resistance.

His words were "the oscillator added to momentum and when they both hit snr i
could buy/sell". The thing these tests are really protecting is the decision
underneath that sentence — the LEVEL triggers, the other two only confirm — and
the fact that all three must point the SAME WAY. A bounce off support taken
while momentum sits at the top of its range is the market disagreeing with
itself, and skipping that trade is the entire reason this strategy exists.

The market used below is a sawtooth between a support and a resistance, which
is what makes levels form at all. Whether the FINAL leg into the level is fast
or slow is what decides if momentum is stretched when price arrives — so the
same market at two speeds gives a three-way agreement and a two-way one, which
is exactly the pair of cases worth pinning down.
"""

from __future__ import annotations

from core.config import BotConfig
from core.momentum_sr_strategy import (MomentumSrSettings, MomentumSrStrategy,
                                       _stoch_direction)
from core.strategy import Candle, Direction
from core.trader import build_evaluator

SUPPORT = 1.0900
RESISTANCE = 1.1000


def _sawtooth(final_leg: int = 8, invert: bool = False):
    """
    Five cycles between two levels, with a final approach whose speed we choose.

    `final_leg` is how many candles the last move into the level takes. Twenty
    is the same speed as every other leg, so momentum is unremarkable when price
    arrives; eight is a rush into the level, which stretches momentum to the
    edge of its own range. Nothing else differs.

    `invert` ends at resistance rather than support, so the mirror-image trade
    can be checked rather than assumed.
    """
    candles = []
    price = RESISTANCE if invert else SUPPORT
    clock = 0

    def bar(open_, close, high=None, low=None):
        nonlocal clock
        clock += 1
        return Candle(time=float(clock), open=open_, close=close,
                      high=high if high is not None else max(open_, close),
                      low=low if low is not None else min(open_, close))

    for cycle in range(5):
        last = cycle == 4
        # Away from the starting level...
        for k in range(1, 21):
            step = (RESISTANCE - SUPPORT) * k / 20
            nxt = (SUPPORT + step) if not invert else (RESISTANCE - step)
            candles.append(bar(price, nxt))
            price = nxt
        # ...touch the far level and be rejected by it.
        far = RESISTANCE if not invert else SUPPORT
        if not invert:
            candles.append(bar(price, far - 0.0008, high=far, low=far - 0.0010))
            price = far - 0.0008
        else:
            candles.append(bar(price, far + 0.0008, high=far + 0.0010, low=far))
            price = far + 0.0008
        # ...and back again. The last leg before the final touch is the one
        # whose speed decides whether momentum is stretched on arrival.
        back_legs = final_leg if last else 20
        for k in range(1, back_legs + 1):
            step = (RESISTANCE - SUPPORT) * k / back_legs
            nxt = (RESISTANCE - step) if not invert else (SUPPORT + step)
            candles.append(bar(price, nxt))
            price = nxt
        home = SUPPORT if not invert else RESISTANCE
        if not invert:
            candles.append(bar(price, home + 0.0008,
                               high=home + 0.0010, low=home))
            price = home + 0.0008
        else:
            candles.append(bar(price, home - 0.0008,
                               high=home, low=home - 0.0010))
            price = home - 0.0008
    return candles


def _fire(candles, **kw):
    return MomentumSrStrategy(MomentumSrSettings(**kw)).evaluate(candles)


# ----------------------------------------------------- all three lined up
def test_a_rush_into_support_gets_all_three_and_buys():
    sig = _fire(_sawtooth(final_leg=8))
    assert sig.direction is Direction.CALL
    assert "3/3 agree" in sig.reason
    # The log has to name the parts, not just count them.
    assert "level CALL" in sig.reason
    assert "momentum agrees" in sig.reason
    assert "oversold" in sig.reason


def test_the_mirror_image_at_resistance_sells():
    # Written out rather than assumed. A sign flipped in the confirmation half
    # would leave the support case passing and sell nothing all day.
    sig = _fire(_sawtooth(final_leg=8, invert=True))
    assert sig.direction is Direction.PUT
    assert "3/3 agree" in sig.reason
    assert "overbought" in sig.reason


# ------------------------------------------------- one confirmation missing
def test_a_slow_drift_into_support_is_skipped_when_all_three_are_required():
    sig = _fire(_sawtooth(final_leg=20))
    assert sig.direction is Direction.NONE
    assert "2/3 line up" in sig.reason
    # And it must say WHICH one is missing — "2/3" alone gives him nothing to
    # act on, while "momentum not stretched" tells him why this pair is quiet.
    assert "momentum not stretched" in sig.reason
    assert "bounced off support" in sig.reason


def test_the_looser_entry_takes_that_same_trade():
    sig = _fire(_sawtooth(final_leg=20), require_all=False)
    assert sig.direction is Direction.CALL
    assert "2/3 agree" in sig.reason


# -------------------------------------------------------- nothing to trade
def test_no_level_means_no_trade_and_says_so_first():
    # A straight line has no turns, so no level ever forms. The reason must lead
    # with that rather than with an indicator reading, because "no level" is the
    # normal state and he reads this line constantly.
    rising = [Candle(time=float(i), open=1.1 + i * 0.0001, high=1.1 + i * 0.0001,
                     low=1.1 + i * 0.0001, close=1.1 + i * 0.0001)
              for i in range(200)]
    sig = MomentumSrStrategy(MomentumSrSettings()).evaluate(rising)
    assert sig.direction is Direction.NONE
    assert sig.reason.startswith("no level")


def test_it_warms_up_rather_than_trading_on_nothing():
    few = [Candle(time=float(i), open=1.1, high=1.1, low=1.1, close=1.1)
           for i in range(5)]
    sig = MomentumSrStrategy(MomentumSrSettings()).evaluate(few)
    assert sig.direction is Direction.NONE
    assert "warming up" in sig.reason


# ------------------------------------------------------------- oscillator
def test_the_oscillator_only_votes_at_its_extremes():
    flat = [Candle(time=float(i), open=1.1, high=1.1005, low=1.0995, close=1.1)
            for i in range(60)]
    direction, note = _stoch_direction(flat, MomentumSrSettings())
    assert direction is None
    assert "mid-range" in note


def test_the_oscillator_is_not_ready_before_it_has_the_bars():
    two = [Candle(time=float(i), open=1.1, high=1.1, low=1.1, close=1.1)
           for i in range(2)]
    direction, note = _stoch_direction(two, MomentumSrSettings())
    assert direction is None
    assert "not ready" in note


# ------------------------------------------------------------------ wiring
def test_both_strictnesses_are_reachable_from_the_panel():
    cfg = BotConfig()
    cfg.strategy_mode = "momentum_sr"
    built = build_evaluator(cfg)
    assert isinstance(built, MomentumSrStrategy)
    assert built.settings.require_all is True

    cfg.strategy_mode = "momentum_sr_any"
    built = build_evaluator(cfg)
    assert isinstance(built, MomentumSrStrategy)
    assert built.settings.require_all is False, \
        "the looser entry demanded all three anyway"


def test_the_momentum_box_on_the_panel_still_applies_here():
    """
    The Momentum length/percentage box is visible while this strategy is
    selected, so it had better do something. If this strategy kept a private
    copy of those settings, the box would sit there editing nothing — a control
    that lies about what it does, which is worse than one that is missing.
    """
    cfg = BotConfig()
    cfg.strategy_mode = "momentum_sr"
    cfg.momentum.period = 20
    cfg.momentum.band_percentile = 25.0
    built = build_evaluator(cfg)
    assert built.settings.momentum.period == 20
    assert built.settings.momentum.band_percentile == 25.0


def test_momentum_confirms_as_a_state_not_as_an_event():
    # As its own strategy momentum fires only on the candle that ARRIVES at the
    # edge. Here it is being asked "is it stretched right now", so the cross
    # requirement must be off — otherwise all three would have to coincide on
    # one candle and the strategy would essentially never fire.
    assert MomentumSrSettings().momentum.require_cross is False

    cfg = BotConfig()
    cfg.strategy_mode = "momentum_sr"
    built = build_evaluator(cfg)
    assert built.settings.momentum.require_cross is False
    # ...and switching to it must not have quietly changed the plain Momentum
    # strategy underneath him.
    assert cfg.momentum.require_cross is True


def test_both_entries_are_on_the_dropdown():
    from core.web_ui import STRATEGY_CHOICES
    ids = [k for k, _ in STRATEGY_CHOICES]
    assert "momentum_sr" in ids and "momentum_sr_any" in ids
