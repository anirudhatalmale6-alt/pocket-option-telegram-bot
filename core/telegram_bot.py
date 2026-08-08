"""
Telegram control interface.

Wraps python-telegram-bot (v20+, async). Provides:
  * /start /stop  -> flip the master run switch
  * /status       -> full snapshot (config + PnL)
  * /stake /expiry /asset  -> quick single-value changes
  * /set a.b.c value       -> change ANY nested config field live
  * /martingale on|off ... -> martingale controls
  * inline buttons for Start / Stop / Status
Only the authorised chat id (TELEGRAM_CHAT_ID) may control the bot.

The bot pushes async notifications from the trader via `send()`.
"""

from __future__ import annotations

from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from .config import BotConfig


HELP = (
    "Pocket Option Bot commands:\n"
    "/start – start trading\n"
    "/stop – pause trading\n"
    "/status – show settings + PnL\n"
    "/stake <amount> – base stake\n"
    "/expiry <seconds> – expiry (e.g. 180 = 3m)\n"
    "/asset <symbol> – e.g. EURUSD_otc\n"
    "/strategy pullback|linreg|ema|donchian|custom|alligator|rsi – switch entry model\n"
    "/martingale on|off [mult] [maxsteps]\n"
    "/risk <daily_loss_cap> [profit_target]\n"
    "/set <field.path> <value> – advanced (e.g. /set strategy.rsi_oversold 25)\n"
    "/reset – reset today's PnL + martingale\n"
    "/help – this message"
)


