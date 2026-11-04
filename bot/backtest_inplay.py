"""Backtest the stocks-in-play opening-range breakout on real Alpaca data.

Data pipeline (cached in data_cache/, so only the first run is slow):
  1. All active US stocks/ETFs -> daily bars -> 14-day ATR and average volume.
  2. Each day, the opening N-minute bar of every liquid stock -> relative volume vs its last 14 openings.
  3. Pick the top N by relative volume -> download those stocks' 1-minute bars -> simulate the trades.
Then a portfolio pass applies position sizing, leverage and the account-level risk limits.

Known bias: the universe is today's listed stocks, so companies delisted during the test period are missing
(survivorship bias, which flatters results somewhat).
"""

from __future__ import annotations

import hashlib
import logging
import os
import pickle
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from .config import InPlayParams, RiskParams
from .risk import position_size
from .scanner import SimTrade, daily_stats, opening_stats, range_end, select_in_play, simulate_trade

log = logging.getLogger("bot.backtest_inplay")
NY = ZoneInfo("America/New_York")
SESSION_OPEN = time(9, 30)


class _Cache:
    def __init__(self, folder: str):
        self.folder = folder
        os.makedirs(folder, mode=0o700, exist_ok=True)

    def get(self, key: str, make):
        path = os.path.join(self.folder, key + ".pkl")
        if os.path.exists(path):
            with open(path, "rb") as f:
                return pickle.load(f)  # noqa: S301 - only files this program wrote
        value = make()
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump(value, f)
        os.replace(tmp, path)
        return value


def _h(items) -> str:
    return hashlib.sha1(",".join(sorted(items)).encode()).hexdigest()[:10]


def build_dataset(
    broker,
    start: date,
    end: date,
    p: InPlayParams,
    open_feed: str = "sip",
    cache_dir: str = "data_cache",
    progress=print,
):
    """Returns (trading_days, picks_by_day, bars_by_day). Network-heavy on the first run, cached afterwards."""
    cache = _Cache(cache_dir)
    universe, shortable = cache.get(f"universe_{end}", broker.get_universe)
    progress(f"Universe: {len(universe)} active US stocks/ETFs")

    d_start = datetime.combine(start - timedelta(days=45), time(0), tzinfo=NY)
    d_end = datetime.combine(end, time(23, 59), tzinfo=NY)
    daily = cache.get(
        f"daily_{start}_{end}_{_h(universe)}", lambda: broker.get_daily_bars(universe, d_start, d_end, feed="sip")
    )
    stats = daily_stats(daily, p.lookback_days)

    all_days = sorted(d for d in daily["date"].unique())
    test_days = [d for d in all_days if start <= d <= end]
    warmup = [d for d in all_days if d < start][-p.lookback_days:]
    in_window = stats[stats["date"].isin(test_days)]
    eligible = in_window[
        (in_window["avg_volume"] >= p.min_avg_volume)
        & (in_window["atr"] > p.min_atr)
        & (in_window["prev_close"] > p.min_price * 0.8)
    ]["symbol"].unique().tolist()
    progress(f"Liquid candidates: {len(eligible)} stocks; {len(test_days)} trading days (+{len(warmup)} warm-up)")

    key_e = _h(eligible)
    frames = []
    for i, d in enumerate(warmup + test_days):
        frames.append(
            cache.get(
                f"open_{open_feed}_{p.or_minutes}_{d}_{key_e}",
                lambda d=d: broker.get_opening_bars(eligible, d, p.or_minutes, feed=open_feed),
            )
        )
        if (i + 1) % 25 == 0:
            progress(f"  opening ranges: {i + 1}/{len(warmup) + len(test_days)} days")
    opening = opening_stats(pd.concat(frames, ignore_index=True), p.lookback_days)
    merged = opening.merge(stats, on=["symbol", "date"], how="inner")

    picks_by_day: dict[date, pd.DataFrame] = {}
    bars_by_day: dict[date, dict[str, pd.DataFrame]] = {}
    for i, d in enumerate(test_days):
        picks = select_in_play(merged[merged["date"] == d], p, shortable)
        picks_by_day[d] = picks
        if picks.empty:
            bars_by_day[d] = {}
            continue
        syms = picks["symbol"].tolist()
        s = datetime.combine(d, SESSION_OPEN, tzinfo=NY)
        e = datetime.combine(d, time(16, 0), tzinfo=NY)
        bars_by_day[d] = cache.get(f"min_{d}_{_h(syms)}", lambda syms=syms, s=s, e=e: broker.get_bars(syms, s, e, feed="sip"))
        if (i + 1) % 25 == 0:
            progress(f"  intraday bars: {i + 1}/{len(test_days)} days")
    return test_days, picks_by_day, bars_by_day


