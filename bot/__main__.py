"""Command line.

  python -m bot status                         account, market clock, open positions
  python -m bot backtest --days 60             backtest on Alpaca historical data
  python -m bot backtest --csv bars.csv        backtest on your own CSV
  python -m bot run                            start trading (paper unless you opted into live)
  python -m bot flatten                        PANIC BUTTON: cancel all orders, close all positions
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import load_settings

NY = ZoneInfo("America/New_York")


def _setup_logging(log_dir: str, verbose: bool) -> None:
    os.makedirs(log_dir, exist_ok=True)
    fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=fmt,
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(os.path.join(log_dir, "bot.log"))],
    )
    for noisy in ("urllib3", "alpaca"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _broker(settings):
    from .broker import AlpacaBroker

    settings.validate_for_trading()
    return AlpacaBroker(settings.api_key, settings.secret_key, settings.paper, settings.data_feed)


def cmd_status(settings, _args) -> None:
    b = _broker(settings)
    a, c = b.get_account(), b.get_clock()
    print(f"Mode:          {'PAPER' if settings.paper else 'LIVE MONEY'}")
    print(f"Market open:   {c.is_open}  (now {c.now:%Y-%m-%d %H:%M} ET, next open {c.next_open:%a %H:%M})")
    print(f"Equity:        ${a.equity:,.2f}  (today {a.equity - a.last_equity:+,.2f})")
    print(f"Buying power:  ${a.buying_power:,.2f}")
    print(f"Symbols:       {', '.join(settings.symbols)}")
    positions = b.get_positions()
    print(f"Positions:     {len(positions) or 'none'}")
    for p in positions:
        print(f"  {p.symbol:6} {p.qty:>8g} {p.side:5} unrealized {p.unrealized_pl:+,.2f}")


def cmd_run(settings, _args) -> None:
    from .engine import Engine
    from .notifier import Notifier

    b = _broker(settings)
    if not settings.paper:
        print("*** LIVE MONEY MODE ***  Ctrl+C within 10 seconds to abort.")
        import time

        time.sleep(10)
    n = Notifier(settings.telegram_bot_token, settings.telegram_chat_id, settings.discord_webhook_url)
    Engine(settings, b, n).run_forever()


def cmd_flatten(settings, args) -> None:
    b = _broker(settings)
    if not args.yes:
        if input("Cancel ALL orders and close ALL positions? type 'yes': ").strip().lower() != "yes":
            print("aborted")
            return
    b.close_all()
    print("All orders cancelled, all positions closing.")


def cmd_backtest(settings, args) -> None:
    from .backtest import load_csv, run_backtest, summarize

    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else settings.symbols
    if args.csv:
        bars = {k: v for k, v in load_csv(args.csv).items() if k in symbols}
    else:
        b = _broker(settings)
        end = datetime.now(NY) - timedelta(minutes=20)  # free data plans can't query the last 15 min
        start = end - timedelta(days=args.days)
        print(f"Downloading 1-min bars for {len(symbols)} symbols, {start:%Y-%m-%d} -> {end:%Y-%m-%d} ...")
        bars = b.get_bars(symbols, start, end)
    if not bars:
        raise SystemExit("No bar data loaded.")

    trades, curve = run_backtest(
        bars, settings.strategy, settings.risk, args.equity, args.slippage_bps, args.fee_per_share
    )
    print(json.dumps(summarize(trades, curve, args.equity), indent=2, default=str))
    if not trades.empty:
        print("\nBy symbol:")
        print(trades.groupby("symbol").agg(trades=("pnl", "size"), pnl=("pnl", "sum"), avg_R=("r_multiple", "mean")).round(2))
        os.makedirs(settings.log_dir, exist_ok=True)
        out = os.path.join(settings.log_dir, "backtest_trades.csv")
        trades.to_csv(out, index=False)
        print(f"\nTrade list saved to {out}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m bot", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", default=".env", help="env file to load (default .env)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("run")
    f = sub.add_parser("flatten")
    f.add_argument("--yes", action="store_true", help="skip confirmation")
    bt = sub.add_parser("backtest")
    bt.add_argument("--days", type=int, default=90, help="calendar days of history (Alpaca)")
    bt.add_argument("--csv", help="use a CSV file instead of Alpaca")
    bt.add_argument("--symbols", help="comma list; default BOT_SYMBOLS")
    bt.add_argument("--equity", type=float, default=25_000.0)
    bt.add_argument("--slippage-bps", type=float, default=2.0)
    bt.add_argument("--fee-per-share", type=float, default=0.0)
    args = ap.parse_args(argv)

    settings = load_settings(args.env)
    _setup_logging(settings.log_dir, args.verbose)
    {"status": cmd_status, "run": cmd_run, "flatten": cmd_flatten, "backtest": cmd_backtest}[args.cmd](settings, args)


if __name__ == "__main__":
    main()
