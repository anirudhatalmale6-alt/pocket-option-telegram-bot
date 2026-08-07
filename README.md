# Pocket Option Telegram Trading Bot

Automated binary-options trading on Pocket Option, fully controllable from
Telegram. The strategy blends a **trend filter** (fast/slow EMA) with
**scalping pull-back entries** (RSI + Stochastic exhaustion), with configurable
expiry from 1m up to 1h. Every parameter — stake, expiry, indicators,
martingale, risk caps, start/stop — is adjustable live from chat.

> **Please read first.** Pocket Option has no official API; this bot connects
> over their WebSocket using your browser session token (SSID), which is against
> Pocket Option's Terms of Service and can break if they change their protocol.
> **Always run on the DEMO balance first.** No trading bot can guarantee a high
> win rate — binary options are high-risk. Backtests help you *compare settings*,
> they are **not** a profit promise. Use money you can afford to lose.

---

## Features

- **Direct execution** on Pocket Option (binary CALL/PUT).
- **Multiple strategies, switchable live**: pull-back (EMA trend + RSI/Stochastic dip entry) plus three pure-trend modes — `linreg` (trend-line slope), `ema` (EMA trend), `donchian` (breakout). All thresholds tunable.
- **Telegram control**: `/start` `/stop` `/status`, quick setters (`/stake`, `/expiry`, `/asset`), martingale & risk commands, a generic `/set field value`, plus inline Start/Stop/Status buttons.
- **Instant notifications** on every entry, result, error and reconnect.
- **Money management**: base stake, optional martingale (multiplier + max steps), daily loss cap and daily profit target.
- **24/7 operation** with auto-reconnect and exponential backoff.
- **Backtester + offline paper mode** so you can test the whole pipeline with no account and no credentials.
- **Unit-tested** indicators, strategy and risk logic.

## Project layout

```
pocket_bot/
├── main.py            # entrypoint (Telegram + trader together)
├── demo_run.py        # offline console demo of the live loop (no account)
├── backtest.py        # backtest on synthetic data or your own CSV
├── core/
│   ├── config.py      # all settings (env + live-editable)
│   ├── indicators.py  # EMA / RSI / Stochastic (pure, tested)
│   ├── strategy.py    # trend + pullback signal logic
│   ├── risk.py        # stake sizing, martingale, daily caps
│   ├── broker.py      # broker interface + offline PaperBroker
│   ├── po_broker.py   # real Pocket Option WebSocket broker
│   ├── trader.py      # main async orchestration loop
│   └── telegram_bot.py# Telegram command interface
├── tests/             # pytest unit tests
├── requirements.txt
└── .env.example
```

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # then edit .env

# 1) See it work with zero setup (offline simulator):
python demo_run.py

# 2) Compare strategy settings on data:
python backtest.py

# 3) Live/demo trading (after filling .env — see docs/SETUP.md):
python main.py
```

Full setup — creating the Telegram bot, getting your Pocket Option SSID, and
running 24/7 on a VPS — is in **[docs/SETUP.md](docs/SETUP.md)**.

## Telegram commands

| Command | Action |
|---|---|
| `/start` `/stop` | start / pause trading |
| `/status` | show all settings + today's PnL |
| `/stake <amount>` | set base stake |
| `/expiry <seconds>` | set expiry (e.g. `180` = 3m) |
| `/asset <symbol>` | set asset (e.g. `EURUSD_otc`) |
| `/strategy pullback\|linreg\|ema\|donchian` | switch entry model (pull-back or a pure-trend mode) |
| `/martingale on\|off [mult] [maxsteps]` | martingale controls |
| `/risk <loss_cap> [profit_target]` | daily caps |
| `/set <field.path> <value>` | change any setting, e.g. `/set strategy.rsi_oversold 25` |
| `/reset` | reset today's PnL + martingale |
| `/help` | list commands |

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```
