"""Stocks-in-play: scanner rules, trade simulation, backtest pipeline and live engine (fake brokers only)."""

from dataclasses import replace
from datetime import datetime, time

import pandas as pd
import pytest

from bot.backtest_inplay import build_dataset, run_portfolio, simulate_days
from bot.broker import AccountInfo, ClockInfo, PositionInfo
from bot.config import InPlayParams, RiskParams, Settings
from bot.engine_inplay import InPlayEngine
from bot.scanner import daily_stats, latest_stats, opening_stats, select_in_play, simulate_trade
from zoneinfo import ZoneInfo

from tests.helpers import make_day

NY = ZoneInfo("America/New_York")

P = InPlayParams()
DAYS = [d.date() for d in pd.bdate_range("2026-02-02", periods=20)]
TEST_DAY = DAYS[-1]

# name: (price, avg daily volume, opening volume on TEST_DAY, opening candle direction on TEST_DAY)
STOCKS = {
    "HOT": (100.0, 2_000_000, 50_000, "up"),      # 5x volume, should be picked, long
    "DROP": (100.0, 2_000_000, 30_000, "down"),   # 3x, short, shortable
    "NOSHORT": (100.0, 2_000_000, 40_000, "down"),  # 4x, short but not shortable -> excluded
    "COLD": (100.0, 2_000_000, 5_000, "up"),      # 0.5x -> excluded
    "PENNY": (3.0, 2_000_000, 50_000, "up"),      # price < $5 -> excluded
    "THIN": (100.0, 100_000, 50_000, "up"),       # avg volume < 1M -> excluded
    "DOJI": (100.0, 2_000_000, 60_000, "flat"),   # no direction -> excluded
}
SHORTABLE = {"DROP", "HOT"}


def _daily():
    rows = []
    for sym, (px, vol, _, _) in STOCKS.items():
        for d in DAYS:
            rows.append({"symbol": sym, "date": d, "open": px, "high": px + 1, "low": px - 1, "close": px, "volume": vol})
    return pd.DataFrame(rows)


def _opening(day):
    rows = []
    for sym, (px, _, test_vol, direction) in STOCKS.items():
        vol = test_vol if day == TEST_DAY else 10_000
        c = {"up": px + 0.1, "down": px - 0.1, "flat": px}[direction] if day == TEST_DAY else px + 0.1
        rows.append({"symbol": sym, "date": day, "or_open": px, "or_high": px + 0.3, "or_low": px - 0.3,
                     "or_close": c, "or_volume": vol})
    return pd.DataFrame(rows)


def _intraday(sym, day):
    px = STOCKS[sym][0]
    rng = [px, px + 0.2, px - 0.2, px + 0.1, px]                    # 09:30-09:34 opening range
    if sym == "HOT":
        tail = [px + 0.2, px + 0.35, px + 0.6, px + 1.0] + [px + 2.0] * 400   # breaks up, runs
    elif sym == "DROP":
        tail = [px - 0.2, px - 0.35, px - 0.6, px - 1.0] + [px - 2.0] * 400   # breaks down, runs
    else:
        tail = [px] * 404
    return make_day(str(day), rng + tail, spread=0.02)


# ---- scanner ------------------------------------------------------------------------------------
def test_daily_stats_use_only_prior_days():
    df = _daily()
    # make the last day huge: it must NOT affect that same day's ATR/volume
    df.loc[(df.symbol == "HOT") & (df.date == TEST_DAY), ["high", "low", "volume"]] = [200, 1, 10**9]
    st = daily_stats(df, 14)
    row = st[(st.symbol == "HOT") & (st.date == TEST_DAY)].iloc[0]
    assert row.atr == pytest.approx(2.0) and row.avg_volume == pytest.approx(2_000_000)
    assert st[(st.symbol == "HOT") & (st.date == DAYS[5])].atr.isna().all()  # not enough history yet


def test_latest_stats_for_live_prep():
    st = latest_stats(_daily(), 14).set_index("symbol")
    assert st.loc["HOT", "atr"] == pytest.approx(2.0) and st.loc["THIN", "avg_volume"] == 100_000


def test_selection_rules():
    op = opening_stats(pd.concat([_opening(d) for d in DAYS]), 14)
    merged = op.merge(daily_stats(_daily(), 14), on=["symbol", "date"])
    day = merged[merged.date == TEST_DAY]
    picks = select_in_play(day, P, SHORTABLE)
    assert picks.symbol.tolist() == ["HOT", "DROP"]           # ranked by relative volume, filters applied
    hot = picks.set_index("symbol").loc["HOT"]
    assert hot.side == "buy" and hot.trigger == pytest.approx(100.3) and hot.stop_dist == pytest.approx(0.2)
    assert picks.set_index("symbol").loc["DROP"].trigger == pytest.approx(99.7)
    assert select_in_play(day, replace(P, allow_short=False), SHORTABLE).symbol.tolist() == ["HOT"]
    assert select_in_play(day, replace(P, top_n=1), SHORTABLE).symbol.tolist() == ["HOT"]


