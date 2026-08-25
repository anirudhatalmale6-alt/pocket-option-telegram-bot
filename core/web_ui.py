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
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Optional

from . import version
from .config import BotConfig
from .ssid import session_value
from .stats import verdict

# Strategy modes offered in the dropdown, with a plain-English label so a
# non-technical user can pick one without reading the source.
# Most pairs the watchlist will accept. Not arbitrary: the loop checks one pair
# per poll tick and holds one trade at a time, so beyond roughly this many the
# extra pairs generate signals that are already stale by the time a slot frees
# up — and Pocket Option throttles a client that reconnects too eagerly.
MAX_PAIRS = 12

STRATEGY_CHOICES = [
    ("confluence", "Confluence — only trade when setups agree (recommended)"),
    ("ai", "AI reads the setup — needs your own API key, costs per trade"),
    ("sr", "Support & resistance — bounce off the level"),
    ("sr_fade", "Support & resistance — REVERSED (bet the level breaks)"),
    ("sr_break", "Support & resistance — trade the breakout"),
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
        # Why the SAVED cookie was refused at startup, if it was. Kept apart
        # from practice_note because it means something different: practice is a
        # choice, this is a failure, and the panel must not present a token it
        # already knows is unusable as though one were on its way in.
        self.token_error = ""
        # Verify a pasted cookie against Pocket Option to find its real account
        # id. Switched off in tests, which must never touch the network.
        self.auto_discover = True
        self._log: List[dict] = []  # newest-last ring buffer of event lines
        self._assets_cache: List[dict] = []   # live payout table
        self._assets_at = 0.0                 # when we last fetched it
        self._plugins_cache: List[tuple] = [] # strategy files found in strategies/
        self._plugins_at = 0.0
        self._lock = threading.Lock()
        self._server: Optional[ThreadingHTTPServer] = None
        # The last cookie that arrived, kept in memory only.
        #
        # The account-id listener on the Pocket Option page reports an id
        # SECONDS OR MINUTES after the cookie came in — that is its whole
        # purpose, since the id only appears when the account switcher is used.
        # By then the cookie is nowhere: a failed search saves nothing, on
        # purpose. Without this the late id arrives with nothing to try it
        # against. Never written to disk; a restart correctly forgets it.
        self._last_session = ""
        self._seen_uids: dict = {}  # id -> when it was last acted on

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
                    "asset": getattr(t, "asset", ""),
                })
        with self._lock:
            log = [dict(x) for x in self._log[-60:][::-1]]
        return {
            "running": c.running,
            "connected": self.connected,
            "mode": "PRACTICE" if self.paper else ("DEMO" if c.po_demo else "LIVE"),
            "is_live": self._is_live(),
            # A cookie that startup already rejected does not count as having
            # one. Saying otherwise made the panel show "Connecting…" forever
            # against an account it knew it could never reach.
            "has_token": (bool(c.po_ssid) and not self.token_error) or self.paper,
            "token_error": self.token_error,
            # Whether a token is set, never the token. This payload is served
            # over plain HTTP and echoed into the browser; the session string is
            # a password and has no business travelling back out again.
            "session_set": bool(c.po_ssid),
            "uid": c.po_uid,
            "demo": c.po_demo,
            "practice_note": self.practice_note,
            # The candle size practice is ACTUALLY replaying, which is not
            # always the one in the box: free EUR/USD history has no detail
            # below 5 minutes. An expiry that is not a whole number of these
            # gets rounded when the trade settles, so the panel would otherwise
            # show 500s while the run was really scoring 600s options.
            "practice_candle": getattr(
                getattr(self.trader, "broker", None), "effective_timeframe", 0),
            # Updating downloads new code but a running bot keeps the old code in
            # memory, so the page can be older than the files it came from. Say
            # so here rather than letting a fixed bug look unfixed.
            "version": version.RUNNING,
            "stale_code": version.code_on_disk_is_newer(),
            # Proof of life. A selective strategy sitting out looks exactly like
            # a crashed bot, so show what it checked, when, and why it passed.
            "checks": getattr(self.trader, "checks", 0),
            "last_check": getattr(self.trader, "last_check_ts", 0.0),
            "last_reason": getattr(self.trader, "last_reason", ""),
            "asset": c.asset,
            # Every pair in rotation, primary first. One entry means the bot
            # behaves exactly as it always did.
            "pairs": c.watched(),
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
            "auto_restart": c.risk.auto_restart,
            # How many times it has banked the target and gone again today, and
            # how far into the current run it is. Both belong on screen: without
            # them a bot that has quietly restarted six times looks exactly like
            # one that has traded all day and made very little.
            "restarts": getattr(self.risk, "restarts", 0) if self.risk else 0,
            "run_pnl": round(getattr(self.risk, "run_pnl", 0.0), 2) if self.risk else 0.0,
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
            #
            # Judged against the WORST payout being watched, not the primary
            # pair's. The win rate above pools trades from every pair in the
            # rotation, and those pairs do not share a break-even — 92% needs
            # 52.1%, 52% needs 65.8%. Scoring a pooled record against the
            # friendliest line in the set would paint a losing week green.
            # With one pair this is the pair's own payout, unchanged.
            "breakeven": 100.0 * 100.0 / (100.0 + c.worst_payout()) if c.worst_payout() else 100.0,
            "payout": c.payout_percent,
            "worst_payout": c.worst_payout(),
            # Whether the record so far actually beats that line, or only looks
            # like it. See core/stats.py — the panel used to call 100 trades
            # enough, and 100 trades is not remotely enough at these margins.
            "verdict": verdict(
                r.wins if r else 0, r.losses if r else 0,
                100.0 * 100.0 / (100.0 + c.worst_payout()) if c.worst_payout()
                else 100.0),
            "trades": trades,
            "log": log,
        }

    # ---------------------------------------------------------- diagnostics
    def diagnostics(self) -> str:
        """
        Everything I need to explain a stuck panel, as plain text he can paste.

        Every report so far has arrived as a photograph of a laptop screen, at
        an angle, cropped. Five separate times the one field that would have
        settled the question was outside the frame — the connection badge twice,
        the profit target three times — and each miss cost a day of round trips.
        The screen holds the answer; the camera is the part that keeps failing.

        So: the same facts as text. No cropping, no angle, no asking him to
        scroll to a particular card and try again.

        It carries settings and log lines, so it is written to be safe to paste
        into a chat window with me in it. The session cookie is a password and
        is never included — only whether one is set and how long it is. The log
        is scrubbed on the way out as a second line of defence: the lines are
        written not to contain it, and this does not trust that.
        """
        s = self.state()
        c = self.config
        secret = session_value(c.po_ssid or "")

        def clean(text) -> str:
            text = str(text or "")
            if secret and len(secret) > 8 and secret in text:
                return text.replace(secret, "[cookie removed]")
            return text

        when = time.strftime("%Y-%m-%d %H:%M:%S")
        mode = s["mode"]
        v = s["verdict"] if isinstance(s["verdict"], dict) else {}
        out = [
            "----- POCKET OPTION BOT — DIAGNOSTICS -----",
            f"time            {when}",
            f"version         {s['version'] or 'unknown'}"
            + ("  (NEWER CODE IS ON DISK — restart the bot)" if s["stale_code"] else ""),
            f"mode            {mode}"
            + ("   <-- SIMULATOR. No account, no money, nothing sent to Pocket Option."
               if mode == "PRACTICE" else ""),
            f"running         {'yes' if s['running'] else 'no'}",
            f"connected       {'yes' if s['connected'] else 'no'}",
            # Length only, never the value. The length alone tells me whether he
            # pasted a whole cookie, a truncated one, or the wrong field.
            f"cookie          {str(len(secret)) + ' characters' if secret else 'NOT SET'}",
            f"cookie refused  {clean(s['token_error']) if s['token_error'] else 'no'}",
            f"account id      {s['uid'] or 'not set (it works this out)'}",
            f"which balance   {'demo' if s['demo'] else 'REAL MONEY'}",
            f"balance         {'—' if s['balance'] is None else format(s['balance'], '.2f')}",
            "",
            f"strategy        {s['strategy']}",
            f"pairs           {', '.join(s['pairs']) or s['asset']}",
            f"stake           {s['stake']}",
            f"expiry          {s['expiry']}s"
            + (f"   (practice really settles {max(1, round(s['expiry'] / s['practice_candle'])) * s['practice_candle']}s)"
               if mode == "PRACTICE" and s["practice_candle"]
               and max(1, round(s["expiry"] / s["practice_candle"])) * s["practice_candle"] != s["expiry"]
               else ""),
            f"candle size     {s['timeframe']}s"
            + (f"   (practice replays {s['practice_candle']}s)"
               if s["practice_candle"] and s["practice_candle"] != s["timeframe"] else ""),
            f"stop on loss    {s['loss_cap']}",
            f"stop on profit  {s['profit_target']}"
            + ("   (0 = off, it keeps going)" if not s["profit_target"] else ""),
            f"after target    {'BANK IT AND RESTART' if s['auto_restart'] else 'stop'}"
            + (f"   ({s['restarts']} restart(s) today, this run "
               f"{s['run_pnl']:+.2f})" if s["auto_restart"] else ""),
            f"martingale      {'ON x' + str(s['mg_mult']) + ', ' + str(s['mg_steps']) + ' steps'
                               if s['mg_enabled'] else 'off'}",
            "",
            f"today           P/L {s['pnl']:+.2f}, {s['wins']} won, {s['losses']} lost",
            f"win rate        {s['winrate']:.1f}%  (needs {s['breakeven']:.1f}% to break even)",
            # The verdict is a dict of the confidence interval; pasted raw it was
            # an unreadable line of Python. Flattened to the sentence it means.
            f"verdict         {v.get('state', '?')}"
            + (f" — {v['n']} decided trades, true rate somewhere between "
               f"{v['lo']}% and {v['hi']}%" if v.get("n") else " (no trades yet)"),
            f"checks made     {s['checks']}",
            f"last look       {clean(s['last_reason']) or '—'}",
            "",
            "----- LAST 40 LOG LINES (newest first) -----",
        ]
        out += [f"{time.strftime('%H:%M:%S', time.localtime(x['ts']))}  "
                f"{clean(x['text'])}" for x in s["log"][:40]] or ["(nothing yet)"]
        out.append("----- END -----")
        return "\n".join(out)

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
        # Clean here, not just inside normalise(): the raw value is what gets
        # written to .env and handed to discovery, so a 'ci_session=' prefix
        # would validate fine and then be saved in a form nothing can use.
        from .ssid import clean_session_input
        session = clean_session_input(str(body.get("session", "")))
        raw_uid = str(body.get("uid", "")).strip()
        demo = bool(body.get("demo", True))
        # The bookmarklet reads document.cookie in one piece, so a half-copy
        # cannot happen on that route and the truncation guard must not fire.
        # It once refused a perfectly good 427-character cookie and left the
        # bot with no way in at all.
        trusted = str(body.get("via", "")) == "bookmarklet"

        if not session:
            return {"ok": False, "message": "Paste the ci_session cookie first."}

        try:
            typed_uid = int(raw_uid) if raw_uid else 0
        except ValueError:
            return {"ok": False, "message": "The account id should be digits only."}

        # An account id means nothing on its own. The demo balance and the real
        # balance have DIFFERENT ids, and sending one id with the other's isDemo
        # flag is refused in silence. So a previously saved id may only be
        # reused as a hint when it belongs to the same KIND of account as the
        # one being asked for now.
        #
        # This line used to be `int(raw_uid or c.po_uid or 0)`. The bookmarklet
        # sends uid:'' deliberately, so that `or` quietly resurrected the
        # REAL-money id saved on a previous connection and applied it to a
        # practice request — producing "Trying your practice (account id
        # 138033625)…" for an id that is not the practice account and never
        # could be. Every practice attempt was doomed before it was sent.
        hint = typed_uid or (c.po_uid if c.po_demo == demo else 0)

        # Account ids the bookmarklet scraped off Pocket Option's own page.
        # Filtered hard on the way in as well as on the way out: this arrives
        # over HTTP and is about to be put into an auth frame.
        candidates = []
        for raw in (body.get("uids") or [])[:8]:
            try:
                n = int(str(raw).strip())
            except (TypeError, ValueError):
                continue
            if 99999 < n < 10 ** 13 and n not in candidates:
                candidates.append(n)

        # Only the /hook page can tell an out-of-date bookmark from a current
        # one that found nothing: this page is always served by the current
        # server, so both post a uids field, and the difference lives in the
        # fragment the bookmark itself produced. Hence a flag rather than an
        # inference from the list being empty.
        if trusted and body.get("stale"):
            self.log("Note: that bookmark is an older version. It sent the "
                     "cookie fine, but it cannot watch the Pocket Option page "
                     "for your practice account — which is the step that has "
                     "been failing. Drag the blue button on this page onto "
                     "your bookmarks bar again, delete the old bookmark, and "
                     "use the new one.")

        # Validate BEFORE writing anything. ssid.normalise knows the difference
        # between the trading token and the chart token that gets copied by
        # mistake, and explains which is which — far better than saving a broken
        # value and surfacing it later as a silent failure to connect. uid 1 is
        # a stand-in purely so the shape check runs when no id was typed; the
        # real id is discovered below.
        from .ssid import SsidError, normalise
        try:
            normalise(session, uid=typed_uid or 1, demo=demo, trusted=trusted)
        except SsidError as exc:
            # Also into the log feed, which scrolls and stays. A pop-up is the
            # wrong home for several lines of instructions — it was reported as
            # "the message that pops up is only for a short time period", and by
            # then the only copy of the explanation had gone.
            self.log(f"Cookie not accepted: {exc}")
            return {"ok": False, "message": str(exc)}

        # Save straight away ONLY when an id was typed in THIS request, so a
        # restart is never left with nothing, then check it in the background.
        # With no id there is nothing worth writing yet — discovery supplies it.
        #
        # Never pre-save a leftover id. Pairing the previous account's id with
        # the newly requested isDemo flag writes a combination Pocket Option
        # always refuses, and the trader then reconnects on it and reports
        # "Pocket Option is NOT accepting your login" — seconds after a
        # perfectly good cookie arrived, and about a pair the panel invented
        # rather than anything the cookie was wrong about.
        result = (self._save_account(session, typed_uid, demo) if typed_uid else
                  {"ok": True, "message": "Working out which account this cookie "
                                          "belongs to — watch the log below."})

        # Verify rather than trust. Getting the account id out of DevTools is the
        # single worst step in setting this up, and a mismatched one is refused
        # in complete silence — so the panel works it out itself. On a thread,
        # because each combination takes a few seconds and the page must not hang.
        # Remember it for the listener. See _last_session in __init__: an id
        # that arrives after this search has already failed needs a cookie to
        # be tried with, and by then there is none saved anywhere.
        self._last_session = session

        self._discover_async(session, hint, demo, candidates,
                             via="bookmarklet" if trusted else "typed")
        return result

    def page_html(self) -> str:
        """
        The panel, stamped with the version that served it.

        A tab left open across an update keeps the HTML — and therefore the
        JavaScript — it was given hours ago, while /api/state answers from the
        new process. Everything on the page then looks updated because the
        NUMBERS are current, and the code around them is not.

        That is not hypothetical: it cost this client an afternoon. He updated,
        dragged the "Send PO cookie to bot" button from a tab that predated the
        update, and got a bookmark built from the old JavaScript — which then
        failed in exactly the way the update had just fixed. He did every step
        right. The page handed him the wrong button.
        """
        return PAGE.replace("__BUILD__", version.RUNNING or "")

    def late_uid(self, uid: int, demo: bool) -> str:
        """
        An account id the listener on the Pocket Option page caught, after the
        fact. Returns a short line for the log; the caller decides whether to
        show it.

        This is the answer to the one step that could not be automated: Pocket
        Option only ever hands over the id of the balance you are CURRENTLY
        looking at, so a scrape done while the real balance is selected finds
        the real id and nothing else. Sitting on the page and watching for the
        switch is the only way to see the other one without asking somebody to
        read a WebSocket frame in DevTools.
        """
        if not (99999 < uid < 10 ** 13):
            return ""
        now = time.time()
        with self._lock:
            when = self._seen_uids.get(uid, 0.0)
            # The listener fires on every re-auth, and Pocket Option's socket
            # re-authenticates on its own. Without this, leaving the tab open
            # would kick off a fresh account search every few seconds — each
            # one taking half a minute and writing over the log.
            if now - when < 120:
                return ""
            self._seen_uids[uid] = now
            session = self._last_session

        if not session:
            self.log(f"The Pocket Option page just offered account id {uid}, "
                     "but no cookie has been sent yet. Click the bookmark on "
                     "the Pocket Option tab and this id will be used with it.")
            return "no cookie yet"

        which = "practice" if demo else "real-money"
        self.log(f"The Pocket Option page just handed over a {which} account "
                 f"id ({uid}). Trying it now — no need to touch anything.")
        # demo=True always: this route exists to find the PRACTICE account, and
        # a search asked for practice can never save a real-money one. Even an
        # id the page labelled real-money is worth trying as practice — being
        # refused costs a few seconds, and on some accounts the two ids are the
        # same. What it must never do is aim at real money on its own.
        self._discover_async(session, uid, True, [uid], via="bookmarklet")
        return "searching"

    def connect_saved(self) -> bool:
        """
        Try the cookie already in .env, on start-up, without anybody clicking.

        This closes a trap the client sat in for weeks. A cookie can be saved
        with no account id — that happens whenever a search fails and the id it
        had is cleared as wrong — and from then on every start-up ends the same
        way: the token is refused for having no uid, the bot comes up in
        practice, and the panel prints "also set PO_UID", which is DevTools
        homework aimed at somebody who has no text editor. The one thing that
        could have fixed it, the account search, only ever ran when a cookie
        ARRIVED. A restart therefore could not recover, no matter how good the
        saved cookie was, and the log said nothing about needing another click.

        So: if there is a cookie and no id, go and look for the id — the same
        search, on the same thread, saving to the same place. Returns True when
        a search was started, so the caller can say so instead of printing
        instructions for a step that is already running.
        """
        from .ssid import session_value
        c = self.config
        session = session_value(c.po_ssid or "")
        if not session or not session.strip():
            return False
        # Only for a token that was REFUSED. A saved pair that the token check
        # accepted is a connected bot, and searching it would spend half a
        # minute of connect-and-wait re-proving something that already works —
        # and worse, could clear a good id if a probe happened to be refused.
        if not self.token_error:
            return False
        with self._lock:
            self._last_session = session
        self.log("There is a saved cookie but no account id. Looking the account "
                 "up now — nothing for you to click.")
        self._discover_async(session, c.po_uid, c.po_demo, [], via="startup")
        return True

    def _save_account(self, session: str, uid: int, demo: bool) -> dict:
        """Write the details to .env, point the config at them, and reconnect."""
        c = self.config
        try:
            from .envfile import update
            from .ssid import _canonical
            # PO_SSID is normally blanked deliberately. config.from_env reads
            #   po_ssid = PO_SSID or PO_SESSION
            # so a leftover PO_SSID from an earlier attempt would silently win
            # over what was just typed here, and the panel would look like it
            # had saved something it had not.
            #
            # The exception is uid 0, which means discovery found that Pocket
            # Option accepts this session without an account id at all. The
            # cookie-plus-uid route cannot express that, so store the finished
            # auth frame instead — it is the only form that survives a restart.
            # decode=False: this string goes into .env, which stays parseable
            # only while the blob is percent-encoded. It is decoded again on the
            # way to Pocket Option, inside _canonical.
            ssid = _canonical(session, 0, demo, decode=False) if uid == 0 else ""
            update({"PO_SESSION": session, "PO_UID": str(uid),
                    "PO_DEMO": "true" if demo else "false",
                    "PO_SSID": ssid})
        except OSError as exc:
            # Say it did not save. Claiming success here would be the worst
            # possible outcome: he restarts and it is still not connected.
            return {"ok": False,
                    "message": f"Could not write the .env file: {exc}"}

        c.po_ssid = ssid or session      # mirrors what was just written to .env
        c.po_uid = uid
        c.po_demo = demo
        self.paper = False
        self.practice_note = ""
        self.token_error = ""       # a new cookie retires the old complaint
        # Never log the token itself — this feed is on screen and in the logs.
        self.log(f"Account details saved (uid {uid}, "
                 f"{'DEMO' if demo else 'LIVE'}). Reconnecting…")

        if self.reload_cb is None:
            return {"ok": True, "message": "Saved to .env. Restart the bot to connect: "
                                           "bash stop.sh && bash open_panel.sh"}
        try:
            self.reload_cb()
        except Exception as exc:
            return {"ok": True,
                    "message": f"Saved to .env, but could not reconnect automatically "
                               f"({exc}). Restart with: bash run.sh"}
        return {"ok": True, "message": "Saved. Connecting to your Pocket Option "
                                       "account now — watch the badge at the top."}

    def _discover_async(self, session: str, uid: int, demo: bool,
                        candidates: Optional[List[int]] = None,
                        via: str = "") -> None:
        """Run the account search on its own thread, logging as it goes."""
        import threading
        from .discover import _uids

        if not self.auto_discover:      # off in tests; this step talks to the network
            return

        def work() -> None:
            import asyncio as _aio
            from .discover import find_account
            self.log("Working out which Pocket Option account this cookie opens…")

            # Ask Pocket Option's own website for the account id before trying
            # anything. The cookie IS a login, so the site will answer as him —
            # and every other route to this number (DevTools, walking the page's
            # JavaScript from the bookmark) has failed on his machine for weeks.
            # It also tells the two dead ends apart for the first time: a site
            # that answers logged-OUT means the cookie is finished, which used
            # to look identical to a cookie whose id we simply could not find.
            ids = list(candidates or [])
            try:
                from .uid_lookup import account_ids
                found_ids = account_ids(session, log=self.log)
                for value in found_ids.ids:
                    if value not in ids:
                        ids.append(value)
            except Exception as exc:
                # Never fatal. This is an extra source of guesses, and the old
                # routes still work without it.
                self.log(f"Could not ask the website for the account id "
                         f"({type(exc).__name__}). Carrying on with what we have.")

            # How many REAL account ids this search gets to try. uid 0 is always
            # in the list and is not one of them — it is the "maybe the cookie is
            # enough on its own" long shot. The difference decides what a total
            # failure is allowed to conclude, so it is worked out from the same
            # function the search uses rather than re-derived here. Counted after
            # the lookup, because ids it found are ids that get tried.
            ids_tried = len([u for u in _uids(uid, ids) if u])

            try:
                found = _aio.run(find_account(session, uid_hint=uid,
                                              demo_hint=demo, log=self.log,
                                              candidates=ids))
            except Exception as exc:
                self.log(f"Could not check the account: {type(exc).__name__}: {exc}")
                found = None
            self._apply_discovery(found, session, demo, ids_tried, via)

        threading.Thread(target=work, daemon=True,
                         name="po-account-discovery").start()

    def _forget_account_id(self) -> None:
        """
        Drop a saved account id that has just been shown to be the wrong one.

        Only the id. The cookie and the demo/live choice are left exactly as
        they are: this runs on a failed search, and quietly changing which
        ACCOUNT the bot points at as a side effect of a failure is the class of
        move that cost this project real money once already.
        """
        try:
            from .envfile import update
            update({"PO_UID": ""})
        except OSError:
            pass                     # a stale id in the file is not worth a crash
        self.config.po_uid = 0
        self.log("Clearing the saved account id — it is not this account's, so "
                 "the next attempt will search from scratch instead of "
                 "repeating it.")

    def _apply_discovery(self, found, session: str, demo: bool,
                         ids_tried: int = 0, via: str = "") -> None:
        """
        Decide what to do with what the search came back with.

        Split out from the thread above so it can be tested directly. What this
        decides is which account a self-trading bot gets pointed at, so it must
        not be reachable only through a background thread and a live socket.
        """
        # Whatever happened below, the search has just proved something about
        # the id sitting in .env: if the requested kind of account was not
        # reached, then a saved id claiming to BE that kind is wrong, and
        # leaving it there makes the next attempt repeat this one exactly.
        #
        # This matters because a now-fixed bug wrote precisely that pair: the
        # real-money id stored with PO_DEMO=true. A file in that state survives
        # the fix, and the kind-matches test in _connect would be satisfied by
        # it and hand the same doomed id back to the search for ever.
        if (found is None or not found.matches_request) and \
                self.config.po_uid and self.config.po_demo == demo:
            self._forget_account_id()

        if found is None:
            # The length is safe to print and worth printing: a complete
            # ci_session is several hundred characters, so a suspiciously
            # short one that still passed the shape check narrows this from
            # "expired" to "half of it got copied". Never the value itself —
            # this feed is on screen and in the log file.
            size = f"the cookie it tried was {len(session)} characters"

            if ids_tried:
                # A real account id was among the attempts and was refused
                # alongside every other combination, so the cookie is the
                # thing at fault. This is the only case that may say so.
                self.log(f"⚠️ Pocket Option refused every combination "
                         f"({size}). That means the cookie itself is no "
                         "longer valid, not that the account id is wrong. "
                         "Log in to pocketoption.com, copy a FRESH "
                         "ci_session cookie, paste it here — and do not log "
                         "out afterwards, because logging out kills it.")
                return

            # Nothing but uid 0 was ever tried, and uid 0 has never once been
            # accepted by Pocket Option. So this run proves NOTHING about the
            # cookie, and the message above used to claim it did: it told
            # people to go and fetch a fresh cookie when the fresh one would
            # fail in exactly the same way, because what is missing is the
            # account id. Two different faults that produce an identical log
            # must not share a conclusion.
            #
            # Which of the two it is depends on how the cookie arrived, and
            # that is the only thing that separates them.
            if via == "bookmarklet":
                self.log(f"⚠️ Could not get in ({size}) — but this does not "
                         "mean your cookie is bad. No account id was "
                         "available to try, and Pocket Option has never "
                         "accepted a login without one, so this attempt was "
                         "never going to work either way. The bookmark could "
                         "not find your account id on the Pocket Option page. "
                         "It has switched on a listener there: go back to the "
                         "Pocket Option tab, click the Demo/Real switch once "
                         "(or just leave it open a minute), then click the "
                         "bookmark again. That second click is the one that "
                         "finds it.")
            elif via == "startup":
                self.log(f"⚠️ The cookie saved in your settings could not get in "
                         f"({size}), and Pocket Option's website did not give up "
                         "an account id for it either. That combination usually "
                         "means the cookie has expired — a cookie dies when you "
                         "log out, and after a while on its own. Open "
                         "pocketoption.com, log in, click the blue bookmark, and "
                         "do not log out afterwards.")
            else:
                self.log(f"⚠️ Could not get in ({size}) — but this does not "
                         "mean your cookie is bad. A pasted cookie carries no "
                         "account id, and Pocket Option has never accepted a "
                         "login without one, so this attempt was never going "
                         "to work either way. Use the blue bookmark button on "
                         "this page instead of pasting: it reads the account "
                         "id off the Pocket Option page as well as the "
                         "cookie, which is the part that is missing here.")
            return

        if not found.matches_request:
            # The other kind of account answered — and it is NOT the one
            # that was asked for. Saving it would connect the bot to real
            # money because the demo happened to be unreachable, which is
            # the single worst thing this program can do on its own. Report
            # it and stop; switching to real money is a decision a person
            # makes, deliberately, with the tick box.
            asked = "practice" if demo else "real money"
            self.log(f"⚠️ Your {found.label} answers, but your {asked} account "
                     f"does not. Nothing has been saved and the bot is NOT "
                     f"connected to it.")
            if demo:
                self.log("Open pocketoption.com and switch to the demo balance "
                         "there (top right), then click the bookmark again — "
                         "that usually wakes the demo account up. I have "
                         "deliberately not connected you to real money.")
            if self.config.po_ssid:
                # Refusing to save leaves the trader on whatever was saved
                # BEFORE, which is usually a stale cookie sitting at -1.00 with
                # a warning under it about expired sessions. Read straight after
                # sending a fresh cookie, that warning looks like a verdict on
                # the cookie just sent — it is not. Say whose number it is.
                self.log("Note: any balance or connection warning below this is "
                         "about the cookie that was saved BEFORE, not the one "
                         "you just sent. Yours was not used.")
            return

        if found.balance > 0:
            self.log(f"✓ Found it — your {found.label}, "
                     f"balance {found.balance:,.2f}.")
        else:
            self.log(f"✓ Pocket Option accepted your {found.label}, but there "
                     f"is no money in it.")
        res = self._save_account(session, found.uid, found.demo)
        if not res.get("ok"):
            self.log(res.get("message", "Could not save the account details."))

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
            # data (docs/RESULTS.md, 0 of 40 combinations), so pressing START on
            # a funded account is a decision to fund an experiment. The panel
            # sends confirm_live only after showing that in plain words.
            if self._is_live() and not body.get("confirm_live"):
                need = 100.0 * 100.0 / (100.0 + c.payout_percent) if c.payout_percent else 100.0
                # This is the one screen where "how much can this lose while I
                # am not watching" has to be answered completely. With restarts
                # on, the target no longer ends the session — it keeps trading
                # all day and the loss cap is the only thing that stops it.
                restart_note = ""
                if c.risk.auto_restart:
                    restart_note = (f", and it RESTARTS after each "
                                    f"${c.risk.daily_profit_target:.2f} target, so "
                                    f"the loss cap is the only thing that stops it")
                return {"ok": False, "needs_live_confirm": True,
                        "message": (
                            f"This is your REAL money account.\n\n"
                            f"At a {c.payout_percent:.0f}% payout you must win "
                            f"{need:.1f}% of trades just to break even. No strategy "
                            f"in this bot has yet proven it can do that — of 40 "
                            f"tested combinations, none beats break-even at an 80% "
                            f"payout and exactly one does at 92%, and that same "
                            f"strategy wins 39% on the next timeframe up "
                            f"(docs/RESULTS.md).\n\n"
                            f"Stake ${c.risk.base_stake:.2f}, stops after losing "
                            f"${c.risk.daily_loss_cap:.2f} today"
                            f"{', MARTINGALE IS ON' if c.martingale.enabled else ''}"
                            f"{restart_note}.\n\n"
                            f"Start trading real money anyway?")}
            if self._is_live():
                self.log("⚠ START on a LIVE account — real money is now at risk.")
            c.running = True
            self.log("▶ START pressed from the control panel.")
            # Whose money is about to move — said at the moment of the press,
            # not only on a badge in the header.
            #
            # The badge, the PRACTICE note and the balance tile all say this
            # already, and it still was not enough: the client has twice now
            # watched a practice run expecting his Pocket Option demo balance to
            # move, and reported the bot as broken when it did not. A mode you
            # have to go and check is a mode that gets assumed. The moment the
            # button is pressed is the moment the assumption forms, so the
            # answer belongs there — and it has to name the consequence
            # ("your demo balance will NOT move"), not just the mode.
            if self.paper:
                self.log("This is PRACTICE — replayed history on this computer. "
                         "Nothing is sent to Pocket Option and your DEMO BALANCE "
                         "WILL NOT MOVE. To trade your real demo account, send "
                         "your cookie with the blue bookmark above."
                         + (f" (The cookie you sent was refused: {self.token_error})"
                            if self.token_error else ""))
            elif c.po_demo:
                self.log("This is your Pocket Option DEMO account — real trades "
                         "on their server, practice money. Your demo balance "
                         "will move.")
            # Say what it is doing and roughly how long a quiet spell is normal.
            # Without this, a selective strategy looks identical to a dead bot.
            pairs = c.watched()
            where = (f"{len(pairs)} pairs ({', '.join(pairs)})" if len(pairs) > 1
                     else c.asset)
            self.log(f"Watching {where} on {c.candle_timeframe}s candles with the "
                     f"'{c.strategy_mode}' strategy. Trades appear below when a "
                     f"setup matches — a quiet spell is normal, not a fault.")
            if len(pairs) > 1:
                # The whole point of a watchlist is more trades per hour, and
                # more trades per hour is also the fast route to the daily loss
                # cap. Both halves of that belong on screen at the moment it
                # starts, not just the half that sounds like progress.
                self.log(f"That is about {len(pairs)}x as many trades per hour as one "
                         f"pair, so a verdict arrives roughly {len(pairs)}x sooner — and "
                         f"your ${c.risk.daily_loss_cap:.0f} daily loss cap is reached "
                         f"roughly {len(pairs)}x sooner too, if the strategy is losing. "
                         f"Still one trade at a time; the pairs take turns.")
                # How stale an entry can be. Taking turns means each pair waits
                # its turn, and on short candles that wait is a real fraction of
                # the bar — the entry price is not the close the signal was
                # computed on. It belongs on screen next to the good news.
                lag = self.trader.look_interval(len(pairs)) if self.trader else 0.0
                if lag:
                    share = 100.0 * lag / max(1, c.candle_timeframe)
                    self.log(f"Each pair gets looked at every {lag:.0f}s, which is "
                             f"{share:.0f}% of a {c.candle_timeframe}s candle — so an "
                             f"entry can be up to {lag:.0f}s after the candle it was "
                             f"decided on closed. Fewer pairs, or bigger candles, "
                             f"tighten that.")
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
                # Pocket Option only serves these sizes. Anything else returns an
                # empty candle list, which on screen is indistinguishable from a
                # dead connection — so refuse it here, where it can be explained.
                allowed_tf = (5, 15, 30, 60, 300)
                if v not in allowed_tf:
                    return {"ok": False, "message":
                            f"Pocket Option only provides these candle sizes: "
                            f"{', '.join(str(t) for t in allowed_tf)} seconds. "
                            f"Anything else comes back with no data at all."}
                c.candle_timeframe = v
                changed.append(f"candle {v}s")

            if "asset" in body:
                asset = str(body["asset"]).strip()
                if not asset:
                    return {"ok": False, "message": "Asset cannot be empty."}
                c.asset = asset
                changed.append(f"asset {asset}")

            if "pairs" in body:
                # The watchlist, as typed or as filled in by the payout table.
                # Accepts a list or a comma/space/newline separated string,
                # because the box is a free-text field and a person separating
                # things by whatever comes to hand is not making a mistake.
                raw = body["pairs"]
                if isinstance(raw, str):
                    raw = raw.replace("\n", ",").replace(" ", ",").split(",")
                names, seen = [], set()
                for item in raw or []:
                    name = str(item).strip()
                    if name and name not in seen:
                        seen.add(name)
                        names.append(name)
                if len(names) > MAX_PAIRS:
                    return {"ok": False, "message": (
                        f"That is {len(names)} pairs. The bot checks one pair per "
                        f"second and holds one trade at a time, so past about "
                        f"{MAX_PAIRS} it is queueing signals it cannot act on — "
                        f"and Pocket Option starts refusing a connection that asks "
                        f"too often. Trim the list to your best {MAX_PAIRS}.")}
                # The first entry becomes the primary pair, so the box shows
                # exactly what is being watched rather than the primary silently
                # appearing on top of whatever was typed.
                if names:
                    c.asset = names[0]
                    c.assets = names[1:]
                else:
                    c.assets = []
                changed.append(f"watching {len(c.watched())} pair(s)")

            if "payouts" in body:
                # Sent alongside `pairs` when the list comes from the live payout
                # table, so the break-even line can be worked out per pair
                # instead of assuming they all pay the same. They do not.
                try:
                    c.asset_payouts = {str(k): float(v)
                                       for k, v in dict(body["payouts"]).items()
                                       if 1 <= float(v) <= 100}
                except (TypeError, ValueError, AttributeError):
                    return {"ok": False, "message": "Payout list was not readable."}

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

            if "auto_restart" in body:
                want = bool(body["auto_restart"])
                # Checked BEFORE it is applied, and against the target as it
                # stands after the block above — the two are nearly always sent
                # in the same save. Ticking this with no target is a box that
                # cannot do anything, and nothing on the page would say so.
                if want and c.risk.daily_profit_target <= 0:
                    return {"ok": False,
                            "message": "Set a 'stop after winning' amount as well "
                                       "— there is no target to restart from "
                                       "otherwise, so the bot would simply keep "
                                       "trading until the loss limit stops it."}
                c.risk.auto_restart = want
                changed.append(f"restart after target {'on' if want else 'off'}")

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
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    # The browser went away mid-response — a refresh or a closed
                    # tab. Normal, and nothing to report: the alternative is a
                    # traceback in the terminal that reads like a crash.
                    return

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
                    self._send(200, iface.page_html().encode(),
                               "text/html; charset=utf-8")
                elif path == "/hook":
                    # Where the bookmarklet lands. Unauthenticated on purpose:
                    # it serves a page, and the page does the authenticated POST
                    # like any other. The cookie itself arrives in the URL
                    # fragment, which browsers never send to a server — so it
                    # stays in the tab until that POST.
                    self._send(200, HOOK_PAGE.encode(), "text/html; charset=utf-8")
                elif path == "/uid":
                    # Where the listener on the Pocket Option page reports an
                    # account id it has just seen. A GET that answers with an
                    # image, because that is the only shape of request a script
                    # on somebody else's HTTPS page can send to this port
                    # without CORS, a preflight, or navigating the tab away —
                    # and navigating the tab away is what killed the listener
                    # every previous time. DIGITS ONLY ever travel this way:
                    # the cookie still goes by the fragment-and-POST route.
                    self._uid_ping(path)
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
                elif path == "/api/diagnostics":
                    # Behind the password like any other reading of the
                    # settings: it says which pairs, what stake, and how much
                    # is being risked. It never carries the cookie itself.
                    if not self._authed():
                        self._json(401, {"error": "unauthorised"})
                        return
                    self._send(200, iface.diagnostics().encode(),
                               "text/plain; charset=utf-8")
                else:
                    self._send(404, b"Not found", "text/plain")

            # A 1x1 transparent GIF. The reply is never looked at — the answer
            # shows up in the panel's own log — but something image-shaped has
            # to come back or the browser reports the beacon as an error.
            PIXEL = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\x00\x00"
                     b"\x00!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01"
                     b"\x00\x01\x00\x00\x02\x02D\x01\x00;")

            def do_OPTIONS(self):
                """
                Chrome's Private Network Access preflight.

                A page on the public internet asking a browser to fetch
                something from 127.0.0.1 is exactly the shape of a router
                attack, so Chrome will not send that request at all until the
                thing on 127.0.0.1 has said, in these headers, that it expects
                it. Without this the beacon fails as ERR_FAILED before it
                leaves the browser — invisibly, with the page's own status box
                still cheerfully reporting success.
                """
                self.send_response(204)
                self._cors()
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "*")
                self.send_header("Access-Control-Max-Age", "600")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _cors(self) -> None:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Private-Network", "true")

            def _uid_ping(self, path: str) -> None:
                from urllib.parse import parse_qs, urlparse
                # No password is possible on this route: a cross-origin image
                # request cannot carry a header. So when the panel IS password
                # protected, only the machine it runs on may use it. On a
                # Chromebook — where every one of these comes from the browser
                # on the same machine — nothing changes.
                host = self.client_address[0] if self.client_address else ""
                local = host in ("127.0.0.1", "::1", "localhost")
                try:
                    q = parse_qs(urlparse(self.path).query)
                    uid = int((q.get("id") or ["0"])[0])
                    demo = (q.get("demo") or ["1"])[0] not in ("0", "false")
                    # The visible fallback: a real tab, opened by a click, for
                    # when the silent beacon is refused.
                    page = (q.get("close") or [""])[0] == "1"
                except (ValueError, TypeError):
                    uid, demo, page = 0, True, False

                if not (iface.password and not local):
                    try:
                        iface.late_uid(uid, demo)
                    except Exception as exc:      # a beacon must never 500
                        iface.log(f"Could not use that account id: "
                                  f"{type(exc).__name__}: {exc}")

                if page:
                    body = UID_PAGE.encode()
                    ctype = "text/html; charset=utf-8"
                else:
                    body, ctype = self.PIXEL, "image/gif"
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self._cors()
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError,
                        ConnectionAbortedError):
                    return

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

        class QuietServer(ThreadingHTTPServer):
            """
            A disconnected browser is not an error worth printing.

            socketserver's default handle_error dumps a full traceback to the
            terminal. Refreshing the page is enough to trigger one, and to
            somebody watching that window it looks exactly like the bot
            crashing — which is how it was reported.
            """
            daemon_threads = True

            def handle_error(self, request, client_address):
                exc = sys.exc_info()[1]
                if isinstance(exc, (BrokenPipeError, ConnectionResetError,
                                    ConnectionAbortedError)):
                    return
                super().handle_error(request, client_address)

        self._server = QuietServer((self.host, self.port), Handler)
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
  /* Sits above everything, because it explains why everything else looks wrong. */
  #stalebar{background:rgba(245,158,11,.14);border:1px solid rgba(245,158,11,.6);
            border-radius:10px;padding:13px 15px;margin:0 0 14px;
            font-size:14.5px;line-height:1.55;color:#fde68a}
  #stalebar b{color:#fff}
  #stalebar code{background:rgba(0,0,0,.35);padding:1px 6px;border-radius:5px;
                 font:13px ui-monospace,SFMono-Regular,Menlo,monospace;color:#fff}
  #stalebar button{background:#1d2430;border:1px solid rgba(245,158,11,.6);
                   color:#fde68a;border-radius:8px;padding:7px 14px;cursor:pointer;
                   font-size:14px}
  .warn{background:rgba(245,158,11,.14);border:1px solid rgba(245,158,11,.6);
        border-radius:8px;padding:8px 11px;margin:8px 0 0;font-size:13.5px;
        line-height:1.5;color:#fde68a}
  .warn b{color:#fff}
  #diagbox{width:100%;height:220px;margin-top:12px;resize:vertical;
           background:var(--panel2);border:1px solid var(--line);border-radius:8px;
           padding:10px;color:var(--text);font:12px/1.45 ui-monospace,monospace;
           white-space:pre;overflow:auto}
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
         opacity:0;pointer-events:none;transition:opacity .2s;
         /* Errors here are instructions, not status: several lines, meant to be
            read and followed. Left-aligned, wrapped, and wide enough to hold
            them. */
         max-width:min(560px,92vw);white-space:pre-line;text-align:left}
  /* Deliberately looks like a button you would drag, not a link you would
     read past. It is the most important control on the page. */
  .bmk{display:inline-block;background:var(--blue);color:#fff;font-weight:650;
       border-radius:9px;padding:8px 15px;text-decoration:none;margin:4px 0 0 4px;
       cursor:grab;box-shadow:0 3px 10px rgba(59,130,246,.35)}
  .howto{margin:12px 0 0;padding-left:22px}
  .howto li{margin-bottom:9px}
  .toast.show{opacity:1}
  .toast.bad{border-color:rgba(239,68,68,.55);pointer-events:auto}
  .toast button{margin-top:10px;font-size:12.5px;padding:6px 12px}
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

  <!-- Shown only when the files on disk are newer than this running process. -->
  <div id="stalebar" style="display:none"></div>

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

  <!-- The one-click route, above the manual one because it is now the route.
       Copying the cookie by hand failed four separate times for the person this
       was built for — the value cell would not select, Ctrl+A grabbed the whole
       table, the Console paste went into the filter box. Re-explaining the step
       did not work any of those times, so the step is gone: this bookmark reads
       the cookie itself and hands it straight to this panel. -->
  <div class="card">
    <h2>One-click connect</h2>
    <div class="note">The easy way. No DevTools, no copying, and no way to
      accidentally copy only half of it.</div>
    <ol class="howto">
      <li>Press <b>Ctrl+Shift+B</b> to show Chrome's bookmarks bar.</li>
      <li>Drag this button up onto that bar:
        <a id="bm-link" class="bmk" href="#" onclick="bmClick(event)">Send PO cookie to bot</a>
        <div id="bm-stale" class="warn" style="display:none">
          Don't drag it yet — this page is older than the bot, so this button is
          the previous version's. Press <b>Ctrl+R</b> to reload first.
        </div>
        <div class="sub">If there is already one there from before, delete it.
          A bookmark is a copy taken the day you made it, so an old one keeps
          working the old way however many times the bot is updated.</div></li>
      <li>Open <b>pocketoption.com</b> and log in.</li>
      <li>On that page, click the bookmark you just made.</li>
      <li><b>Leave that tab open</b>, and switch your balance to <b>Demo</b> in
        the account box at the top right of Pocket Option's page.
        <div class="sub">Pocket Option only ever tells anything which balance
          you are looking at right now, so the practice account is invisible
          until you go to it. The bookmark stays on that page and picks it up
          the moment you do — you do not have to click anything again.</div></li>
    </ol>
    <div class="btns">
      <button class="ghost" onclick="copyBookmarklet()">Dragging won't work? Copy the address instead</button>
    </div>
    <div class="sub" id="bm-alt" style="display:none;margin-top:10px">
      Copied. Now open a new tab, go to <b>chrome://bookmarks</b>, click the
      three dots at the top right, choose <b>Add new bookmark</b>, type any name,
      paste into the URL box, and save. Then open Pocket Option, log in, and
      click it.
    </div>
    <div class="sub" style="margin-top:10px">Your cookie goes from the Pocket
      Option tab straight into this panel on your own computer. It is not sent
      anywhere else, and it is wiped out of the address bar the moment it
      arrives.</div>
  </div>

  <!-- Account connection. Lives on the page because the alternative is asking
       a non-technical user to paste a 400-character secret into a hidden
       dotfile from a terminal, which is not a real option. -->
  <div class="card">
    <h2>Your Pocket Option account &mdash; the manual way</h2>
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
      <div><label for="f-uid">Account id (uid) &mdash; optional</label>
        <input id="f-uid" type="text" inputmode="numeric" placeholder="leave blank — it works this out">
        <div class="sub">Pocket Option gives your DEMO and your REAL balance
          <b>different</b> ids, and the wrong one is refused in silence. So you no
          longer have to find it: leave this blank and the panel tries each
          combination against your cookie and keeps whichever one answers.</div></div>
      <div>
        <label>Which balance</label>
        <div class="row"><input type="checkbox" id="f-demo" checked><label for="f-demo">Demo — practice money (recommended)</label></div>
        <div class="sub">Not a preference — a requirement. If your demo cannot be
          reached, the bot stops and says so; it will never connect you to real
          money because the demo did not answer.</div>
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
      <div style="grid-column:1/-1">
        <label for="f-pairs">Watch several pairs at once</label>
        <input id="f-pairs" placeholder="EURUSD_otc, GBPUSD_otc, AUDCAD_otc"
               oninput="pairsDirty = true">
        <div class="sub">Separate them with commas. The first one is the main pair.
          Ten pairs means about ten times as many trades an hour, so you find out
          ten times faster whether a strategy actually works — it does not make
          the strategy any better. Still one trade at a time.
          <a href="#" onclick="loadPayouts(true);return false;">Fill this from the
          best-paying pairs &rarr;</a></div>
      </div>
      <div><label for="f-stake">Stake per trade ($)</label><input id="f-stake" type="number" step="0.01" min="0.01"></div>
      <div><label for="f-expiry">Expiry (seconds)</label><input id="f-expiry" type="number" min="60" step="1"><div class="sub">Pocket Option minimum is 60</div>
        <div class="sub" id="f-expiry-note" style="display:none"></div></div>
      <div><label for="f-timeframe">Candle size (seconds)</label>
        <select id="f-timeframe">
          <option value="5">5 seconds</option>
          <option value="15">15 seconds</option>
          <option value="30">30 seconds</option>
          <option value="60">60 seconds (1 minute)</option>
          <option value="300">300 seconds (5 minutes)</option>
        </select>
        <div class="sub">Only these are available — Pocket Option serves no others.
          Can be shorter than the expiry.</div></div>
      <div><label for="f-loss">Stop after losing ($)</label><input id="f-loss" type="number" step="0.01" min="0"></div>
      <div><label for="f-target">Stop after winning ($, 0 = off)</label><input id="f-target" type="number" step="0.01" min="0"></div>
      <div>
        <label>After the target, start again</label>
        <div class="row"><input type="checkbox" id="f-restart"><label for="f-restart">Bank it and keep trading</label></div>
        <div class="sub">Each target is banked and a new one starts from there.
          The "stop after losing" limit above is NOT restarted with it — it keeps
          counting the whole day and still stops everything. Restarting does not
          improve the odds of the next trade; it only decides when to stop.</div>
      </div>
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
        <thead><tr><th>Time</th><th>Pair</th><th>Direction</th><th>Stake</th><th>Result</th><th>Profit</th></tr></thead>
        <tbody id="t-body"></tbody>
      </table>
    </div>
    <div class="empty" id="t-empty">No trades yet. Press START to begin.</div>
  </div>

  <div class="card">
    <h2>Activity</h2>
    <div class="log" id="logbox"><div class="empty">Waiting for the bot…</div></div>
  </div>

  <!-- Photographs of this screen have now missed the one field that mattered
       five separate times. This is the same facts as text, so a report is a
       paste instead of a photo. -->
  <div class="card">
    <h2>Something wrong? Send me this</h2>
    <div class="note">Press the button, then paste it to me on Freelancer. It is
      everything I need to see what the bot is doing — your settings, what it is
      connected to, and the last 40 things it did. <b>It does not contain your
      Pocket Option cookie or password</b>, only whether one is set.</div>
    <div class="btns" style="margin-top:12px">
      <button class="ghost" onclick="copyDiag()">Copy report</button>
    </div>
    <textarea id="diagbox" readonly spellcheck="false" style="display:none"
              onclick="this.select()"></textarea>
    <div class="sub" id="diaghint" style="display:none">Copied. If your clipboard
      did not take it, click inside the box, press Ctrl+A then Ctrl+C.</div>
  </div>

