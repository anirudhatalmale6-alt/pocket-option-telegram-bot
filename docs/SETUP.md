# Setup Guide (Windows & Linux)

This walks you from zero to a running bot on the **demo** account, then how to
keep it running 24/7 on a VPS.

---

## 1. Install

You need Python 3.10+ .

**Windows**
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

**Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copy the config template and open it in a text editor:
```bash
cp .env.example .env
```

---

## 2. Choose how you want to control the bot

You have two options. **You do not need both.**

### Option A — Browser control panel (recommended, nothing to install)

Already on by default. When the bot starts it prints a line like:

```
Control panel: http://localhost:8080
```

Open that address in any browser — laptop, Chromebook, phone — and you get:

* big **START** / **STOP** buttons,
* today's profit/loss, win rate and balance,
* stake, expiry, candle size, asset, strategy and risk caps (saved instantly,
  applied on the next candle — no restart),
* a live list of every trade with win/loss and profit,
* an activity feed of what the bot is doing.

If the bot runs on a VPS, use the server's address instead of `localhost`
(e.g. `http://203.0.113.10:8080`) and **set `WEB_PASSWORD` in `.env` first** —
without it, anyone who can reach that port can start and stop your trading.

### Option B — Telegram

Optional. Leave `TELEGRAM_TOKEN` blank in `.env` and Telegram never starts.

1. In Telegram, open **@BotFather** → send `/newbot` → follow the prompts.
2. It gives you a **token** like `123456789:AAErr...`. Put it in `.env` as
   `TELEGRAM_TOKEN`.
3. Get **your chat id**: message **@userinfobot**, it replies with your numeric
   id. Put it in `.env` as `TELEGRAM_CHAT_ID`. (This locks the bot so only you
   can control it.)
4. Open a chat with your new bot and press **Start** once.

---

## 3. Get your Pocket Option SSID (DEMO)

The bot logs in the same way your browser does, using the session token from a
logged-in session. **Use your DEMO account for this.**

1. Open **pocketoption.com** in **Google Chrome** and log in.
2. Switch the account selector to **Demo (QT-Demo)**.
3. Press **F12** → **Network** tab → in the filter box type `socket`.
4. Refresh the page. Click the `wss://...` entry that appears → **Messages**.
5. Look at the outgoing (green ▲) frames for one starting with:
   ```
   42["auth",{"session":"...","isDemo":1,"uid":...,"platform":...}]
   ```
6. Right-click → **Copy message**. That entire `42["auth",{...}]` string is your
   SSID. Paste it into `.env` as `PO_SSID` (keep it in quotes if your editor
   needs it) and set `PO_DEMO=true`.

> The token expires when your browser session ends. If the bot says "auth
> failed", just grab a fresh one the same way.

---

## 4. First run

Before touching credentials you can watch the whole engine offline:
```bash
python demo_run.py          # prints simulated entries/results to the console
python backtest.py          # strategy stats on synthetic data
```

You can also open the control panel with no account at all — it runs the offline
simulator so you can click around safely:
```bash
python main.py --paper       # then open http://localhost:8080
```

Then start the real bot (demo balance):
```bash
python main.py
```

**Using the panel:** open the printed URL, check the badge says `DEMO`, set your
stake and expiry, pick a strategy, press **START**. Trades appear in the list as
they settle.

**Using Telegram instead:** send `/help`, then `/status`, then `/start`. You get
a message on every trade entry and result. Tune anything live, e.g.:
```
/stake 2
/expiry 180
/strategy confluence
/martingale on 2 2
/risk 20 15
/set strategy.rsi_oversold 25
```

---

## 5. Run 24/7 on a Linux VPS

Use `systemd` so it restarts on crash/reboot. Create
`/etc/systemd/system/pobot.service`:

```ini
[Unit]
Description=Pocket Option Telegram Bot
After=network-online.target

[Service]
WorkingDirectory=/opt/pocket_bot
ExecStart=/opt/pocket_bot/.venv/bin/python /opt/pocket_bot/main.py
Restart=always
RestartSec=5
EnvironmentFile=/opt/pocket_bot/.env

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pobot
sudo journalctl -u pobot -f      # live logs
```

(Windows alternative: run `python main.py` inside a scheduled task, or use NSSM
to install it as a service.)

---

## 6. Going live (later, at your own risk)

Only after you're happy with demo results: log into your **real** account,
repeat step 3 to get a live SSID (it will show `isDemo:0`), set `PO_DEMO=false`,
and **start with the smallest stake**. Keep the daily loss cap on.

Remember: no settings guarantee profit. Binary options are high-risk; trade only
what you can afford to lose.