# ---- trade simulation ---------------------------------------------------------------------------
RE, LAST, FLAT = time(9, 35), time(15, 0), time(15, 55)


def test_long_runs_to_close():
    t = simulate_trade("HOT", "buy", 100.3, 0.2, _intraday("HOT", TEST_DAY), RE, LAST, FLAT, 0)
    assert t.reason == "eod" and t.entry == pytest.approx(100.3) and t.exit == pytest.approx(102.0)
    assert t.exit_time.time() == FLAT


def test_short_runs_to_close():
    t = simulate_trade("DROP", "sell", 99.7, 0.2, _intraday("DROP", TEST_DAY), RE, LAST, FLAT, 0)
    assert t.reason == "eod" and t.exit < t.entry


def test_stop_and_same_bar_stop():
    bars = make_day(str(TEST_DAY), [100, 100.1, 100, 100.1, 100] + [100.35, 100.0] + [100.0] * 50, spread=0.02)
    t = simulate_trade("X", "buy", 100.3, 0.2, bars, RE, LAST, FLAT, 0)
    assert t.reason == "stop" and t.exit == pytest.approx(t.entry - 0.2)
    wide = make_day(str(TEST_DAY), [100] * 5 + [100.31] * 3, spread=0.3)  # one bar spans trigger and stop
    t2 = simulate_trade("X", "buy", 100.3, 0.2, wide, RE, LAST, FLAT, 0)
    assert t2.reason == "stop" and t2.entry_time == t2.exit_time


def test_no_chasing_and_no_trigger():
    gap = make_day(str(TEST_DAY), [100] * 5 + [101.0] * 20, spread=0.02)
    gap.iloc[5, gap.columns.get_loc("open")] = 101.0   # 09:35 candle OPENS at 101: 0.7 past trigger > stop 0.2
    gap.iloc[5, gap.columns.get_loc("low")] = 100.98
    assert simulate_trade("X", "buy", 100.3, 0.2, gap, RE, LAST, FLAT, 0) is None
    small = gap.copy()
    small.iloc[5, small.columns.get_loc("open")] = 100.4   # only 0.1 past trigger: take it, fill at the open
    small.iloc[5, small.columns.get_loc("low")] = 100.38
    t = simulate_trade("X", "buy", 100.3, 0.2, small, RE, LAST, FLAT, 0)
    assert t is not None and t.entry == pytest.approx(100.4)
    flat = make_day(str(TEST_DAY), [100] * 60, spread=0.02)
    assert simulate_trade("X", "buy", 100.3, 0.2, flat, RE, LAST, FLAT, 0) is None


def test_slippage_makes_it_worse():
    b = _intraday("HOT", TEST_DAY)
    t0 = simulate_trade("HOT", "buy", 100.3, 0.2, b, RE, LAST, FLAT, 0)
    t10 = simulate_trade("HOT", "buy", 100.3, 0.2, b, RE, LAST, FLAT, 10)
    assert t10.entry > t0.entry and t10.exit < t0.exit


# ---- backtest pipeline --------------------------------------------------------------------------
class FakeData:
    def __init__(self):
        self.calls = 0

    def get_universe(self):
        self.calls += 1
        return sorted(STOCKS), SHORTABLE

    def get_daily_bars(self, symbols, start, end, feed="sip"):
        self.calls += 1
        return _daily()

    def get_opening_bars(self, symbols, day, minutes, feed="sip"):
        self.calls += 1
        op = _opening(day)
        return op[op.symbol.isin(symbols)]

    def get_bars(self, symbols, start, end, feed=None):
        self.calls += 1
        return {s: _intraday(s, start.date()) for s in symbols}


def test_backtest_pipeline_and_cache(tmp_path):
    fake = FakeData()
    days, picks, bars = build_dataset(fake, TEST_DAY, TEST_DAY, P, cache_dir=str(tmp_path), progress=lambda *_: None)
    assert days == [TEST_DAY] and picks[TEST_DAY].symbol.tolist() == ["HOT", "DROP"]
    first_calls = fake.calls
    build_dataset(fake, TEST_DAY, TEST_DAY, P, cache_dir=str(tmp_path), progress=lambda *_: None)
    assert fake.calls == first_calls  # second run served entirely from cache

    sims = simulate_days(days, picks, bars, P, FLAT, 0)
    trades, curve = run_portfolio(sims, P, RiskParams(), 100_000, fee_per_share=0)
    assert set(trades.symbol) == {"HOT", "DROP"} and (trades.pnl > 0).all()
    # leverage cap: total position value never above max_leverage x equity
    assert (trades.qty * trades.entry).sum() <= P.max_leverage * 100_000 + 1
    one = run_portfolio(sims, P, RiskParams(max_open_positions=1), 100_000, fee_per_share=0)[0]
    assert len(one) == 1


