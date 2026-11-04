# Project notes for Claude

Day-trading bot for Alpaca (Python). Owner: non-technical; explain results in plain English, give copy-paste
commands for their Mac, and never ask them to paste API keys into chat.

## Ground rules (agreed with the owner)
- **Paper trading only.** Never switch to live money (`ALPACA_PAPER_TRADE=false`), never place a non-paper order,
  and never buy anything (data plans, servers) without asking the owner first.
- Don't let a strategy trade (even paper) until its backtest has been shown to and approved by the owner.
- Any API keys available in a cloud session must be **paper** keys.

## Layout
- `bot/strategy.py`, `bot/engine.py`: `orb` strategy (fixed symbol list, 15-min opening range breakout).
- `bot/scanner.py`, `bot/backtest_inplay.py`, `bot/engine_inplay.py`: `in_play` strategy (top-20 relative-volume
  "stocks in play", 5-min range, stop = 10% ATR, exit at close). Based on Zarattini, Barbon & Aziz 2024.
- `bot/risk.py` sizing/limits, `bot/broker.py` Alpaca wrapper (rate-limited), `bot/notifier.py` alerts + key redaction.
- Docs: `docs/strategy-research.md` (evidence), `docs/tradingview-evaluation.md`, `docs/claude-bot-stack.md`.
- Mac scripts: `setup_mac.sh`, `update_mac.sh`, `autostart_mac.sh`.

## Commands
```bash
.venv/bin/python -m pytest -q                                   # 41+ tests, no network
.venv/bin/python -m bot status
.venv/bin/python -m bot backtest --days 365 --equity 100000                       # orb baseline (SIP data)
.venv/bin/python -m bot backtest --strategy in_play --days 365 --equity 100000    # first run ~10-20 min, cached
# paper-style settings (closer to the study): env vars override .env
RISK_MAX_OPEN_POSITIONS=20 RISK_MAX_TRADES_PER_DAY=20 RISK_MAX_POSITION_PCT=1.0 RISK_PER_TRADE_PCT=0.01 \
  INPLAY_MAX_LEVERAGE=4 .venv/bin/python -m bot backtest --strategy in_play --days 365 --equity 100000
```
In a cloud session, keys come from environment variables `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`
(run with `--env /dev/null` if there is no `.env`). Create the venv with `uv venv --python 3.12 .venv &&
uv pip install --python .venv/bin/python -r requirements-dev.txt`.

## Status (2026-11-04)
- Owner's Mac: bot installed at `~/Downloads/day-trading`, paper account connected ($100k), Mac in US Central time.
- `orb` baseline, 1 year, **IEX** data: 675 trades, win 45%, avg −0.05R, profit factor 0.91, **−5.4%**,
  max drawdown −5.9%. Don't trade it. (Re-run on SIP data for a fair comparison.)
- `in_play`: built and unit-tested on synthetic data only. **Next: run its 1-year backtest on real data**, read the
  slippage table (needs to stay profitable at 5–10 bp), and report to the owner.
- If `in_play` holds up: paper trade it 4–6 weeks via `autostart_mac.sh install`. If not: try the SPY intraday
  momentum ("noise area") strategy from the research doc next.
- News: Alpaca/Benzinga headlines are used in in-play alerts. X/StockTwits deliberately not used for trading
  (no affordable history to backtest; see research doc).
