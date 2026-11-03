"""Settings loaded from environment variables / .env.

Every knob has a conservative default. Live (real-money) trading needs TWO explicit opt-ins:
ALPACA_PAPER_TRADE=false *and* I_UNDERSTAND_LIVE_TRADING=yes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time

from dotenv import load_dotenv


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return default if raw in (None, "") else float(raw)


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw in (None, "") else int(raw)


def _time(name: str, default: str) -> time:
    raw = os.getenv(name) or default
    hh, mm = raw.split(":")
    return time(int(hh), int(mm))


def _list(name: str, default: str) -> list[str]:
    raw = os.getenv(name) or default
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


@dataclass(frozen=True)
class StrategyParams:
    """Opening-range breakout parameters. All times are US/Eastern."""

    opening_range_minutes: int = 15        # 09:30-09:45 builds the range
    last_entry_time: time = time(11, 30)   # no new trades after this
    flatten_time: time = time(15, 55)      # close everything before the bell
    reward_risk: float = 2.0               # take-profit = entry + 2 x stop distance
    stop_mode: str = "range"               # "range" = other side of the range, "mid" = range midpoint
    min_range_pct: float = 0.002           # skip days whose opening range is < 0.2% of price (dead tape)
    max_range_pct: float = 0.03            # skip days whose opening range is > 3% (too wild for sizing)
    require_vwap: bool = True              # longs only above VWAP, shorts only below
    allow_short: bool = False              # shorting needs a margin account; off by default


@dataclass(frozen=True)
class RiskParams:
    risk_per_trade_pct: float = 0.005      # lose at most 0.5% of equity if the stop is hit
    max_position_pct: float = 0.20         # never put more than 20% of equity in one name
    max_daily_loss_pct: float = 0.02       # down 2% on the day -> close all, stop trading until tomorrow
    max_open_positions: int = 3
    max_trades_per_day: int = 5
    min_stop_pct: float = 0.001            # stops tighter than 0.1% get widened (avoids huge share counts)


@dataclass(frozen=True)
class Settings:
    api_key: str = ""
    secret_key: str = ""
    paper: bool = True
    live_confirmed: bool = False
    data_feed: str = "iex"                 # "iex" is free; "sip" needs Alpaca's paid data plan
    symbols: list[str] = field(default_factory=list)
    poll_seconds: int = 30
    log_dir: str = "logs"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    discord_webhook_url: str = ""
    strategy: StrategyParams = field(default_factory=StrategyParams)
    risk: RiskParams = field(default_factory=RiskParams)

    def validate_for_trading(self) -> None:
        if not self.api_key or not self.secret_key:
            raise SystemExit(
                "Missing ALPACA_API_KEY / ALPACA_SECRET_KEY. Copy .env.example to .env and fill them in."
            )
        if not self.paper and not self.live_confirmed:
            raise SystemExit(
                "Refusing to trade real money: ALPACA_PAPER_TRADE=false but I_UNDERSTAND_LIVE_TRADING "
                "is not 'yes'. Paper trade first."
            )
        if not self.symbols:
            raise SystemExit("BOT_SYMBOLS is empty.")


DEFAULT_SYMBOLS = "SPY,QQQ,AAPL,MSFT,NVDA,AMD,META,TSLA"


def load_settings(env_file: str | None = ".env") -> Settings:
    if env_file:
        load_dotenv(env_file, override=False)
    strategy = StrategyParams(
        opening_range_minutes=_int("ORB_MINUTES", 15),
        last_entry_time=_time("ORB_LAST_ENTRY", "11:30"),
        flatten_time=_time("ORB_FLATTEN", "15:55"),
        reward_risk=_float("ORB_REWARD_RISK", 2.0),
        stop_mode=os.getenv("ORB_STOP_MODE", "range"),
        min_range_pct=_float("ORB_MIN_RANGE_PCT", 0.002),
        max_range_pct=_float("ORB_MAX_RANGE_PCT", 0.03),
        require_vwap=_bool("ORB_REQUIRE_VWAP", True),
        allow_short=_bool("ORB_ALLOW_SHORT", False),
    )
    risk = RiskParams(
        risk_per_trade_pct=_float("RISK_PER_TRADE_PCT", 0.005),
        max_position_pct=_float("RISK_MAX_POSITION_PCT", 0.20),
        max_daily_loss_pct=_float("RISK_MAX_DAILY_LOSS_PCT", 0.02),
        max_open_positions=_int("RISK_MAX_OPEN_POSITIONS", 3),
        max_trades_per_day=_int("RISK_MAX_TRADES_PER_DAY", 5),
        min_stop_pct=_float("RISK_MIN_STOP_PCT", 0.001),
    )
    return Settings(
        api_key=os.getenv("ALPACA_API_KEY", ""),
        secret_key=os.getenv("ALPACA_SECRET_KEY", ""),
        paper=_bool("ALPACA_PAPER_TRADE", True),
        live_confirmed=(os.getenv("I_UNDERSTAND_LIVE_TRADING", "").strip().lower() == "yes"),
        data_feed=os.getenv("ALPACA_DATA_FEED", "iex").lower(),
        symbols=_list("BOT_SYMBOLS", DEFAULT_SYMBOLS),
        poll_seconds=_int("BOT_POLL_SECONDS", 30),
        log_dir=os.getenv("BOT_LOG_DIR", "logs"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),
        strategy=strategy,
        risk=risk,
    )
