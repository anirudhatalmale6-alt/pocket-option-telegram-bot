# Connecting your Pocket Option account

Read the last section first if you are in a hurry.

---

## The easy way: do it on the control panel

Start the bot (`bash run.sh --paper`), open the panel, and use the **Your Pocket
Option account** card near the top:

1. Paste the `ci_session` cookie (step 1 below explains where to find it).
2. **Leave the account id box empty.**
3. Leave **Demo** ticked.
4. Press **Save & connect**.

That writes the `.env` file for you, with the right permissions, and reconnects
without a restart. You never have to open a hidden file or use a text editor.

**You do not need to find your account id.** There are only four combinations of
id and demo/real flag, so the panel tries them against your cookie and keeps
whichever one Pocket Option answers — logging each attempt as it goes. Finding
that id by hand means reading a WebSocket frame in DevTools, and a wrong one is
refused in complete silence. That is what step 2b below is for, and it is now a
fallback, not a requirement.

If every combination is refused, the log says so plainly. That means the cookie
itself has expired — a different id would not have helped — so get a fresh one
and **do not log out afterwards**, because logging out kills the session you
just copied.

If you paste the wrong token by mistake — there are two, and they look alike —
it tells you which one you grabbed and what to look for instead, *before* saving
anything.

The rest of this page is the manual route, and the reasoning you should read
before switching to real money either way.

---

## 1. Get your session token

The bot signs in the same way your browser already has: it borrows the session
cookie. Nothing is typed, and your password is never involved.

Do this **in Chrome on the same machine the bot runs on**. Pocket Option ties a
session to the browser and network it was created on, so a cookie copied from
your phone will not work on the Chromebook.

1. Open <https://pocketoption.com> and log in.
2. Press `Ctrl` + `Shift` + `I` to open DevTools.
3. Go to **Application** (you may need the `»` arrow to find it).
4. In the left sidebar: **Storage → Cookies → https://pocketoption.com**
5. Find the row named **`ci_session`**. Double-click its **Value** and copy it.

It is a long string that starts with `a%3A4%3A%7B`.

## 2. Find your account id

> **The demo and the real balance have DIFFERENT account ids.** Using the real
> account's id while asking for the demo is refused — and refused *silently*:
> the socket still opens, the balance reads `-1.00`, and no candles ever arrive.
> Nothing on screen says "wrong id". If you are setting up the demo, take the id
> from a session where you were **on the demo account**.
>
> The most reliable way to get a matching pair of id and flag is the auth frame
> in step 2b below, because the browser sends all of it together.

In the same DevTools window, click the **Console** tab, paste this, press Enter:

```js
document.cookie.match(/uid=(\d+)/)?.[1] || 'not found — see below'
```

If that prints `not found`, use step 2b.

## 2b. Fallback: read the auth frame yourself

Only needed if the panel cannot find a working combination *and* you believe the
cookie is fresh. It gets the id **and** the demo/real flag as a matching pair,
exactly as the browser sends them.

1. DevTools → **Network** tab.
2. In the row of type buttons (All, Fetch/XHR, Doc, …) click **Socket**. Make
   sure the filter box to the left is **empty** — typing there filters the list
   of connections by name, hides everything, and looks like a broken DevTools.
3. Press `Ctrl+R` to reload. DevTools only records from the moment it is
   watching, so the login is missed unless you reload with it open.
4. Three or so connections appear. The trading one is whichever contains a
   message reading `451-["successauth",…]`. The others are just the chart.
5. Click it → **Messages** → scroll to the **top**.
6. Find the **green** (outgoing) line beginning `42["auth",{…}]`.

Inside it:

```
42["auth",{"session":"a%3A4%3A%7B…","isDemo":1,"uid":123456789,"platform":2}]
                                     ^^^^^^^^^  ^^^^^^^^^^^^^^
                                     these two are what you need
```

`isDemo:1` means that id belongs to the **demo** balance, `isDemo:0` the real
one. They must match what you put in `.env`.

That line contains your session, which is a password — don't paste it into a
chat, a ticket or a screenshot without covering it.

## 3. Put both in `.env`

`.env` sits next to `main.py`. Open it in a text editor and set:

```ini
PO_SESSION=a%3A4%3A%7B...the whole thing...
PO_UID=123456789
PO_DEMO=true
```

**`PO_DEMO=true` is not a formality.** It points the bot at your practice
balance. Leave it `true` until you have a strategy that has beaten its
break-even line over 100+ trades. See the last section.

Restart the bot. The pill at the top of the panel should read **DEMO** and
**Connected**.

## 4. Only when you are ready: real money

Change one line:

```ini
PO_DEMO=false
```

Restart. The panel now shows a red **REAL MONEY** bar, and START asks you to
confirm once, with the numbers that matter on screen.

---

## Your token is a password. Treat it like one.

Anyone holding that `ci_session` string can sign into your account. So:

- **Never** paste it into a chat, an email, or a screenshot.
- It only ever belongs in the `.env` file on your own machine.
- `.env` is in `.gitignore`, so it is never committed. Keep it that way.
- If it does leak: log out of Pocket Option and log back in. That kills the old
  session immediately.

Sessions expire on their own too, so expect to redo step 1 every so often. A
stale token shows up as `Connecting…` that never turns green.

---

## Read this before you switch `PO_DEMO` to false

Every strategy in this project has been measured against real EUR/USD history —
28 strategy-and-timeframe combinations. **None of them showed a statistically
real edge.** The full table is in [RESULTS.md](RESULTS.md).

That includes the confluence strategy, which an earlier note of mine described
as scoring around 70%. That figure came from 11 trades. It was noise, and it is
retracted.

The number that decides whether a bot can win at all is not the win rate — it is
the win rate **minus** the rate you need to break even:

```
break-even win rate = 100 / (100 + payout)
```

| Payout | You must win | 
|-------:|-------------:|
|    92% |        52.1% |
|    80% |        55.6% |
|    68% |        59.5% |
|    52% |        65.8% |

A 50% win rate is not "nearly break-even". At a 92% payout it loses about 4% of
everything staked, every trade, forever. That is the house edge, and it is why a
strategy has to be *proven* rather than *plausible*.

So the honest order of operations is:

1. Run on **demo** until you have **100+ settled trades**.
2. Compare the win rate against the break-even figure the panel shows next to
   it — not against 50%.
3. Only if it is clearly above that line, with enough trades that it is not
   luck, consider real money.
4. Keep **martingale off** while testing. Doubling after losses hides a losing
   strategy behind a rising equity curve until the run that empties the account.

None of that is legal boilerplate. It is the difference between a bot that
trades and a bot that makes money, and right now this one is the first thing.
