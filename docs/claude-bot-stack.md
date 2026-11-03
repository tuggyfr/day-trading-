# Plugins, add-ons and extensions for a Claude day-trading bot

Researched November 2026. Everything listed here is a real, published project. Each entry says whether it is
**official** (published by the vendor) or **community** (third-party; read the code before giving it keys).

---

## How the pieces fit

```
                ┌──────────────────────────── Claude Code (you + Claude) ───────────────────────────┐
                │  writes/edits the bot · runs backtests · researches tickers · reviews trades      │
                │                                                                                    │
                │   MCP servers ("connectors") that give Claude live access:                         │
                │   ├─ Broker ........ Alpaca (orders, positions, data)            ← required        │
                │   ├─ Market data ... Massive/Polygon or Alpha Vantage            ← required        │
                │   ├─ Charts ........ TradingView MCP                             ← optional        │
                │   ├─ Backtesting ... QuantConnect MCP                            ← recommended     │
                │   ├─ News/macro .... Finnhub, FRED                               ← optional        │
                │   ├─ Alerts ........ Telegram / Discord notification MCP          ← recommended     │
                │   └─ Code .......... GitHub (already connected)                                    │
                └────────────────────────────────────────────────────────────────────────────────────┘
                                                   │ produces
                                                   ▼
                    bot/  (plain Python using alpaca-py; runs on a schedule, no LLM in the hot loop)
```

**Important design point:** keep Claude *out of the per-trade execution loop*. An LLM call takes
seconds and costs money, and its answers can vary. Successful setups use Claude to **build, test,
monitor and improve** a deterministic Python bot, and optionally to run a slower daily or pre-market
decision step through the Claude Agent SDK. The MCP broker connection is for research, supervised
trades and checking on the bot, not for scalping.

---

## Tier 1: Required (minimum working bot)

### 1. Alpaca MCP server (broker + market data): **official**
Stocks, ETFs, options and crypto. Orders, positions, account, market data, news. **Defaults to paper trading.**
- Repo: https://github.com/alpacahq/alpaca-mcp-server
- Get keys: https://alpaca.markets → Paper Trading → API Keys (free)
- Add to Claude Code:
  ```bash
  claude mcp add alpaca --scope user --transport stdio uvx alpaca-mcp-server \
    --env ALPACA_API_KEY=xxx --env ALPACA_SECRET_KEY=xxx
  ```
