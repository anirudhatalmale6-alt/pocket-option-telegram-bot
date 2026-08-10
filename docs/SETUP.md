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

### Option A — the WebSocket frame

1. Open **pocketoption.com** in **Google Chrome** and log in.
2. Switch the account selector to **Demo (QT-Demo)**.
3. Press **F12** to open DevTools **first**, then click **Network** → **WS**.
   DevTools only records frames from the moment it is open, so opening it
   afterwards gives you an empty list.
4. **Now** press **Ctrl+R** to refresh. Several `wss://...` entries appear.
5. Click each one → **Messages**. You want the connection that contains a line
   reading `451-["successauth",...]` — that is the **trading** socket. The
   others are chart/price feeds and cannot place orders.
6. In that connection, find the outgoing (green ▲) frame starting with:
   ```
   42["auth",{"session":"a:4:{s:10:\"session_id\";...","isDemo":1,"uid":...,"platform":2}]
   ```
   The `session` value is long and full of backslashes. If what you found is
   short (32 characters) or says `sessionToken` / `isChart`, that is the chart
   socket — keep looking.
7. Right-click → **Copy message**. Paste the entire `42["auth",{...}]` string
   into `.env` as `PO_SSID` and set `PO_DEMO=true`.

### Option B — the cookie (usually easier)

1. In DevTools open the **Application** tab → **Storage** → **Cookies** →
   `https://pocketoption.com`.
2. Find the row named **`ci_session`** and copy its **Value** (it starts with
   `a:4:{`).
3. Put that in `.env` as `PO_SESSION`, and put your numeric account id in
   `PO_UID` (it is the `uid` shown in any auth frame, or on your PO profile).

The bot builds the auth line for you and checks it at start-up. If you paste the
wrong token it tells you exactly which one you grabbed and what to look for
instead, rather than silently failing to connect.

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
