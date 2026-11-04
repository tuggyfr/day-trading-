"""Thin wrapper around alpaca-py.

The engine only talks to this small interface, so tests can swap in a fake broker and no real
order can be sent from a test.
"""

from __future__ import annotations

import logging
import time as _time
from collections import deque
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
    market_value: float = 0.0


class RateLimiter:
    """Sliding-window limiter: at most `max_calls` per `per_seconds`, blocking until a slot frees up.

    Alpaca allows 200 requests/minute per account, and alpaca-py already retries HTTP 429s. This keeps
    the bot well under the limit even with many symbols or a misbehaving loop.
    """

    def __init__(self, max_calls: int = 150, per_seconds: float = 60.0, clock=_time.monotonic, sleep=_time.sleep):
        self.max_calls, self.per = max_calls, per_seconds
        self._clock, self._sleep = clock, sleep
        self._calls: deque[float] = deque()

    def acquire(self) -> None:
        while True:
            now = self._clock()
            while self._calls and now - self._calls[0] >= self.per:
                self._calls.popleft()
            if len(self._calls) < self.max_calls:
                self._calls.append(now)
                return
            wait = self.per - (now - self._calls[0])
            log.warning("rate limit: pausing %.1fs", wait)
            self._sleep(max(wait, 0.05))


class _RateLimited:
    """Wraps an alpaca-py client so every method call waits for the rate limiter first."""

    def __init__(self, client, limiter: RateLimiter):
        self._client, self._limiter = client, limiter

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            self._limiter.acquire()
            return attr(*args, **kwargs)

        return call


