# Pocket Option Trading Bot

Automated binary-options trading on Pocket Option, controllable from a **browser
control panel** or from **Telegram** — your choice, neither is required by the
other. Eight switchable strategies, configurable expiry from 30s up to 1h, and
every parameter — stake, expiry, indicators, martingale, risk caps, start/stop —
adjustable live without a restart.

![Control panel](docs/panel_top.png)

> **Please read first.** Pocket Option has no official API; this bot connects
> over their WebSocket using your browser session token (SSID), which is against
> Pocket Option's Terms of Service and can break if they change their protocol.
> **Always run on the DEMO balance first.** No trading bot can guarantee a high
> win rate — binary options are high-risk. Backtests help you *compare settings*,
> they are **not** a profit promise. Use money you can afford to lose.

---

## Features

- **Direct execution** on Pocket Option (binary CALL/PUT).
- **Browser control panel** (no install, no accounts): START/STOP, live PnL,
  win rate, balance, all settings, and a live trade list. Works on a laptop,
  Chromebook or phone. Optional password.
- **Eight strategies, switchable live**: `confluence` (trade only when several
  setups agree), `custom` (ZigZag + Stochastic + Keltner), `alligator`
  (Bill Williams + RSI), `rsi` (fast RSI reversal, built for 30s candles),
  `pullback` (EMA trend + RSI/Stochastic dip), plus three pure-trend modes —
  `linreg`, `ema`, `donchian`. All thresholds tunable.
- **Telegram control (optional)**: `/start` `/stop` `/status`, quick setters (`/stake`, `/expiry`, `/asset`), martingale & risk commands, a generic `/set field value`, plus inline Start/Stop/Status buttons.
- **Instant notifications** on every entry, result, error and reconnect.
- **Money management**: base stake, optional martingale (multiplier + max steps), daily loss cap and daily profit target.
- **24/7 operation** with auto-reconnect and exponential backoff.
- **Backtester + offline paper mode** so you can test the whole pipeline with no account and no credentials.
- **Unit-tested** indicators, strategy and risk logic.

## Project layout

```
pocket_bot/
├── main.py                    # entrypoint (trader + control interfaces)
├── demo_run.py                # offline console demo of the live loop (no account)
├── backtest.py                # backtest on synthetic data or your own CSV
├── *_backtest.py              # per-strategy backtests on real EUR/USD data
├── core/
│   ├── config.py              # all settings (env + live-editable)
│   ├── indicators.py          # EMA / RSI / Stochastic (pure, tested)
│   ├── strategy.py            # trend + pullback signal logic
│   ├── trend_strategy.py      # linreg / ema / donchian trend modes
│   ├── custom_strategy.py     # ZigZag + Stochastic + Keltner
│   ├── alligator_strategy.py  # Bill Williams Alligator + RSI
│   ├── rsi_strategy.py        # fast RSI reversal (30s candles)
│   ├── confluence_strategy.py # trade only when strategies agree
│   ├── risk.py                # stake sizing, martingale, daily caps
│   ├── broker.py              # broker interface + offline PaperBroker
│   ├── po_broker.py           # real Pocket Option WebSocket broker
│   ├── trader.py              # main async orchestration loop
│   ├── web_ui.py              # browser control panel (no dependencies)
│   └── telegram_bot.py        # Telegram command interface (optional)
├── tests/                     # pytest unit tests
├── requirements.txt
└── .env.example
```

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # then edit .env

# 1) See it work with zero setup — opens the control panel on the simulator:
python main.py --paper        # then open http://localhost:8080

# 2) Compare strategy settings on data:
python backtest.py

# 3) Live/demo trading (after filling .env — see docs/SETUP.md):
python main.py
```

Full setup — the control panel, the optional Telegram bot, getting your Pocket
Option SSID, and running 24/7 on a VPS — is in **[docs/SETUP.md](docs/SETUP.md)**.

## Control panel

| | |
|---|---|
| ![Trades](docs/panel_trades.png) | ![Mobile](docs/panel_mobile.png) |

Every trade is listed with direction, stake, result and profit, and the activity
feed shows exactly why each entry was taken. Settings apply on the next candle —
no restart. Set `WEB_PASSWORD` in `.env` before exposing the panel on a VPS.

## Telegram commands (optional)

| Command | Action |
|---|---|
| `/start` `/stop` | start / pause trading |
| `/status` | show all settings + today's PnL |
| `/stake <amount>` | set base stake |
| `/expiry <seconds>` | set expiry (e.g. `180` = 3m) |
| `/asset <symbol>` | set asset (e.g. `EURUSD_otc`) |
| `/strategy pullback\|linreg\|ema\|donchian\|custom\|alligator\|rsi\|confluence` | switch entry model |
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
