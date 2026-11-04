"""Command line.

  python -m bot status                         account, market clock, open positions
  python -m bot backtest --days 60             backtest on Alpaca historical data
  python -m bot backtest --strategy in_play --days 365   stocks-in-play backtest
  python -m bot backtest --csv bars.csv        backtest on your own CSV
  python -m bot backtest --tv-csv spy.csv --symbols SPY   backtest on a TradingView chart export
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


def _redactor(settings):
    from .notifier import Redactor

    return Redactor([settings.api_key, settings.secret_key, settings.telegram_bot_token, settings.discord_webhook_url])


def _setup_logging(settings, verbose: bool) -> None:
    from .notifier import RedactingFilter

    os.makedirs(settings.log_dir, mode=0o700, exist_ok=True)  # logs show your balance: keep them private
    fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    handlers = [logging.StreamHandler(sys.stdout), logging.FileHandler(os.path.join(settings.log_dir, "bot.log"))]
    redacting = RedactingFilter(_redactor(settings))
    for h in handlers:
        h.addFilter(redacting)  # API keys / tokens never reach the screen or the log file
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format=fmt, handlers=handlers)
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
    if settings.strategy_name == "in_play":
        ip = settings.in_play
        print(f"Strategy:      stocks in play (top {ip.top_n} by opening volume, {ip.or_minutes}-min range, "
              f"shorts {'on' if ip.allow_short else 'off'})")
    else:
        print("Strategy:      opening-range breakout on a fixed list")
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
    n = Notifier(
        settings.telegram_bot_token, settings.telegram_chat_id, settings.discord_webhook_url, _redactor(settings)
    )
    if settings.strategy_name == "in_play":
        from .engine_inplay import InPlayEngine

        InPlayEngine(settings, b, n).run_forever()
    else:
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
    from .backtest import load_csv, load_tradingview_csv, run_backtest, summarize
    from .config import SYMBOL_RE

    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else settings.symbols
    bad = [s for s in symbols if not SYMBOL_RE.fullmatch(s)]
    if bad:
        raise SystemExit(f"invalid symbols: {bad}")
    if not (1 <= args.days <= 3650):
        raise SystemExit("--days must be between 1 and 3650")
    if args.equity <= 0 or args.slippage_bps < 0 or args.fee_per_share < 0:
        raise SystemExit("--equity must be > 0; --slippage-bps and --fee-per-share must be >= 0")
    if (args.strategy or settings.strategy_name) == "in_play":
        return _backtest_in_play(settings, args)
    if args.tv_csv:
        if not args.symbols or len(symbols) != 1:
            raise SystemExit("--tv-csv needs --symbols with exactly one symbol, e.g. --symbols SPY")
        bars = load_tradingview_csv(args.tv_csv, symbols[0])
    elif args.csv:
        bars = {k: v for k, v in load_csv(args.csv).items() if k in symbols}
    else:
        b = _broker(settings)
        end = datetime.now(NY) - timedelta(minutes=20)  # free data plans can't query the last 15 min
        start = end - timedelta(days=args.days)
        print(f"Downloading 1-min bars for {len(symbols)} symbols, {start:%Y-%m-%d} -> {end:%Y-%m-%d} ...")
        bars = b.get_bars(symbols, start, end, feed=args.feed)
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


def _backtest_in_play(settings, args) -> None:
    from .backtest import summarize
    from .backtest_inplay import build_dataset, run_portfolio, simulate_days

    b = _broker(settings)
    end = (datetime.now(NY) - timedelta(days=1)).date()  # whole days only; today isn't finished
    start = end - timedelta(days=args.days)
    p, rp = settings.in_play, settings.risk
    print(f"Stocks-in-play backtest {start} -> {end}: top {p.top_n} by opening relative volume, "
          f"{p.or_minutes}-min range, stop {p.stop_atr_frac:.0%} of ATR, max leverage {p.max_leverage}x, "
          f"risk {rp.risk_per_trade_pct:.2%}/trade, max {rp.max_open_positions} open.")
    print("First run downloads a lot of data (can take 10-20 minutes); later runs use data_cache/.\n")
    days, picks, bars = build_dataset(b, start, end, p, open_feed=args.open_feed, cache_dir=args.cache_dir)

    fee = args.fee_per_share if args.fee_per_share > 0 else 0.0035
    main_trades = main_curve = None
    print("\nSlippage sensitivity (each way, on entries and stop/close exits):")
    print(f"  {'slippage':>9} {'trades':>7} {'win%':>6} {'avg_R':>7} {'PF':>6} {'return%':>8} {'maxDD%':>7}")
    for bps in sorted({0.0, 2.0, 5.0, 10.0, args.slippage_bps}):
        sims = simulate_days(days, picks, bars, p, settings.strategy.flatten_time, bps)
        trades, curve = run_portfolio(sims, p, rp, args.equity, fee)
        sm = summarize(trades, curve, args.equity)
        if sm.get("trades", 0):
            print(f"  {bps:>7.1f}bp {sm['trades']:>7} {sm['win_rate']*100:>5.1f} {sm['avg_R']:>7.3f} "
                  f"{sm['profit_factor']:>6} {sm['return_pct']:>8} {sm['max_drawdown_pct']:>7}")
        else:
            print(f"  {bps:>7.1f}bp       0")
        if bps == args.slippage_bps:
            main_trades, main_curve = trades, curve

    print(f"\nDetail at {args.slippage_bps} bp slippage, ${fee}/share commission:")
    print(json.dumps(summarize(main_trades, main_curve, args.equity), indent=2, default=str))
    if main_trades is not None and not main_trades.empty:
        print("\nLong vs short:")
        print(main_trades.groupby("side").agg(trades=("pnl", "size"), pnl=("pnl", "sum"), avg_R=("r_multiple", "mean")).round(2))
        os.makedirs(settings.log_dir, exist_ok=True)
        out = os.path.join(settings.log_dir, "backtest_inplay_trades.csv")
        main_trades.to_csv(out, index=False)
        print(f"\nTrade list saved to {out}")
    print("\nNote: the universe is today's listed stocks (delisted ones are missing), which flatters results a bit.")


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
    bt.add_argument("--tv-csv", help="use a TradingView 'Download chart data' export (1-minute chart)")
    bt.add_argument("--symbols", help="comma list; default BOT_SYMBOLS")
    bt.add_argument("--equity", type=float, default=25_000.0)
    bt.add_argument("--slippage-bps", type=float, default=2.0)
    bt.add_argument("--fee-per-share", type=float, default=0.0)
    bt.add_argument("--strategy", choices=["orb", "in_play"], help="default: BOT_STRATEGY from .env")
    bt.add_argument("--feed", default="sip", choices=["sip", "iex"],
                    help="price data for backtests (default sip: full market, free for history)")
    bt.add_argument("--open-feed", default="sip", choices=["sip", "iex"],
                    help="in_play: data used to rank opening volume (live free plan uses iex)")
    bt.add_argument("--cache-dir", default="data_cache", help="in_play: where downloaded data is cached")
    args = ap.parse_args(argv)

    settings = load_settings(args.env)
    _setup_logging(settings, args.verbose)
    {"status": cmd_status, "run": cmd_run, "flatten": cmd_flatten, "backtest": cmd_backtest}[args.cmd](settings, args)


if __name__ == "__main__":
    main()
