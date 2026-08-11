"""
Drop-in strategy files: discovery, the forgiving return contract, and the
promise that a broken upload cannot take the trading loop down.

That last one is the point of most of these tests. The feature exists so someone
other than me can add a strategy, which means the failure modes to design for
are a typo, a missing function, and a crash mid-run — not a well-formed module.
"""

from __future__ import annotations

import os
import textwrap

import pytest

from core import plugins
from core.strategy import Candle, Direction, Signal


def _write(folder, name, body):
    path = os.path.join(folder, name)
    with open(path, "w") as fh:
        fh.write(textwrap.dedent(body))
    return path


def _candles(n=30):
    return [Candle(time=float(i), open=1.0, high=1.1, low=0.9, close=1.0 + i * 0.01)
            for i in range(n)]


# ------------------------------------------------------------------ discovery
def test_discovers_a_plain_evaluate_function(tmp_path):
    _write(tmp_path, "mine.py", '''
        def evaluate(candles):
            return "call"
    ''')
    found = plugins.discover(str(tmp_path))
    assert "plugin:mine" in found


def test_label_defaults_to_the_filename(tmp_path):
    _write(tmp_path, "my_thing.py", "def evaluate(c): return None\n")
    labels = dict(plugins.choices(str(tmp_path)))
    assert "My Thing" in labels["plugin:my_thing"]


def test_name_attribute_wins_over_the_filename(tmp_path):
    _write(tmp_path, "x.py", 'NAME = "Bob\'s cross"\ndef evaluate(c): return None\n')
    labels = dict(plugins.choices(str(tmp_path)))
    assert "Bob's cross" in labels["plugin:x"]


def test_order_attribute_sorts_the_dropdown(tmp_path):
    _write(tmp_path, "late.py", "ORDER = 50\ndef evaluate(c): return None\n")
    _write(tmp_path, "early.py", "ORDER = 10\ndef evaluate(c): return None\n")
    assert [k for k, _ in plugins.choices(str(tmp_path))] == ["plugin:early", "plugin:late"]


def test_private_and_non_python_files_are_ignored(tmp_path):
    _write(tmp_path, "_helper.py", "def evaluate(c): return 'call'\n")
    _write(tmp_path, "notes.txt", "def evaluate(c): return 'call'\n")
    assert plugins.discover(str(tmp_path)) == {}


def test_missing_folder_is_not_an_error(tmp_path):
    assert plugins.discover(str(tmp_path / "nope")) == {}


def test_a_class_based_strategy_also_works(tmp_path):
    _write(tmp_path, "klass.py", '''
        class Strategy:
            def evaluate(self, candles):
                return "put"
    ''')
    ev = plugins.build("plugin:klass", str(tmp_path))
    assert ev.evaluate(_candles()).direction is Direction.PUT


# --------------------------------------------------- a broken file is survivable
def test_a_file_that_fails_to_import_is_skipped_not_fatal(tmp_path):
    _write(tmp_path, "broken.py", "this is not python(\n")
    _write(tmp_path, "good.py", "def evaluate(c): return None\n")
    found = plugins.discover(str(tmp_path))
    assert "plugin:good" in found and "plugin:broken" not in found


def test_a_file_without_evaluate_is_skipped(tmp_path):
    _write(tmp_path, "empty.py", "NAME = 'nothing here'\n")
    assert plugins.discover(str(tmp_path)) == {}


def test_a_strategy_that_raises_sits_out_and_says_why(tmp_path):
    _write(tmp_path, "boom.py", '''
        def evaluate(candles):
            raise ValueError("bad maths")
    ''')
    sig = plugins.build("plugin:boom", str(tmp_path)).evaluate(_candles())
    # No trade, and the reason names the failure rather than looking like a
    # market with no setup — those two must never be confusable.
    assert sig.direction is Direction.NONE
    assert "bad maths" in sig.reason


def test_build_returns_none_when_the_file_is_gone(tmp_path):
    assert plugins.build("plugin:ghost", str(tmp_path)) is None


def test_build_ignores_non_plugin_modes(tmp_path):
    assert plugins.build("confluence", str(tmp_path)) is None


# ------------------------------------------------------- the return contract
@pytest.mark.parametrize("returned,expected", [
    ("call", Direction.CALL),
    ("buy", Direction.CALL),
    ("up", Direction.CALL),
    ("put", Direction.PUT),
    ("sell", Direction.PUT),
    ("down", Direction.PUT),
    (None, Direction.NONE),
    ("none", Direction.NONE),
    ("hold", Direction.NONE),
    (Direction.CALL, Direction.CALL),
])
def test_every_accepted_return_shape(returned, expected):
    assert plugins._coerce(returned).direction is expected


def test_a_tuple_carries_the_reason_through():
    sig = plugins._coerce(("put", "RSI came out of 20"))
    assert sig.direction is Direction.PUT
    assert sig.reason == "RSI came out of 20"


def test_a_full_signal_passes_through_untouched():
    original = Signal(Direction.CALL, "already a Signal")
    assert plugins._coerce(original) is original


def test_nonsense_return_is_reported_not_silently_ignored():
    # Silently treating garbage as "no trade" would hide a real bug in someone's
    # strategy for hours. PluginStrategy turns this into a logged sit-out.
    with pytest.raises(ValueError):
        plugins._coerce("mabye?")


def test_nonsense_return_becomes_a_logged_sit_out(tmp_path):
    _write(tmp_path, "weird.py", "def evaluate(c): return 'maybe'\n")
    sig = plugins.build("plugin:weird", str(tmp_path)).evaluate(_candles())
    assert sig.direction is Direction.NONE
    assert "maybe" in sig.reason


# ------------------------------------------------------- the shipped example
def test_the_shipped_example_loads_and_evaluates():
    """The example is documentation; documentation that does not run is a lie."""
    found = plugins.discover()
    assert "plugin:example_ema_cross" in found
    ev = plugins.build("plugin:example_ema_cross")
    sig = ev.evaluate(_candles(60))
    assert sig.direction in (Direction.CALL, Direction.PUT, Direction.NONE)
    assert "errored" not in sig.reason
