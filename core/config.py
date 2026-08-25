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
from typing import Dict, List, Optional

from dotenv import load_dotenv

from .strategy import StrategySettings
from .trend_strategy import TrendSettings
from .custom_strategy import CustomSettings
from .alligator_strategy import AlligatorSettings
from .rsi_strategy import RsiSettings
from .confluence_strategy import ConfluenceSettings
from .sr_strategy import SrSettings
from .momentum_strategy import MomentumSettings
from .momentum_sr_strategy import MomentumSrSettings
from .ai_strategy import AiSettings

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
    # Carry on trading after the profit target instead of stopping, banking each
    # run's profit as it goes. The DAILY LOSS CAP is deliberately not restarted
    # with it — see RiskManager.bank_and_restart for why that asymmetry is the
    # entire point. Off unless asked for.
    auto_restart: bool = False


@dataclass
class BotConfig:
    # --- Pocket Option connection ---
    po_ssid: str = ""                  # browser session token; supplied at connect step
    po_uid: int = 0                    # account id, only needed if po_ssid is a bare session
    po_demo: bool = True               # ALWAYS start on demo
    asset: str = "EURUSD_otc"          # default traded asset
    expiry_seconds: int = 60           # binary expiry (60 = 1m). Client range 3m..1h ok.

    # Extra pairs to watch alongside `asset`. Empty means "just the one".
    #
    # This is the only lever that changes how FAST you find out whether a
    # strategy works, as opposed to what it does. A strategy firing on 7% of
    # candles gives about four trades an hour on one pair; the same strategy
    # across ten pairs gives about forty, so a verdict that would have taken
    # three months arrives in ten days. It does not improve the strategy by one
    # decimal point — it only shortens the wait for the truth.
    assets: List[str] = field(default_factory=list)

    # Payout per watched pair, learnt when pairs are picked from the live table.
    # Kept because the pairs in a watchlist do NOT share a break-even: at 92% you
    # need 52.1% wins, at 52% you need 65.8%. One win-rate figure spanning both
    # is meaningless, so the panel judges the record against the WORST pair being
    # watched rather than quietly averaging them.
    asset_payouts: Dict[str, float] = field(default_factory=dict)

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

    # Payout % your asset pays on a win. It decides the ONLY number that matters:
    # the win rate you must beat to break even, 100 / (100 + payout). At 80% you
    # need 55.6%; at 92% only 52.1%. The panel shows your win rate against this
    # line, because a win rate on its own tells you nothing. Run scan_assets.py
    # to see the live payout of every pair.
    payout_percent: float = 80.0

    # Which entry model to trade with:
    #   "pullback" -> trend + RSI/Stochastic pull-back (core/strategy.py)
    #   "linreg" / "ema" / "donchian" -> pure trend modes (core/trend_strategy.py)
    #   "custom" -> client's ZigZag + Stochastic + Keltner strategy
    #   "alligator" -> client's Bill Williams Alligator + RSI strategy
    #   "rsi" -> simple fast RSI reversal (RSI 10, for 30s candles / 1-2m expiry)
    #   "confluence" -> trade only when multiple best strategies agree (higher WR)
    #   "sr" / "sr_break" / "sr_fade" -> support & resistance (core/sr_strategy.py)
    #   "momentum" / "momentum_follow" -> Momentum(10) at the top/bottom of its
    #       own recent range (core/momentum_strategy.py)
    #   "momentum_sr" / "momentum_sr_any" -> client's own combination: a support
    #       or resistance level, confirmed by momentum and by Stochastic
    #       (core/momentum_sr_strategy.py)
    strategy_mode: str = "pullback"

    strategy: StrategySettings = field(default_factory=StrategySettings)
    trend: TrendSettings = field(default_factory=TrendSettings)
    custom: CustomSettings = field(default_factory=CustomSettings)
    alligator: AlligatorSettings = field(default_factory=AlligatorSettings)
    rsi: RsiSettings = field(default_factory=RsiSettings)
    confluence: ConfluenceSettings = field(default_factory=ConfluenceSettings)
    sr: SrSettings = field(default_factory=SrSettings)
    momentum: MomentumSettings = field(default_factory=MomentumSettings)
    momentum_sr: MomentumSrSettings = field(default_factory=MomentumSrSettings)
    ai: AiSettings = field(default_factory=AiSettings)
    martingale: MartingaleSettings = field(default_factory=MartingaleSettings)
    risk: RiskSettings = field(default_factory=RiskSettings)

    # Master run switch, toggled by /start and /stop from Telegram.
    running: bool = False

    def watched(self) -> List[str]:
        """
        Every pair the bot should be watching, in order, without duplicates.

        `asset` is always first and always present. Single-asset behaviour is
        therefore exactly what it was before the watchlist existed — an empty
        list cannot accidentally mean "watch nothing", which would look
        identical to a dead connection on the panel.
        """
        out = [self.asset] if self.asset else []
        for a in self.assets:
            if a and a not in out:
                out.append(a)
        return out

    def worst_payout(self) -> float:
        """
        The lowest payout among the pairs being watched, defaulting to the
        configured one for any pair whose payout we were never told.

        Deliberately the worst rather than the average. The win rate on screen
        pools trades from every pair, so the only break-even line it can be
        judged against honestly is the hardest one in the set. An average would
        mark a losing session as a winning one whenever the cheap pairs traded
        more than the generous ones.
        """
        rates = [self.asset_payouts.get(a, self.payout_percent)
                 for a in self.watched()]
        return min(rates) if rates else self.payout_percent

    @classmethod
    def from_env(cls) -> "BotConfig":
        cfg = cls()
        # PO_SSID is the whole 42["auth",{...}] line. PO_SESSION is the easier
        # route: just the ci_session cookie value, paired with PO_UID.
        cfg.po_ssid = _s("PO_SSID") or _s("PO_SESSION")
        cfg.po_uid = _i("PO_UID", 0)
        cfg.po_demo = _b("PO_DEMO", True)
        cfg.asset = _s("PO_ASSET", cfg.asset)
        # Comma-separated, e.g. PO_ASSETS=EURUSD_otc,GBPUSD_otc,AUDCAD_otc
        cfg.assets = [a.strip() for a in _s("PO_ASSETS").split(",") if a.strip()]
        cfg.expiry_seconds = _i("PO_EXPIRY_SECONDS", cfg.expiry_seconds)

        cfg.telegram_token = _s("TELEGRAM_TOKEN")
        cfg.telegram_chat_id = _s("TELEGRAM_CHAT_ID")

        cfg.web_enabled = _b("WEB_ENABLED", cfg.web_enabled)
        cfg.web_host = _s("WEB_HOST", cfg.web_host)
        cfg.web_port = _i("WEB_PORT", cfg.web_port)
        cfg.web_password = _s("WEB_PASSWORD")

        cfg.candle_timeframe = _i("CANDLE_TIMEFRAME", cfg.candle_timeframe)
        cfg.poll_interval = _f("POLL_INTERVAL", cfg.poll_interval)
        cfg.payout_percent = _f("PAYOUT_PERCENT", cfg.payout_percent)
        cfg.strategy_mode = _s("STRATEGY_MODE", cfg.strategy_mode)
        # The AI key is a password: it lives in .env on his own machine, never
        # in the repo and never in a log. Read here so the panel can report
        # whether one is set without ever echoing the value back out.
        cfg.ai.api_key = _s("ANTHROPIC_API_KEY")
        cfg.ai.model = _s("AI_MODEL", cfg.ai.model)
        cfg.ai.gate = _s("AI_GATE", cfg.ai.gate)
        cfg.ai.daily_budget_usd = _f("AI_DAILY_BUDGET", cfg.ai.daily_budget_usd)
        cfg.trend.mode = _s("TREND_MODE", cfg.trend.mode)
        cfg.momentum.period = _i("MOMENTUM_PERIOD", cfg.momentum.period)
        cfg.momentum.band_lookback = _i("MOMENTUM_LOOKBACK", cfg.momentum.band_lookback)
        cfg.momentum.band_percentile = _f("MOMENTUM_PERCENTILE", cfg.momentum.band_percentile)

        cfg.risk.base_stake = _f("BASE_STAKE", cfg.risk.base_stake)
        cfg.risk.daily_loss_cap = _f("DAILY_LOSS_CAP", cfg.risk.daily_loss_cap)
        cfg.risk.daily_profit_target = _f("DAILY_PROFIT_TARGET", cfg.risk.daily_profit_target)
        cfg.risk.auto_restart = _b("AUTO_RESTART", cfg.risk.auto_restart)

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
