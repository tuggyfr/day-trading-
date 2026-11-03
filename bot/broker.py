"""Thin wrapper around alpaca-py.

The engine only talks to this small interface, so tests can swap in a fake broker and no real
order can be sent from a test.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

log = logging.getLogger("bot.broker")
NY = ZoneInfo("America/New_York")
SESSION_OPEN, SESSION_CLOSE = time(9, 30), time(16, 0)


@dataclass(frozen=True)
class AccountInfo:
    equity: float
    last_equity: float      # equity at the previous close: the baseline for the daily loss limit
    buying_power: float
    trading_blocked: bool


@dataclass(frozen=True)
class ClockInfo:
    is_open: bool
    now: datetime           # tz-aware
    next_open: datetime
    next_close: datetime


@dataclass(frozen=True)
class PositionInfo:
    symbol: str
    qty: float
    side: str               # "long" / "short"
    unrealized_pl: float


def client_order_id(day: datetime, symbol: str) -> str:
    """Deterministic per symbol per day. Alpaca rejects duplicate IDs, so a restarted bot can't double-enter."""
    return f"orb-{day:%Y%m%d}-{symbol}"


def regular_session(df: pd.DataFrame) -> pd.DataFrame:
    """Keep 09:30-16:00 ET bars and convert the index to US/Eastern."""
    if df.empty:
        return df
    idx = df.index.tz_convert(NY) if df.index.tz is not None else df.index.tz_localize(NY)
    df = df.set_axis(idx)
    t = df.index.time
    return df[(t >= SESSION_OPEN) & (t < SESSION_CLOSE)]


class AlpacaBroker:
    def __init__(self, api_key: str, secret_key: str, paper: bool = True, data_feed: str = "iex"):
        from alpaca.data.enums import DataFeed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.trading.client import TradingClient

        self.paper = paper
        self.trading = TradingClient(api_key, secret_key, paper=paper)
        self.data = StockHistoricalDataClient(api_key, secret_key)
        self.feed = DataFeed(data_feed)

    # ---- account / market state -------------------------------------------------------------
    def get_account(self) -> AccountInfo:
        a = self.trading.get_account()
        return AccountInfo(
            equity=float(a.equity),
            last_equity=float(a.last_equity),
            buying_power=float(a.buying_power),
            trading_blocked=bool(a.trading_blocked or a.account_blocked),
        )

    def get_clock(self) -> ClockInfo:
        c = self.trading.get_clock()
        return ClockInfo(c.is_open, c.timestamp.astimezone(NY), c.next_open.astimezone(NY), c.next_close.astimezone(NY))

    def get_positions(self) -> list[PositionInfo]:
        out = []
        for p in self.trading.get_all_positions():
            side = p.side.value if hasattr(p.side, "value") else str(p.side)
            out.append(PositionInfo(p.symbol, float(p.qty), side, float(p.unrealized_pl or 0)))
        return out

    def symbols_traded_today(self, now: datetime) -> set[str]:
        """Symbols this bot already entered today (recovered from order IDs, so it survives restarts)."""
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        start = datetime.combine(now.date(), time(0, 0), tzinfo=NY)
        orders = self.trading.get_orders(
            GetOrdersRequest(status=QueryOrderStatus.ALL, after=start, limit=500, nested=False)
        )
        prefix = f"orb-{now:%Y%m%d}-"
        return {o.client_order_id[len(prefix):] for o in orders if (o.client_order_id or "").startswith(prefix)}

    # ---- data ---------------------------------------------------------------------------------
    def get_bars(self, symbols: list[str], start: datetime, end: datetime) -> dict[str, pd.DataFrame]:
        """1-minute regular-session bars per symbol, indexed by bar start time (US/Eastern)."""
        from alpaca.data.enums import Adjustment
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        req = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Minute,
            start=start,
            end=end,
            feed=self.feed,
            adjustment=Adjustment.SPLIT,
        )
        raw = self.data.get_stock_bars(req).df
        out: dict[str, pd.DataFrame] = {}
        if raw.empty:
            return out
        for sym in symbols:
            if sym not in raw.index.get_level_values(0):
                continue
            df = raw.xs(sym, level=0)[["open", "high", "low", "close", "volume"]]
            out[sym] = regular_session(df)
        return out

    def get_today_bars(self, symbols: list[str], now: datetime) -> dict[str, pd.DataFrame]:
        """Today's completed bars only (a bar starting at t is complete once now >= t + 1 min)."""
        start = datetime.combine(now.date(), SESSION_OPEN, tzinfo=NY)
        bars = self.get_bars(symbols, start, now)
        cutoff = pd.Timestamp(now).tz_convert(NY) - pd.Timedelta(minutes=1)
        return {s: df[df.index <= cutoff] for s, df in bars.items()}

    def latest_price(self, symbol: str) -> float:
        from alpaca.data.requests import StockLatestTradeRequest

        trade = self.data.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol, feed=self.feed))
        return float(trade[symbol].price)

    # ---- orders -------------------------------------------------------------------------------
    def submit_bracket(self, symbol: str, side: str, qty: int, stop: float, take_profit: float, coid: str) -> str:
        """Market entry with an attached stop-loss and take-profit (one cancels the other)."""
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest

        order = self.trading.submit_order(
            MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                order_class=OrderClass.BRACKET,
                take_profit=TakeProfitRequest(limit_price=take_profit),
                stop_loss=StopLossRequest(stop_price=stop),
                client_order_id=coid,
            )
        )
        return str(order.id)

    def close_all(self) -> None:
        """Cancel every open order and flatten every position."""
        self.trading.close_all_positions(cancel_orders=True)


def next_session_wait(clock: ClockInfo, max_sleep: float = 3600) -> float:
    """Seconds to sleep while the market is closed (capped so the loop re-checks periodically)."""
    return max(5.0, min((clock.next_open - clock.now).total_seconds(), max_sleep))
