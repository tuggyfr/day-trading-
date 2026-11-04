"""Stocks-in-play selection and trade simulation. Pure functions, no broker calls.

Shared by the backtest and the live engine, so both pick the same stocks with the same levels.
Rules follow Zarattini, Barbon & Aziz (2024); see docs/strategy-research.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import pandas as pd

from .config import InPlayParams


def daily_stats(daily: pd.DataFrame, lookback: int = 14) -> pd.DataFrame:
    """Per symbol and date: 14-day ATR and average volume, using only days BEFORE that date.

    `daily` columns: symbol, date, open, high, low, close, volume (one row per symbol per day).
    Returns columns: symbol, date, atr, avg_volume, prev_close.
    """
    df = daily.sort_values(["symbol", "date"]).copy()
    g = df.groupby("symbol", sort=False)
    df["prev_close"] = g["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - df["prev_close"]).abs(), (df["low"] - df["prev_close"]).abs()], axis=1
    ).max(axis=1)
    df["tr"] = tr
    # shift(1): today's ATR/volume are not known at 09:35, so use the previous `lookback` days only
    df["atr"] = g["tr"].transform(lambda s: s.rolling(lookback, min_periods=lookback).mean().shift(1))
    df["avg_volume"] = g["volume"].transform(lambda s: s.rolling(lookback, min_periods=lookback).mean().shift(1))
    return df[["symbol", "date", "atr", "avg_volume", "prev_close"]]


def latest_stats(daily: pd.DataFrame, lookback: int = 14) -> pd.DataFrame:
    """Live pre-market version of daily_stats: ATR / average volume over the most recent `lookback` sessions.

    Returns one row per symbol: symbol, atr, avg_volume, prev_close (symbols with too little history dropped).
    """
    df = daily.sort_values(["symbol", "date"]).copy()
    g = df.groupby("symbol", sort=False)
    prev = g["close"].shift(1)
    df["tr"] = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    tail = df.groupby("symbol", sort=False).tail(lookback)
    agg = tail.groupby("symbol").agg(atr=("tr", "mean"), avg_volume=("volume", "mean"), n=("tr", "size"),
                                     prev_close=("close", "last"))
    return agg[agg["n"] >= lookback].drop(columns="n").reset_index()


def range_end(minutes: int) -> time:
    return (datetime.combine(date(2000, 1, 3), time(9, 30)) + timedelta(minutes=minutes)).time()


def opening_stats(opening: pd.DataFrame, lookback: int = 14) -> pd.DataFrame:
    """Add relative volume to opening-range bars.

    `opening` columns: symbol, date, or_open, or_high, or_low, or_close, or_volume (one row per symbol per day:
    the first N-minute bar of the session). rel_volume = today's opening volume / average opening volume of the
    previous `lookback` sessions.
    """
    df = opening.sort_values(["symbol", "date"]).copy()
    avg = df.groupby("symbol", sort=False)["or_volume"].transform(
        lambda s: s.rolling(lookback, min_periods=lookback).mean().shift(1)
    )
    df["avg_or_volume"] = avg
    df["rel_volume"] = df["or_volume"] / avg.where(avg > 0)
    return df


def select_in_play(day: pd.DataFrame, p: InPlayParams, shortable: set[str] | None = None) -> pd.DataFrame:
    """Pick today's trades from one day's rows of opening_stats merged with daily_stats.

    Returns the top `p.top_n` by relative volume with columns: symbol, side, trigger, stop_dist, rel_volume, atr.
    """
    d = day.dropna(subset=["atr", "avg_volume", "rel_volume"])
    d = d[
        (d["or_open"] > p.min_price)
        & (d["avg_volume"] >= p.min_avg_volume)
        & (d["atr"] > p.min_atr)
        & (d["rel_volume"] >= p.min_rel_volume)
        & (d["or_close"] != d["or_open"])          # doji opening candle: no direction, no trade
    ].copy()
    d["side"] = (d["or_close"] > d["or_open"]).map({True: "buy", False: "sell"})
    if not p.allow_short:
        d = d[d["side"] == "buy"]
    elif shortable is not None:
        d = d[(d["side"] == "buy") | d["symbol"].isin(shortable)]
    d = d.sort_values("rel_volume", ascending=False).head(p.top_n)
    d["trigger"] = d["or_high"].where(d["side"] == "buy", d["or_low"])
    d["stop_dist"] = p.stop_atr_frac * d["atr"]
    return d[["symbol", "side", "trigger", "stop_dist", "rel_volume", "atr"]].reset_index(drop=True)


@dataclass(frozen=True)
class SimTrade:
    symbol: str
    side: str
    entry_time: pd.Timestamp
    entry: float
    stop: float
    exit_time: pd.Timestamp
    exit: float
    reason: str


def simulate_trade(
    symbol: str,
    side: str,
    trigger: float,
    stop_dist: float,
    bars: pd.DataFrame,
    range_end: time,
    last_entry: time,
    flatten: time,
    slippage_bps: float,
) -> SimTrade | None:
    """Per-share result of one in-play trade on one day's 1-minute bars (index = bar start, US/Eastern).

    Pessimistic fills: entry at the trigger (or the bar's open if it gapped past it) plus slippage; if the stop is
    touched in the entry bar or any later bar, the stop fills (at the bar open if it gapped through). Stop and
    end-of-day exits pay slippage too. Positions still open at `flatten` exit at that bar's open.
    """
    slip = slippage_bps / 10_000
    long = side == "buy"
    t = bars.index.time
    after = bars[(t >= range_end)]
    entry = stop = None
    entry_time = None
    for ts, bar in after.iterrows():
        o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])
        tt = ts.time()
        if entry is None:
            if tt >= last_entry or tt >= flatten:
                return None
            if long and h >= trigger:
                if o > trigger + stop_dist:
                    return None  # gapped too far past the trigger: live bot skips rather than chase
                entry = max(trigger, o) * (1 + slip)
            elif not long and l <= trigger:
                if o < trigger - stop_dist:
                    return None
                entry = min(trigger, o) * (1 - slip)
            else:
                continue
            entry_time = ts
            stop = entry - stop_dist if long else entry + stop_dist
            # same bar: assume the worst, i.e. the stop was hit after we got in
            if (long and l <= stop) or (not long and h >= stop):
                px = stop * (1 - slip) if long else stop * (1 + slip)
                return SimTrade(symbol, side, entry_time, entry, stop, ts, px, "stop")
            continue
        if tt >= flatten:
            px = o * (1 - slip) if long else o * (1 + slip)
            return SimTrade(symbol, side, entry_time, entry, stop, ts, px, "eod")
        if long:
            if o <= stop:
                return SimTrade(symbol, side, entry_time, entry, stop, ts, o * (1 - slip), "stop(gap)")
            if l <= stop:
                return SimTrade(symbol, side, entry_time, entry, stop, ts, stop * (1 - slip), "stop")
        else:
            if o >= stop:
                return SimTrade(symbol, side, entry_time, entry, stop, ts, o * (1 + slip), "stop(gap)")
            if h >= stop:
                return SimTrade(symbol, side, entry_time, entry, stop, ts, stop * (1 + slip), "stop")
    if entry is None:
        return None
    last_ts = after.index[-1]
    close = float(after.iloc[-1]["close"])
    px = close * (1 - slip) if long else close * (1 + slip)
    return SimTrade(symbol, side, entry_time, entry, stop, last_ts, px, "eod")