</div>
<div class="toast" id="toast"><span id="toastmsg"></span>
  <div><button id="toastx" onclick="hideToast()">Got it</button></div></div>

<script>
// The version of the bot that served THIS page, stamped in at the time. It is
// compared against the version answering /api/state on every poll: when they
// differ, this tab is older than the bot and everything in it — the drag-to-
// bookmark button above all — is out of date. See the stale-page banner below.
const PAGE_VERSION = '__BUILD__';

// Password is only needed if the server was started with one. It is kept in
// this browser only (localStorage) and sent as a header on every request.
let pass = localStorage.getItem('pobot_pass') || '';

function headers(){
  const h = {'Content-Type':'application/json'};
  if (pass) h['X-Auth'] = pass;
  return h;
}

// ------------------------------------------ account ids from the PO tab
//
// The bookmarklet stays on pocketoption.com and watches for the account
// switcher being used, because that is the only moment the PRACTICE account id
// ever appears. Getting that number the four inches from that tab to this one
// is the awkward part: Chrome 143 refuses outright any network request from a
// public page to something on 127.0.0.1, so the Pocket Option page cannot
// simply call this server.
//
// postMessage is not a network request, so none of that applies — and the
// bookmarklet opened this tab, so it has a handle to it. It sends the digits
// here, and this page, which IS the panel's own origin, passes them on.
//
// Digits only, and they can only ever start a search for a PRACTICE account.
// Nothing here can point the bot at real money.
window.addEventListener('message', ev => {
  let m;
  try{ m = JSON.parse(String(ev.data)); }catch(e){ return; }
  if (!m || typeof m.po_uid === 'undefined') return;
  const uid = parseInt(m.po_uid, 10);
  if (!(uid > 99999 && uid < 1e13)) return;
  fetch('/uid?id=' + uid + '&demo=' + (m.demo ? 1 : 0)).catch(() => {});
});