# ---- live engine ----------------------------------------------------------------------------------
class FakeLive(FakeData):
    def __init__(self):
        super().__init__()
        self.now = datetime.combine(TEST_DAY, time(8, 50), tzinfo=NY)
        self.is_open = False
        self.account = AccountInfo(100_000, 100_000, 400_000, False)
        self.positions: list[PositionInfo] = []
        self.orders: list[tuple] = []
        self.closed = 0
        self.price: dict[str, float] = {}

    def get_clock(self):
        nxt = datetime.combine(TEST_DAY, time(9, 30), tzinfo=NY)
        return ClockInfo(self.is_open, self.now, nxt, datetime.combine(TEST_DAY, time(16), tzinfo=NY))

    def get_account(self):
        return self.account

    def get_positions(self):
        return self.positions

    def symbols_traded_today(self, now, prefix="orb"):
        return {o[0] for o in self.orders}

    def get_daily_bars(self, symbols, start, end, feed="sip"):
        d = _daily()
        return d[d.date < TEST_DAY]  # live prep only sees completed sessions

    def latest_prices(self, symbols):
        return {s: self.price.get(s, STOCKS[s][0]) for s in symbols}

    def get_headlines(self, symbols, since):
        return {"HOT": "HOT beats earnings, raises guidance"}

    def submit_with_stop(self, symbol, side, qty, stop, coid):
        self.orders.append((symbol, side, qty, stop, coid))
        self.positions.append(PositionInfo(symbol, qty, "long" if side == "buy" else "short", 0.0, qty * 100.0))
        return f"id{len(self.orders)}"

    def close_all(self):
        self.closed += 1
        self.positions = []


class Alerts:
    def __init__(self):
        self.msgs = []

    def send(self, text):
        self.msgs.append(text)


def _live_engine(tmp_path, broker):
    s = Settings(api_key="k", secret_key="s", symbols=["SPY"], log_dir=str(tmp_path), strategy_name="in_play")
    alerts = Alerts()
    return InPlayEngine(s, broker, alerts), alerts


def test_live_day(tmp_path):
    b = FakeLive()
    e, alerts = _live_engine(tmp_path, b)

    e.tick()  # 08:50, closed: pre-market prep
    assert e.prep["day"] == TEST_DAY and "Pre-market scan ready" in alerts.msgs[-1]

    b.is_open = True
    b.now = datetime.combine(TEST_DAY, time(9, 35, 5), tzinfo=NY)
    e.tick()  # opening bar may not be complete yet
    assert e.watch_day is None

    b.now = datetime.combine(TEST_DAY, time(9, 35, 30), tzinfo=NY)
    e.tick()  # scan: HOT and DROP picked, nothing triggered yet (prices at 100)
    assert e.watch.symbol.tolist() == ["HOT", "DROP"] and b.orders == []
    assert "HOT beats earnings" in alerts.msgs[-1]

    b.price = {"HOT": 100.35, "DROP": 100.0}
    b.now = datetime.combine(TEST_DAY, time(9, 36), tzinfo=NY)
    e.tick()  # HOT triggers: market buy with stop 10% ATR below
    assert len(b.orders) == 1
    sym, side, qty, stop, coid = b.orders[0]
    assert (sym, side, coid) == ("HOT", "buy", f"inplay-{TEST_DAY:%Y%m%d}-HOT")
    assert stop == pytest.approx(100.15) and qty == 199  # 20% cap: floor(20_000 / 100.35)
    e.tick()
    assert len(b.orders) == 1  # no double entry

    b.price = {"HOT": 100.35, "DROP": 98.0}  # DROP gapped 1.7 past its 99.70 trigger: don't chase
    e.tick()
    assert len(b.orders) == 1 and "DROP" in e.state.entered

    b.now = datetime.combine(TEST_DAY, time(15, 56), tzinfo=NY)
    e.tick()
    assert b.closed == 1


def test_live_short_and_restart(tmp_path):
    b = FakeLive()
    b.is_open = True
    b.now = datetime.combine(TEST_DAY, time(9, 40), tzinfo=NY)  # started late: prep runs on first tick
    e, _ = _live_engine(tmp_path, b)
    b.price = {"HOT": 100.0, "DROP": 99.65}
    e.tick()
    assert [o[:2] for o in b.orders] == [("DROP", "sell")] and b.orders[0][3] == pytest.approx(99.85)
    e2, _ = _live_engine(tmp_path, b)  # restarted process, same day
    e2.tick()
    assert len(b.orders) == 1
