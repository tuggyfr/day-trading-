import pandas as pd
import pytest

from bot.backtest import run_backtest, summarize
from bot.config import RiskParams, StrategyParams
from tests.helpers import breakout_day, make_day

SP, RP = StrategyParams(), RiskParams()


def test_winner_hits_target_for_2R():
    day = breakout_day(after=[101.5, 102.0, 103.0, 104.0, 105.0] + [105.0] * 100)
    trades, curve = run_backtest({"SPY": day}, SP, RP, 100_000, slippage_bps=0)
    assert len(trades) == 1
    t = trades.iloc[0]
    assert t.reason == "target"
    assert t.r_multiple == pytest.approx(2.0, abs=0.01)
    assert curve.iloc[-1] > 100_000


def test_loser_hits_stop_for_minus_1R():
    day = breakout_day(after=[100.5, 100.0, 99.0, 98.0] + [98.0] * 100)
    trades, _ = run_backtest({"SPY": day}, SP, RP, 100_000, slippage_bps=0)
    t = trades.iloc[0]
    assert t.reason.startswith("stop")
    assert t.r_multiple <= -1.0


def test_open_trade_closed_at_flatten_time():
    n = 18 + 400
    day = breakout_day(after=[101.2] * (n - 18))
    trades, _ = run_backtest({"SPY": day}, SP, RP, 100_000, slippage_bps=0)
    t = trades.iloc[0]
    assert t.reason == "eod"
    assert t.exit_time.time() == pd.Timestamp("15:55").time()


def test_max_open_positions_enforced():
    rp = RiskParams(max_open_positions=1)
    bars = {s: breakout_day(after=[101.2] * 380) for s in ("AAA", "BBB", "CCC")}
    trades, _ = run_backtest(bars, SP, rp, 100_000)
    assert len(trades) == 1


def test_summary_on_multi_day():
    days = [
        breakout_day("2026-03-02", after=[101.5, 102.0, 103.0, 104.0, 105.0] + [105.0] * 50),
        breakout_day("2026-03-03", after=[100.5, 100.0, 99.0, 98.0] + [98.0] * 50),
        make_day("2026-03-04", [100.0] * 60),
    ]
    trades, curve = run_backtest({"SPY": pd.concat(days)}, SP, RP, 100_000)
    s = summarize(trades, curve, 100_000)
    assert s["trades"] == 2 and s["days"] == 3
    assert 0 < s["win_rate"] < 1