def simulate_days(test_days, picks_by_day, bars_by_day, p: InPlayParams, flatten: time, slippage_bps: float):
    """Per-share trades for every pick on every day."""
    out: dict[date, list[tuple[SimTrade, float, float]]] = {}
    rend = range_end(p.or_minutes)
    for d in test_days:
        day_trades = []
        for row in picks_by_day[d].itertuples(index=False):
            bars = bars_by_day[d].get(row.symbol)
            if bars is None or bars.empty:
                continue
            t = simulate_trade(row.symbol, row.side, row.trigger, row.stop_dist, bars, rend, p.last_entry_time,
                               flatten, slippage_bps)
            if t is not None:
                day_trades.append((t, row.stop_dist, row.rel_volume))
        out[d] = day_trades
    return out


def run_portfolio(
    sims: dict[date, list[tuple[SimTrade, float, float]]],
    p: InPlayParams,
    rp: RiskParams,
    starting_equity: float,
    fee_per_share: float = 0.0035,
) -> tuple[pd.DataFrame, pd.Series]:
    """Apply sizing, leverage cap, max positions/trades and the daily loss stop. Returns (trades, equity curve)."""
    equity = starting_equity
    rows, curve = [], {}
    for d in sorted(sims):
        start_eq = equity
        open_pos: list[dict] = []
        realized = 0.0
        taken = 0
        for t, stop_dist, rel_vol in sorted(sims[d], key=lambda x: x[0].entry_time):
            still = []
            for pos in open_pos:
                if pos["exit_time"] <= t.entry_time:
                    realized += pos["pnl"]
                else:
                    still.append(pos)
            open_pos = still
            if realized <= -rp.max_daily_loss_pct * start_eq:
                break
            if len(open_pos) >= rp.max_open_positions or taken >= rp.max_trades_per_day:
                continue
            exposure = sum(pos["notional"] for pos in open_pos)
            room = p.max_leverage * start_eq - exposure
            size = position_size(start_eq, room, t.entry, t.stop, rp)
            if size.qty < 1:
                continue
            q = size.qty
            sign = 1 if t.side == "buy" else -1
            pnl = sign * (t.exit - t.entry) * q - 2 * fee_per_share * q
            pos = {"exit_time": t.exit_time, "pnl": pnl, "notional": q * t.entry}
            open_pos.append(pos)
            taken += 1
            rows.append({
                "symbol": t.symbol, "day": str(d), "side": t.side, "entry_time": t.entry_time, "entry": round(t.entry, 4),
                "stop": round(t.stop, 4), "qty": q, "exit_time": t.exit_time, "exit": round(t.exit, 4),
                "reason": t.reason, "rel_volume": round(rel_vol, 2), "pnl": round(pnl, 2),
                "r_multiple": round(pnl / (q * stop_dist), 3) if stop_dist > 0 else 0.0,
            })
        realized_all = sum(r["pnl"] for r in rows if r["day"] == str(d))
        equity = start_eq + realized_all
        curve[pd.Timestamp(d)] = equity
    return pd.DataFrame(rows), pd.Series(curve, name="equity", dtype=float)
