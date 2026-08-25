# What the testing actually shows

Short version: **the bot works, the strategies are not proven to make money.**
Those are two different claims and this page keeps them apart.

## A correction to something I told you earlier

I previously said the confluence strategy "scored around 70% in my testing".
That number came out of a real backtest, and it was worthless: it was based on
**11 trades**. Eleven coin flips come up 8 heads about 5% of the time, so 72.7%
from 11 trades is exactly what a strategy with *no edge whatsoever* looks like a
reasonable fraction of the time. I should have looked at the sample size before
I quoted it. Retracting it.

`honest_backtest.py` now prints the trade count and a 95% confidence interval on
every row, so no result can be quoted again without the evidence behind it.

## The full table

Real EUR/USD history, four timeframes, twelve strategies, one-candle expiry.
Payout assumed 80%, so break-even is 55.6%. Ties (entry price == expiry price)
are refunded by Pocket Option, so they are excluded from the win rate and shown
separately.

```
  tf         strategy  trades   ties  win rate         95% CI  verdict
-------------------------------------------------------------------------------
  5m       confluence      20     15     50.0%         30-70%  inconclusive
  5m           custom      93     47     60.2%         50-70%  inconclusive
  5m        alligator     155    102     51.6%         44-59%  unproven
  5m              rsi     205     85     41.5%         35-48%  no edge
  5m    momentum_turn     388    151     56.4%         51-61%  unproven
  5m  momentum_follow     388    151     43.6%         39-49%  no edge
  5m        sr_bounce     449    185     43.4%         39-48%  no edge
  5m         sr_break       5      1      0.0%         -0-43%  inconclusive
  5m          sr_fade     449    185     56.6%         52-61%  unproven
  5m         pullback     305    149     53.8%         48-59%  unproven
  5m           linreg    1667    613     48.1%         46-51%  no edge
  5m         donchian     474    176     41.8%         37-46%  no edge

 15m       confluence      31      5     54.8%         38-71%  inconclusive
 15m           custom     131     24     61.8%         53-70%  unproven
 15m        alligator     208     49     49.5%         43-56%  unproven
 15m              rsi     217     55     53.5%         47-60%  unproven
 15m    momentum_turn     322     68     52.8%         47-58%  unproven
 15m  momentum_follow     322     68     47.2%         42-53%  no edge
 15m        sr_bounce     772    196     46.8%         43-50%  no edge
 15m         sr_break      41      7     43.9%         30-59%  inconclusive
 15m          sr_fade     772    196     53.2%         50-57%  unproven
 15m         pullback     320     87     55.3%         50-61%  unproven
 15m           linreg    2692    608     48.8%         47-51%  no edge
 15m         donchian     479     97     44.1%         40-49%  no edge

 30m       confluence      16      1      6.2%          1-28%  inconclusive
 30m           custom      64      9     39.1%         28-51%  inconclusive
 30m        alligator     101      9     57.4%         48-67%  unproven
 30m              rsi     117     16     48.7%         40-58%  unproven
 30m    momentum_turn     178     14     52.2%         45-59%  unproven
 30m  momentum_follow     178     14     47.8%         41-55%  no edge
 30m        sr_bounce     500     69     47.8%         43-52%  no edge
 30m         sr_break      43      3     48.8%         35-63%  inconclusive
 30m          sr_fade     500     69     52.2%         48-57%  unproven
 30m         pullback     186     35     52.2%         45-59%  unproven
 30m           linreg    1814    273     48.5%         46-51%  no edge
 30m         donchian     258     30     48.1%         42-54%  no edge

  1h       confluence      19      1     31.6%         15-54%  inconclusive
  1h           custom      70      7     47.1%         36-59%  inconclusive
  1h        alligator     132     10     53.8%         45-62%  unproven
  1h              rsi     148     10     56.8%         49-64%  unproven
  1h    momentum_turn     214     22     53.7%         47-60%  unproven
  1h  momentum_follow     214     22     46.3%         40-53%  no edge
  1h        sr_bounce     523     48     49.3%         45-54%  no edge
  1h         sr_break      96      6     44.8%         35-55%  inconclusive
  1h          sr_fade     523     48     50.7%         46-55%  no edge
  1h         pullback     185     20     47.0%         40-54%  no edge
  1h           linreg    2379    239     48.4%         46-50%  no edge
  1h         donchian     286     24     44.4%         39-50%  no edge
```

