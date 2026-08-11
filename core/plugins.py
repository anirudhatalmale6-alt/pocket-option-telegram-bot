"""
Drop-in strategies: put a .py file in strategies/, restart, pick it in the panel.

Why this exists
---------------
"Can other people upload their strategy?" — yes, and it should not require
editing this project's source. Before this file, adding a strategy meant writing
a module in core/ AND registering it in two places (build_evaluator and the
panel's dropdown). Miss either one and it silently does not appear. That is a
fine workflow for me and a bad one for anybody else.

Now: any .py file in the strategies/ folder is discovered at start-up and shows
up in the Strategy dropdown by itself.

The contract is deliberately tiny
---------------------------------
A strategy file needs ONE function::

    def evaluate(candles):
        ...

`candles` is a list of Candle objects, oldest first, each with .time .open
.high .low .close. The LAST one is the most recently CLOSED candle — the bot
never hands over the candle that is still forming (see Trader._closed_candles).

Return any of these — all four mean the same thing to the bot:

    return "call"                      # bet price will be higher at expiry
    return "put", "RSI came out of 20" # ...with a reason shown in the log
    return None                        # no trade
    return Signal(Direction.CALL, "…") # the full object, if you prefer

Two optional module-level extras::

    NAME  = "Bob's EMA cross"   # label in the dropdown (defaults to the filename)
    ORDER = 10                  # sort position in the dropdown

A file that fails to import, or has no evaluate(), is skipped with a warning
rather than stopping the bot — one person's broken upload must not take the
trading loop down with it.

Safety note worth being explicit about: a strategy file is ordinary Python and
runs with the bot's permissions. Only add files from someone you trust, exactly
as you would with any other program.
"""

from __future__ import annotations

import importlib.util
import logging
import os
from typing import Callable, Dict, List, Optional, Tuple

from .strategy import Direction, Signal

log = logging.getLogger("plugins")

# Where user strategies live, relative to the project root (this file's parent's
# parent), so it works no matter which directory the bot was launched from.
PLUGIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "strategies")

# Mode ids are prefixed so a plugin can never collide with a built-in name.
PREFIX = "plugin:"


def _coerce(result) -> Signal:
    """
    Turn whatever the plugin returned into a Signal.

    Accepting several shapes is not sloppiness — it is the difference between a
    contributor's first attempt working and them giving up. The strict form is
    still available for anyone who wants it.
    """
    if result is None:
        return Signal(Direction.NONE, "no signal")
    if isinstance(result, Signal):
        return result

    reason = ""
    if isinstance(result, tuple):
        if not result:
            return Signal(Direction.NONE, "no signal")
        result, reason = result[0], (str(result[1]) if len(result) > 1 else "")

    if result is None:
        return Signal(Direction.NONE, reason or "no signal")
    if isinstance(result, Direction):
        return Signal(result, reason or result.value)

    text = str(result).strip().lower()
    if text in ("call", "buy", "up", "higher"):
        return Signal(Direction.CALL, reason or "plugin says up")
    if text in ("put", "sell", "down", "lower"):
        return Signal(Direction.PUT, reason or "plugin says down")
    if text in ("", "none", "no", "hold", "wait", "false"):
        return Signal(Direction.NONE, reason or "no signal")
    raise ValueError(
        f"strategy returned {result!r}; expected 'call', 'put', None, or a Signal"
    )


class PluginStrategy:
    """Wraps a plugin's evaluate() so the trader can treat it like any other."""

    def __init__(self, name: str, fn: Callable) -> None:
        self.name = name
        self._fn = fn

    def evaluate(self, candles) -> Signal:
        try:
            return _coerce(self._fn(candles))
        except Exception as exc:
            # A bad strategy must not kill the trading loop or, worse, be
            # mistaken for a market that simply had no setup. Say which file.
            log.warning("Strategy '%s' raised %s: %s", self.name, type(exc).__name__, exc)
            return Signal(Direction.NONE, f"{self.name} errored: {exc}")


def _load_one(path: str) -> Optional[Tuple[str, str, int, Callable]]:
    """Import one file. Returns (id, label, order, evaluate) or None."""
    stem = os.path.splitext(os.path.basename(path))[0]
    try:
        spec = importlib.util.spec_from_file_location(f"po_strategy_{stem}", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:
        log.warning("Skipping strategies/%s.py — it failed to load: %s: %s",
                    stem, type(exc).__name__, exc)
        return None

    fn = getattr(module, "evaluate", None)
    if fn is None:
        obj = getattr(module, "Strategy", None) or getattr(module, "STRATEGY", None)
        if obj is not None:
            inst = obj() if isinstance(obj, type) else obj
            fn = getattr(inst, "evaluate", None)
    if not callable(fn):
        log.warning("Skipping strategies/%s.py — it has no evaluate(candles) function.", stem)
        return None

    label = str(getattr(module, "NAME", "") or stem.replace("_", " ").title())
    try:
        order = int(getattr(module, "ORDER", 100))
    except (TypeError, ValueError):
        order = 100
    return stem, label, order, fn


def discover(folder: str = PLUGIN_DIR) -> Dict[str, dict]:
    """
    Return {mode_id: {"label":…, "order":…, "fn":…}} for every usable file.

    Called at start-up and whenever the panel asks to rescan, so a strategy can
    be added without touching this project's code.
    """
    found: Dict[str, dict] = {}
    if not os.path.isdir(folder):
        return found
    for entry in sorted(os.listdir(folder)):
        if not entry.endswith(".py") or entry.startswith(("_", ".")):
            continue
        loaded = _load_one(os.path.join(folder, entry))
        if loaded is None:
            continue
        stem, label, order, fn = loaded
        found[PREFIX + stem] = {"label": f"{label} (added)", "order": order, "fn": fn}
    if found:
        log.info("Loaded %d strategy file(s) from %s: %s",
                 len(found), folder, ", ".join(sorted(found)))
    return found


def choices(folder: str = PLUGIN_DIR) -> List[Tuple[str, str]]:
    """[(mode_id, label)] for the panel dropdown, in each file's ORDER."""
    items = discover(folder)
    return [(k, v["label"])
            for k, v in sorted(items.items(), key=lambda kv: (kv[1]["order"], kv[0]))]


def build(mode: str, folder: str = PLUGIN_DIR) -> Optional[PluginStrategy]:
    """Return an evaluator for a 'plugin:x' mode, or None if it is not one."""
    if not mode.startswith(PREFIX):
        return None
    entry = discover(folder).get(mode)
    if entry is None:
        log.warning("Strategy '%s' is selected but its file is gone.", mode)
        return None
    return PluginStrategy(mode[len(PREFIX):], entry["fn"])
