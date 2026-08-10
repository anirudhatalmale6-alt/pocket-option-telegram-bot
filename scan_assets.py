"""
Show Pocket Option's live payouts, and what win rate each one demands.

    python scan_assets.py                 # OTC currency pairs (the usual case)
    python scan_assets.py --all           # every asset class
    python scan_assets.py --expiry 60     # only pairs offering a 60s expiry
    python scan_assets.py --kind stock

Reads PO_SSID / PO_SESSION from your .env. This only ever reads — it uses the
chart socket, which cannot place trades or see your balance.

Read the "need" column as: the percentage of trades that must win before you
make a penny. Trading a 68% payout instead of a 92% one raises that bar from
52.1% to 59.5%, which is the difference between most strategies working and
most strategies failing.
"""

from __future__ import annotations

import argparse
import asyncio

from core.assets import best_asset, fetch_assets
from core.config import BotConfig


async def main(kind: str, show_all: bool, expiry: int | None, closed: bool) -> None:
    cfg = BotConfig.from_env()
    if not cfg.po_ssid:
        print("No PO_SSID / PO_SESSION in .env — see docs/SETUP.md.")
        return

    print("Fetching live asset table from Pocket Option...\n")
    assets = await fetch_assets(cfg.po_ssid, demo=cfg.po_demo)

    rows = assets if show_all else [a for a in assets if a.kind == kind]
    if not closed:
        rows = [a for a in rows if a.is_open]
    if expiry is not None:
        rows = [a for a in rows if expiry in a.expiries]
    rows.sort(key=lambda a: (-a.payout, a.symbol))

    print(f"{'payout':>7} {'need':>6}  {'open':<6} {'symbol':<20} {'name':<26} min expiry")
    print("-" * 88)
    for a in rows:
        mn = f"{a.min_expiry}s" if a.min_expiry else "-"
        print(f"{a.payout:>6}% {a.breakeven_win_rate:>5.1f}%  "
              f"{'yes' if a.is_open else 'no':<6} {a.symbol:<20} {a.name:<26} {mn}")

    pick = best_asset(assets, kind=kind, otc=True, expiry=expiry)
    if pick:
        print(f"\nBest-paying open OTC {kind} right now: {pick.symbol} "
              f"({pick.name}) at {pick.payout}% — you need "
              f"{pick.breakeven_win_rate:.1f}% wins to break even.")
        print(f"Set PO_ASSET={pick.symbol} in your .env, or pick it in the control panel.")
    print("\nPayouts move during the day, so re-run this before a session.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="currency",
                    help="currency | stock | commodity | cryptocurrency | index")
    ap.add_argument("--all", action="store_true", help="show every asset class")
    ap.add_argument("--expiry", type=int, default=None,
                    help="only assets offering this expiry, in seconds")
    ap.add_argument("--closed", action="store_true", help="include closed assets")
    args = ap.parse_args()
    asyncio.run(main(args.kind, args.all, args.expiry, args.closed))