**0 of 48 combinations clear break-even at an 80% payout. Exactly one does at
92%** — `custom` on 15m, 61.8% over 131 trades — and the same strategy on the
next timeframe up wins 39.1%. A real edge does not do that; that is noise
changing sign, and it is the reason one green row in forty-eight is not a
finding. Nineteen rows are outright losing. The rest are "unproven": the data
cannot tell them apart from a coin toss.

## Momentum 10, since you asked for that too

You asked for Momentum, length 10, drawn as a line, with a trade every time it
reaches the top or the bottom. Two things in that sentence were decisions rather
than settings, and both are worth knowing about.

**Where "the top" is.** Momentum has no fixed 70/30 scale the way RSI does. In
the form Pocket Option draws it, it sits near 100 and wanders a few hundredths
either side — and how far is "a lot" depends on the pair, the candle size and
the hour. A fixed threshold would fire on every candle on one pair and never on
another. So the top and the bottom are read off the indicator's own recent
range: the outer 10% of the last 100 bars, measured from the bars *before* the
one being judged. That is self-scaling, which is why it behaves the same on
EUR/USD at 1.09 and USD/JPY at 150.

**Which way the trade goes.** "It gives a trade" does not say. Both readings are
in the table and in the dropdown:

- **`momentum_turn`** — the push has run out, bet it comes back. Top = PUT.
  **56.4% on 5m over 388 trades**, then 52.8%, 52.2%, 53.7%. Positive on all
  four timeframes, best on the shortest — the same shape `sr_fade` has.
- **`momentum_follow`** — the exact mirror image, and it loses on all four. That
  is not independent evidence for the first one; it is the same 388 trades read
  backwards, and it is in the table only so nobody can pick the losing side by
  accident.

The 5-minute row is the second-best number this project has produced, and it
still does not clear the bar: 56.4% with a 95% interval of **51.5% to 61.3%**,
against a break-even of 55.6% at an 80% payout and 52.1% at 92%. The interval
contains both. "Promising and unproven" is the whole of the claim.

Two caveats specific to this one:

1. **You are asking for 1-minute candles and 60-second expiries; the shortest
   row here is 5 minutes.** Free 1-minute FX data has open = high = low = close
   on every bar, so it cannot honestly be tested. Momentum only reads closes, so
   it is less damaged by that than the level-based strategies — but "less
   damaged" is not "measured". The 1-minute answer can only come from the demo
   account.
2. **This is real EUR/USD, not Pocket Option's OTC pairs**, which are synthetic
   and behave differently by design.

## Support and resistance, since you asked

Your own idea, tested three ways, and it produced the most interesting rows in
the table — mostly because it trades *a lot*, so the samples are the biggest
here apart from the trend lines.

- **`sr_bounce`** — bet the level holds. This is support and resistance as
  normally described, and it **loses**, decisively, on all four timeframes: 43%,
  47%, 48%, 49%, over 449 to 772 trades each. Those sample sizes make this a
  real conclusion rather than a shrug.
- **`sr_break`** — bet the level fails once price closes through it. Almost
  never fires (5 to 96 trades). Nothing can be concluded either way.
- **`sr_fade`** — the *opposite* of the bounce: when price is rejected at a
  level, bet against the rejection. 56.6% on 5m over 449 trades, 53.2% on 15m
  over 772, 52.2% on 30m, 50.7% on 1h. Positive on every timeframe, best on the
  shortest.