// ------------------------------------------------------------- bookmarklet
//
// Built here rather than written into the HTML because it has to carry this
// panel's own address, and that address differs per machine: localhost:8080 for
// most people, penguin.linux.test:8080 on a Chromebook, a LAN address on a VPS.
// location.origin is always whatever actually worked to load this page.
//
// Pocket Option sets no HttpOnly flag on ci_session (checked against the live
// headers), so a script on their page can read it. That is the whole trick.
//
// It does NOT navigate this tab any more, and that is the whole point of the
// current version. It used to end with location.href = panel + '/hook#…',
// which meant the listener it had just installed on Pocket Option's WebSocket
// died in the same instant — the page it lived on was gone. So the listener,
// whose entire job is to notice the account switcher being used LATER, never
// once got the chance to notice anything. The panel opens in a second tab
// instead and this one stays put, watching.
const BOOKMARKLET =
  "javascript:(function(){" +
  "var O=" + JSON.stringify(location.origin) + ";" +
  "var c=(document.cookie.split('; ').filter(function(x){" +
  "return x.indexOf('ci_session=')===0})[0]||'').slice(11);" +
  "if(!c){alert('No Pocket Option cookie on this page.\\n\\n" +
  "Open pocketoption.com, log in, and click this bookmark from that tab.');return}" +
  // Also collect account-id candidates from the page's own storage.
  //
  // The cookie identifies the SESSION; it does not say which of your balances
  // it may touch. That is the uid, the demo and real balances have different
  // ones, and sending the wrong one is refused in silence. Pocket Option's own
  // page has to know both to draw the account switcher, so they are in here
  // somewhere — under a key whose name nobody promised us, hence the walk.
  //
  // ONLY INTEGERS LEAVE THE PAGE. This walks whole parsed objects, which will
  // include tokens and personal details, and keeps nothing but numbers in a
  // plausible id range. Nothing else is recorded, sent or logged.
  "var d={},sent={},queue=[],flushed=0,w=null;" +
  "var K=/uid|userid|user_id|account|profile/i;" +
  // Two tighter patterns used together, below: an object that carries BOTH an
  // id-shaped key and a demo flag is an account record, and it says which KIND
  // of account the id belongs to. That is the one fact the cookie never
  // carries and the whole reason this step exists.
  "var ID=/^(id|uid|user_?id|account_?id)$/i;" +
  "var DM=/^is_?demo$/i;" +
  "function num(x){var n=parseInt(x,10);" +
  "return(n>99999&&n<1e13&&String(n)===String(x).trim())?n:0}" +
  "function keep(x){var n=num(x);if(n)d[n]=1}" +
  // Report one id to the panel as an image request. Cross-origin, no CORS, no
  // preflight, and — unlike a navigation — it leaves this page alive. Only
  // digits are in that URL; the cookie goes the other way, in a fragment.
  //
  // Three ways, because the obvious one does not work. Chrome 143 refuses any
  // network request from a public page to 127.0.0.1 outright — "Permission was
  // denied for this request to access the local address space" — no preflight,
  // no prompt, nothing this page can do about it. Measured, not assumed: the
  // image below fails exactly that way on a real browser.
  //
  //   1. postMessage to the panel tab this bookmark opened. Not a network
  //      request at all, so none of that applies. Silent and instant.
  //   2. The image, for when the panel is not across an address-space
  //      boundary — an older browser, or the panel reached over the LAN.
  //   3. A box on this page to click. A click carries user activation, and a
  //      window it opens is a top-level navigation, which is allowed where
  //      every quiet route is not. This is the one that always works, so it is
  //      always offered rather than kept for a failure nothing can detect.
  "function beam(n,f){if(!n||sent[n+':'+f])return;sent[n+':'+f]=1;" +
  "if(!flushed){queue.push([n,f]);return}" +
  "try{if(w&&!w.closed)w.postMessage(JSON.stringify({po_uid:n,demo:f}),O)}catch(e){}" +
  "try{new Image().src=O+'/uid?id='+n+'&demo='+f+'&r='+Math.random()}catch(e){}}" +
  // Ids found by the scrape are held back for a few seconds. They also travel
  // in the fragment, and the panel cannot try an id until the cookie it goes
  // with has arrived — so beaming them the instant they are found just makes
  // the panel say "an id, but no cookie yet" and log a line that reads like a
  // fault. Anything the listener catches later arrives long after this.
  "setTimeout(function(){flushed=1;var q=queue;queue=[];" +
  "q.forEach(function(p){sent[p[0]+':'+p[1]]=0;beam(p[0],p[1])})},4000);" +
  // A visited set and a node budget, because this now walks `window` too. A
  // live trading page has cyclic references everywhere (parent, ownerDocument,
  // Vue's $root) and without both of these the bookmark locks the tab up.
  "var seen=new Set();var budget=30000;" +
  "function walk(v,z){if(!v||typeof v!=='object'||z>4||budget<0)return;" +
  // Never descend into the DOM. window.document alone would drag in every node
  // on the page, spend the whole budget and find nothing: ids live in the
  // app's state, not its markup.
  "if(v instanceof Node||v===window||seen.has(v))return;seen.add(v);" +
  // f and ids are this object's own: is it an account record, and which
  // account. Collected across the whole loop because the flag and the id can
  // be in either order.
  "var f=-1,ids=[];" +
  "for(var k in v){if(--budget<0)return;" +
  "try{if(K.test(k))keep(v[k]);" +
  "if(DM.test(k))f=(v[k]===1||v[k]==='1'||v[k]===true||v[k]==='true')?1:0;" +
  "if(ID.test(k)){var q=num(v[k]);if(q)ids.push(q)}" +
  "walk(v[k],z+1)}catch(e){}}" +
  "if(f>=0)ids.forEach(function(n){d[n]=1;beam(n,f)})}" +
  "[localStorage,sessionStorage].forEach(function(s){" +
  "for(var i=0;i<s.length;i++){try{var k=s.key(i),r=s.getItem(k);" +
  "if(K.test(k))keep(r);" +
  "if(r&&(r.charAt(0)==='{'||r.charAt(0)==='['))walk(JSON.parse(r),0)" +
  "}catch(e){}}});" +
  // Their other cookies, and the app's own state on `window`. The first search
  // read localStorage and sessionStorage only, found nothing at all on the
  // real site, and the panel then blamed the cookie for it. A framework keeps
  // the logged-in profile in memory — __NUXT__, a Vue store, whatever they use
  // — and never has to write it down anywhere this could see.
  "try{document.cookie.split('; ').forEach(function(p){var i=p.indexOf('=');" +
  "if(i>0&&K.test(p.slice(0,i)))keep(decodeURIComponent(p.slice(i+1)))})}catch(e){}" +
  "try{for(var g in window){try{if(K.test(g))keep(window[g]);" +
  "walk(window[g],0)}catch(e){}}}catch(e){}" +
  // Whatever a previous run's listener wrote down.
  "try{['demo','real'].forEach(function(t){" +
  "var v=localStorage.getItem('pobot_account_'+t);" +
  "if(v){keep(v);beam(num(v),t==='demo'?1:0)}})}catch(e){}" +

  // The cookie goes to the panel in a fragment, which no browser sends to a
  // server; the panel page in that tab posts it properly.
  //
  // In a SECOND tab, and the handle is kept. Navigating THIS tab — which is
  // what this used to do — is what stopped every listener below from ever
  // seeing anything: they were installed and destroyed in the same instant.
  // The handle is also the only channel back to the panel that Chrome does not
  // treat as a public page reaching into the local network.
  //
  // If the browser blocks the popup there is nothing left but to navigate, and
  // lose them again — but then the page never got to watch anyway.
  // The trailing |2 is the bookmark's version. Updating the bot does NOT
  // update a bookmark — the JavaScript was copied into Chrome the day it was
  // made and stays exactly as it was — and an old one still sends a perfectly
  // good cookie, so nothing looks wrong. It just quietly cannot do any of the
  // watching below, which is the entire fix. Saying so is only possible if it
  // says which version it is.
  "var P=O+'/hook#'+encodeURIComponent(c)+'|'+" +
  "Object.keys(d).slice(0,8).join(',')+'|2';" +
  "try{w=window.open(P,'_blank')}catch(e){}" +
  "if(!w){location.href=P;return}" +

  // A panel of its own, on Pocket Option's page.
  //
  // Everything from here on happens on THIS tab, seconds or minutes after the
  // click, and the bot's log is in a different window. Without something on
  // screen here there is no way to tell "switch your balance and I will catch
  // it" from "nothing happened".
  "var B=document.getElementById('pobot_box')||document.createElement('div');" +
  "B.id='pobot_box';" +
  "B.style.cssText='position:fixed;z-index:2147483647;top:12px;right:12px;" +
  "width:290px;background:#11161f;color:#e6edf3;font:14px/1.45 system-ui," +
  "Arial,sans-serif;border:1px solid #2a3342;border-radius:10px;" +
  "padding:12px 14px;box-shadow:0 8px 28px rgba(0,0,0,.45)';" +
  // Built as text nodes, never innerHTML: some of this is a number that came
  // off somebody else's page.
  "function say(h,t,ok){B.textContent='';" +
  "var a=document.createElement('div');" +
  "a.style.cssText='font-weight:700;margin-bottom:6px;color:'+(ok?'#22c55e':'#e6edf3');" +
  "a.textContent=h;var b=document.createElement('div');" +
  "b.style.color='#9fb0c4';b.textContent=t;" +
  "var x=document.createElement('div');x.textContent='close';" +
  "x.style.cssText='margin-top:10px;font-size:12px;color:#6b7a8d;cursor:pointer';" +
  "x.onclick=function(ev){ev.stopPropagation();B.remove()};" +
  "B.appendChild(a);B.appendChild(b);B.appendChild(x)}" +
  "say('Cookie sent to the bot','Now click your balance at the TOP RIGHT of " +
  "this page and choose the Demo account. Leave this tab open — I am watching " +
  "for it, and the bot will pick it up on its own.',0);" +
  "try{document.body.appendChild(B)}catch(e){}" +

  // And the listener itself.
  //
  // Pocket Option's page has to send the id — it is in the auth frame on their
  // WebSocket, the one place it is guaranteed to exist — and it sends a fresh
  // one when the account switcher is used, because the socket authenticates
  // per account. That frame is the ONLY place the practice id appears while
  // the real balance is the one on screen.
  //
  // Only digits are kept. The same frames carry the session, and that is never
  // written down, beamed or stored.
  "function got(n,f){n=num(n);if(!n)return;" +
  "try{localStorage.setItem('pobot_account_'+(f?'demo':'real'),String(n))}catch(e){}" +
  "if(f&&!sent[n+':1']){beam(n,f);" +
  "say('Found your practice account — '+n,'Sent to the bot: go back to the bot " +
  "tab and watch the log at the bottom. If nothing appears there within about " +
  "ten seconds, click this box and it will go through for certain.',1);" +
  "B.style.cursor='pointer';B.onclick=function(){" +
  "window.open(O+'/uid?id='+n+'&demo=1&close=1','_blank')};return}" +
  "beam(n,f)}" +
  // An id and a demo flag inside the same object, in either order. Bounded
  // between the two so a match cannot span a whole frame full of prices and
  // pair up an id with somebody else's flag.
  "function scan(x){try{" +
  "if(x.indexOf('uid')<0&&x.indexOf('emo')<0)return;var m;" +
  "var r1=/\"(?:uid|id|user_id|account_id)\"\\s*:\\s*\"?(\\d{5,13})\"?" +
  "[^{}]{0,80}?\"is_?[Dd]emo\"\\s*:\\s*\"?(\\d|true|false)/g;" +
  "while((m=r1.exec(x)))got(m[1],(m[2]==='1'||m[2]==='true')?1:0);" +
  "var r2=/\"is_?[Dd]emo\"\\s*:\\s*\"?(\\d|true|false)\"?" +
  "[^{}]{0,80}?\"(?:uid|id|user_id|account_id)\"\\s*:\\s*\"?(\\d{5,13})/g;" +
  "while((m=r2.exec(x)))got(m[2],(m[1]==='1'||m[1]==='true')?1:0);" +
  "}catch(e){}}" +
  // Outgoing frames: the auth frame this page sends.
  "try{if(!WebSocket.prototype.__po){var S=WebSocket.prototype.send;" +
  "WebSocket.prototype.send=function(x){" +
  "try{if(typeof x==='string')scan(x)}catch(e){}return S.apply(this,arguments)};" +
  "WebSocket.prototype.__po=1}}catch(e){}" +
  // Incoming frames on sockets opened from now on. Their reply to auth, and
  // the balance updates that follow, name the account too — and a reconnect
  // happens by itself, so this can find it without anything being clicked.
  "try{if(!window.__poRx){var W=window.WebSocket;" +
  "var N=function(){var s=new(Function.prototype.bind.apply(" +
  "W,[null].concat([].slice.call(arguments))))();" +
  "try{s.addEventListener('message',function(e){" +
  "try{if(typeof e.data==='string')scan(e.data)}catch(z){}})}catch(z){}return s};" +
  "N.prototype=W.prototype;N.CONNECTING=0;N.OPEN=1;N.CLOSING=2;N.CLOSED=3;" +
  "window.WebSocket=N;window.__poRx=1}}catch(e){}})()";

