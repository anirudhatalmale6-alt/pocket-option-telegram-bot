# Add your own strategy

Drop a `.py` file in this folder. It appears in the panel's **Strategy**
dropdown within a few seconds — no restart, and nothing else to edit.

## The whole contract

One function:

```python
def evaluate(candles):
    return "call"     # or "put", or None for no trade
```

That is it. Everything else is optional.

### What you get

`candles` is a list, **oldest first**. Each item has `.time` (unix seconds),
`.open`, `.high`, `.low`, `.close`.

`candles[-1]` is the most recently **closed** candle. The bot never hands you a
candle that is still forming — reading indicators off a half-built bar shows
crosses that vanish before the close, which is how a backtest and live trading
end up disagreeing.

### What you return

Any of these four; they mean the same thing:

```python
return "call"                        # price will be higher at expiry
return "put", "RSI came out of 20"   # ...and a reason, shown in the log
return None                          # sit this one out
return Signal(Direction.CALL, "…")   # the strict form, if you prefer
```

Returning a reason is worth the extra few characters: it appears on the panel
and in the trade log, so a quiet stretch reads as *"1/3 agree — need 2"* instead
of a blank screen you cannot tell apart from a crashed bot.

### Optional extras

```python
NAME  = "Bob's EMA cross"   # dropdown label (default: the filename)
ORDER = 10                  # sort position in the dropdown
```

### Helpers you can import

```python
from core.indicators import sma, ema, ema_series, rsi, stochastic
```

`ema(values, period)` returns a single number — the latest value. `ema_series`
returns the whole line, which is what you need to spot a *cross* rather than a
level. `example_ema_cross.py` in this folder shows the difference.

## If it doesn't show up

A file that fails to import, or has no `evaluate()`, is **skipped with a warning
in the terminal** rather than taking the bot down. Check the terminal output —
the reason is printed there. A strategy that raises an error while running is
caught the same way: it logs the error and sits that candle out.

## Before you trust it

Test it against real history before it sees money:

```
./.venv/bin/python honest_backtest.py
```

That reports a Wilson confidence interval and compares it against the win rate
you need to break even (`100 / (100 + payout)`). A raw win rate on its own is
the easiest number in trading to fool yourself with — 70% from 11 trades is
noise, and this project has already made that mistake once (see
`docs/RESULTS.md`).

## One safety note

A strategy file is ordinary Python and runs with the bot's permissions — it can
read files and reach the network. Only add files from someone you trust, the
same as any other program you run.