That last one is the closest thing this project has produced to an edge, and it
still does not clear the bar. At a 92% payout you need 52.083%; the 5m interval
runs from **51.95%** to 61.1%. It misses by thirteen thousandths of a percentage
point. Read that as "promising and unproven", not as "nearly profitable" —
the interval includes 52%, and it also includes 61%.

Two honest caveats on it, both of which cut against the good number:

1. **I tried reversing it *because* the straight version lost.** Flipping a
   losing rule is the first thing anyone tries, and a hypothesis chosen after
   seeing the data is worth less than one chosen before. Note also that 43.4%
   reversed is not automatically 56.6% of *profit* — the broker's cut applies
   either way.
2. **The 5-minute file is the dirtiest one here** — 16% of its bars have
   open = high = low = close, which is also why 185 of its 634 trades were ties.
   The 15-minute file is clean (1.3% flat) and larger in time span, and there
   the same strategy is 53.2%.

What is genuinely encouraging is the *shape*: 56.6 → 53.2 → 52.2 → 50.7 as the
timeframe lengthens. That is a consistent, monotonic pattern in the direction
short-horizon mean reversion predicts. Compare `custom`, which goes 60.2 → 61.8
→ 39.1 → 47.1. One of those looks like an effect; the other looks like luck.

And the thing none of this measures: **you traded support and resistance by hand
and did well.** You chose which levels mattered. The code takes every level with
two touches on it, which is a different and much dumber strategy. If your
by-hand version keeps winning and this one does not, the difference is the
judgement, and that is worth knowing too.

## What I did *not* do about it

I did not sweep the parameters until something looked good. With 40 combinations
already on the table, tuning thresholds until one clears 55.6% would find a
winner by chance alone, and it would fall apart the moment it met live prices.
That is the single most common way backtests lie, and it would have been easy to
hand you a great-looking chart instead of this page.

## What this does and does not rule out

Against it:

- Free 1-minute FX data has no intrabar detail (open = high = low = close on
  every bar, ~60% of consecutive closes identical), so the shortest honest
  timeframe here is 5 minutes — and you want to trade 60-second expiries.
- Interbank EUR/USD is not Pocket Option's OTC pairs, which are synthetic and
  move differently.
- A few months of history is a few months of one market regime.

In favour of it: 39 out of 40 landing on "no" is not a marginal result. If there
were a strong edge here, some of these would have found it.

## The one thing that genuinely moves the odds

Payout, not strategy. Break-even is `100 / (100 + payout)`:

| Payout | Win rate needed |
|-------:|----------------:|
|    92% |           52.1% |
|    80% |           55.6% |
|    68% |           59.5% |

`EURUSD_otc` — the pair the bot shipped pointing at — pays **68%**, so it needs
59.5%. Sixteen OTC pairs pay **92%** and need only 52.1%. Run `scan_assets.py`
to list them live. Switching pair buys you more than 7 percentage points of
required win rate; no amount of indicator tuning in this table did that.

That is worth doing regardless. It is not, by itself, an edge.

## So what should you do

1. Run the bot on your Pocket Option **demo** account, on a 92% pair, for a few
   hundred trades. That is the only test on the actual prices you would trade.
2. Judge it on 100+ trades against the break-even line — the panel now shows
   both, and warns while the sample is too small.
3. Keep martingale off while you measure. It hides a losing win rate behind a
   rising equity curve right up until the run of losses that takes the account.
4. **Watch several pairs at once.** This is the only setting that changes how
   *fast* you get an answer rather than what the answer is. A strategy firing on
   7% of candles gives about four trades an hour on one pair and about forty
   across ten, so a verdict that would have taken three months arrives in ten
   days. Put your ten best-paying pairs in the watchlist box on the panel. It
   does not improve the strategy by one decimal point — and it reaches the daily
   loss cap ten times sooner too, if the strategy is losing.

If the demo run clears break-even over a real sample, you have something. If it
does not, the honest answer is that this bot automates the trading — reliably,
24/7, with risk caps — but the profitable strategy is still an open question,
and no amount of code on my side changes that.
