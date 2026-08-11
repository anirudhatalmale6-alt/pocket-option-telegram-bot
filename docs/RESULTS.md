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

Real EUR/USD history, four timeframes, seven strategies, one-candle expiry.
Payout assumed 80%, so break-even is 55.6%. Ties (entry price == expiry price)
are refunded by Pocket Option, so they are excluded from the win rate and shown
separately.

```
  tf    strategy  trades   ties  win rate         95% CI  verdict
  5m  confluence      20     15     50.0%         30-70%  inconclusive
  5m      custom      93     47     60.2%         50-70%  inconclusive
  5m   alligator     155    102     51.6%         44-59%  unproven
  5m         rsi     205     85     41.5%         35-48%  no edge
  5m    pullback     305    149     53.8%         48-59%  unproven
  5m      linreg    1667    613     48.1%         46-51%  no edge
  5m    donchian     474    176     41.8%         37-46%  no edge

 15m  confluence      31      5     54.8%         38-71%  inconclusive
 15m      custom     131     24     61.8%         53-70%  unproven
 15m   alligator     208     49     49.5%         43-56%  unproven
 15m         rsi     217     55     53.5%         47-60%  unproven
 15m    pullback     320     87     55.3%         50-61%  unproven
 15m      linreg    2692    608     48.8%         47-51%  no edge
 15m    donchian     479     97     44.1%         40-49%  no edge

 30m  confluence      16      1      6.2%          1-28%  inconclusive
 30m      custom      64      9     39.1%         28-51%  inconclusive
 30m   alligator     101      9     57.4%         48-67%  unproven
 30m         rsi     117     16     48.7%         40-58%  unproven
 30m    pullback     186     35     52.2%         45-59%  unproven
 30m      linreg    1814    273     48.5%         46-51%  no edge
 30m    donchian     258     30     48.1%         42-54%  no edge

  1h  confluence      19      1     31.6%         15-54%  inconclusive
  1h      custom      70      7     47.1%         36-59%  inconclusive
  1h   alligator     132     10     53.8%         45-62%  unproven
  1h         rsi     148     10     56.8%         49-64%  unproven
  1h    pullback     185     20     47.0%         40-54%  no edge
  1h      linreg    2379    239     48.4%         46-50%  no edge
  1h    donchian     286     24     44.4%         39-50%  no edge
```

**0 of 28 combinations show a statistically real edge, at an 80% payout or a
92% one.** Nine are outright losing. The rest are "unproven" — the data cannot
tell them apart from a coin toss.

Notice also that confluence is 54.8% on 15m and 6.2% on 30m. A strategy with a
genuine edge does not do that. That is noise changing sign.

## What I did *not* do about it

I did not sweep the parameters until something looked good. With 28 combinations
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

In favour of it: 28 out of 28 landing on "no" is not a marginal result. If there
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

If the demo run clears break-even over a real sample, you have something. If it
does not, the honest answer is that this bot automates the trading — reliably,
24/7, with risk caps — but the profitable strategy is still an open question,
and no amount of code on my side changes that.
