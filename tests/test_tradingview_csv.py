import pandas as pd

from bot.backtest import load_tradingview_csv, run_backtest
from bot.config import RiskParams, StrategyParams
from tests.helpers import breakout_day


def _write_tv_export(path, day: pd.DataFrame, iso: bool) -> None:
    """Write bars the way TradingView's "Download chart data" does, including an indicator column."""
    utc = day.index.tz_convert("UTC")
    epoch = pd.Timestamp("1970-01-01", tz="UTC")
    t = utc.strftime("%Y-%m-%dT%H:%M:%SZ") if iso else ((utc - epoch) // pd.Timedelta("1s")).astype("int64")
    out = pd.DataFrame({
        "time": t, "open": day.open.values, "high": day.high.values, "low": day.low.values,
        "close": day.close.values, "VWAP": 0.0, "Volume": day.volume.values,
    })
    # a pre-market bar that must be dropped
    pre = out.iloc[[0]].copy()
    pre["time"] = "2026-03-02T13:00:00Z" if iso else int(pd.Timestamp("2026-03-02T13:00:00Z").timestamp())
    pd.concat([pre, out]).to_csv(path, index=False)


def test_loads_unix_and_iso_exports(tmp_path):
    day = breakout_day(after=[101.5, 102.0, 103.0, 104.0, 105.0] + [105.0] * 50)
    for iso in (False, True):
        p = tmp_path / f"tv_{iso}.csv"
        _write_tv_export(p, day, iso)
        bars = load_tradingview_csv(str(p), "spy")
        assert list(bars) == ["SPY"]
        df = bars["SPY"]
        assert len(df) == len(day)                       # pre-market bar dropped
        assert str(df.index.tz) == "America/New_York"
        assert df.index[0].strftime("%H:%M") == "09:30"
        trades, _ = run_backtest(bars, StrategyParams(), RiskParams(), 100_000, slippage_bps=0)
        assert len(trades) == 1 and trades.iloc[0].reason == "target"