function bmClick(ev){
  // Clicking it here would run it against this page, which has no PO cookie —
  // a confusing dead end. Say what to do with it instead.
  ev.preventDefault();
  toast("Don't click it here — this page has no Pocket Option cookie.\n\n" +
        "Drag it up onto the bookmarks bar (Ctrl+Shift+B shows the bar), then " +
        "click it while you are on pocketoption.com and logged in.", true);
}

async function copyBookmarklet(){
  try{
    await navigator.clipboard.writeText(BOOKMARKLET);
    document.getElementById('bm-alt').style.display = 'block';
    toast('Copied.');
  }catch(e){
    toast('This browser would not let me copy it. Drag the blue button onto ' +
          'the bookmarks bar instead — same result.', true);
  }
}

function hideToast(){
  const t = document.getElementById('toast');
  clearTimeout(t._h);
  t.className = 'toast';
}

function toast(msg, bad){
  const t = document.getElementById('toast');
  document.getElementById('toastmsg').textContent = msg;
  t.className = 'toast show' + (bad ? ' bad' : '');
  clearTimeout(t._h);
  // Failures wait to be dismissed; only good news disappears on its own. An
  // error here is usually a paragraph explaining what to do differently, and
  // 2.6 seconds is not long enough to read one — never mind act on it.
  document.getElementById('toastx').style.display = bad ? 'inline-block' : 'none';
  if (!bad) t._h = setTimeout(() => { t.className = 'toast'; }, 2600);
}