- Env vars: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_PAPER_TRADE` (default `true`; set `false` only for live money),
  `ALPACA_TOOLSETS` (limit which tool groups Claude gets).
- The server is local-only (stdio). Its README warns against exposing it publicly with your keys.
- Why Alpaca first: commission-free, free paper account, clean API, and the Python SDK the bot itself will use (`alpaca-py`).

### 2. Market data (pick one)
| Option | Type | Cost | Install |
|---|---|---|---|
| **Massive (formerly Polygon.io)** | official | paid plan for most real-time data | `uv tool install "mcp_massive @ git+https://github.com/massive-com/mcp_massive@v0.10.0"` then `claude mcp add massive -e MASSIVE_API_KEY=xxx -- mcp_massive` |
| **Alpha Vantage** | official, hosted | free key (about 25 requests/day), paid for more | `claude mcp add --transport http alphavantage "https://mcp.alphavantage.co/mcp?apikey=xxx"` |

- Massive: https://github.com/massive-com/mcp_massive. Stocks, options, forex and crypto bars, trades/quotes, snapshots, Benzinga news. It loads results into a local SQLite database that Claude can query with SQL. Best choice for intraday work. (`POLYGON_API_KEY` still works as an alias.)
- Alpha Vantage: https://mcp.alphavantage.co. Free, and good for starting out. Exposes 100+ tools; limit them if Claude gets confused.
- Alpaca's own data feed (above) also works for a start. The free tier is IEX-only, and the full SIP feed is paid.

### 3. Claude Code + GitHub: **already set up**
This session already has Claude Code and the GitHub connector on `tuggyfr/day-trading-`. Nothing to add.

---

## Tier 2: Strongly recommended

### 4. QuantConnect MCP (backtesting before risking money): **official**
Lets Claude write a strategy, backtest it on years of data, read the results and fix errors on its own.
- Docs: https://www.quantconnect.com/docs/v2/ai-assistance/mcp-server/key-concepts
- Runs as Docker image `quantconnect/mcp-server`; needs a QuantConnect user ID + API token.
- Community alternative (pip/uvx): https://github.com/taylorwilsdon/quantconnect-mcp
- No-account alternative: have Claude backtest locally with `vectorbt` or `backtesting.py` (see the Python libraries section).

### 5. Notifications (so the bot can reach you): **community**
- Telegram: https://github.com/kstonekuan/telegram-notification-mcp (bot from @BotFather + your chat ID; deploys on Cloudflare Workers)
- Discord: same author publishes a Discord-webhook variant.
- Simpler option without MCP: the bot posts to a Discord webhook or Telegram bot directly with `requests` (about 5 lines of code).
- Note: the **Gmail** connector is already attached to this account and can send emails as an alert channel.

### 6. Claude Agent SDK (only if Claude should make decisions automatically): **official**
For a scheduled "Claude reviews the pre-market and picks today's watchlist" step that runs without you.
- `pip install claude-agent-sdk` (Python) / `npm i @anthropic-ai/claude-agent-sdk`
- Docs: https://docs.claude.com/en/api/agent-sdk/overview
- Give it the same MCP servers through config. Keep `ALPACA_PAPER_TRADE=true` until it has a track record.

---

## Tier 3: Optional add-ons

### Charts & technical analysis
- **TradingView MCP** (community, the most complete one): https://github.com/tradesdontlie/tradingview-mcp
  Controls TradingView Desktop over Chrome DevTools (port 9222), 84 tools. Real-time data needs a paid TradingView plan.
- **mcp-tradingview-server** (community, lighter): https://github.com/bidouilles/mcp-tradingview-server
  Indicator snapshots + OHLCV only, no desktop app needed.

### News, fundamentals, macro
- **Finnhub** (community): `npx -y aigroup-finnhub-mcp` with `FINNHUB_API_KEY` (quotes, candles, news sentiment, indicators). Free key at finnhub.io.
- **Financial Datasets** (vendor-published): https://github.com/financial-datasets/mcp-server (financials, prices, news; `FINANCIAL_DATASETS_API_KEY`).
- **FRED** economic data (community): `fred-mcp-server` on PyPI (CPI, rates, jobs data; useful for event days). Free key from fred.stlouisfed.org.

### Anthropic's Financial Services plugins: **official**
```bash
claude plugin marketplace add anthropics/financial-services
claude plugin install financial-analysis@claude-for-financial-services
claude plugin install equity-research@claude-for-financial-services
claude plugin install market-researcher@claude-for-financial-services
```
Repo: https://github.com/anthropics/financial-services-plugins. **Caution:** these are aimed at analysts and banks
(DCF models, earnings notes, research). Most of the bundled data connectors (FactSet, Morningstar, S&P, LSEG, PitchBook)
require **enterprise subscriptions**. `market-researcher` and `equity-research` are the only parts that help a day trader much.
Requires a paid Claude plan.

---

## Alternative brokers (if not Alpaca)

| Broker | MCP server | Type | Notes |
|---|---|---|---|
| Tradier | Hosted. Docs: https://docs.tradier.com/docs/tradier-mcp | official | Works with Claude web/desktop/Code. Paper account available. API key in the server URL. |
| Webull | `uvx webull-openapi-mcp serve`: https://github.com/webull-inc/webull-openapi-mcp | official | Built-in guardrails: `WEBULL_SYMBOL_WHITELIST`, `WEBULL_MAX_ORDER_QUANTITY`, `WEBULL_MAX_ORDER_NOTIONAL_USD`. `WEBULL_ENVIRONMENT=uat` (sandbox) by default. |
| Interactive Brokers | e.g. `code-rabi/interactive-brokers-mcp` | community only | Needs TWS/IB Gateway running. No official server. |
| Schwab | `jkoelker/schwab-mcp`, `sudowealth/schwab-mcp` | community only | No official server. Some forks are read-only. |

### Crypto (trades 24/7, no PDT rules)
| Exchange | MCP server | Type |
|---|---|---|
| Kraken | `kraken-mcp`, shipped inside `kraken-cli` (152 tools, paper + live). Docs: https://docs.kraken.com/home/mcp | official |
| Coinbase | Hosted at `agents.coinbase.com/mcp` (OAuth). Verify at docs.cdp.coinbase.com | official (unverified first-hand) |
| 100+ exchanges | `mcp-ccxt` (PyPI): wraps the CCXT library | community |

---

## Python libraries the bot itself will use
```
alpaca-py          # broker + data SDK (the bot's orders go through this, not through MCP)
pandas numpy       # data handling
pandas-ta          # indicators (or TA-Lib if you can compile it)
vectorbt           # fast local backtests (or backtesting.py)
APScheduler        # run the strategy on market hours
python-dotenv      # load keys from .env
loguru             # logging / trade journal
```

---

## Accounts & API keys checklist

| Service | Cost to start | Where |
|---|---|---|
| Claude plan (Pro/Max) for Claude Code | paid | claude.ai |
| Alpaca paper account | free | alpaca.markets |
| Alpha Vantage key | free | alphavantage.co |
| Massive (Polygon) key | free tier, paid for real-time | massive.com |
| QuantConnect | free tier | quantconnect.com |
| Finnhub / FRED keys | free | finnhub.io / fred.stlouisfed.org |
| Telegram bot | free | @BotFather in Telegram |

Keys go in `.env` (git-ignored). **Never commit keys.** See `.env.example` and `.mcp.json` in the repo root.

---

## Rules & risk notes

- **PDT rule is gone.** The SEC approved FINRA's removal of the Pattern Day Trader rule and the $25,000
  minimum on April 14, 2026, effective June 4, 2026. It was replaced with real-time intraday margin; the general
  margin-account minimum is $2,000. Some brokers have until Oct 2027 to adopt it. Check your broker's current rules.
- **Paper trade first.** Every broker server above defaults to paper or sandbox. Keep it there until a strategy
  has passed both a backtest and several weeks of paper trading.
- **Hard limits in code**, not just in prompts: max position size, max daily loss (kill switch), symbol whitelist.
- **Community MCP servers get your broker keys.** Read their code first, and prefer official servers for anything that places orders.
- The tools are proven to work, but making money is not. Most retail day traders lose money, and an
  AI-built bot doesn't change that by default. The backtest → paper → small-live sequence is how you find out whether a strategy
  has an edge before it costs you anything.

---

## Sources
- Alpaca MCP: https://github.com/alpacahq/alpaca-mcp-server · https://alpaca.markets/learn/how-to-deploy-alpaca-mcp-server-remotely-on-claude-mobile-app
- Massive/Polygon MCP: https://github.com/massive-com/mcp_massive
- Alpha Vantage MCP: https://mcp.alphavantage.co/
- QuantConnect MCP: https://www.quantconnect.com/docs/v2/ai-assistance/mcp-server/key-concepts · https://github.com/taylorwilsdon/quantconnect-mcp
- TradingView MCP: https://github.com/tradesdontlie/tradingview-mcp/wiki/Home · https://github.com/bidouilles/mcp-tradingview-server
- Financial Services plugins: https://github.com/anthropics/financial-services-plugins · https://support.claude.com/en/articles/13851150-install-financial-services-plugins
- Tradier: https://docs.tradier.com/docs/tradier-mcp · https://tradier.com/company/press/Tradier-Launches-MCP-Service-LLM-Powered-Trading
- Webull: https://github.com/webull-inc/webull-openapi-mcp · https://developer.webull.com.sg/apis/docs/ai-friendly-resources/mcp
- IBKR (community): https://claudemarketplaces.com/mcp/code-rabi/interactive-brokers-mcp
- Schwab (community): https://github.com/sudowealth/schwab-mcp · https://mcpservers.org/servers/jkoelker/schwab-mcp
- Kraken: https://docs.kraken.com/home/mcp · https://bitcoinist.com/kraken-launches-open-source-mcp-server-for-ai-trading-agents/
- Coinbase: https://www.developersdigest.tech/mcp-servers/coinbase-mcp
- CCXT: https://pypi.org/project/mcp-ccxt/
- Finnhub: https://glama.ai/mcp/servers/ttnokjmg9h
- Financial Datasets: https://playbooks.com/mcp/financial-datasets
- FRED: https://pypi.org/project/fred-mcp-server/
- Telegram/Discord notifications: https://glama.ai/mcp/servers/@kstonekuan/telegram-notification-mcp/blob/91440f973dd35477a32cb9e5cc882013a363b6c6/README.md
- PDT rule removal: https://international.schwab.com/story/sec-approves-scrapping-25000-day-trader-minimum · https://www.quantinsti.com/articles/finra-pdt-rule-removal-2026/
