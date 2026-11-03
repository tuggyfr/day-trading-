"""Opening-range breakout (ORB) with a VWAP filter.

Pure functions only: no broker, no clock. The live engine and the backtester both call
`annotate_day`, so whatever you backtest is exactly what trades.

Rules (per symbol, per day, US/Eastern):
  1. Opening range = high/low of the first N minutes after 09:30.
  2. Skip the day if the range is too small or too large relative to price.
  3. LONG when a completed bar closes above the range high (and above VWAP).
     SHORT (optional) when a bar closes below the range low (and below VWAP).
  4. Stop = other side of the range (or its midpoint). Target = entry +/- reward_risk x risk.
  5. One trade per symbol per day, no entries after `last_entry_time`, flat by `flatten_time`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time, timedelta, datetime

import pandas as pd

from .config import StrategyParams

MARKET_OPEN = time(9, 30)
TZ = "America/New_York"


@dataclass(frozen=True)
class Signal:
    symbol: str
    side: str            # "buy" or "sell"
    bar_time: pd.Timestamp
    ref_price: float     # close of the signal bar (actual fill will differ)
    stop_price: float
    take_profit: float


def _range_end(params: StrategyParams) -> time:
    end = datetime.combine(datetime(2000, 1, 3), MARKET_OPEN) + timedelta(minutes=params.opening_range_minutes)
    return end.time()


def annotate_day(day: pd.DataFrame, params: StrategyParams) -> pd.DataFrame:
    """Add opening range, VWAP and signal columns to one symbol's regular-session minute bars for one day.

    `day` must be indexed by tz-aware bar *start* timestamps (US/Eastern) with columns
    open, high, low, close, volume. Returns a copy with extra columns:
      or_high, or_low, vwap, in_range, can_enter, long_signal, short_signal
    """
    df = day.sort_index().copy()
    t = df.index.time
    or_end = _range_end(params)

    in_range = (t >= MARKET_OPEN) & (t < or_end)
    df["in_range"] = in_range

    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    cum_vol = df["volume"].cumsum().replace(0, float("nan"))
    df["vwap"] = (typical * df["volume"]).cumsum() / cum_vol

    rng = df[in_range]
    if len(rng) == 0:
        df["or_high"] = float("nan")
        df["or_low"] = float("nan")
    else:
        df["or_high"] = rng["high"].max()
        df["or_low"] = rng["low"].min()

    after_range = t >= or_end
    before_cutoff = t < params.last_entry_time
    df["can_enter"] = after_range & before_cutoff

    if len(rng):
        mid = (df["or_high"] + df["or_low"]) / 2.0
        width_pct = (df["or_high"] - df["or_low"]) / mid
        width_ok = (width_pct >= params.min_range_pct) & (width_pct <= params.max_range_pct)
    else:
        width_ok = pd.Series(False, index=df.index)

    vwap_long = (df["close"] > df["vwap"]) if params.require_vwap else True
    vwap_short = (df["close"] < df["vwap"]) if params.require_vwap else True

    df["long_signal"] = df["can_enter"] & width_ok & (df["close"] > df["or_high"]) & vwap_long
    df["short_signal"] = (
        df["can_enter"] & width_ok & (df["close"] < df["or_low"]) & vwap_short
        if params.allow_short
        else False
    )
    df["short_signal"] = df["short_signal"].astype(bool)
    df["long_signal"] = df["long_signal"].astype(bool)
    return df


def make_signal(symbol: str, row: pd.Series, bar_time: pd.Timestamp, params: StrategyParams) -> Signal | None:
    """Build stop/target for a bar flagged by `annotate_day`."""
    entry = float(row["close"])
    or_high, or_low = float(row["or_high"]), float(row["or_low"])
    mid = (or_high + or_low) / 2.0
    if row["long_signal"]:
        stop = or_low if params.stop_mode == "range" else mid
        risk = entry - stop
        if risk <= 0:
            return None
        return Signal(symbol, "buy", bar_time, entry, stop, entry + params.reward_risk * risk)
    if row["short_signal"]:
        stop = or_high if params.stop_mode == "range" else mid
        risk = stop - entry
        if risk <= 0:
            return None
        return Signal(symbol, "sell", bar_time, entry, stop, entry - params.reward_risk * risk)
    return None


def first_signal(symbol: str, day: pd.DataFrame, params: StrategyParams) -> Signal | None:
    """Backtest use: the first entry signal of the day, if any."""
    df = annotate_day(day, params)
    hits = df[df["long_signal"] | df["short_signal"]]
    if hits.empty:
        return None
    return make_signal(symbol, hits.iloc[0], hits.index[0], params)


def live_signal(symbol: str, day: pd.DataFrame, params: StrategyParams, max_age_bars: int = 3) -> Signal | None:
    """Live use: the day's first signal, but only while it is still fresh.

    Uses the same `first_signal` as the backtest, so live and backtest pick the same bar. If the bot
    was offline when the breakout happened (signal older than `max_age_bars` completed bars), skip it
    rather than chase a move that is already underway.
    """
    if day.empty:
        return None
    sig = first_signal(symbol, day, params)
    if sig is None:
        return None
    bars_since = int((day.index > sig.bar_time).sum())
    return sig if bars_since < max_age_bars else None