// ------------------------------------------------------------ diagnostics
//
// navigator.clipboard needs a secure context. http://localhost counts as one,
// but http://penguin.linux.test:8080 — the address that works when localhost
// does not, so the address he ends up on — does NOT. So the textarea is filled
// and selected FIRST and shown either way; the clipboard call is the shortcut,
// not the mechanism. A copy button that silently did nothing on the fallback
// address would be worse than no button.
async function copyDiag(){
  const box = document.getElementById('diagbox');
  const hint = document.getElementById('diaghint');
  try{
    const r = await fetch('/api/diagnostics', {headers: headers()});
    if (r.status === 401){ askPass(); return; }
    box.value = await r.text();
  }catch(e){
    toast('Could not read the report — is the bot still running?', true);
    return;
  }
  box.style.display = 'block';
  hint.style.display = 'block';
  box.focus();
  box.select();
  let done = false;
  try{
    if (navigator.clipboard && window.isSecureContext){
      await navigator.clipboard.writeText(box.value);
      done = true;
    }
  }catch(e){ /* fall through to the selection, which is already made */ }
  if (!done){ try{ done = document.execCommand('copy'); }catch(e){} }
  toast(done ? 'Report copied — paste it to me on Freelancer.'
             : 'Report is in the box below, already selected. Press Ctrl+C.', !done);
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
    return j;
  }catch(e){ toast('Connection to the bot was lost.', true); }
}

