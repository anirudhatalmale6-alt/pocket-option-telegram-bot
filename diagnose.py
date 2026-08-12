"""
Find out exactly what Pocket Option is doing, one step at a time.

    bash diag.sh

Every guess I have made about why no candles arrive has cost the client hours,
so this stops guessing and reports facts. It walks the connection from the
bottom up — token, socket, balance, asset list, candle feed — and prints the
result of each step with the time it took. Every step has a timeout, so it can
never hang the way the bot did.

It places no trades. It only reads.
"""

from __future__ import annotations

import asyncio
import sys
import time
import traceback

from core.config import BotConfig


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(n: str, msg: str) -> None:
    say(f"\n[{n}] {msg}")


def ok(msg: str) -> None:
    say(f"    OK   {msg}")


def bad(msg: str) -> None:
    say(f"    FAIL {msg}")


def info(msg: str) -> None:
    say(f"         {msg}")


async def timed(label: str, coro, timeout: float):
    """Await something with a timeout, reporting how long it actually took."""
    start = time.time()
    try:
        result = await asyncio.wait_for(coro, timeout=timeout)
        return True, result, time.time() - start
    except asyncio.TimeoutError:
        return False, f"timed out after {timeout:.0f}s", time.time() - start
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}", time.time() - start


async def main() -> int:
    say("=" * 68)
    say("  Pocket Option connection diagnosis")
    say("=" * 68)

    cfg = BotConfig.from_env()

    # ---------------------------------------------------------------- token
    step("1", "Checking your saved login token")
    if not cfg.po_ssid:
        bad("No token in .env. Paste your cookie into the control panel first.")
        return 1
    ok(f"token present ({len(cfg.po_ssid)} characters)")
    info(f"account id : {cfg.po_uid or 'NOT SET — this is required'}")
    info(f"account    : {'DEMO' if cfg.po_demo else 'REAL MONEY'}")
    info(f"asset      : {cfg.asset}")
    info(f"candle size: {cfg.candle_timeframe}s")
    if not cfg.po_uid:
        bad("PO_UID is not set — Pocket Option will refuse the connection.")
        return 1

    # ------------------------------------------------------------- connect
    step("2", "Opening the Pocket Option connection")
    from core.po_broker import PocketOptionBroker
    from core.ssid import SsidError
    try:
        broker = PocketOptionBroker(cfg.po_ssid, demo=cfg.po_demo, uid=cfg.po_uid)
    except SsidError as exc:
        bad("The token is not the right one:")
        info(str(exc))
        return 1

    good, res, secs = await timed("connect", broker.connect(), 30)
    if not good:
        bad(f"could not connect — {res}")
        return 1
    ok(f"connected in {secs:.1f}s")

    # ------------------------------------------------------------- balance
    step("3", "Reading your balance (proves the login actually worked)")
    for attempt in range(1, 7):
        good, res, secs = await timed("balance", broker.balance(), 15)
        if good and isinstance(res, float) and res > 0:
            ok(f"balance {res:,.2f} (after {attempt} read{'s' if attempt > 1 else ''})")
            break
        info(f"read {attempt}: {res}")
        await asyncio.sleep(1)
    else:
        bad("balance never became a sensible number.")
        info("If pocketoption.com shows a real balance, the login is fine but")
        info("the library is not reading it — tell me and I will chase that.")

    # -------------------------------------------------------- asset is open
    step("4", f"Checking '{cfg.asset}' is a real pair and open right now")
    try:
        from core.assets import fetch_assets
        good, assets, secs = await timed("assets", fetch_assets(), 45)
        if not good:
            bad(f"could not fetch the asset list — {assets}")
        else:
            match = [a for a in assets if a.symbol == cfg.asset]
            if not match:
                bad(f"'{cfg.asset}' is not in Pocket Option's list at all.")
                openz = [a for a in assets if a.is_open and a.payout > 0][:8]
                info("Open pairs you could use instead:")
                for a in openz:
                    info(f"  {a.symbol}  ({a.payout}% payout)")
            else:
                a = match[0]
                if a.is_open:
                    ok(f"{a.symbol} is OPEN, paying {a.payout}%")
                else:
                    bad(f"{a.symbol} is CLOSED right now — no candles will arrive.")
                    info("Pick an _otc pair; those trade at weekends too.")
    except Exception as exc:
        bad(f"asset check failed: {exc}")

    # ---------------------------------------------------------- the candles
    step("5", "Asking for candles — this is the step that has been failing")
    info("Opening the live feed and waiting up to 60s for the first batch...")
    start = time.time()
    got = []
    try:
        for attempt in range(1, 13):
            candles = await asyncio.wait_for(
                broker.get_candles(cfg.asset, cfg.candle_timeframe, 200), 20
            )
            if candles:
                got = candles
                break
            info(f"  {time.time() - start:4.0f}s — still empty "
                 f"(stream error: {broker._stream_error or 'none'})")
            await asyncio.sleep(5)
    except Exception as exc:
        bad(f"the candle request itself raised: {type(exc).__name__}: {exc}")
        traceback.print_exc()

    if got:
        ok(f"{len(got)} candles arrived after {time.time() - start:.0f}s")
        info(f"oldest: {time.strftime('%H:%M:%S', time.localtime(got[0].time))}"
             f"  close {got[0].close}")
        info(f"newest: {time.strftime('%H:%M:%S', time.localtime(got[-1].time))}"
             f"  close {got[-1].close}")
        gap = got[-1].time - got[-2].time if len(got) > 1 else 0
        info(f"spacing between candles: {gap:.0f}s "
             f"(should be {cfg.candle_timeframe}s)")
        say()
        say("VERDICT: the feed works. If the bot still shows nothing, the fault")
        say("is in the bot's loop and not the connection — send me this output.")
    else:
        bad(f"no candles after {time.time() - start:.0f}s")
        info(f"last stream error: {broker._stream_error or 'none reported'}")
        say()
        say("VERDICT: Pocket Option accepted the login but never sent price data.")
        say("Send me this whole output — it tells me which of the remaining")
        say("possibilities it is, and I will not have to guess again.")

    await broker.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        say("\nStopped.")
