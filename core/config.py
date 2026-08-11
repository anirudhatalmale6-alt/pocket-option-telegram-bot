"""
Runtime configuration.

Loaded from environment variables / a .env file at start-up, but most trading
parameters can also be changed live from Telegram (see telegram_bot.py). The
config object is the single source of truth the trader reads on every cycle, so
a change from Telegram takes effect on the next candle without a restart.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from typing import Optional

from dotenv import load_dotenv

from .strategy import StrategySettings
from .trend_strategy import TrendSettings
from .custom_strategy import CustomSettings
from .alligator_strategy import AlligatorSettings
from .rsi_strategy import RsiSettings
from .confluence_strategy import ConfluenceSettings

load_dotenv()  # read a local .env if present


def _s(name: str, default: str = "") -> str:
    """
    Read a string setting, tolerating a trailing comment on an EMPTY value.

    python-dotenv strips an inline `# comment` when the value is non-empty, but
    for a line like

        WEB_PASSWORD=          # set this on a public VPS

    the value comes back as the comment text itself. That silently turned an
    unset password into a real one nobody knew, and locked the control panel.
    A value that begins with '#' is never something a user meant to set, so
    treat it as unset.
    """
    val = os.getenv(name)
    if val is None:
        return default
    val = val.strip()
    if val.startswith("#"):
        return default
    return val


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _b(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class MartingaleSettings:
    enabled: bool = False
    multiplier: float = 2.0   # stake multiplier after a loss
    max_steps: int = 2        # how many consecutive martingale steps before reset


@dataclass
class RiskSettings:
    base_stake: float = 1.0            # normal stake per trade (account currency)
    daily_loss_cap: float = 20.0       # stop trading after net loss reaches this
    daily_profit_target: float = 0.0   # stop after this net profit (0 = disabled)
    max_concurrent_trades: int = 1     # binary options: usually 1 at a time


@dataclass
class BotConfig:
    # --- Pocket Option connection ---
    po_ssid: str = ""                  # browser session token; supplied at connect step
    po_uid: int = 0                    # account id, only needed if po_ssid is a bare session
    po_demo: bool = True               # ALWAYS start on demo
    asset: str = "EURUSD_otc"          # default traded asset
    expiry_seconds: int = 60           # binary expiry (60 = 1m). Client range 3m..1h ok.

    # --- Telegram (optional) ---
    # Leave the token blank to run without Telegram entirely; the browser
    # control panel below is a complete replacement for it.
    telegram_token: str = ""
    telegram_chat_id: str = ""         # authorised chat that may control the bot

    # --- Browser control panel ---
    web_enabled: bool = True
    web_host: str = "0.0.0.0"          # 0.0.0.0 so a VPS is reachable from your laptop
    web_port: int = 8080
    web_password: str = ""             # SET THIS on a public VPS: it can start trading

    # --- Trading loop ---
    candle_timeframe: int = 60         # seconds per candle used for signals
    poll_interval: float = 1.0         # main loop tick in seconds

    # Which entry model to trade with:
    #   "pullback" -> trend + RSI/Stochastic pull-back (core/strategy.py)
    #   "linreg" / "ema" / "donchian" -> pure trend modes (core/trend_strategy.py)
    #   "custom" -> client's ZigZag + Stochastic + Keltner strategy
    #   "alligator" -> client's Bill Williams Alligator + RSI strategy
    #   "rsi" -> simple fast RSI reversal (RSI 10, for 30s candles / 1-2m expiry)
    #   "confluence" -> trade only when multiple best strategies agree (higher WR)
    strategy_mode: str = "pullback"

    strategy: StrategySettings = field(default_factory=StrategySettings)
    trend: TrendSettings = field(default_factory=TrendSettings)
    custom: CustomSettings = field(default_factory=CustomSettings)
    alligator: AlligatorSettings = field(default_factory=AlligatorSettings)
    rsi: RsiSettings = field(default_factory=RsiSettings)
    confluence: ConfluenceSettings = field(default_factory=ConfluenceSettings)
    martingale: MartingaleSettings = field(default_factory=MartingaleSettings)
    risk: RiskSettings = field(default_factory=RiskSettings)

    # Master run switch, toggled by /start and /stop from Telegram.
    running: bool = False

    @classmethod
    def from_env(cls) -> "BotConfig":
        cfg = cls()
        # PO_SSID is the whole 42["auth",{...}] line. PO_SESSION is the easier
        # route: just the ci_session cookie value, paired with PO_UID.
        cfg.po_ssid = _s("PO_SSID") or _s("PO_SESSION")
        cfg.po_uid = _i("PO_UID", 0)
        cfg.po_demo = _b("PO_DEMO", True)
        cfg.asset = _s("PO_ASSET", cfg.asset)
        cfg.expiry_seconds = _i("PO_EXPIRY_SECONDS", cfg.expiry_seconds)

        cfg.telegram_token = _s("TELEGRAM_TOKEN")
        cfg.telegram_chat_id = _s("TELEGRAM_CHAT_ID")

        cfg.web_enabled = _b("WEB_ENABLED", cfg.web_enabled)
        cfg.web_host = _s("WEB_HOST", cfg.web_host)
        cfg.web_port = _i("WEB_PORT", cfg.web_port)
        cfg.web_password = _s("WEB_PASSWORD")

        cfg.candle_timeframe = _i("CANDLE_TIMEFRAME", cfg.candle_timeframe)
        cfg.poll_interval = _f("POLL_INTERVAL", cfg.poll_interval)
        cfg.strategy_mode = _s("STRATEGY_MODE", cfg.strategy_mode)
        cfg.trend.mode = _s("TREND_MODE", cfg.trend.mode)

        cfg.risk.base_stake = _f("BASE_STAKE", cfg.risk.base_stake)
        cfg.risk.daily_loss_cap = _f("DAILY_LOSS_CAP", cfg.risk.daily_loss_cap)
        cfg.risk.daily_profit_target = _f("DAILY_PROFIT_TARGET", cfg.risk.daily_profit_target)

        cfg.martingale.enabled = _b("MARTINGALE_ENABLED", cfg.martingale.enabled)
        cfg.martingale.multiplier = _f("MARTINGALE_MULTIPLIER", cfg.martingale.multiplier)
        cfg.martingale.max_steps = _i("MARTINGALE_MAX_STEPS", cfg.martingale.max_steps)
        return cfg

    def public_dict(self) -> dict:
        """Config snapshot for /status, with the SSID/token redacted."""
        d = asdict(self)
        if d.get("po_ssid"):
            d["po_ssid"] = "***set***"
        if d.get("telegram_token"):
            d["telegram_token"] = "***set***"
        if d.get("web_password"):
            d["web_password"] = "***set***"
        return d