def client_order_id(day: datetime, symbol: str, prefix: str = "orb") -> str:
    """Deterministic per symbol per day. Alpaca rejects duplicate IDs, so a restarted bot can't double-enter."""
    return f"{prefix}-{day:%Y%m%d}-{symbol}"


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
        limiter = RateLimiter()  # shared: Alpaca's limit is per account across both clients
        self._limiter, self._api_key, self._secret_key = limiter, api_key, secret_key
        self.trading = _RateLimited(TradingClient(api_key, secret_key, paper=paper), limiter)
        self.data = _RateLimited(StockHistoricalDataClient(api_key, secret_key), limiter)
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
            out.append(PositionInfo(p.symbol, float(p.qty), side, float(p.unrealized_pl or 0), float(p.market_value or 0)))
        return out

    def symbols_traded_today(self, now: datetime, prefix: str = "orb") -> set[str]:
        """Symbols this bot already entered today (recovered from order IDs, so it survives restarts)."""
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        start = datetime.combine(now.date(), time(0, 0), tzinfo=NY)
        orders = self.trading.get_orders(
            GetOrdersRequest(status=QueryOrderStatus.ALL, after=start, limit=500, nested=False)
        )
        tag = f"{prefix}-{now:%Y%m%d}-"
        return {o.client_order_id[len(tag):] for o in orders if (o.client_order_id or "").startswith(tag)}

    # ---- data ---------------------------------------------------------------------------------
    def _feed(self, feed: str | None):
        from alpaca.data.enums import DataFeed

        return self.feed if feed is None else DataFeed(feed)

    def get_bars(
        self, symbols: list[str], start: datetime, end: datetime, feed: str | None = None
    ) -> dict[str, pd.DataFrame]:
        """1-minute regular-session bars per symbol, indexed by bar start time (US/Eastern).

        feed=None uses the live feed from settings. Backtests pass "sip": Alpaca's free plan includes full-market
        (SIP) history as long as it is more than 15 minutes old.
        """
        from alpaca.data.enums import Adjustment
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        req = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Minute,
            start=start,
            end=end,
            feed=self._feed(feed),
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
        return self.latest_prices([symbol])[symbol]

    def latest_prices(self, symbols: list[str]) -> dict[str, float]:
        from alpaca.data.requests import StockLatestTradeRequest

        trades = self.data.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbols, feed=self.feed))
        return {s: float(t.price) for s, t in trades.items()}

    # ---- stocks-in-play scan data ---------------------------------------------------------------
    def get_universe(self) -> tuple[list[str], set[str]]:
        """Active, tradable US-listed stocks and ETFs. Returns (symbols, symbols that are easy to short)."""
        from alpaca.trading.enums import AssetClass, AssetStatus
        from alpaca.trading.requests import GetAssetsRequest

        from .config import SYMBOL_RE

        exchanges = {"NYSE", "NASDAQ", "ARCA", "AMEX", "BATS", "NYSEARCA"}
        assets = self.trading.get_all_assets(GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY))
        symbols, shortable = [], set()
        for a in assets:
            exch = a.exchange.value if hasattr(a.exchange, "value") else str(a.exchange)
            if not a.tradable or exch not in exchanges or not SYMBOL_RE.fullmatch(a.symbol) or "/" in a.symbol:
                continue
            symbols.append(a.symbol)
            if a.shortable and a.easy_to_borrow:
                shortable.add(a.symbol)
        return sorted(symbols), shortable

    def get_daily_bars(self, symbols: list[str], start: datetime, end: datetime, feed: str = "sip") -> pd.DataFrame:
        """Daily bars as rows: symbol, date, open, high, low, close, volume. Requests are chunked by symbol."""
        from alpaca.data.enums import Adjustment
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        days = max(1, (end - start).days)
        chunk = max(1, min(300, 9000 // days))  # keep each request to about one 10k-bar page
        frames = []
        for i in range(0, len(symbols), chunk):
            req = StockBarsRequest(
                symbol_or_symbols=symbols[i : i + chunk], timeframe=TimeFrame.Day, start=start, end=end,
                feed=self._feed(feed), adjustment=Adjustment.SPLIT,
            )
            raw = self.data.get_stock_bars(req).df
            if not raw.empty:
                frames.append(raw.reset_index())
        if not frames:
            return pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "volume"])
        df = pd.concat(frames, ignore_index=True)
        df["date"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(NY).dt.date
        return df[["symbol", "date", "open", "high", "low", "close", "volume"]]

    def get_opening_bars(self, symbols: list[str], day, minutes: int, feed: str = "sip") -> pd.DataFrame:
        """The first `minutes`-long bar of `day` for each symbol: symbol, date, or_open/high/low/close/volume."""
        from alpaca.data.enums import Adjustment
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        start = datetime.combine(day, SESSION_OPEN, tzinfo=NY)
        end = start + pd.Timedelta(minutes=1)  # only bars that START at 09:30
        frames = []
        for i in range(0, len(symbols), 400):
            req = StockBarsRequest(
                symbol_or_symbols=symbols[i : i + 400], timeframe=TimeFrame(minutes, TimeFrameUnit.Minute),
                start=start, end=end, feed=self._feed(feed), adjustment=Adjustment.SPLIT,
            )
            raw = self.data.get_stock_bars(req).df
            if not raw.empty:
                frames.append(raw.reset_index())
        cols = ["symbol", "date", "or_open", "or_high", "or_low", "or_close", "or_volume"]
        if not frames:
            return pd.DataFrame(columns=cols)
        df = pd.concat(frames, ignore_index=True)
        ts = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(NY)
        df = df[ts.dt.time == SESSION_OPEN].copy()
        df["date"] = day
        df = df.rename(columns={"open": "or_open", "high": "or_high", "low": "or_low", "close": "or_close", "volume": "or_volume"})
        return df[cols]

    def get_headlines(self, symbols: list[str], since: datetime, limit: int = 50) -> dict[str, str]:
        """Latest news headline per symbol since `since` (Alpaca/Benzinga). Best effort: {} on any error."""
        try:
            from alpaca.data.historical.news import NewsClient
            from alpaca.data.requests import NewsRequest

            if not hasattr(self, "_news"):
                self._news = _RateLimited(NewsClient(self._api_key, self._secret_key), self._limiter)
            res = self._news.get_news(NewsRequest(symbols=",".join(symbols), start=since, limit=limit))
            items = res.data.get("news", []) if hasattr(res, "data") else []
            out: dict[str, str] = {}
            for n in sorted(items, key=lambda n: n.created_at):  # newest last, so it wins
                for s in n.symbols:
                    if s in symbols:
                        out[s] = n.headline
            return out
        except Exception as exc:
            log.warning("news lookup failed: %s", exc.__class__.__name__)
            return {}

    # ---- orders -------------------------------------------------------------------------------
    def submit_with_stop(self, symbol: str, side: str, qty: int, stop: float, coid: str) -> str:
        """Market entry with an attached stop-loss only (OTO): for strategies that exit at the close, not a target."""
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest, StopLossRequest

        order = self.trading.submit_order(
            MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                order_class=OrderClass.OTO,
                stop_loss=StopLossRequest(stop_price=stop),
                client_order_id=coid,
            )
        )
        return str(order.id)

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
