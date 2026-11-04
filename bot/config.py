"""Settings loaded from environment variables / .env.

Every knob has a conservative default. Live (real-money) trading needs TWO explicit opt-ins:
ALPACA_PAPER_TRADE=false *and* I_UNDERSTAND_LIVE_TRADING=yes.
"""

from __future__ import annotations

import logging
import os
import re
import stat
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
class InPlayParams:
    """"Stocks in play" opening-range breakout (Zarattini, Barbon & Aziz 2024), see docs/strategy-research.md.

    Each day: scan all liquid US stocks, keep the `top_n` with the most unusual volume in the opening range,
    trade the breakout in the direction of the opening candle, stop at `stop_atr_frac` x 14-day ATR, and hold
    to the end of the day (no profit target).
    """

    top_n: int = 20                        # trade at most this many in-play stocks per day
    or_minutes: int = 5                    # opening range length (paper: 5)
    min_rel_volume: float = 1.0            # opening volume >= 100% of its 14-day average
    min_price: float = 5.0
    min_avg_volume: float = 1_000_000      # 14-day average daily shares
    min_atr: float = 0.50                  # 14-day average true range, dollars
    lookback_days: int = 14
    stop_atr_frac: float = 0.10            # stop distance = 10% of ATR
    allow_short: bool = True               # paper trades both directions; shorts need a margin account
    max_leverage: float = 2.0              # total positions <= 2x equity (paper used 4x)
    last_entry_time: time = time(15, 0)    # no new entries after this


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
    strategy_name: str = "orb"             # "orb" = fixed symbol list, "in_play" = daily stocks-in-play scan
    in_play: InPlayParams = field(default_factory=InPlayParams)

    def validate_params(self) -> None:
        """Reject settings that are malformed or dangerous. Runs for every command, including backtests.

        Percentages are fractions: 0.005 = 0.5%. The classic mistake is writing 0.5 meaning "half a
        percent", which would risk 50% of the account per trade, so the bounds below catch it.
        """
        r, p = self.risk, self.strategy
        problems = []

        def bound(name: str, value: float, lo: float, hi: float) -> None:
            if not (lo < value <= hi):
                problems.append(f"{name}={value} must be > {lo} and <= {hi}")

        bound("RISK_PER_TRADE_PCT", r.risk_per_trade_pct, 0, 0.05)
        bound("RISK_MAX_POSITION_PCT", r.max_position_pct, 0, 1.0)
        bound("RISK_MAX_DAILY_LOSS_PCT", r.max_daily_loss_pct, 0, 0.20)
        bound("RISK_MIN_STOP_PCT", r.min_stop_pct, 0, 0.05)
        bound("RISK_MAX_OPEN_POSITIONS", r.max_open_positions, 0, 50)
        bound("RISK_MAX_TRADES_PER_DAY", r.max_trades_per_day, 0, 100)
        bound("ORB_MINUTES", p.opening_range_minutes, 0, 120)
        bound("ORB_REWARD_RISK", p.reward_risk, 0, 20)
        if not (0 <= p.min_range_pct < p.max_range_pct <= 0.5):
            problems.append("need 0 <= ORB_MIN_RANGE_PCT < ORB_MAX_RANGE_PCT <= 0.5")
        if p.stop_mode not in {"range", "mid"}:
            problems.append(f"ORB_STOP_MODE={p.stop_mode!r} must be 'range' or 'mid'")
        if not (p.last_entry_time < p.flatten_time <= time(16, 0)):
            problems.append("need ORB_LAST_ENTRY < ORB_FLATTEN <= 16:00")
        if self.data_feed not in {"iex", "sip", "delayed_sip"}:
            problems.append(f"ALPACA_DATA_FEED={self.data_feed!r} must be iex, sip or delayed_sip")
        if self.poll_seconds < 5:
            problems.append("BOT_POLL_SECONDS must be >= 5 (protects the API rate limit)")
        bad = [s for s in self.symbols if not SYMBOL_RE.fullmatch(s)]
        if bad:
            problems.append(f"invalid symbols in BOT_SYMBOLS: {bad}")
        if len(self.symbols) > 50:
            problems.append("BOT_SYMBOLS has more than 50 symbols")
        if self.strategy_name not in {"orb", "in_play"}:
            problems.append(f"BOT_STRATEGY={self.strategy_name!r} must be 'orb' or 'in_play'")
        ip = self.in_play
        bound("INPLAY_TOP_N", ip.top_n, 0, 50)
        if ip.or_minutes not in {1, 5, 10, 15, 30}:
            problems.append("INPLAY_OR_MINUTES must be one of 1, 5, 10, 15, 30")
        bound("INPLAY_MIN_REL_VOLUME", ip.min_rel_volume, -1e-9, 1000)
        bound("INPLAY_STOP_ATR_FRAC", ip.stop_atr_frac, 0, 2.0)
        bound("INPLAY_MAX_LEVERAGE", ip.max_leverage, 0, 4.0)
        bound("INPLAY_LOOKBACK_DAYS", ip.lookback_days, 1, 60)
        if not (ip.last_entry_time < p.flatten_time):
            problems.append("need INPLAY_LAST_ENTRY < ORB_FLATTEN")
        if self.discord_webhook_url and not self.discord_webhook_url.startswith("https://"):
            problems.append("DISCORD_WEBHOOK_URL must start with https://")
        if problems:
            raise SystemExit("Invalid settings in .env:\n  - " + "\n  - ".join(problems))

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
SYMBOL_RE = re.compile(r"[A-Z]{1,6}([./][A-Z]{1,3})?")  # SPY, BRK.B, BTC/USD


def check_env_file_permissions(env_file: str | None) -> None:
    """Warn if the file holding your API keys is readable by other users on this machine."""
    if not env_file or os.name != "posix" or not os.path.isfile(env_file):
        return
    mode = os.stat(env_file).st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        logging.getLogger("bot.config").warning(
            "%s is readable by other users on this machine. Run: chmod 600 %s", env_file, env_file
        )


def load_settings(env_file: str | None = ".env") -> Settings:
    if env_file:
        check_env_file_permissions(env_file)
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
    settings = Settings(
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
        strategy_name=os.getenv("BOT_STRATEGY", "orb").strip().lower(),
        in_play=InPlayParams(
            top_n=_int("INPLAY_TOP_N", 20),
            or_minutes=_int("INPLAY_OR_MINUTES", 5),
            min_rel_volume=_float("INPLAY_MIN_REL_VOLUME", 1.0),
            min_price=_float("INPLAY_MIN_PRICE", 5.0),
            min_avg_volume=_float("INPLAY_MIN_AVG_VOLUME", 1_000_000),
            min_atr=_float("INPLAY_MIN_ATR", 0.50),
            lookback_days=_int("INPLAY_LOOKBACK_DAYS", 14),
            stop_atr_frac=_float("INPLAY_STOP_ATR_FRAC", 0.10),
            allow_short=_bool("INPLAY_ALLOW_SHORT", True),
            max_leverage=_float("INPLAY_MAX_LEVERAGE", 2.0),
            last_entry_time=_time("INPLAY_LAST_ENTRY", "15:00"),
        ),
    )
    settings.validate_params()
    return settings
