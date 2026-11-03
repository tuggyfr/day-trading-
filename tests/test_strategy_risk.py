from datetime import time

import pytest

from bot.config import RiskParams, StrategyParams
from bot.risk import can_open_new_trade, daily_loss_breached, position_size, round_price
from bot.strategy import annotate_day, first_signal, live_signal
from tests.helpers import breakout_day, make_day

P = StrategyParams()


def test_breakout_produces_long_with_range_stop():
    day = breakout_day()
    sig = first_signal("SPY", day, P)
    assert sig is not None and sig.side == "buy"
    assert sig.bar_time.time() == time(9, 46)  # first close above the range high (100.55)
    df = annotate_day(day, P)
    assert sig.stop_price == pytest.approx(df["or_low"].iloc[0])
    assert sig.take_profit == pytest.approx(sig.ref_price + 2 * (sig.ref_price - sig.stop_price))


def test_no_signal_inside_opening_range():
    day = breakout_day().iloc[:15]
    assert first_signal("SPY", day, P) is None


def test_narrow_range_is_skipped():
    day = make_day("2026-03-02", [100.0] * 15 + [100.3] * 30, spread=0.01)
    assert first_signal("SPY", day, P) is None


def test_no_entries_after_cutoff():
    flat = [100.0, 100.3, 99.7] * 5 + [100.0] * 150  # nothing until 12:30
    day = make_day("2026-03-02", flat + [102.0] * 10)
    assert first_signal("SPY", day, P) is None


def test_shorts_off_by_default_and_on_when_enabled():
    rng = [100.0, 100.3, 99.7, 100.4, 99.6, 100.2, 99.8, 100.4, 99.6, 100.0, 100.3, 99.7, 100.1, 99.9, 100.0]
    day = make_day("2026-03-02", rng + [99.7, 99.2, 99.0] + [98.8] * 30)
    assert first_signal("SPY", day, P) is None
    sig = first_signal("SPY", day, StrategyParams(allow_short=True))
    assert sig is not None and sig.side == "sell" and sig.stop_price > sig.ref_price


def test_live_signal_ignores_stale_breakouts():
    day = breakout_day()
    fresh = day[day.index <= day.index[16]]           # signal bar is the latest completed bar
    stale = day[day.index <= day.index[30]]           # breakout happened 14 bars ago
    assert live_signal("SPY", fresh, P) is not None
    assert live_signal("SPY", stale, P) is None


def test_position_size_by_risk():
    # $100k equity, 0.5% risk = $500; $1 stop -> 500 shares; cap 20% = 200 shares at $100
    rp = RiskParams()
    assert position_size(100_000, 400_000, 100.0, 99.0, rp).qty == 200
    assert position_size(100_000, 400_000, 100.0, 95.0, rp).qty == 100  # $5 stop -> 100 shares
    assert position_size(100_000, 1_000, 100.0, 99.0, rp).qty == 10      # buying power limits
    assert position_size(100, 100, 100.0, 99.9, rp).qty == 0


def test_tiny_stop_is_widened():
    rp = RiskParams(max_position_pct=1.0)
    # stop $0.01 away would mean 50,000 shares; min stop 0.1% of $100 = $0.10 -> 5,000 -> capped by equity
    assert position_size(100_000, 10**9, 100.0, 99.99, rp).qty == 1000


def test_kill_switch_and_limits():
    rp = RiskParams()
    assert not daily_loss_breached(99_000, 100_000, rp)
    assert daily_loss_breached(98_000, 100_000, rp)
    assert can_open_new_trade(3, 0, rp)[0] is False
    assert can_open_new_trade(0, 5, rp)[0] is False
    assert can_open_new_trade(1, 1, rp)[0] is True


def test_round_price():
    assert round_price(101.23456) == 101.23
    assert round_price(0.123456) == 0.1235