function askPass(){
  const p = prompt('Control panel password:');
  if (p !== null){ pass = p; localStorage.setItem('pobot_pass', p); refresh(); }
}

function saveSettings(){
  // The watchlist box wins over the single Asset box when it has anything in
  // it, and its first entry becomes the main pair. Sending both and letting the
  // server guess which one the user meant is how they end up trading a pair
  // that is on neither list.
  const typed = document.getElementById('f-pairs').value.trim();
  const body = {
    action:'settings',
    strategy: document.getElementById('f-strategy').value,
    asset:    document.getElementById('f-asset').value,
    stake:    document.getElementById('f-stake').value,
    expiry:   document.getElementById('f-expiry').value,
    timeframe:document.getElementById('f-timeframe').value,
    loss_cap: document.getElementById('f-loss').value,
    profit_target: document.getElementById('f-target').value,
    auto_restart: document.getElementById('f-restart').checked,
    mg_enabled: document.getElementById('f-mg').checked,
    mg_mult:  document.getElementById('f-mgmult').value,
    mg_steps: document.getElementById('f-mgsteps').value
  };
  if (typed){
    body.pairs = typed;
    body.asset = typed.split(',')[0].trim();
    // Payouts learnt from the live table, for whichever of those pairs appeared
    // in it. Without these the panel would score a mixed watchlist against one
    // pair's break-even, which is the wrong line for every other pair.
    const known = {};
    (typed.replace(/\n/g, ',').split(',')).forEach(function(p){
      p = p.trim();
      if (p && payoutBySymbol[p] !== undefined) known[p] = payoutBySymbol[p];
    });
    if (Object.keys(known).length) body.payouts = known;
  }
  // Hand the box back to the poll only once the server has ACCEPTED the list.
  // On a rejection — "that is 15 pairs, trim it to 12" — the list must stay on
  // screen to be trimmed. Clearing the flag there would revert the box to the
  // old pairs and throw away the work the message just asked them to fix.
  cmd(body).then(function(res){ if (res && res.ok) pairsDirty = false; });
}

