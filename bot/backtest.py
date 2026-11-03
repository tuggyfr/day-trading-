"""Backtest the ORB strategy on historical 1-minute bars.

Uses the same `first_signal` as the live engine. Fill assumptions are deliberately pessimistic:
  * Entry at the OPEN of the bar after the signal bar, plus slippage.
  * Stop/target checked against each bar's high/low. If both are touched in one bar, the stop wins.
  * A bar that gaps through the stop fills at its open (worse than the stop).
  * Anything still open at `flatten_time` exits at that bar's open, minus slippage.
  * Portfolio limits (max open positions, max trades/day, daily loss stop) are enforced.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .config import SYMBOL_RE, RiskParams, StrategyParams
from .risk import position_size
from .strategy import first_signal


@dataclass
class Trade:
    symbol: str
    day: str
    side: str
    entry_time: pd.Timestamp
    entry: float
    stop: float
    target: float
    qty: int
    exit_time: pd.Timestamp
    exit: float
    reason: str
    pnl: float
    r_multiple: float


def _simulate_one(
    symbol: str,
    day: pd.DataFrame,
    sp: StrategyParams,
    equity: float,
    rp: RiskParams,
    slippage_bps: float,
    fee_per_share: float,
) -> Trade | None:
    sig = first_signal(symbol, day, sp)
    if sig is None:
        return None
    after = day[day.index > sig.bar_time]
    if after.empty:
        return None
    entry_bar_time = after.index[0]
    if entry_bar_time.time() >= sp.flatten_time:
        return None

    slip = slippage_bps / 10_000
    long = sig.side == "buy"
    entry = float(after.iloc[0]["open"]) * (1 + slip if long else 1 - slip)
    stop = sig.stop_price
    if (long and entry <= stop) or (not long and entry >= stop):
        return None  # gapped through the stop before we got in
    risk_ps = abs(entry - stop)
    target = entry + sp.reward_risk * risk_ps if long else entry - sp.reward_risk * risk_ps

    size = position_size(equity, equity, entry, stop, rp)
    if size.qty < 1:
        return None
    qty = size.qty

    exit_px, exit_time, reason = None, None, None
    for ts, bar in after.iterrows():
        if ts.time() >= sp.flatten_time:
            exit_px, exit_time, reason = float(bar["open"]), ts, "eod"
            break
        o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])
        if long:
            if o <= stop:
                exit_px, exit_time, reason = o, ts, "stop(gap)"
                break
            if l <= stop:
                exit_px, exit_time, reason = stop, ts, "stop"
                break
            if h >= target:
                exit_px, exit_time, reason = max(o, target), ts, "target"
                break
        else:
            if o >= stop:
                exit_px, exit_time, reason = o, ts, "stop(gap)"
                break
            if h >= stop:
                exit_px, exit_time, reason = stop, ts, "stop"
                break
            if l <= target:
                exit_px, exit_time, reason = min(o, target), ts, "target"
                break
    if exit_px is None:
        exit_px, exit_time, reason = float(after.iloc[-1]["close"]), after.index[-1], "eod"

    if reason != "target":  # stops and market exits slip; limit orders don't
        exit_px *= (1 - slip) if long else (1 + slip)
    gross = (exit_px - entry) * qty if long else (entry - exit_px) * qty
    pnl = gross - 2 * fee_per_share * qty
    return Trade(
        symbol, str(day.index[0].date()), sig.side, entry_bar_time, round(entry, 4), round(stop, 4),
        round(target, 4), qty, exit_time, round(exit_px, 4), reason, round(pnl, 2),
        round(pnl / (risk_ps * qty), 3),
    )


def run_backtest(
    bars: dict[str, pd.DataFrame],
    sp: StrategyParams,
    rp: RiskParams,
    starting_equity: float = 25_000.0,
    slippage_bps: float = 2.0,
    fee_per_share: float = 0.0,
) -> tuple[pd.DataFrame, pd.Series]:
    """Returns (trades DataFrame, daily equity Series)."""
    by_day: dict[object, dict[str, pd.DataFrame]] = {}
    for sym, df in bars.items():
        for d, g in df.groupby(df.index.date):
            by_day.setdefault(d, {})[sym] = g

    equity = starting_equity
    trades: list[Trade] = []
    curve: dict[object, float] = {}
    for d in sorted(by_day):
        start_eq = equity
        candidates = []
        for sym, g in by_day[d].items():
            t = _simulate_one(sym, g, sp, start_eq, rp, slippage_bps, fee_per_share)
            if t is not None:
                candidates.append(t)
        candidates.sort(key=lambda t: t.entry_time)

        taken: list[Trade] = []
        for t in candidates:
            if len(taken) >= rp.max_trades_per_day:
                break
            still_open = [x for x in taken if x.exit_time > t.entry_time]
            if len(still_open) >= rp.max_open_positions:
                continue
            realized = sum(x.pnl for x in taken if x.exit_time <= t.entry_time)
            if realized <= -rp.max_daily_loss_pct * start_eq:
                break
            taken.append(t)

        equity += sum(t.pnl for t in taken)
        trades.extend(taken)
        curve[pd.Timestamp(d)] = equity

    tdf = pd.DataFrame([asdict(t) for t in trades])
    return tdf, pd.Series(curve, name="equity", dtype=float)


def summarize(trades: pd.DataFrame, curve: pd.Series, starting_equity: float) -> dict:
    if trades.empty:
        return {"trades": 0, "note": "no trades. Try more symbols, a longer period, or different params."}
    wins = trades[trades.pnl > 0]
    losses = trades[trades.pnl <= 0]
    eq = pd.concat([pd.Series([starting_equity]), curve.reset_index(drop=True)])
    dd = (eq / eq.cummax() - 1).min()
    daily = curve.pct_change().dropna()
    sharpe = float(np.sqrt(252) * daily.mean() / daily.std()) if len(daily) > 1 and daily.std() > 0 else float("nan")
    gross_loss = -losses.pnl.sum()
    return {
        "trades": int(len(trades)),
        "days": int(len(curve)),
        "win_rate": round(len(wins) / len(trades), 3),
        "avg_R": round(float(trades.r_multiple.mean()), 3),
        "profit_factor": round(float(wins.pnl.sum() / gross_loss), 2) if gross_loss > 0 else float("inf"),
        "net_pnl": round(float(trades.pnl.sum()), 2),
        "return_pct": round(float((curve.iloc[-1] / starting_equity - 1) * 100), 2),
        "max_drawdown_pct": round(float(dd * 100), 2),
        "sharpe_daily": round(sharpe, 2),
        "exits": trades.reason.value_counts().to_dict(),
    }


def load_csv(path: str) -> dict[str, pd.DataFrame]:
    """CSV columns: timestamp,symbol,open,high,low,close,volume (timestamp = bar start, UTC or ET)."""
    from .broker import regular_session

    raw = pd.read_csv(path)
    raw["timestamp"] = pd.to_datetime(raw["timestamp"], utc=True)
    out = {}
    for sym, g in raw.groupby("symbol"):
        if not SYMBOL_RE.fullmatch(str(sym).upper()):
            continue  # ignore junk rows rather than trusting the file
        df = g.set_index("timestamp")[["open", "high", "low", "close", "volume"]].sort_index()
        out[str(sym).upper()] = regular_session(df)
    return out


def load_tradingview_csv(path: str, symbol: str) -> dict[str, pd.DataFrame]:
    """A file from TradingView's chart menu -> "Download chart data" (one symbol per file).

    TradingView writes `time,open,high,low,close,Volume,...` (plus a column per indicator on the chart).
    `time` is Unix seconds by default, or an ISO string if "ISO time" was chosen. Export a 1-minute chart.
    """
    from .broker import regular_session

    raw = pd.read_csv(path)
    raw.columns = [c.strip().lower() for c in raw.columns]
    missing = {"time", "open", "high", "low", "close"} - set(raw.columns)
    if missing:
        raise SystemExit(f"{path}: not a TradingView export (missing columns {sorted(missing)})")
    if "volume" not in raw.columns:
        raw["volume"] = 1  # VWAP then becomes a plain average; export with volume if you can
    t = raw["time"]
    ts = pd.to_datetime(t, unit="s", utc=True) if pd.api.types.is_numeric_dtype(t) else pd.to_datetime(t, utc=True)
    df = raw.assign(timestamp=ts).set_index("timestamp")[["open", "high", "low", "close", "volume"]].sort_index()
    return {symbol.upper(): regular_session(df)}
