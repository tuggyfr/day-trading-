"""Synthetic minute bars for tests."""

from __future__ import annotations

import pandas as pd

NY = "America/New_York"


def make_day(date: str, closes: list[float], start: str = "09:30", spread: float = 0.05, volume: int = 1000) -> pd.DataFrame:
    """One bar per minute from `start`. open = previous close, high/low = +/- spread around the bar."""
    idx = pd.date_range(f"{date} {start}", periods=len(closes), freq="1min", tz=NY)
    rows, prev = [], closes[0]
    for c in closes:
        o = prev
        rows.append({"open": o, "high": max(o, c) + spread, "low": min(o, c) - spread, "close": c, "volume": volume})
        prev = c
    return pd.DataFrame(rows, index=idx)


def breakout_day(date: str = "2026-03-02", after: list[float] | None = None) -> pd.DataFrame:
    """15 min ranging 99.5-100.5 (range ~99.45-100.55), then a breakout, then whatever `after` says."""
    rng = [100.0, 100.3, 99.7, 100.4, 99.6, 100.2, 99.8, 100.4, 99.6, 100.0, 100.3, 99.7, 100.1, 99.9, 100.0]
    breakout = [100.3, 100.8, 101.0]
    tail = after if after is not None else [101.2] * 200
    return make_day(date, rng + breakout + tail)
