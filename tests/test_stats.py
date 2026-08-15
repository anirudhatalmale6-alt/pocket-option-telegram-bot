"""
Reading a win rate honestly.

His result on the practice account, 15 Aug: "out of 100 its 53% on eur usd",
and the panel showed it in green next to "100 trades". At a 92% payout,
break-even is 52.08% — so green meant "winning". It is not: 53 out of 100 has a
95% interval of 43.3% to 62.5%, and break-even sits in the middle of that. The
same record is exactly what a losing bot produces.

This project has already cost the client $200 to a small sample read as a
result. These tests exist so that cannot be done by the screen.
"""
from __future__ import annotations

from core.stats import trades_needed, verdict, wilson

BE92 = 100.0 * 100.0 / 192.0          # 52.083…, a 92% payout


def test_his_hundred_trades_are_not_a_result():
    v = verdict(53, 47, BE92)
    assert v["state"] == "unknown"
    assert v["lo"] < BE92 < v["hi"]


def test_the_interval_is_reported_so_the_number_can_be_judged():
    v = verdict(53, 47, BE92)
    assert (v["lo"], v["hi"]) == (43.3, 62.5)


def test_a_thin_edge_is_reported_as_needing_an_absurd_sample():
    """53% against 52.08% is not just unproven, it is unprovable in practice."""
    assert verdict(53, 47, BE92)["need"] > 10000


def test_a_real_edge_needs_a_reachable_sample():
    # 58% would settle inside an afternoon. This is the number that makes
    # "keep it running" advice worth anything.
    assert trades_needed(0.58, BE92 / 100.0) <= 350


def test_a_genuinely_winning_record_is_called_ahead():
    v = verdict(180, 120, BE92)        # 60% over 300
    assert v["state"] == "ahead"
    assert v["lo"] > BE92
    assert v["need"] is None


def test_a_losing_record_is_called_behind():
    v = verdict(120, 180, BE92)        # 40% over 300
    assert v["state"] == "behind"
    assert v["hi"] < BE92


def test_a_high_win_rate_can_still_be_losing_at_a_poor_payout():
    """
    55% looks like winning. At an 80% payout you need 55.6%, so it is a slow
    bleed — and 55% over a thousand trades still cannot be told apart from
    break-even. What matters is that it never reads as a pass.
    """
    be80 = 100.0 * 100.0 / 180.0
    assert verdict(550, 450, be80)["state"] != "ahead"


def test_nothing_settled_is_its_own_state():
    v = verdict(0, 0, BE92)
    assert v["state"] == "none" and v["n"] == 0


def test_a_single_win_is_never_ahead():
    """The most dangerous moment on the screen: 100% after one trade."""
    assert verdict(1, 0, BE92)["state"] == "unknown"


def test_a_short_losing_streak_is_not_a_verdict_either():
    # The interval on its own excludes break-even here and would say "behind".
    # It is the sample floor that holds it back, in both directions.
    assert verdict(0, 5, BE92)["state"] == "unknown"


def test_a_lucky_streak_never_turns_the_panel_green():
    """
    Twenty straight wins clears break-even on the interval and would have been
    reported as ahead. Twenty trades is not a reason to fund an account.
    """
    assert verdict(20, 0, BE92)["state"] == "unknown"


def test_the_floor_only_holds_back_the_verdict_not_the_numbers():
    v = verdict(20, 0, BE92)
    assert v["n"] == 20 and v["rate"] == 100.0 and v["lo"] > 0


def test_the_interval_never_leaves_the_possible_range():
    for wins, n in ((0, 1), (1, 1), (0, 10), (10, 10), (0, 0)):
        lo, hi = wilson(wins, n)
        assert 0.0 <= lo <= hi <= 1.0


def test_no_edge_to_prove_is_not_confused_with_a_long_wait():
    """Below break-even there is no sample size that helps, and it must say so
    rather than quoting a very large number as if patience would fix it."""
    assert trades_needed(0.50, BE92 / 100.0) is None