// ---- live payouts -------------------------------------------------------
// The payout decides the win rate you must beat: 100 / (100 + payout). It
// changes day to day, so it is fetched, never hard-coded into the page.
// Symbol -> payout, remembered from the last table load so the watchlist can be
// scored pair by pair instead of assuming they all pay the same. They do not:
// the spread on any given day runs from about 92% down to 52%.
var payoutBySymbol = {};

// True while the watchlist box holds edits that have not been saved yet, so the
// poll leaves it alone. Cleared once the server has accepted them.
var pairsDirty = false;

async function loadPayouts(forWatchlist){
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
    payoutBySymbol = {};
    rows.forEach(function(a){ payoutBySymbol[a.symbol] = a.payout; });
    note.textContent = rows.length + ' pairs open. "Need" is the win rate that just ' +
                       'breaks even at that payout — lower is easier.';
    document.getElementById('payout-table').innerHTML =
      '<tr><th>Payout</th><th>Need</th><th>Pair</th><th></th><th></th></tr>' +
      rows.map(a =>
        '<tr><td><b>' + a.payout + '%</b></td><td>' + a.breakeven.toFixed(1) + '%</td>' +
        '<td>' + a.name + '</td>' +
        '<td><button class="ghost" onclick="pickAsset(\'' + a.symbol + '\',' + a.payout +
        ')">Use ' + a.symbol + '</button></td>' +
        '<td><button class="ghost" onclick="addPair(\'' + a.symbol +
        '\')">+ watch</button></td></tr>'
      ).join('');
    if (forWatchlist) fillWatchlist(rows);
  }catch(e){ note.textContent = 'Could not reach the bot.'; }
}

// Fill the watchlist with the best-paying open pairs.
//
// Sorted by payout because that is the setting with the largest effect on
// whether a given win rate makes money: at 92% you need 52.1% wins, at 52% you
// need 65.8%. Padding the list out with cheap pairs would collect trades faster
// while making them harder to win, which is the opposite of the point.
function fillWatchlist(rows){
  const top = rows.filter(a => a.payout >= 80).slice(0, 10);
  if (!top.length){
    toast('No pair is paying 80% or better right now — nothing worth adding.', true);
    return;
  }
  document.getElementById('f-pairs').value = top.map(a => a.symbol).join(', ');
  pairsDirty = true;
  toast('Filled in ' + top.length + ' pairs paying ' + top[top.length-1].payout +
        '% or better. Press Save settings to use them.');
}

