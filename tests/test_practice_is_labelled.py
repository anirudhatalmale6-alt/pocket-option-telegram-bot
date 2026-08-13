"""
The bot must never describe pretend money as a Pocket Option connection.

This project already cost the client $200 because practice, demo and live all
looked identical at the moment of pressing START. The balance tile was fixed to
carry its own label, but the activity log three lines below it still said
"Connected to Pocket Option (DEMO). Balance: 1000.00" while running entirely
offline — which is the same lie in a different place.
"""

import inspect

from core.broker import Broker, PaperBroker
from core.po_broker import PocketOptionBroker
from core.replay_broker import ReplayBroker


def test_the_real_broker_is_not_practice():
    # The one that matters. If this ever flips, real money gets called pretend.
    assert PocketOptionBroker.IS_PRACTICE is False


def test_the_offline_brokers_are_practice():
    assert PaperBroker.IS_PRACTICE is True
    assert ReplayBroker.IS_PRACTICE is True


def test_the_default_is_the_dangerous_answer():
    # A broker that forgets to declare itself must be treated as real, so the
    # cautious wording is what a new subclass gets for free.
    class Unfinished(Broker):
        async def connect(self): ...
        async def close(self): ...
        async def get_candles(self, asset, timeframe, count): ...
        async def balance(self): ...
        async def place_trade(self, asset, amount, direction, expiry_seconds): ...

    assert Unfinished.IS_PRACTICE is False
    assert Unfinished().is_practice is False


def test_paper_broker_instance_reports_practice():
    assert PaperBroker().is_practice is True


def test_the_trader_actually_checks_it():
    # Guards against the check being deleted or the attribute renamed: a
    # getattr(..., False) default would silently pass forever if it were.
    from core import trader
    src = inspect.getsource(trader.Trader.run)
    assert "is_practice" in src
    assert "Practice mode — no Pocket Option account is connected" in src
    assert "Connected to Pocket Option" in src


def test_connected_message_is_not_used_for_practice():
    # The two messages must be mutually exclusive, i.e. the practice branch must
    # not fall through into the Pocket Option wording.
    from core import trader
    src = inspect.getsource(trader.Trader.run)
    practice_at = src.index("Practice mode — no Pocket Option account")
    connected_at = src.index("Connected to Pocket Option (")
    between = src[practice_at:connected_at]
    assert "else:" in between, "practice branch must not fall through"


def test_the_connection_pill_checks_practice_before_connected():
    """
    The panel's connection pill.

    A running practice session sets connected=true — it genuinely is connected,
    to the simulator — so a pill written as `connected ? 'Connected' : ...` said
    "Connected" in green with no account anywhere in the picture. Order matters
    here and nothing else in the file enforces it, so pin it: the PRACTICE test
    must come before the connected test, in both the text and the colour.
    """
    from core import web_ui

    text = open(web_ui.__file__, encoding="utf-8").read()
    start = text.index("const pc = document.getElementById('p-conn')")
    pill = text[start:text.index("b-start", start)]

    label = pill.index("pc.textContent")
    assert pill.index("'PRACTICE'", label) < pill.index("s.connected ?", label)
    # ...and the green class, which is what actually gets read at a glance.
    assert "s.connected && s.mode !== 'PRACTICE'" in pill
