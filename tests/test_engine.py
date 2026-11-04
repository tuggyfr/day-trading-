"""Engine tests against a fake broker. No network, no real orders."""

from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from bot.broker import AccountInfo, ClockInfo, PositionInfo
from bot.config import Settings
from bot.engine import Engine
from tests.helpers import breakout_day

NY = ZoneInfo("America/New_York")


class FakeBroker:
    def __init__(self, bars: dict[str, pd.DataFrame], equity=100_000.0, last_equity=100_000.0):
        self.bars = bars
        self.now = datetime(2026, 3, 2, 9, 50, tzinfo=NY)
        self.is_open = True
        self.account = AccountInfo(equity, last_equity, 4 * equity, False)
        self.positions: list[PositionInfo] = []
        self.orders: list[tuple] = []
        self.closed_all = 0

    def get_clock(self):
        nxt = self.now + timedelta(hours=1)
        return ClockInfo(self.is_open, self.now, nxt, nxt)

    def get_account(self):
        return self.account

    def get_positions(self):
        return self.positions

    def symbols_traded_today(self, now, prefix="orb"):
        return {o[0] for o in self.orders}

    def get_today_bars(self, symbols, now):
        cutoff = pd.Timestamp(now) - pd.Timedelta(minutes=1)
        return {s: df[df.index <= cutoff] for s, df in self.bars.items() if s in symbols}

    def latest_price(self, symbol):
        cutoff = pd.Timestamp(self.now) - pd.Timedelta(minutes=1)
        return float(self.bars[symbol][self.bars[symbol].index <= cutoff]["close"].iloc[-1])

    def submit_bracket(self, symbol, side, qty, stop, take_profit, coid):
        self.orders.append((symbol, side, qty, stop, take_profit, coid))
        self.positions.append(PositionInfo(symbol, qty, "long", 0.0))
        return f"id-{len(self.orders)}"

    def close_all(self):
        self.closed_all += 1
        self.positions = []


def _engine(tmp_path, broker, **kw):
    s = Settings(api_key="k", secret_key="s", symbols=["SPY"], log_dir=str(tmp_path), **kw)
    return Engine(s, broker)


def test_enters_once_with_bracket(tmp_path):
    b = FakeBroker({"SPY": breakout_day()})
    b.now = datetime(2026, 3, 2, 9, 47, 10, tzinfo=NY)  # 09:46 signal bar just completed
    e = _engine(tmp_path, b)
    e.tick()
    assert len(b.orders) == 1
    sym, side, qty, stop, tp, coid = b.orders[0]
    assert (sym, side, coid) == ("SPY", "buy", "orb-20260302-SPY")
    assert stop < 100 < tp
    assert qty == 198  # capped at 20% of $100k equity: floor(20_000 / 100.80)
    assert (tmp_path / "trades.csv").exists()

    b.now += timedelta(minutes=1)
    e.tick()
    assert len(b.orders) == 1  # no duplicate entry


def test_restart_does_not_reenter(tmp_path):
    b = FakeBroker({"SPY": breakout_day()})
    b.now = datetime(2026, 3, 2, 9, 47, 10, tzinfo=NY)
    _engine(tmp_path, b).tick()
    b.positions = []  # position already closed by its stop, say
    _engine(tmp_path, b).tick()  # fresh process, same day
    assert len(b.orders) == 1


def test_no_entry_before_range_complete(tmp_path):
    b = FakeBroker({"SPY": breakout_day()})
    b.now = datetime(2026, 3, 2, 9, 40, tzinfo=NY)
    _engine(tmp_path, b).tick()
    assert b.orders == []


def test_flattens_at_end_of_day(tmp_path):
    b = FakeBroker({"SPY": breakout_day()})
    b.now = datetime(2026, 3, 2, 15, 56, tzinfo=NY)
    e = _engine(tmp_path, b)
    e.tick()
    e.tick()
    assert b.closed_all == 1 and b.orders == []


def test_kill_switch_closes_everything(tmp_path):
    b = FakeBroker({"SPY": breakout_day()}, equity=97_500, last_equity=100_000)
    b.now = datetime(2026, 3, 2, 9, 47, 10, tzinfo=NY)
    e = _engine(tmp_path, b)
    e.tick()
    assert b.closed_all == 1 and b.orders == []
    b.account = replace(b.account, equity=100_000)
    e.tick()
    assert b.orders == []  # stays halted for the rest of the day


def test_market_closed_does_nothing(tmp_path):
    b = FakeBroker({"SPY": breakout_day()})
    b.is_open = False
    wait = _engine(tmp_path, b).tick()
    assert wait > 0 and b.orders == [] and b.closed_all == 0


def test_live_mode_requires_double_opt_in():
    import pytest

    with pytest.raises(SystemExit):
        Settings(api_key="k", secret_key="s", symbols=["SPY"], paper=False).validate_for_trading()
    Settings(api_key="k", secret_key="s", symbols=["SPY"], paper=False, live_confirmed=True).validate_for_trading()