class TelegramInterface:
    def __init__(self, config: BotConfig):
        self.config = config
        self.app: Optional[Application] = None
        self._reset_cb = None  # set by main to reset the risk manager

    # -------------------------------------------------- auth helper
    def _authorised(self, update: Update) -> bool:
        want = str(self.config.telegram_chat_id).strip()
        if not want:
            return True  # no chat lock configured (dev mode)
        got = str(update.effective_chat.id) if update.effective_chat else ""
        return got == want

    async def _guard(self, update: Update) -> bool:
        if not self._authorised(update):
            await update.effective_message.reply_text("Unauthorised chat.")
            return False
        return True

    # -------------------------------------------------- keyboard
    @staticmethod
    def _kb() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("▶ Start", callback_data="start"),
            InlineKeyboardButton("⏸ Stop", callback_data="stop"),
            InlineKeyboardButton("📊 Status", callback_data="status"),
        ]])

    # -------------------------------------------------- commands
    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        self.config.running = True
        await update.effective_message.reply_text("▶ Trading STARTED.", reply_markup=self._kb())

    async def cmd_stop(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        self.config.running = False
        await update.effective_message.reply_text("⏸ Trading STOPPED.", reply_markup=self._kb())

    async def cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        await update.effective_message.reply_text(self._status_text(), reply_markup=self._kb())

    def _status_text(self) -> str:
        c = self.config
        mode = "DEMO" if c.po_demo else "LIVE"
        mg = c.martingale
        mg_txt = f"on x{mg.multiplier} max {mg.max_steps}" if mg.enabled else "off"
        return (
            f"State: {'RUNNING' if c.running else 'STOPPED'} ({mode})\n"
            f"Strategy: {c.strategy_mode}\n"
            f"Asset: {c.asset} | Expiry: {c.expiry_seconds}s | TF: {c.candle_timeframe}s\n"
            f"Stake: {c.risk.base_stake} | Martingale: {mg_txt}\n"
            f"Daily loss cap: {c.risk.daily_loss_cap} | Profit target: {c.risk.daily_profit_target}\n"
            f"Strategy: EMA {c.strategy.ema_fast}/{c.strategy.ema_slow}, "
            f"RSI {c.strategy.rsi_period} ({c.strategy.rsi_oversold}/{c.strategy.rsi_overbought}), "
            f"Stoch {c.strategy.stoch_k}/{c.strategy.stoch_d}"
        )

    async def cmd_stake(self, update: Update, ctx):
        if not await self._guard(update):
            return
        try:
            self.config.risk.base_stake = float(ctx.args[0])
            await update.effective_message.reply_text(f"Stake set to {self.config.risk.base_stake}")
        except (IndexError, ValueError):
            await update.effective_message.reply_text("Usage: /stake <amount>")

    async def cmd_expiry(self, update: Update, ctx):
        if not await self._guard(update):
            return
        try:
            self.config.expiry_seconds = int(ctx.args[0])
            await update.effective_message.reply_text(f"Expiry set to {self.config.expiry_seconds}s")
        except (IndexError, ValueError):
            await update.effective_message.reply_text("Usage: /expiry <seconds>")

    async def cmd_asset(self, update: Update, ctx):
        if not await self._guard(update):
            return
        try:
            self.config.asset = ctx.args[0]
            await update.effective_message.reply_text(f"Asset set to {self.config.asset}")
        except IndexError:
            await update.effective_message.reply_text("Usage: /asset <symbol>  e.g. EURUSD_otc")

    async def cmd_strategy(self, update: Update, ctx):
        if not await self._guard(update):
            return
        valid = ("pullback", "linreg", "ema", "donchian", "custom", "alligator", "rsi")
        if not ctx.args or ctx.args[0] not in valid:
            await update.effective_message.reply_text(
                "Usage: /strategy pullback|linreg|ema|donchian|custom|alligator|rsi\n"
                "custom = your ZigZag + Stochastic + Keltner setup.\n"
                "alligator = your Bill Williams Alligator + RSI setup.\n"
                "rsi = simple fast RSI reversal (RSI 10, for 30s candles).\n"
                "pullback = trend + RSI/Stoch dip entry; linreg = trend-line slope; "
                "ema = EMA trend; donchian = breakout."
            )
            return
        self.config.strategy_mode = ctx.args[0]
        await update.effective_message.reply_text(
            f"Strategy set to '{ctx.args[0]}'. Takes effect on the next candle."
        )

    async def cmd_martingale(self, update: Update, ctx):
        if not await self._guard(update):
            return
        args = ctx.args
        if not args or args[0] not in ("on", "off"):
            await update.effective_message.reply_text("Usage: /martingale on|off [multiplier] [maxsteps]")
            return
        mg = self.config.martingale
        mg.enabled = args[0] == "on"
        try:
            if len(args) >= 2:
                mg.multiplier = float(args[1])
            if len(args) >= 3:
                mg.max_steps = int(args[2])
        except ValueError:
            await update.effective_message.reply_text("Multiplier/maxsteps must be numbers.")
            return
        await update.effective_message.reply_text(
            f"Martingale {'ON' if mg.enabled else 'OFF'} x{mg.multiplier} max {mg.max_steps}"
        )

    async def cmd_risk(self, update: Update, ctx):
        if not await self._guard(update):
            return
        try:
            self.config.risk.daily_loss_cap = float(ctx.args[0])
            if len(ctx.args) >= 2:
                self.config.risk.daily_profit_target = float(ctx.args[1])
            await update.effective_message.reply_text(
                f"Daily loss cap {self.config.risk.daily_loss_cap}, "
                f"profit target {self.config.risk.daily_profit_target}"
            )
        except (IndexError, ValueError):
            await update.effective_message.reply_text("Usage: /risk <daily_loss_cap> [profit_target]")

    async def cmd_set(self, update: Update, ctx):
        """Generic setter for any nested config field, e.g. strategy.rsi_oversold."""
        if not await self._guard(update):
            return
        if len(ctx.args) < 2:
            await update.effective_message.reply_text("Usage: /set <field.path> <value>")
            return
        path, raw = ctx.args[0], ctx.args[1]
        try:
            self._set_nested(path, raw)
            await update.effective_message.reply_text(f"Set {path} = {raw}")
        except Exception as e:
            await update.effective_message.reply_text(f"Could not set {path}: {e}")

    def _set_nested(self, path: str, raw: str) -> None:
        obj = self.config
        parts = path.split(".")
        for p in parts[:-1]:
            obj = getattr(obj, p)
        leaf = parts[-1]
        current = getattr(obj, leaf)  # for type inference / to raise if missing
        # Coerce the string to the current field's type.
        if isinstance(current, bool):
            val = raw.strip().lower() in ("1", "true", "yes", "on")
        elif isinstance(current, int):
            val = int(raw)
        elif isinstance(current, float):
            val = float(raw)
        else:
            val = raw
        setattr(obj, leaf, val)

    async def cmd_reset(self, update: Update, ctx):
        if not await self._guard(update):
            return
        if self._reset_cb:
            self._reset_cb()
        await update.effective_message.reply_text("Daily PnL and martingale reset.")

    async def cmd_help(self, update: Update, ctx):
        if not await self._guard(update):
            return
        await update.effective_message.reply_text(HELP, reply_markup=self._kb())

    async def on_button(self, update: Update, ctx):
        if not self._authorised(update):
            return
        q = update.callback_query
        await q.answer()
        if q.data == "start":
            self.config.running = True
            await q.edit_message_text("▶ Trading STARTED.", reply_markup=self._kb())
        elif q.data == "stop":
            self.config.running = False
            await q.edit_message_text("⏸ Trading STOPPED.", reply_markup=self._kb())
        elif q.data == "status":
            await q.edit_message_text(self._status_text(), reply_markup=self._kb())

    # -------------------------------------------------- lifecycle
    def build(self, reset_cb=None) -> Application:
        self._reset_cb = reset_cb
        app = Application.builder().token(self.config.telegram_token).build()
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("stop", self.cmd_stop))
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(CommandHandler("stake", self.cmd_stake))
        app.add_handler(CommandHandler("expiry", self.cmd_expiry))
        app.add_handler(CommandHandler("asset", self.cmd_asset))
        app.add_handler(CommandHandler("strategy", self.cmd_strategy))
        app.add_handler(CommandHandler("martingale", self.cmd_martingale))
        app.add_handler(CommandHandler("risk", self.cmd_risk))
        app.add_handler(CommandHandler("set", self.cmd_set))
        app.add_handler(CommandHandler("reset", self.cmd_reset))
        app.add_handler(CommandHandler("help", self.cmd_help))
        app.add_handler(CallbackQueryHandler(self.on_button))
        self.app = app
        return app

    async def send(self, text: str) -> None:
        """Push a message to the authorised chat (used by the trader notifier)."""
        if not self.app or not self.config.telegram_chat_id:
            return
        try:
            await self.app.bot.send_message(chat_id=self.config.telegram_chat_id, text=text)
        except Exception:
            pass
