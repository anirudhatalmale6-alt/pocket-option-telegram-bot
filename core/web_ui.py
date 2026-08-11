"""
Browser control panel — the click-and-go alternative to the Telegram interface.

Why this exists: the client runs a Chromebook, where installing apps is awkward
and Telegram is one more account to juggle. A web page needs nothing installed —
open a link, press START.

Design notes:
  * Zero extra dependencies. Uses the standard library's ThreadingHTTPServer in
    a daemon thread, so it does not fight the trader's asyncio loop.
  * The page is a single self-contained HTML string (inline CSS + JS, no CDN),
    so it works on a locked-down machine and offline.
  * State is read straight off the shared BotConfig / RiskManager objects — the
    exact same objects the trader reads every cycle — so a change made in the
    browser takes effect on the next candle, no restart.
  * Optional password (WEB_PASSWORD). Set it whenever the panel is reachable
    from the internet; this endpoint can start and stop real trading.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Optional

from .config import BotConfig

# Strategy modes offered in the dropdown, with a plain-English label so a
# non-technical user can pick one without reading the source.
STRATEGY_CHOICES = [
    ("confluence", "Confluence — only trade when setups agree (recommended)"),
    ("custom", "Custom — your ZigZag + Stochastic + Keltner"),
    ("alligator", "Alligator — your Bill Williams + RSI"),
    ("rsi", "RSI — fast RSI reversal (for 30s candles)"),
    ("pullback", "Pullback — trend + RSI/Stochastic dip"),
    ("linreg", "Trend line — linear-regression slope"),
    ("ema", "Trend — EMA cross"),
    ("donchian", "Breakout — Donchian channel"),
]


class WebInterface:
    """Serves the control panel and exposes a notifier for the trader."""

    def __init__(self, config: BotConfig, host: str = "0.0.0.0",
                 port: int = 8080, password: str = "") -> None:
        self.config = config
        self.host = host
        self.port = port
        self.password = password
        self.risk = None            # set by main() once the Trader exists
        self.trader = None          # ditto — read for the "still watching" line
        self.reset_cb = None
        self.reload_cb = None       # set by main(): reconnect with new details
        self.balance: Optional[float] = None
        self.connected = False
        self.paper = False          # True when running without a real account
        self.practice_note = ""     # what KIND of practice data, set by main.py
        self._log: List[dict] = []  # newest-last ring buffer of event lines
        self._assets_cache: List[dict] = []   # live payout table
        self._assets_at = 0.0                 # when we last fetched it
        self._plugins_cache: List[tuple] = [] # strategy files found in strategies/
        self._plugins_at = 0.0
        self._lock = threading.Lock()
        self._server: Optional[ThreadingHTTPServer] = None

    # ------------------------------------------------------------ notifier
    async def send(self, text: str) -> None:
        """Trader notifier. Same signature as the Telegram one, so it drops in."""
        self.log(text)

    def log(self, text: str) -> None:
        line = {"ts": time.time(), "text": text}
        with self._lock:
            self._log.append(line)
            if len(self._log) > 300:      # keep memory flat on a 24/7 box
                del self._log[:-300]
        # Mirror to stdout so the VPS log tells the same story as the page.
        print(f"[bot] {text}", flush=True)

    # --------------------------------------------------------------- state
    def _plugin_choices(self) -> List[tuple]:
        """
        Strategy files from strategies/, cached for a few seconds.

        Discovery imports every file, so doing it on each 2-second poll would be
        wasteful; caching it means a newly added file shows up within seconds
        without a restart, which is the point of the folder.
        """
        now = time.time()
        if now - self._plugins_at < 5.0:
            return self._plugins_cache
        try:
            from . import plugins
            self._plugins_cache = plugins.choices()
        except Exception as exc:
            self._plugins_cache = []
            print(f"[bot] could not scan strategies/: {exc}", flush=True)
        self._plugins_at = now
        return self._plugins_cache

    def _is_live(self) -> bool:
        """Real money: a genuine account (not practice) that is not on demo."""
        return not self.paper and not self.config.po_demo

    def state(self) -> dict:
        c = self.config
        r = self.risk
        trades = []
        if r is not None:
            for t in r.history[-40:][::-1]:   # newest first, capped for the page
                trades.append({
                    "ts": t.ts,
                    "direction": t.direction,
                    "stake": t.stake,
                    "result": t.result,
                    "profit": t.profit,
                    "step": t.martingale_step,
                })
        with self._lock:
            log = [dict(x) for x in self._log[-60:][::-1]]
        return {
            "running": c.running,
            "connected": self.connected,
            "mode": "PRACTICE" if self.paper else ("DEMO" if c.po_demo else "LIVE"),
            "is_live": self._is_live(),
            "has_token": bool(c.po_ssid) or self.paper,
            # Whether a token is set, never the token. This payload is served
            # over plain HTTP and echoed into the browser; the session string is
            # a password and has no business travelling back out again.
            "session_set": bool(c.po_ssid),
            "uid": c.po_uid,
            "demo": c.po_demo,
            "practice_note": self.practice_note,
            # Proof of life. A selective strategy sitting out looks exactly like
            # a crashed bot, so show what it checked, when, and why it passed.
            "checks": getattr(self.trader, "checks", 0),
            "last_check": getattr(self.trader, "last_check_ts", 0.0),
            "last_reason": getattr(self.trader, "last_reason", ""),
            "asset": c.asset,
            "expiry": c.expiry_seconds,
            "timeframe": c.candle_timeframe,
            "strategy": c.strategy_mode,
            # Built-ins first, then anything dropped into strategies/. Rescanned
            # on every poll so a new file appears without restarting the panel.
            "strategies": [{"id": k, "label": v}
                           for k, v in STRATEGY_CHOICES + self._plugin_choices()],
            "stake": c.risk.base_stake,
            "loss_cap": c.risk.daily_loss_cap,
            "profit_target": c.risk.daily_profit_target,
            "mg_enabled": c.martingale.enabled,
            "mg_mult": c.martingale.multiplier,
            "mg_steps": c.martingale.max_steps,
            "balance": self.balance,
            "pnl": r.daily_pnl if r else 0.0,
            "wins": r.wins if r else 0,
            "losses": r.losses if r else 0,
            "winrate": r.win_rate() if r else 0.0,
            # The number the win rate has to beat. Without it on screen, 48%
            # reads as "nearly there" when at an 80% payout it is a steady loss.
            "breakeven": 100.0 * 100.0 / (100.0 + c.payout_percent) if c.payout_percent else 100.0,
            "payout": c.payout_percent,
            "trades": trades,
            "log": log,
        }

    # -------------------------------------------------------------- payouts
    def payouts(self) -> dict:
        """
        Live payout table, so the pair can be chosen from the panel.

        This matters more than any strategy setting: the payout sets the win
        rate you have to beat (100 / (100 + payout)), and it MOVES — EURUSD_otc
        paid 68% one day and 92% the next. A number I quote in a message goes
        stale; a button does not. Needs no account: the asset table is public.

        Cached briefly because Pocket Option starts refusing connections if you
        reconnect repeatedly.
        """
        now = time.time()
        with self._lock:
            if self._assets_cache and now - self._assets_at < 120:
                return {"ok": True, "cached": True, "assets": self._assets_cache}

        try:
            import asyncio

            from .assets import fetch_assets
            assets = asyncio.run(fetch_assets())
        except Exception as exc:
            return {"ok": False, "message": f"Could not reach Pocket Option: {exc}",
                    "assets": []}

        rows = [{
            "symbol": a.symbol,
            "name": a.name,
            "payout": a.payout,
            "breakeven": round(a.breakeven_win_rate, 1),
            "min_expiry": a.min_expiry,
        } for a in assets if a.is_open and a.payout > 0]
        rows.sort(key=lambda r: -r["payout"])

        with self._lock:
            self._assets_cache = rows
            self._assets_at = now
        return {"ok": True, "cached": False, "assets": rows}

    # -------------------------------------------------------------- connect
    def _connect(self, body: dict) -> dict:
        """
        Save the Pocket Option session from the page and switch to that account.

        Everything about this is shaped by one fact: the person using it is on a
        Chromebook, has no text editor, and has already lost a day to terminals
        and hidden files. So the panel writes .env itself, validates the paste
        before saving rather than after failing to connect, and reconnects
        without a restart.
        """
        c = self.config
        session = str(body.get("session", "")).strip()
        raw_uid = str(body.get("uid", "")).strip()
        demo = bool(body.get("demo", True))

        if not session:
            return {"ok": False, "message": "Paste the ci_session cookie first."}

        try:
            uid = int(raw_uid or c.po_uid or 0)
        except ValueError:
            return {"ok": False, "message": "The account id should be digits only."}

        # Validate BEFORE writing anything. ssid.normalise knows the difference
        # between the trading token and the chart token that gets copied by
        # mistake, and explains which is which — far better than saving a broken
        # value and surfacing it later as a silent failure to connect.
        from .ssid import SsidError, normalise
        try:
            normalise(session, uid=uid, demo=demo)
        except SsidError as exc:
            return {"ok": False, "message": str(exc)}

        try:
            from .envfile import update
            # PO_SSID is blanked deliberately. config.from_env reads
            #   po_ssid = PO_SSID or PO_SESSION
            # so a leftover PO_SSID from an earlier attempt would silently win
            # over what was just typed here, and the panel would look like it
            # had saved something it had not.
            update({"PO_SESSION": session, "PO_UID": str(uid),
                    "PO_DEMO": "true" if demo else "false",
                    "PO_SSID": ""})
        except OSError as exc:
            # Say it did not save. Claiming success here would be the worst
            # possible outcome: he restarts and it is still not connected.
            return {"ok": False,
                    "message": f"Could not write the .env file: {exc}"}

        c.po_ssid = session
        c.po_uid = uid
        c.po_demo = demo
        self.paper = False
        self.practice_note = ""
        # Never log the token itself — this feed is on screen and in the logs.
        self.log(f"Account details saved (uid {uid}, "
                 f"{'DEMO' if demo else 'LIVE'}). Reconnecting…")

        if self.reload_cb is None:
            return {"ok": True, "message": "Saved to .env. Restart the bot to connect: "
                                           "press Ctrl+C, then run  bash run.sh"}
        try:
            self.reload_cb()
        except Exception as exc:
            return {"ok": True,
                    "message": f"Saved to .env, but could not reconnect automatically "
                               f"({exc}). Restart with: bash run.sh"}
        return {"ok": True, "message": "Saved. Connecting to your Pocket Option "
                                       "account now — watch the badge at the top."}

    # ------------------------------------------------------------ commands
    def command(self, body: dict) -> dict:
        """Apply one action from the page. Returns {ok, message}."""
        action = str(body.get("action", ""))
        c = self.config

        if action == "start":
            if not c.po_ssid and not self.paper:
                return {"ok": False, "message": "No Pocket Option token set — cannot trade."}
            # Real money needs one deliberate extra press. Not paperwork: no
            # strategy in this bot has yet beaten its break-even line on real
            # data (docs/RESULTS.md, 0 of 28 combinations), so pressing START on
            # a funded account is a decision to fund an experiment. The panel
            # sends confirm_live only after showing that in plain words.
            if self._is_live() and not body.get("confirm_live"):
                need = 100.0 * 100.0 / (100.0 + c.payout_percent) if c.payout_percent else 100.0
                return {"ok": False, "needs_live_confirm": True,
                        "message": (
                            f"This is your REAL money account.\n\n"
                            f"At a {c.payout_percent:.0f}% payout you must win "
                            f"{need:.1f}% of trades just to break even. No strategy "
                            f"in this bot has yet proven it can do that — 0 of 28 "
                            f"tested combinations showed a real edge (docs/RESULTS.md).\n\n"
                            f"Stake ${c.risk.base_stake:.2f}, stops after losing "
                            f"${c.risk.daily_loss_cap:.2f} today"
                            f"{', MARTINGALE IS ON' if c.martingale.enabled else ''}.\n\n"
                            f"Start trading real money anyway?")}
            if self._is_live():
                self.log("⚠ START on a LIVE account — real money is now at risk.")
            c.running = True
            self.log("▶ START pressed from the control panel.")
            # Say what it is doing and roughly how long a quiet spell is normal.
            # Without this, a selective strategy looks identical to a dead bot.
            self.log(f"Watching {c.asset} on {c.candle_timeframe}s candles with the "
                     f"'{c.strategy_mode}' strategy. Trades appear below when a "
                     f"setup matches — a quiet spell is normal, not a fault.")
            return {"ok": True, "message": "Trading started."}

        if action == "stop":
            c.running = False
            self.log("⏸ STOP pressed from the control panel.")
            return {"ok": True, "message": "Trading stopped."}

        if action == "connect":
            return self._connect(body)

        if action == "reset":
            if self.reset_cb:
                self.reset_cb()
            self.log("Daily PnL and martingale reset.")
            return {"ok": True, "message": "Daily figures reset."}

        if action == "settings":
            return self._apply_settings(body)

        return {"ok": False, "message": f"Unknown action '{action}'."}

    def _apply_settings(self, body: dict) -> dict:
        """Validate and apply the settings form. Rejects nonsense values."""
        c = self.config
        changed = []
        try:
            if "stake" in body:
                v = float(body["stake"])
                if v <= 0:
                    return {"ok": False, "message": "Stake must be greater than 0."}
                c.risk.base_stake = v
                changed.append(f"stake {v}")

            if "expiry" in body:
                v = int(body["expiry"])
                # Not my rule — Pocket Option's. I scanned all 183 of their
                # assets and every one has a 60s minimum expiry, so anything
                # shorter would simply be rejected by their server at entry.
                if v < 60:
                    return {"ok": False, "message":
                            "Pocket Option's shortest expiry is 60 seconds — every one "
                            "of their 183 assets, checked live. A shorter expiry would "
                            "be refused when the trade is placed. Candle size can be "
                            "shorter than the expiry; that is the setting you want."}
                c.expiry_seconds = v
                changed.append(f"expiry {v}s")

            if "timeframe" in body:
                v = int(body["timeframe"])
                # Candle size is yours to choose — 30s candles with a 60s expiry
                # is a perfectly normal pairing. The floor is just a sanity guard
                # against a typo like 0 or 1, which would poll a live feed flat out.
                if v < 5:
                    return {"ok": False, "message":
                            "Candle size must be at least 5 seconds — below that the bot "
                            "would hammer the feed and get the connection dropped."}
                c.candle_timeframe = v
                changed.append(f"candle {v}s")

            if "asset" in body:
                asset = str(body["asset"]).strip()
                if not asset:
                    return {"ok": False, "message": "Asset cannot be empty."}
                c.asset = asset
                changed.append(f"asset {asset}")

            if "payout" in body:
                # Sent when a pair is picked from the live payout list, so the
                # break-even line on screen matches the pair actually traded.
                v = float(body["payout"])
                if not 1 <= v <= 100:
                    return {"ok": False, "message": "Payout must be between 1 and 100."}
                c.payout_percent = v
                changed.append(f"payout {v:.0f}% (break-even "
                               f"{100.0 * 100.0 / (100.0 + v):.1f}%)")

            if "strategy" in body:
                mode = str(body["strategy"])
                allowed = [k for k, _ in STRATEGY_CHOICES] + \
                          [k for k, _ in self._plugin_choices()]
                if mode not in allowed:
                    return {"ok": False, "message": f"Unknown strategy '{mode}'."}
                c.strategy_mode = mode
                changed.append(f"strategy {mode}")

            if "loss_cap" in body:
                v = float(body["loss_cap"])
                if v < 0:
                    return {"ok": False, "message": "Daily loss cap cannot be negative."}
                c.risk.daily_loss_cap = v
                changed.append(f"loss cap {v}")

            if "profit_target" in body:
                v = float(body["profit_target"])
                if v < 0:
                    return {"ok": False, "message": "Profit target cannot be negative."}
                c.risk.daily_profit_target = v
                changed.append(f"profit target {v}")

            if "mg_enabled" in body:
                c.martingale.enabled = bool(body["mg_enabled"])
                changed.append(f"martingale {'on' if c.martingale.enabled else 'off'}")

            if "mg_mult" in body:
                v = float(body["mg_mult"])
                if v < 1:
                    return {"ok": False, "message": "Martingale multiplier must be at least 1."}
                c.martingale.multiplier = v
                changed.append(f"mg x{v}")

            if "mg_steps" in body:
                v = int(body["mg_steps"])
                if v < 0:
                    return {"ok": False, "message": "Martingale steps cannot be negative."}
                c.martingale.max_steps = v
                changed.append(f"mg steps {v}")
        except (TypeError, ValueError):
            return {"ok": False, "message": "One of those values is not a valid number."}

        if changed:
            self.log("Settings updated: " + ", ".join(changed))
        return {"ok": True, "message": "Settings saved. They apply on the next candle."}

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        """Launch the HTTP server on a daemon thread."""
        iface = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):     # silence per-request console spam
                return

            # -- helpers
            def _send(self, code: int, payload: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)

            def _json(self, code: int, obj: dict) -> None:
                self._send(code, json.dumps(obj).encode(), "application/json")

            def _authed(self) -> bool:
                if not iface.password:
                    return True
                return self.headers.get("X-Auth", "") == iface.password

            # -- routes
            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/":
                    self._send(200, PAGE.encode(), "text/html; charset=utf-8")
                elif path == "/api/state":
                    if not self._authed():
                        self._json(401, {"error": "unauthorised"})
                        return
                    self._json(200, iface.state())
                elif path == "/api/assets":
                    if not self._authed():
                        self._json(401, {"error": "unauthorised"})
                        return
                    self._json(200, iface.payouts())
                else:
                    self._send(404, b"Not found", "text/plain")

            def do_POST(self):
                if self.path.split("?")[0] != "/api/cmd":
                    self._send(404, b"Not found", "text/plain")
                    return
                if not self._authed():
                    self._json(401, {"ok": False, "message": "Wrong password."})
                    return
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(n) or b"{}")
                except (ValueError, json.JSONDecodeError):
                    self._json(400, {"ok": False, "message": "Bad request."})
                    return
                self._json(200, iface.command(body))

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.log(f"Control panel running on http://{self.host}:{self.port}")

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


# --------------------------------------------------------------------------
# The page. One self-contained file: no CDN, no build step, works offline.
# --------------------------------------------------------------------------
PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pocket Option Bot — Control Panel</title>
<style>
  :root{
    --bg:#0e1117; --panel:#161b24; --panel2:#1d2430; --line:#2a3342;
    --text:#e6edf3; --muted:#8b97a8; --green:#22c55e; --red:#ef4444;
    --blue:#3b82f6; --amber:#f59e0b;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
       font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
  .wrap{max-width:1050px;margin:0 auto;padding:18px}
  header{display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin-bottom:18px}
  h1{font-size:19px;margin:0;font-weight:650}
  .pill{padding:3px 10px;border-radius:999px;font-size:12px;font-weight:600;
        border:1px solid var(--line);background:var(--panel2);color:var(--muted)}
  .pill.on{background:rgba(34,197,94,.15);color:var(--green);border-color:rgba(34,197,94,.4)}
  .pill.off{background:rgba(239,68,68,.12);color:var(--red);border-color:rgba(239,68,68,.35)}
  .pill.demo{background:rgba(59,130,246,.15);color:var(--blue);border-color:rgba(59,130,246,.4)}
  .pill.live{background:rgba(245,158,11,.15);color:var(--amber);border-color:rgba(245,158,11,.4)}
  #livebar{background:rgba(239,68,68,.12);border:1px solid rgba(239,68,68,.5);
           border-radius:10px;padding:12px 14px;margin:0 0 12px;
           font-size:14px;line-height:1.5;color:#fecaca}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
        padding:16px;margin-bottom:16px}
  .card h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;
           color:var(--muted);margin:0 0 12px;font-weight:600}
  .btns{display:flex;gap:12px;flex-wrap:wrap}
  button{font:inherit;font-weight:600;border:0;border-radius:10px;padding:14px 26px;
         cursor:pointer;color:#fff;transition:filter .15s}
  button:hover{filter:brightness(1.12)}
  button:disabled{opacity:.45;cursor:not-allowed;filter:none}
  .go{background:var(--green)} .halt{background:var(--red)}
  .ghost{background:var(--panel2);color:var(--text);border:1px solid var(--line)}
  .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}
  .stat{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
  .stat .k{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
  .stat .v{font-size:22px;font-weight:680;margin-top:3px;font-variant-numeric:tabular-nums}
  .sub{font-size:11px;color:var(--muted);margin-top:2px;line-height:1.35}
  .pos{color:var(--green)} .neg{color:var(--red)}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px}
  label{display:block;font-size:12px;color:var(--muted);margin-bottom:5px}
  input,select{width:100%;background:var(--panel2);border:1px solid var(--line);
               color:var(--text);border-radius:8px;padding:10px 11px;font:inherit}
  input:focus,select:focus{outline:2px solid rgba(59,130,246,.5);outline-offset:-1px}
  .row{display:flex;align-items:center;gap:9px;margin-top:6px}
  .row input[type=checkbox]{width:17px;height:17px;accent-color:var(--blue)}
  .row label{margin:0}
  table{width:100%;border-collapse:collapse;font-size:14px}
  th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em;
     color:var(--muted);font-weight:600;padding:6px 8px;border-bottom:1px solid var(--line)}
  td{padding:8px;border-bottom:1px solid rgba(42,51,66,.55);font-variant-numeric:tabular-nums}
  .tag{padding:2px 8px;border-radius:6px;font-size:12px;font-weight:600}
  .call{background:rgba(34,197,94,.15);color:var(--green)}
  .put{background:rgba(239,68,68,.13);color:var(--red)}
  .scroll{max-height:300px;overflow-y:auto}
  .log{font:12.5px/1.65 ui-monospace,SFMono-Regular,Menlo,monospace;
       max-height:250px;overflow-y:auto;white-space:pre-wrap}
  .log div{padding:3px 0;border-bottom:1px solid rgba(42,51,66,.4)}
  .log .t{color:var(--muted);margin-right:8px}
  .empty{color:var(--muted);font-size:13.5px;padding:14px 2px}
  .toast{position:fixed;left:50%;transform:translateX(-50%);bottom:22px;
         background:var(--panel2);border:1px solid var(--line);border-radius:10px;
         padding:12px 18px;font-size:14px;box-shadow:0 8px 26px rgba(0,0,0,.45);
         opacity:0;pointer-events:none;transition:opacity .2s}
  .toast.show{opacity:1}
  .toast.bad{border-color:rgba(239,68,68,.55)}
  .note{color:var(--muted);font-size:12.5px;margin-top:10px}
  @media(max-width:560px){ button{flex:1 1 100%} .wrap{padding:12px} }
</style>
</head>
<body>
<div class="wrap">

  <header>
    <h1>Pocket Option Bot</h1>
    <span class="pill" id="p-run">—</span>
    <span class="pill" id="p-mode">—</span>
    <span class="pill" id="p-conn">—</span>
  </header>

  <div class="card">
    <h2>Controls</h2>
    <div class="btns">
      <button class="go"   id="b-start" onclick="cmd({action:'start'})">▶ START TRADING</button>
      <button class="halt" id="b-stop"  onclick="cmd({action:'stop'})">■ STOP</button>
      <button class="ghost" onclick="if(confirm('Reset today\'s profit/loss counters?'))cmd({action:'reset'})">Reset today</button>
    </div>
    <!-- Real money deserves a standing reminder, not a one-off dialog. -->
    <div id="livebar" style="display:none"></div>
    <div class="note" id="hint"></div>
    <!-- Live proof the bot is awake while a picky strategy sits out. -->
    <div class="note" id="watch" style="display:none"></div>
  </div>

  <!-- Account connection. Lives on the page because the alternative is asking
       a non-technical user to paste a 400-character secret into a hidden
       dotfile from a terminal, which is not a real option. -->
  <div class="card">
    <h2>Your Pocket Option account</h2>
    <div class="note" id="conn-state" style="margin-bottom:14px"></div>
    <div class="grid">
      <div style="grid-column:1/-1">
        <label for="f-session">Session cookie (ci_session)</label>
        <input id="f-session" type="password" autocomplete="off" spellcheck="false"
               placeholder="starts with a%3A4%3A%7B and is very long">
        <div class="sub">Chrome on this machine &rarr; open Pocket Option &rarr; Ctrl+Shift+I &rarr;
          Application &rarr; Cookies &rarr; pocketoption.com &rarr; copy the value of
          <b>ci_session</b>. It is a password: it is stored on this computer only
          and never shown again.</div>
      </div>
      <div><label for="f-uid">Account id (uid)</label><input id="f-uid" type="text" inputmode="numeric"></div>
      <div>
        <label>Which balance</label>
        <div class="row"><input type="checkbox" id="f-demo" checked><label for="f-demo">Demo — practice money (recommended)</label></div>
      </div>
    </div>
    <div class="btns" style="margin-top:14px">
      <button class="ghost" onclick="saveAccount()">Save &amp; connect</button>
    </div>
  </div>

  <div class="card">
    <h2>Today</h2>
    <div class="stats">
      <div class="stat"><div class="k">Profit / Loss</div><div class="v" id="s-pnl">—</div></div>
      <div class="stat"><div class="k">Win rate</div><div class="v" id="s-wr">—</div><div class="sub" id="s-be"></div></div>
      <div class="stat"><div class="k">Wins / Losses</div><div class="v" id="s-wl">—</div></div>
      <!-- The balance says whose money it is, on the tile. A PRACTICE badge in
           the header was not enough: the client watched a simulated $997.20 for
           an hour believing it was his real Pocket Option demo balance. -->
      <div class="stat"><div class="k">Balance</div><div class="v" id="s-bal">—</div>
        <div class="sub" id="s-bal-sub"></div></div>
    </div>
  </div>

  <div class="card">
    <h2>Settings</h2>
    <div class="grid">
      <div><label for="f-strategy">Strategy</label><select id="f-strategy"></select></div>
      <div><label for="f-asset">Asset</label><input id="f-asset" placeholder="EURUSD_otc">
        <div class="sub"><a href="#" onclick="loadPayouts();return false;">Show live payouts &rarr;</a></div></div>
      <div><label for="f-stake">Stake per trade ($)</label><input id="f-stake" type="number" step="0.01" min="0.01"></div>
      <div><label for="f-expiry">Expiry (seconds)</label><input id="f-expiry" type="number" min="60" step="1"><div class="sub">Pocket Option minimum is 60</div></div>
      <div><label for="f-timeframe">Candle size (seconds)</label><input id="f-timeframe" type="number" min="5" step="1"><div class="sub">Can be shorter than the expiry, e.g. 30</div></div>
      <div><label for="f-loss">Stop after losing ($)</label><input id="f-loss" type="number" step="0.01" min="0"></div>
      <div><label for="f-target">Stop after winning ($, 0 = off)</label><input id="f-target" type="number" step="0.01" min="0"></div>
      <div>
        <label>Martingale (double up after a loss)</label>
        <div class="row"><input type="checkbox" id="f-mg"><label for="f-mg">Enabled — off is safer</label></div>
      </div>
      <div><label for="f-mgmult">Martingale multiplier</label><input id="f-mgmult" type="number" step="0.1" min="1"></div>
      <div><label for="f-mgsteps">Max martingale steps</label><input id="f-mgsteps" type="number" min="0"></div>
    </div>
    <div class="btns" style="margin-top:14px">
      <button class="ghost" onclick="saveSettings()">Save settings</button>
    </div>
    <div class="note">Changes apply on the next candle — no restart needed.</div>

    <!-- Live payout table. Payouts move day to day and set the win rate you
         have to beat, so this has to be looked up, never remembered. -->
    <div id="payouts" style="display:none;margin-top:16px">
      <h2>Live payouts — click a pair to use it</h2>
      <div class="note" id="payout-note">Loading…</div>
      <div style="overflow-x:auto"><table id="payout-table"></table></div>
    </div>
  </div>

  <div class="card">
    <h2>Trades</h2>
    <div class="scroll">
      <table>
        <thead><tr><th>Time</th><th>Direction</th><th>Stake</th><th>Result</th><th>Profit</th></tr></thead>
        <tbody id="t-body"></tbody>
      </table>
    </div>
    <div class="empty" id="t-empty">No trades yet. Press START to begin.</div>
  </div>

  <div class="card">
    <h2>Activity</h2>
    <div class="log" id="logbox"><div class="empty">Waiting for the bot…</div></div>
  </div>

</div>
<div class="toast" id="toast"></div>

<script>
// Password is only needed if the server was started with one. It is kept in
// this browser only (localStorage) and sent as a header on every request.
let pass = localStorage.getItem('pobot_pass') || '';

function headers(){
  const h = {'Content-Type':'application/json'};
  if (pass) h['X-Auth'] = pass;
  return h;
}

function toast(msg, bad){
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show' + (bad ? ' bad' : '');
  clearTimeout(t._h);
  t._h = setTimeout(() => { t.className = 'toast'; }, 2600);
}

async function cmd(body){
  try{
    const r = await fetch('/api/cmd', {method:'POST', headers:headers(), body:JSON.stringify(body)});
    if (r.status === 401){ askPass(); return; }
    const j = await r.json();
    // Real money gets one deliberate extra press, with the break-even maths on
    // screen at the moment of the decision rather than buried in a doc.
    if (j.needs_live_confirm){
      if (confirm(j.message)) return cmd(Object.assign({}, body, {confirm_live:true}));
      toast('Not started. Switch PO_DEMO=true in your .env to practise safely.');
      return;
    }
    toast(j.message, !j.ok);
    refresh();
  }catch(e){ toast('Connection to the bot was lost.', true); }
}

function askPass(){
  const p = prompt('Control panel password:');
  if (p !== null){ pass = p; localStorage.setItem('pobot_pass', p); refresh(); }
}

function saveSettings(){
  cmd({
    action:'settings',
    strategy: document.getElementById('f-strategy').value,
    asset:    document.getElementById('f-asset').value,
    stake:    document.getElementById('f-stake').value,
    expiry:   document.getElementById('f-expiry').value,
    timeframe:document.getElementById('f-timeframe').value,
    loss_cap: document.getElementById('f-loss').value,
    profit_target: document.getElementById('f-target').value,
    mg_enabled: document.getElementById('f-mg').checked,
    mg_mult:  document.getElementById('f-mgmult').value,
    mg_steps: document.getElementById('f-mgsteps').value
  });
}

// ---- live payouts -------------------------------------------------------
// The payout decides the win rate you must beat: 100 / (100 + payout). It
// changes day to day, so it is fetched, never hard-coded into the page.
async function loadPayouts(){
  const box  = document.getElementById('payouts');
  const note = document.getElementById('payout-note');
  box.style.display = 'block';
  note.textContent = 'Asking Pocket Option… this takes a few seconds.';
  document.getElementById('payout-table').innerHTML = '';
  try{
    const r = await fetch('/api/assets', {headers:headers()});
    if (r.status === 401){ askPass(); return; }
    const j = await r.json();
    if (!j.ok){ note.textContent = j.message || 'Could not load payouts.'; return; }
    const rows = j.assets || [];
    if (!rows.length){ note.textContent = 'No pairs are open right now.'; return; }
    note.textContent = rows.length + ' pairs open. "Need" is the win rate that just ' +
                       'breaks even at that payout — lower is easier.';
    document.getElementById('payout-table').innerHTML =
      '<tr><th>Payout</th><th>Need</th><th>Pair</th><th></th></tr>' +
      rows.map(a =>
        '<tr><td><b>' + a.payout + '%</b></td><td>' + a.breakeven.toFixed(1) + '%</td>' +
        '<td>' + a.name + '</td>' +
        '<td><button class="ghost" onclick="pickAsset(\'' + a.symbol + '\',' + a.payout +
        ')">Use ' + a.symbol + '</button></td></tr>'
      ).join('');
  }catch(e){ note.textContent = 'Could not reach the bot.'; }
}

function saveAccount(){
  const s = document.getElementById('f-session').value.trim();
  if (!s){ toast('Paste the ci_session cookie first.', true); return; }
  const demo = document.getElementById('f-demo').checked;
  if (!demo && !confirm('Save this as your REAL money account?\n\n' +
      'No strategy in this bot has yet beaten its break-even line on real data. ' +
      'Tick "Demo" instead unless you have proven one on 100+ trades.')) return;
  cmd({action:'connect', session:s, uid:document.getElementById('f-uid').value, demo:demo});
  // Clear the box the moment it is sent. Leaving a session token sitting in a
  // form field is how it ends up in a screenshot.
  document.getElementById('f-session').value = '';
}

function pickAsset(symbol, payout){
  // Set the pair AND its payout together, so the break-even figure on the
  // Today card is the one that actually applies to what is being traded.
  document.getElementById('f-asset').value = symbol;
  cmd({action:'settings', asset:symbol, payout:payout});
}

const money = n => (n >= 0 ? '+' : '') + '$' + n.toFixed(2);
const clock = ts => new Date(ts * 1000).toLocaleTimeString();

// Don't fight the user: skip repainting a field while it is being edited.
function setField(id, val){
  const el = document.getElementById(id);
  if (document.activeElement === el) return;
  if (el.type === 'checkbox') el.checked = !!val; else el.value = val;
}

let builtStrategies = '';   // ids currently in the dropdown, '|'-joined

function render(s){
  const pr = document.getElementById('p-run');
  pr.textContent = s.running ? 'RUNNING' : 'STOPPED';
  pr.className = 'pill ' + (s.running ? 'on' : 'off');

  const pm = document.getElementById('p-mode');
  pm.textContent = s.mode;
  pm.className = 'pill ' + (s.mode === 'LIVE' ? 'live' : 'demo');

  const pc = document.getElementById('p-conn');
  pc.textContent = s.connected ? 'Connected' : (s.has_token ? 'Connecting…' : 'No token');
  pc.className = 'pill' + (s.connected ? ' on' : '');

  document.getElementById('b-start').disabled = s.running || !s.has_token;
  document.getElementById('b-stop').disabled  = !s.running;
  const hints = {
    'PRACTICE': 'Practice mode — offline simulator, no account and no money involved.',
    'DEMO':     'Demo mode — practice money only, your real balance is untouched.',
    'LIVE':     'LIVE mode — real money is at risk.'
  };
  // Standing live-money warning. The single most expensive thing this panel can
  // do is let real trading feel the same as practice, so it must not look it.
  const lb2 = document.getElementById('livebar');
  if (s.is_live){
    lb2.style.display = 'block';
    lb2.innerHTML = '<b>REAL MONEY</b> — you need <b>' + s.breakeven.toFixed(1) +
      '%</b> wins at this ' + s.payout + '% payout just to break even. No strategy ' +
      'here has proven it can do that yet (0 of 28 tested — see docs/RESULTS.md). ' +
      'Set PO_DEMO=true in your .env to practise instead.';
  } else {
    lb2.style.display = 'none';
  }

  document.getElementById('hint').textContent = s.has_token
    ? ((s.mode === 'PRACTICE' && s.practice_note) ? s.practice_note : hints[s.mode])
    : 'No Pocket Option token loaded yet, so trading is disabled.';

  // "Still watching" line. Without it, a strategy that is deliberately picky is
  // indistinguishable from a bot that has died, and the only honest way to tell
  // them apart is to show the work: candles checked, when, and why it passed.
  const watch = document.getElementById('watch');
  if (s.running && s.checks > 0){
    const ago = Math.max(0, Math.round(Date.now()/1000 - s.last_check));
    watch.style.display = 'block';
    watch.textContent = 'Watching — ' + s.checks + ' candle' + (s.checks === 1 ? '' : 's') +
      ' checked, last ' + (ago < 2 ? 'just now' : ago + 's ago') +
      (s.last_reason ? '. No trade: ' + s.last_reason : '') +
      '. Sitting out is a decision, not a fault.';
  } else if (s.running){
    watch.style.display = 'block';
    watch.textContent = 'Watching — waiting for the first candle to close.';
  } else {
    watch.style.display = 'none';
  }

  // Connection card. Says what IS set without ever restating the secret.
  const cs = document.getElementById('conn-state');
  if (s.session_set){
    cs.textContent = 'Connected details are saved (account ' + s.uid + ', ' +
      (s.demo ? 'demo balance' : 'REAL money') + '). ' +
      (s.connected ? 'Pocket Option is responding.'
                   : 'Not responding yet — if this does not clear, the cookie has expired; paste a fresh one.');
  } else {
    cs.textContent = 'No account connected yet — the bot is on practice data. ' +
      'Paste your cookie below to trade on your own Pocket Option balance.';
  }
  setField('f-uid', s.uid || '');
  setField('f-demo', s.demo);

  const pnl = document.getElementById('s-pnl');
  pnl.textContent = money(s.pnl);
  pnl.className = 'v ' + (s.pnl > 0 ? 'pos' : s.pnl < 0 ? 'neg' : '');
  // Always show the win rate next to the rate that breaks even, and say when
  // the sample is still too small to mean anything. A bare percentage after a
  // handful of trades is the easiest number in trading to fool yourself with.
  const decided = s.wins + s.losses;
  const wr = document.getElementById('s-wr');
  wr.textContent = decided ? s.winrate.toFixed(0) + '%' : '—';
  wr.className = 'v ' + (!decided ? '' : (s.winrate >= s.breakeven ? 'pos' : 'neg'));
  const be = document.getElementById('s-be');
  if (!decided) {
    be.textContent = 'need ' + s.breakeven.toFixed(1) + '% to break even at ' + s.payout + '% payout';
  } else if (decided < 100) {
    be.textContent = 'need ' + s.breakeven.toFixed(1) + '% — only ' + decided +
                     ' trades, too few to judge (100+ before it means anything)';
  } else {
    be.textContent = 'need ' + s.breakeven.toFixed(1) + '% to break even — ' + decided + ' trades';
  }
  document.getElementById('s-wl').textContent = s.wins + ' / ' + s.losses;
  const bal = document.getElementById('s-bal');
  bal.textContent = s.balance === null ? '—' : '$' + s.balance.toFixed(2);
  // Never let an invented number look like the user's own money.
  const balSub = document.getElementById('s-bal-sub');
  if (s.mode === 'PRACTICE'){
    bal.className = 'v';
    balSub.textContent = 'PRETEND money — not your Pocket Option balance. ' +
                         'Nothing here has been sent to Pocket Option.';
  } else if (s.balance !== null && s.balance <= 0){
    // A binary-options account cannot really go negative, so a number like
    // -1.00 is either an empty account or a bad read. Either way, saying it
    // plainly beats printing it as though it were an ordinary balance and
    // letting every trade fail for reasons nobody can see.
    bal.className = 'v neg';
    balSub.textContent = 'Pocket Option reports no money in this account. ' +
      'Every trade will be rejected until it has a balance — top the demo back ' +
      'up on pocketoption.com. If the website shows a healthy balance and this ' +
      'does not, tell me: that is a bug at my end, not your account.';
  } else if (s.mode === 'DEMO'){
    bal.className = 'v';
    balSub.textContent = 'your Pocket Option DEMO balance — practice money';
  } else {
    bal.className = 'v';
    balSub.textContent = 'your REAL Pocket Option money';
  }

  // Rebuild only when the list actually changes, so dropping a new file into
  // strategies/ makes it appear on its own without stealing focus every 2s.
  const stratKey = s.strategies.map(x => x.id).join('|');
  if (stratKey !== builtStrategies){
    const sel = document.getElementById('f-strategy');
    sel.innerHTML = s.strategies.map(x => `<option value="${x.id}">${x.label}</option>`).join('');
    builtStrategies = stratKey;
  }
  setField('f-strategy', s.strategy);
  setField('f-asset', s.asset);
  setField('f-stake', s.stake);
  setField('f-expiry', s.expiry);
  setField('f-timeframe', s.timeframe);
  setField('f-loss', s.loss_cap);
  setField('f-target', s.profit_target);
  setField('f-mg', s.mg_enabled);
  setField('f-mgmult', s.mg_mult);
  setField('f-mgsteps', s.mg_steps);

  const tb = document.getElementById('t-body');
  document.getElementById('t-empty').style.display = s.trades.length ? 'none' : 'block';
  tb.innerHTML = s.trades.map(t => `
    <tr>
      <td>${clock(t.ts)}</td>
      <td><span class="tag ${t.direction === 'call' ? 'call' : 'put'}">${t.direction === 'call' ? '▲ UP' : '▼ DOWN'}</span></td>
      <td>$${t.stake.toFixed(2)}</td>
      <td>${t.result === 'win' ? '✅ Win' : t.result === 'loss' ? '❌ Loss' : '➖ Draw'}</td>
      <td class="${t.profit > 0 ? 'pos' : t.profit < 0 ? 'neg' : ''}">${money(t.profit)}</td>
    </tr>`).join('');

  const lb = document.getElementById('logbox');
  lb.innerHTML = s.log.length
    ? s.log.map(l => `<div><span class="t">${clock(l.ts)}</span>${escapeHtml(l.text)}</div>`).join('')
    : '<div class="empty">Waiting for the bot…</div>';
}

function escapeHtml(t){
  return t.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function refresh(){
  try{
    const r = await fetch('/api/state', {headers:headers()});
    if (r.status === 401){ askPass(); return; }
    render(await r.json());
  }catch(e){ /* server restarting; the next tick will pick it up */ }
}

refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""