function addPair(symbol){
  const box = document.getElementById('f-pairs');
  const have = box.value.split(',').map(s => s.trim()).filter(Boolean);
  if (have.indexOf(symbol) >= 0){ toast(symbol + ' is already on the list.'); return; }
  have.push(symbol);
  box.value = have.join(', ');
  pairsDirty = true;
  toast(symbol + ' added — now ' + have.length + '. Press Save settings to use them.');
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
  // "Connecting…" is a promise. Only say it when something really is on its way
  // in: a rejected cookie is never going to connect, and practice mode is not
  // connecting to anything at all.
  // Practice is checked BEFORE s.connected, not after. A running practice
  // session sets connected=true — it is connected to the simulator — and this
  // pill then read "Connected" in green with no account anywhere near it.
  // Every place this bot can imply a real connection it does not have has to be
  // shut, not just the ones that have already burned someone.
  pc.textContent = s.mode === 'PRACTICE' ? 'No account'
    : (s.token_error ? 'Cookie rejected'
    : (s.connected ? 'Connected'
    : (s.has_token ? 'Connecting…' : 'No token')));
  // Green is for a real account only, or the colour says "connected" while the
  // words say "No account" and the colour is what gets read at a glance.
  pc.className = 'pill' + (s.connected && s.mode !== 'PRACTICE' ? ' on' : '');

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
      'here has proven it can do that yet (0 of 40 tested at this payout — ' +
      'see docs/RESULTS.md). ' +
      'Set PO_DEMO=true in your .env to practise instead.';
  } else {
    lb2.style.display = 'none';
  }

  // Updating the files does not update a bot that is already running. Without
  // this, a fixed bug still looks unfixed and the page gives no clue why.
  const sb = document.getElementById('stalebar');
  // This tab is older than the bot answering it. Checked FIRST, because the
  // fix is different and this is the one that hands out a broken bookmark: the
  // blue button's address was built by the JavaScript in this page, so a tab
  // left open across an update gives you the previous version's bookmark and
  // nothing anywhere says so. He did every step right and it still failed.
  const pageStale = PAGE_VERSION && s.version && PAGE_VERSION !== s.version;
  document.getElementById('bm-stale').style.display = pageStale ? 'block' : 'none';
  if (pageStale){
    sb.style.display = 'block';
    sb.innerHTML = '<b>This page is older than the bot.</b> The bot has been ' +
      'updated since this tab was opened (it is on ' + s.version + ', this page ' +
      'came from ' + PAGE_VERSION + '), so everything on it — including the blue ' +
      '"Send PO cookie to bot" button — is the previous version. ' +
      '<b>Reload before you use it.</b>' +
      '<br><br><button onclick="location.reload(true)">Reload this page</button> ' +
      '&nbsp;or press <b>Ctrl+R</b>.';
  } else if (s.stale_code){
    sb.style.display = 'block';
    // These instructions were written before start.sh and the launcher icon
    // existed, and still said "press Ctrl+C in the terminal window". Telling
    // someone to do something that no longer applies, in the one banner that
    // appears when they are already stuck, is worse than saying nothing.
    sb.innerHTML = '<b>This page is running the OLD code.</b> You downloaded the ' +
      'update, but the bot was already running and kept the old version in memory. ' +
      'Nothing you change here will behave like the new version until you restart it.' +
      '<br><br>Run this one line in the terminal, then press <b>Ctrl+Shift+R</b> here:' +
      '<br><code>cd ~/pocket-option-telegram-bot &amp;&amp; bash stop.sh &amp;&amp; bash open_panel.sh</code>';
  } else {
    sb.style.display = 'none';
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
    // checks is still 0 here, which happens either because the first candle has
    // not closed yet OR because no price data is arriving at all. The second is
    // a fault and must not hide behind the first's reassuring wording.
    watch.textContent = s.last_reason
      ? 'Not trading — ' + s.last_reason
      : 'Watching — waiting for the first candle to close.';
  } else {
    watch.style.display = 'none';
  }

  // Connection card. Says what IS set without ever restating the secret.
  const cs = document.getElementById('conn-state');
  if (s.token_error){
    // A cookie IS saved, so the branch below would have called it "connected
    // details" and — because practice sets connected=true — followed it with
    // "Pocket Option is responding", about an account the bot never reached.
    cs.textContent = 'The saved cookie was refused, so no account is connected. ' +
      'Send a fresh one with the one-click button above.';
  } else if (s.session_set){
    // 'account 0' would read as an account called zero. It means no id was
    // needed, which is a fine outcome, so say that instead.
    cs.textContent = 'Connected details are saved (' +
      (s.uid ? 'account ' + s.uid : 'no account id needed') + ', ' +
      (s.demo ? 'demo balance' : 'REAL money') + '). ' +
      (s.mode === 'PRACTICE'
         ? 'Not in use right now — this run is practice, on pretend money.'
         : (s.connected ? 'Pocket Option is responding.'
                        : 'Not responding yet — if this does not clear, the cookie has expired; paste a fresh one.'));
  } else {
    cs.textContent = 'No account connected yet — the bot is on practice data. ' +
      'Paste your cookie below to trade on your own Pocket Option balance.';
  }
  // Deliberately NOT prefilled from the saved account. The box says "leave
  // blank" and the panel finds the id itself, so filling it in would be an
  // instruction fighting itself — and the id in use is already stated in the
  // line just above.
  setField('f-demo', s.demo);

  const pnl = document.getElementById('s-pnl');
  pnl.textContent = money(s.pnl);
  pnl.className = 'v ' + (s.pnl > 0 ? 'pos' : s.pnl < 0 ? 'neg' : '');
  // Always show the win rate next to the rate that breaks even, and say when
  // the sample is still too small to mean anything. A bare percentage after a
  // handful of trades is the easiest number in trading to fool yourself with.
  const decided = s.wins + s.losses;
  const v = s.verdict || {state:'none'};
  const wr = document.getElementById('s-wr');
  wr.textContent = decided ? s.winrate.toFixed(0) + '%' : '—';
  // Green ONLY for a win rate that is provably ahead. It used to go green the
  // moment the raw percentage cleared break-even, so 53% after 100 trades —
  // which is statistically indistinguishable from a losing bot — showed as a
  // pass. Colour is the part people read; it may not say more than the maths
  // supports.
  wr.className = 'v ' + (v.state === 'ahead' ? 'pos' :
                         v.state === 'behind' ? 'neg' : '');
  const be = document.getElementById('s-be');
  // With a watchlist the pairs do not share a break-even, and this win rate
  // pools all of them — so it is scored against the HARDEST pair being watched
  // and says so. Averaging the payouts would quietly move the pass mark down.
  const multi = s.pairs && s.pairs.length > 1;
  const bar = (s.worst_payout !== undefined ? s.worst_payout : s.payout);
  const need = 'need ' + s.breakeven.toFixed(1) + '% to break even at ' + bar +
               '% payout' + (multi ? ' (the worst of your ' + s.pairs.length +
                                     ' pairs — this rate covers them all)' : '');
  if (v.state === 'none') {
    be.textContent = need;
  } else if (v.state === 'ahead') {
    be.textContent = need + ' — ahead of it, and ' + v.n +
                     ' trades is enough to say so (worst case ' + v.lo + '%)';
  } else if (v.state === 'behind') {
    be.textContent = need + ' — behind it after ' + v.n +
                     ' trades (best case ' + v.hi + '%)';
  } else {
    // The usual state, and the one that must not read as encouraging. A
    // straddling interval means the record so far is equally consistent with a
    // winning bot and a losing one.
    be.textContent = need + ' — TOO CLOSE TO CALL after ' + v.n +
      ' trades. True rate is somewhere between ' + v.lo + '% and ' + v.hi +
      '%, and break-even is inside that.' +
      (v.need ? ' At this rate it would take about ' +
                v.need.toLocaleString() + ' trades to prove an edge.'
              : ' At this rate there is no edge to prove.');
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
  } else if (s.balance !== null && s.balance < 0){
    // Reproduced with a deliberately invalid token: the socket opens, then the
    // balance is -1.00 for ever and no candle ever arrives. A negative balance
    // is impossible on a real account — this is "login refused", not "no money".
    bal.className = 'v neg';
    bal.textContent = 'not logged in';
    balSub.textContent = 'Pocket Option is not accepting your session cookie, so ' +
      'there is no balance and no price data. Sign in at pocketoption.com, copy a ' +
      'fresh ci_session cookie into the box above — and do NOT log out afterwards, ' +
      'because logging out cancels the cookie you just pasted.';
  } else if (s.balance !== null && s.balance === 0){
    bal.className = 'v neg';
    balSub.textContent = 'this account is empty — every trade will be rejected ' +
      'until it is topped up. A demo balance can be refilled on pocketoption.com.';
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
  // Only fill the watchlist box once more than one pair is actually being
  // watched. Echoing the single pair into it would make the box look like a
  // setting the user had chosen, and then the next Save would treat it as one.
  // ...but not while the box holds unsaved edits. The "skip it while focused"
  // rule that protects every other field is not enough here: the two buttons
  // that fill this box (Fill from best-paying pairs, + watch) leave the focus
  // elsewhere, so the next 2-second poll would silently wipe a list the user
  // had just assembled and the page would look like it had ignored the click.
  if (!pairsDirty)
    setField('f-pairs', (s.pairs && s.pairs.length > 1) ? s.pairs.join(', ') : '');
  setField('f-stake', s.stake);
  setField('f-expiry', s.expiry);
  setField('f-timeframe', s.timeframe);
  // Practice settles against replayed candles, so an option can only be a
  // whole number of them. 500s against 5-minute bars is really a 600s option,
  // and a win rate read off that is a win rate for a setting nobody chose.
  // Only practice rounds; on a real account Pocket Option honours the number.
  const expNote = document.getElementById('f-expiry-note');
  const repCandle = s.practice_candle || 0;
  const repSpan = repCandle ? Math.max(1, Math.round(s.expiry / repCandle)) : 0;
  if (s.mode === 'PRACTICE' && repCandle && repSpan * repCandle !== s.expiry){
    expNote.style.display = 'block';
    expNote.innerHTML = 'In practice this is really a <b>' + (repSpan * repCandle) +
      's</b> option — replayed history comes in ' + repCandle + 's candles and an ' +
      'option has to be a whole number of them. Set the expiry to a multiple of ' +
      repCandle + ' and the panel will match what is being scored. On a real ' +
      'account your ' + s.expiry + 's is used as-is.';
  } else {
    expNote.style.display = 'none';
  }
  setField('f-loss', s.loss_cap);
  setField('f-target', s.profit_target);
  setField('f-restart', s.auto_restart);
  setField('f-mg', s.mg_enabled);
  setField('f-mgmult', s.mg_mult);
  setField('f-mgsteps', s.mg_steps);

  const tb = document.getElementById('t-body');
  document.getElementById('t-empty').style.display = s.trades.length ? 'none' : 'block';
  tb.innerHTML = s.trades.map(t => `
    <tr>
      <td>${clock(t.ts)}</td>
      <td>${escapeHtml(t.asset || s.asset || '')}</td>
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

// Dragging an anchor to the bookmarks bar copies its href, so the href has to
// be the real thing — a placeholder here would bookmark the placeholder.
document.getElementById('bm-link').href = BOOKMARKLET;

refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# Where the bookmarklet lands.
#
# The manual route — DevTools, find the cookie, select exactly its value, copy,
# switch tabs, paste — failed four times in a row for the person this was
# written for, in a different place each time: the value cell would not select,
# Ctrl+A took the whole table, the paste went into the Console's filter box
# instead of its prompt. Every one of those was answered by explaining the step
# again, and every one failed again.
#
# So the step is gone. A bookmarklet on pocketoption.com reads document.cookie
# (verified: Pocket Option sets no HttpOnly flag on ci_session, so script can
# see it) and navigates here with the value in the URL fragment. Fragments are
# never sent to a server, so it arrives in this page and nowhere else; the page
# posts it to the panel over the same authenticated call the form uses, then
# wipes it out of the address bar and the history entry.
#
# No selecting, no copying, no second window, and no way to copy half of it.
# --------------------------------------------------------------------------
UID_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Account id received</title>
<style>
  body{background:#0d1117;color:#e6edf3;font:16px/1.5 system-ui,Arial,sans-serif;
       display:grid;place-items:center;height:100vh;margin:0;text-align:center}
  .box{max-width:440px;padding:26px 30px;background:#11161f;
       border:1px solid #2a3342;border-radius:14px}
  h1{font-size:19px;margin:0 0 10px;color:#22c55e}
  p{margin:0 0 12px;color:#9fb0c4;font-size:14px}
  a{display:inline-block;margin-top:6px;background:#1d2430;border:1px solid #2a3342;
    color:#e6edf3;border-radius:9px;padding:10px 16px;text-decoration:none}
</style></head><body><div class="box">
<h1>Got it — that is your practice account</h1>
<p>The bot is checking it now. Go back to the control panel and watch the log
   at the bottom; it will say whether Pocket Option accepted it.</p>
<p>You can close this tab.</p>
<a href="/">Back to the control panel</a>
</div>
<script>setTimeout(function(){try{window.close()}catch(e){}}, 2500);</script>
</body></html>
"""

HOOK_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connecting your Pocket Option account…</title>
<style>
  body{margin:0;background:#0e1117;color:#e6edf3;display:flex;min-height:100vh;
       align-items:center;justify-content:center;padding:20px;
       font:16px/1.6 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
  .box{background:#161b24;border:1px solid #2a3342;border-radius:14px;
       padding:26px 28px;max-width:560px;width:100%}
  h1{font-size:19px;margin:0 0 14px}
  p{margin:0 0 12px}
  .muted{color:#8b97a8;font-size:14px}
  .ok{color:#22c55e}
  .bad{color:#ef4444}
  a.btn{display:inline-block;margin-top:10px;background:#1d2430;border:1px solid #2a3342;
        color:#e6edf3;border-radius:9px;padding:10px 16px;text-decoration:none}
</style>
</head>
<body>
<div class="box">
  <h1 id="head">Connecting your Pocket Option account…</h1>
  <p id="msg" class="muted">Reading the cookie the bookmark just handed over.</p>
  <p id="len" class="muted"></p>
  <a class="btn" href="/">Back to the control panel</a>
</div>
<script>
// Same origin as the panel, so the password it stored is right here.
const pass = localStorage.getItem('pobot_pass') || '';

// This is the tab the bookmarklet opened, so it is the one holding the handle
// the Pocket Option page posts account ids to — and on several of the screens
// below it is where that tab STAYS, rather than moving on to the panel. The
// listener has to be here as well as there or the id lands in a tab that is
// not listening. Same rules: digits only, practice only.
window.addEventListener('message', ev => {
  let m;
  try{ m = JSON.parse(String(ev.data)); }catch(e){ return; }
  if (!m || typeof m.po_uid === 'undefined') return;
  const uid = parseInt(m.po_uid, 10);
  if (!(uid > 99999 && uid < 1e13)) return;
  fetch('/uid?id=' + uid + '&demo=' + (m.demo ? 1 : 0)).catch(() => {});
});

function say(head, msg, cls){
  document.getElementById('head').textContent = head;
  const m = document.getElementById('msg');
  m.textContent = msg;
  m.className = cls || 'muted';
}

async function go(){
  const raw = location.hash.slice(1);
  // Out of the address bar and out of the back button before anything else:
  // this is a session secret and it should not sit in either.
  history.replaceState(null, '', location.pathname);

  if (!raw){
    say('Nothing was handed over',
        'Open pocketoption.com, log in, and click the bookmark from that tab. ' +
        'Opening this address directly cannot work — the cookie comes from the ' +
        'Pocket Option page, not from here.', 'bad');
    return;
  }

  // The bookmark appends "|<id>,<id>,…" — account-id candidates it scraped off
  // the Pocket Option page. The demo balance and the real balance have
  // different ids and the cookie carries neither, so without these the only
  // way to reach a demo account whose id we do not have is DevTools, which
  // cost this project two days. Integers only ever cross: the scrape reads
  // whole objects but keeps nothing but numbers.
  // Chrome leaves the '|' alone in a fragment (checked against a real browser
  // run), but it is a character browsers are entitled to percent-encode and
  // this one character is the whole difference between "your bookmark is out of
  // date" and "your account id is not on the page" — two completely different
  // instructions. Accept either form rather than bet the message on it.
  const sep = /\||%7C/i.exec(raw);
  const bar = sep ? sep.index : -1;
  const session = decodeURIComponent(bar === -1 ? raw : raw.slice(0, bar));
  // cookie | ids | version.  The version is what a bookmark made before the
  // rewrite does not have, and a bookmark without it cannot watch for the
  // practice account — see `ver` below.
  const rest = bar === -1 ? [] :
    raw.slice(bar + sep[0].length).split(/\||%7C/i);
  const uids = (rest[0] || '').split(',')
       .filter(x => /^[0-9]{6,13}$/.test(x));
  const ver = rest[1] || '';
  // The length is the one number worth showing. A whole cookie is several
  // hundred characters, so a short one says "half of it came through" rather
  // than "your login expired" — two failures that otherwise look identical.
  document.getElementById('len').textContent =
    'Cookie received: ' + session.length + ' characters.';

  // Updating the bot does NOT update a bookmark: the JavaScript was copied into
  // Chrome when it was made and stays exactly as it was. An old bookmark still
  // sends the cookie, so nothing looks broken — it just silently cannot do the
  // part that matters, and the search goes on failing for a reason nobody can
  // see. Two generations of that now: the first sent no account ids at all,
  // the second navigated its own tab away and so could never watch for the
  // practice account. Both are told apart from a current one here and nowhere
  // else, because from the server's side they are identical.
  const stale = bar === -1 || ver !== '2';

  try{
    const h = {'Content-Type':'application/json'};
    if (pass) h['X-Auth'] = pass;
    const r = await fetch('/api/cmd', {
      method:'POST', headers:h,
      // No account id on purpose — the panel tries every combination and keeps
      // whichever one Pocket Option answers on.
      // `stale` has to be sent explicitly. This page is always served by the
      // CURRENT server, so it always posts a uids field — an out-of-date
      // bookmark is invisible from the server side, and only the missing
      // separator in the fragment gives it away. Deciding it here is the only
      // place it can be decided.
      body: JSON.stringify({action:'connect', session:session, uid:'', demo:true,
                            uids:uids, stale:stale, via:'bookmarklet'})
    });
    if (r.status === 401){
      say('The panel wants its password',
          'Go back to the control panel, enter the password when it asks, then ' +
          'click the bookmark again.', 'bad');
      return;
    }
    const res = await r.json();
    if (res.ok && stale){
      // Deliberately NOT auto-returning to the panel here, and deliberately
      // not styled as success. The cookie did arrive, so the run continues —
      // but this is the difference between a search that can find the demo
      // account and one that cannot, and it is invisible from the log.
      say('Cookie accepted — but your bookmark is out of date',
          (res.message || '') + ' Updating the bot does not update a bookmark: ' +
          'yours still has the JavaScript from the day you made it. The current ' +
          'one stays on the Pocket Option page after you click it and watches ' +
          'for your practice account, which is the part that has been failing. ' +
          'Go back to the panel, drag the blue button onto your bookmarks bar ' +
          'again, DELETE the old bookmark so you cannot click it by mistake, ' +
          'and use the new one on pocketoption.com.', 'bad');
    } else if (res.ok && uids.length === 0){
      // Current bookmark, ran properly, and still found no account id on the
      // page. Worth its own screen: the cookie is fine and the search that is
      // about to run cannot succeed, and those two facts together look
      // identical in the log to an expired cookie. It sent people off to fetch
      // a fresh cookie that failed the same way.
      say('Cookie received — but not your account id yet',
          'Pocket Option needs both, and the id was not anywhere the bookmark ' +
          'could read it. It has just switched on a listener on that page, so: ' +
          'go back to the Pocket Option tab, click the Demo/Real switch once ' +
          '(or leave the tab open for a minute), then click the bookmark again. ' +
          'The second click is the one that finds it. Nothing is wrong with ' +
          'your login.', 'bad');
    } else if (res.ok){
      say('Cookie accepted', (res.message || '') +
          ' Sending you back to the panel — watch the log at the bottom.', 'ok');
      setTimeout(() => { location.href = '/'; }, 4000);
    } else {
      say('Pocket Option would not take that cookie', res.message || 'No reason given.', 'bad');
    }
  }catch(e){
    say('The panel is not answering',
        'The terminal window that runs the bot has to stay open. If it was ' +
        'closed, start it again with: bash run.sh --paper', 'bad');
  }
}
go();
</script>
</body>
</html>
"""
