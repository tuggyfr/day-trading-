"""Position sizing and account-level guardrails. Pure functions, no broker calls."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import RiskParams


@dataclass(frozen=True)
class SizeDecision:
    qty: int
    reason: str = ""


def position_size(
    equity: float,
    buying_power: float,
    entry: float,
    stop: float,
    params: RiskParams,
) -> SizeDecision:
    """Shares to trade so that hitting the stop loses about `risk_per_trade_pct` of equity.

    Capped by `max_position_pct` of equity and by available buying power. Returns qty=0 with a reason
    when the trade shouldn't be taken.
    """
    if equity <= 0 or entry <= 0:
        return SizeDecision(0, "no equity or bad price")
    stop_dist = abs(entry - stop)
    min_dist = entry * params.min_stop_pct
    if stop_dist < min_dist:
        stop_dist = min_dist  # treat ultra-tight stops as wider so size doesn't explode

    risk_dollars = equity * params.risk_per_trade_pct
    by_risk = math.floor(risk_dollars / stop_dist)
    by_cap = math.floor(equity * params.max_position_pct / entry)
    by_bp = math.floor(max(buying_power, 0) / entry)
    qty = min(by_risk, by_cap, by_bp)
    if qty < 1:
        return SizeDecision(0, f"size<1 (risk={by_risk}, cap={by_cap}, bp={by_bp})")
    return SizeDecision(qty)


def daily_loss_breached(equity: float, start_of_day_equity: float, params: RiskParams) -> bool:
    """True once the account is down `max_daily_loss_pct` or more versus the prior close."""
    if start_of_day_equity <= 0:
        return False
    return (equity - start_of_day_equity) / start_of_day_equity <= -params.max_daily_loss_pct


def can_open_new_trade(open_positions: int, trades_today: int, params: RiskParams) -> tuple[bool, str]:
    if open_positions >= params.max_open_positions:
        return False, f"max open positions ({params.max_open_positions}) reached"
    if trades_today >= params.max_trades_per_day:
        return False, f"max trades per day ({params.max_trades_per_day}) reached"
    return True, ""


def round_price(price: float) -> float:
    """Alpaca accepts 2 decimals for prices >= $1 and 4 below."""
    return round(price, 2) if price >= 1 else round(price, 4)
