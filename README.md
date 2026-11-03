# day-trading-
making a bot to day trade for me and make me money

An **opening-range breakout** day-trading bot for Alpaca. It runs in **paper trading (fake money) by default**.

## What it does
Every trading day, for each symbol in `BOT_SYMBOLS`:

1. Records the high and low of the first 15 minutes (09:30–09:45 ET), called the *opening range*.
2. **Buys** when a 1-minute bar closes above the range high and above VWAP, before 11:30 ET.
3. Every order is a **bracket order**: the stop-loss (bottom of the range) and take-profit (2× the risk)
   sit at Alpaca, so the position stays protected even if the bot crashes.
4. Closes everything at 15:55 ET. It never holds overnight.

Safety rails (all set in `.env`):
| Rule | Default |
|---|---|
| Max loss per trade | 0.5% of equity |
| Max size per position | 20% of equity |
| Daily loss kill switch | down 2% → close all, stop for the day |
| Max open positions / trades per day | 3 / 5 |
| One trade per symbol per day | survives restarts (deduplicated by order ID) |
| Real-money trading | needs `ALPACA_PAPER_TRADE=false` **and** `I_UNDERSTAND_LIVE_TRADING=yes` |

## Setup
```bash
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                     # then paste your Alpaca PAPER keys into .env
```
Get free paper keys at https://alpaca.markets → Paper Trading → API Keys.

## Use it
```bash
python -m bot status              # check the connection: account, market clock, positions
python -m bot backtest --days 90  # test the strategy on the last 90 days of real data FIRST
python -m bot run                 # start trading (paper). Leave it running during market hours.
python -m bot flatten             # panic button: cancel all orders + close all positions
python -m bot backtest --tv-csv spy.csv --symbols SPY   # backtest a TradingView chart export
pytest                            # run the test suite
```
Logs go to `logs/bot.log`. Every order is recorded in `logs/trades.csv`.

## The plan before real money
1. **Backtest** across several months and symbols. Look at `profit_factor` (needs to be > 1.2 after costs)
   and `max_drawdown_pct`. Change settings in `.env` and compare.
2. **Paper trade for at least 4–6 weeks.** Compare the paper results to the backtest.
3. Only if both hold up: go live with a small account, and keep the same risk limits.

> This strategy is a well-known starting template, not a proven money-maker. Most day-trading
> strategies lose money after costs. The backtest and paper steps are there to show you whether
> this one has an edge before any real money is at risk.

## Project layout
```
bot/config.py     settings from .env (with safe defaults)
bot/strategy.py   ORB signal logic: pure functions shared by live trading and backtest
bot/risk.py       position sizing, kill switch, limits
bot/broker.py     Alpaca wrapper (alpaca-py)
bot/engine.py     live/paper trading loop
bot/backtest.py   historical simulation with pessimistic fills
bot/notifier.py   Telegram / Discord alerts
tests/            tests against a fake broker and simulated prices (no network, no real orders)
tradingview/orb_strategy.pine   the same strategy in Pine Script, for TradingView's Strategy Tester
docs/claude-bot-stack.md        researched list of Claude connectors / MCP servers for this project
docs/tradingview-evaluation.md  is TradingView good for tuning the bot? (short answer: for checking, not as the data source)
```

## Using Claude with it
`.mcp.json` connects Claude Code to Alpaca (paper) and Alpha Vantage. Start `claude` in this folder with
your keys exported (`set -a; source .env; set +a`) and ask things like "how did the bot do today?",
"show my open positions", or "backtest with a 30-minute opening range". See
[docs/claude-bot-stack.md](docs/claude-bot-stack.md) for more connectors.
