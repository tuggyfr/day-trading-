"""Live / paper trading loop.

Each tick (default every 30s while the market is open):
  1. Daily loss limit hit?        -> close everything, stop for the day.
  2. Past flatten time?           -> close everything, stop for the day.
  3. Inside the entry window?     -> look for fresh ORB signals, size them, send bracket orders.
Every order carries its own stop-loss and take-profit at the broker, so positions stay protected
even if this process dies.
"""

from __future__ import annotations

import csv
import logging
import os
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime

from .broker import client_order_id, next_session_wait
from .config import Settings
from .notifier import Notifier
from .risk import can_open_new_trade, daily_loss_breached, position_size, round_price
from .strategy import _range_end, live_signal

log = logging.getLogger("bot.engine")


@dataclass
class DayState:
    day: date | None = None
    halted: bool = False          # daily loss limit hit
    flattened: bool = False       # end-of-day close done
    entered: set[str] = field(default_factory=set)   # symbols done for today (traded or skipped)
    trades: int = 0                                   # orders actually sent today


class Engine:
    def __init__(self, settings: Settings, broker, notifier: Notifier | None = None):
        self.s = settings
        self.broker = broker
        self.notify = notifier or Notifier()
        self.state = DayState()
        self.journal_path = os.path.join(settings.log_dir, "trades.csv")

    # ------------------------------------------------------------------------------------------
    def tick(self) -> float:
        """Run one cycle. Returns seconds to sleep before the next one."""
        clock = self.broker.get_clock()
        if not clock.is_open:
            return next_session_wait(clock)

        now = clock.now
        if self.state.day != now.date():
            self.state = DayState(day=now.date())
            self.state.entered = set(self.broker.symbols_traded_today(now))
            self.state.trades = len(self.state.entered)
            log.info("new session %s, already traded: %s", now.date(), sorted(self.state.entered) or "none")

        if self.state.halted or self.state.flattened:
            return self.s.poll_seconds

        acct = self.broker.get_account()
        if acct.trading_blocked:
            log.warning("account is blocked from trading")
            return self.s.poll_seconds

        if daily_loss_breached(acct.equity, acct.last_equity, self.s.risk):
            self.broker.close_all()
            self.state.halted = True
            pnl = acct.equity - acct.last_equity
            self.notify.send(f"KILL SWITCH: down ${pnl:,.2f} today. Closed all positions; done for the day.")
            return self.s.poll_seconds

        p = self.s.strategy
        if now.time() >= p.flatten_time:
            self.broker.close_all()
            self.state.flattened = True
            self.notify.send(f"End of day: flattened. Equity ${acct.equity:,.2f} ({acct.equity - acct.last_equity:+,.2f}).")
            return self.s.poll_seconds

        if not (_range_end(p) <= now.time() < p.last_entry_time):
            return self.s.poll_seconds

        self._look_for_entries(now, acct)
        return self.s.poll_seconds

    # ------------------------------------------------------------------------------------------
    def _look_for_entries(self, now: datetime, acct) -> None:
        positions = self.broker.get_positions()
        held = {pos.symbol for pos in positions}
        candidates = [sym for sym in self.s.symbols if sym not in self.state.entered and sym not in held]
        if not candidates:
            return

        ok, why = can_open_new_trade(len(positions), self.state.trades, self.s.risk)
        if not ok:
            log.debug("no new trades: %s", why)
            return

        bars = self.broker.get_today_bars(candidates, now)
        open_count = len(positions)
        buying_power = acct.buying_power
        for sym in candidates:
            day = bars.get(sym)
            if day is None or day.empty:
                continue
            sig = live_signal(sym, day, self.s.strategy)
            if sig is None:
                continue

            ok, why = can_open_new_trade(open_count, self.state.trades, self.s.risk)
            if not ok:
                log.info("%s signal skipped: %s", sym, why)
                break

            price = self.broker.latest_price(sym)
            stop = sig.stop_price
            if sig.side == "buy":
                if price <= stop:
                    log.info("%s: price %.2f already at/below stop %.2f, skip", sym, price, stop)
                    self.state.entered.add(sym)
                    continue
                target = price + self.s.strategy.reward_risk * (price - stop)
            else:
                if price >= stop:
                    log.info("%s: price %.2f already at/above stop %.2f, skip", sym, price, stop)
                    self.state.entered.add(sym)
                    continue
                target = price - self.s.strategy.reward_risk * (stop - price)

            size = position_size(acct.equity, buying_power, price, stop, self.s.risk)
            if size.qty < 1:
                log.info("%s: not sized (%s)", sym, size.reason)
                self.state.entered.add(sym)
                continue

            coid = client_order_id(now, sym)
            stop_r, target_r = round_price(stop), round_price(target)
            try:
                order_id = self.broker.submit_bracket(sym, sig.side, size.qty, stop_r, target_r, coid)
            except Exception as exc:
                log.error("%s order rejected: %s", sym, exc)
                self.state.entered.add(sym)  # don't retry the same signal every tick
                continue

            self.state.entered.add(sym)
            self.state.trades += 1
            open_count += 1
            buying_power -= size.qty * price
            self._journal(now, sym, sig.side, size.qty, price, stop_r, target_r, order_id)
            self.notify.send(
                f"{'BUY' if sig.side == 'buy' else 'SHORT'} {size.qty} {sym} @ ~{price:.2f} | "
                f"stop {stop_r} | target {target_r} | risk ${size.qty * abs(price - stop):,.0f}"
                f"{'' if self.s.paper else ' | LIVE'}"
            )

    def _journal(self, now, sym, side, qty, price, stop, target, order_id) -> None:
        os.makedirs(self.s.log_dir, exist_ok=True)
        new = not os.path.exists(self.journal_path)
        with open(self.journal_path, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "symbol", "side", "qty", "approx_price", "stop", "target", "order_id", "paper"])
            w.writerow([now.isoformat(), sym, side, qty, f"{price:.2f}", stop, target, order_id, self.s.paper])

    # ------------------------------------------------------------------------------------------
    def run_forever(self) -> None:
        mode = "PAPER" if self.s.paper else "LIVE MONEY"
        self.notify.send(f"Bot started ({mode}). Watching {', '.join(self.s.symbols)}.")
        errors = 0
        while True:
            try:
                wait = self.tick()
                errors = 0
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                errors += 1
                log.exception("tick failed")
                if errors in (1, 10, 100):
                    self.notify.send(f"Bot error ({errors}x): {exc.__class__.__name__}: {exc}")
                wait = min(300, self.s.poll_seconds * errors)
            _time.sleep(wait)
