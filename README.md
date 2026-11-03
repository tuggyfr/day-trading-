# day-trading-
making a bot to day trade for me and make me money

## Setup: tools to connect to Claude
See **[docs/claude-bot-stack.md](docs/claude-bot-stack.md)** for the researched list of MCP servers, plugins,
API keys and Python libraries, with install commands.

Quick start:
1. Create a free **Alpaca paper** account and an **Alpha Vantage** key.
2. `cp .env.example .env` and fill in the keys.
3. Export them in your shell (`set -a; source .env; set +a`) and start `claude` in this folder.
   The project's `.mcp.json` connects Alpaca (paper mode) and Alpha Vantage automatically. Run `/mcp` to check.
