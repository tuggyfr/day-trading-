# Is TradingView reasonable for "training" the bot?

Researched November 2026. `tradingview.com` was blocked by the network policy of the cloud session that wrote
this, so these findings come from TradingView's published docs, support pages and third-party guides, not from
a hands-on test.

## First: what "training" means for this bot
The bot follows fixed rules (opening-range breakout). It doesn't learn from data like an AI model. "Training" it
means **testing those rules on past prices and tuning the settings** (range length, reward:risk, cutoff time
and so on), then checking that the result still holds on data you didn't tune on.

## Verdict
**Use TradingView for looking and checking. Use Alpaca for the bot's data.**

| Use | Reasonable? | Why |
|---|---|---|
| Looking at charts to understand what the bot does | **Yes, great** | Best charting available. The included Pine script draws the range, VWAP and signals. |
| Cross-checking the strategy in the Strategy Tester | **Yes** | Same rules as the bot (`tradingview/orb_strategy.pine`). If both backtests roughly agree, that's good evidence neither has a bug. |
| Bar Replay (stepping through past days candle by candle) | **Yes** | Good for building intuition on why trades win or lose. |
| Main data source for tuning the bot | **No** | Too little 1-minute history per export (see below), it's manual, and Alpaca gives years of the same data for free through the API. |
| Sending trade signals to the bot (webhooks) | **Not needed** | The bot already computes its own signals. Adding TradingView in the middle adds a paid dependency and another place for things to fail. |
| Scraping TradingView data automatically | **No** | TradingView's terms restrict automated access and data use, and unofficial "TradingView API" tools rely on undocumented endpoints. |

## The data limit that matters
A regular trading day has 390 one-minute bars. TradingView limits the intraday bars loaded on a chart by plan:

| Plan | Intraday bars | About how many trading days of 1-min data |
|---|---|---|
| Basic (free) | 5,000 | ~13 |
| Essential / Plus | 10,000 | ~25 |
| Premium | 20,000 | ~51 |
| Ultimate | 40,000 | ~100 |

Chart data export ("Download chart data") is a **paid-plan** feature and only exports what's loaded on the chart.
That's a few weeks to a few months, which is too short to judge a day-trading strategy: you want at least a year,
covering both calm and volatile markets.

By comparison, `python -m bot backtest --days 365` pulls a year of 1-minute bars for all your symbols from Alpaca
with a free account.

**Exception:** TradingView **Premium** has *Deep Backtesting*, which runs a Pine strategy over the full history
TradingView has (1-minute data back to roughly 2009–2011 for many symbols). That's a legitimate way to test the
Pine version over many years, but the results stay inside TradingView. You can't export years of bars that way.

## Costs to be aware of
- Webhook alerts and data export need a paid plan (Essential or higher). Webhooks also need 2FA turned on.
- Real-time data for US exchanges is a separate paid add-on on TradingView.
- A 30-day trial is usually available. Check current prices on tradingview.com/pricing.

## Recommended workflow
1. **Tune on Alpaca data:** `python -m bot backtest --days 365`, then change settings in `.env` and rerun.
   Hold back the most recent ~3 months and only test on them once, after tuning. That's your honest check against overfitting.
2. **Sanity-check on TradingView (free plan works):** open a 1-minute SPY chart with Session set to **Regular**, paste
   `tradingview/orb_strategy.pine` into the Pine Editor, Add to chart, and compare the Strategy Tester to the bot's backtest over the same
   dates. Expect close numbers, not identical ones (they handle stop-vs-target-in-the-same-candle differently).
3. **If you already pay for TradingView:** you can export a 1-minute chart and run
   `python -m bot backtest --tv-csv spy.csv --symbols SPY` to compare both backtesters on the same data.
4. **Optional:** connect Alpaca inside TradingView's Trading Panel to watch the bot's paper positions on the chart.
   Alpaca is a supported TradingView broker.

## About the Pine script
`tradingview/orb_strategy.pine` is Pine Script v6. It couldn't be compiled where it was written (no TradingView
access), so if the Pine Editor shows an error, paste it back to Claude to fix.

## Sources
- Intraday bar limits by plan: https://www.tradingview.com/support/solutions/43000480679
- Bar Replay and 1-minute history depth: https://ru.tradingview.com/support/solutions/43000712747
- Chart data export: https://www.tradingview.com/blog/en/export-chart-data-in-csv-14395 · https://blog.traderspost.io/article/tradingview-data-export
- Plans/pricing: https://www.tradingview.com/gopro/
- Webhook plan requirements: https://crosstrade.io/blog/tradingview-plan-for-webhook-alerts · https://www.tv-hub.org/guide/tradingview-alerts-setup
- Terms/policies: https://in.tradingview.com/policies · https://blog.traderspost.io/article/tradingview-api
- Repainting/backtest realism: https://tradingview.com/support/solutions/43000478429-script-or-strategy-gives-different-results-after-refreshing-the-page-repainting · https://blog.traderspost.io/article/pine-script-backtesting-limitations-and-validation
- Alpaca on TradingView: https://alpaca.markets/learn/trade-with-alpaca-on-tradingview · https://alpaca.markets/blog/tradingview-api-integration
