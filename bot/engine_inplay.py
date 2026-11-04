"""Live / paper trading for the stocks-in-play strategy (BOT_STRATEGY=in_play).

Timeline each trading day (US/Eastern):
  ~08:45  pre-market prep: scan all US stocks, keep liquid ones (avg volume, ATR, price), load the last
          14 opening-range volumes for each.
  09:35+  scan: rank by today's opening relative volume, keep the top N, note direction and trigger level,
          send an alert listing them (with the latest news headline when there is one).
  until 15:00  each tick, if a pick trades through its trigger, buy (or short) at market with a stop-loss
          attached at the broker (10% of ATR away). No profit target.
  15:55   everything is closed (shared with the base engine), as is the daily loss kill switch.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from .broker import client_order_id
from .engine import Engine
from .risk import can_open_new_trade, position_size, round_price
from .scanner import latest_stats, range_end, select_in_play

log = logging.getLogger("bot.inplay")
NY = ZoneInfo("America/New_York")
PREP_MINUTES_BEFORE_OPEN = 45


class InPlayEngine(Engine):
    order_prefix = "inplay"

    def __init__(self, settings, broker, notifier=None):
        super().__init__(settings, broker, notifier)
        self.prep: dict | None = None        # pre-market scan for one date
        self.watch: pd.DataFrame | None = None
        self.watch_day = None

    # ---- hooks ----------------------------------------------------------------------------------
    def _poll(self) -> float:
        return min(self.s.poll_seconds, 10)  # breakouts move fast: check at least every 10s

    def _while_closed(self, clock) -> float:
        mins = (clock.next_open - clock.now).total_seconds() / 60
        if mins <= PREP_MINUTES_BEFORE_OPEN:
            if self.prep is None or self.prep["day"] != clock.next_open.date():
                self._prepare(clock.next_open.date())
            return max(5.0, min(60.0, mins * 60))
        # sleep until the prep window (capped so the loop still checks in hourly)
        return max(5.0, min(3600.0, (mins - PREP_MINUTES_BEFORE_OPEN + 1) * 60))

    def _entry_phase(self, now: datetime, acct) -> None:
        p = self.s.in_play
        if now.time() < range_end(p.or_minutes) or now.time() >= p.last_entry_time:
            return
        if self.prep is None or self.prep["day"] != now.date():
            self._prepare(now.date())  # bot was started after the pre-market window
        if self.watch_day != now.date():
            ready_at = datetime.combine(now.date(), range_end(p.or_minutes), tzinfo=NY) + timedelta(seconds=20)
            if now < ready_at:
                return  # give the data feed a moment to finish the opening bar
            self._scan(now)
        self._check_triggers(now, acct)

    # ---- steps ----------------------------------------------------------------------------------
    def _prepare(self, day) -> None:
        p = self.s.in_play
        universe, shortable = self.broker.get_universe()
        start = datetime.combine(day - timedelta(days=45), time(0), tzinfo=NY)
        end = datetime.combine(day - timedelta(days=1), time(23, 59), tzinfo=NY)
        daily = self.broker.get_daily_bars(universe, start, end, feed="sip")  # history: free plan allows SIP
        daily = daily[daily["date"] < day]
        stats = latest_stats(daily, p.lookback_days)
        stats = stats[
            (stats["avg_volume"] >= p.min_avg_volume) & (stats["atr"] > p.min_atr)
            & (stats["prev_close"] > p.min_price * 0.8)
        ]
        eligible = stats["symbol"].tolist()

        # opening volume history must come from the same feed the live scan uses (IEX on the free plan)
        hist_days = sorted(daily["date"].unique())[-p.lookback_days:]
        frames = [self.broker.get_opening_bars(eligible, d, p.or_minutes, feed=self.s.data_feed) for d in hist_days]
        hist = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["symbol", "or_volume"])
        counts = hist.groupby("symbol")["or_volume"].agg(["mean", "size"])
        avg_open = counts.loc[counts["size"] >= p.lookback_days, "mean"]

        self.prep = {"day": day, "stats": stats.set_index("symbol"), "avg_open": avg_open, "shortable": shortable}
        log.info("pre-market prep for %s: %d liquid candidates", day, len(avg_open))
        self.notify.send(f"Pre-market scan ready for {day}: {len(avg_open)} liquid stocks to watch at the open.")

    def _scan(self, now: datetime) -> None:
        p = self.s.in_play
        prep = self.prep
        cands = list(prep["avg_open"].index)
        op = self.broker.get_opening_bars(cands, now.date(), p.or_minutes, feed=self.s.data_feed)
        self.watch_day = now.date()
        if op.empty:
            self.watch = op
            self.notify.send("In-play scan: no opening-range data came back, so no trades today.")
            return
        op = op.copy()
        op["avg_or_volume"] = op["symbol"].map(prep["avg_open"])
        op["rel_volume"] = op["or_volume"] / op["avg_or_volume"]
        merged = op.join(prep["stats"][["atr", "avg_volume", "prev_close"]], on="symbol", how="inner")
        picks = select_in_play(merged, p, prep["shortable"])
        picks = picks[~picks["symbol"].isin(self.state.entered)]
        self.watch = picks.reset_index(drop=True)

        if picks.empty:
            self.notify.send("In-play scan: nothing passed the filters today.")
            return
        since = datetime.combine(now.date() - timedelta(days=1), time(16, 0), tzinfo=NY)
        heads = self.broker.get_headlines(picks["symbol"].tolist(), since) if hasattr(self.broker, "get_headlines") else {}
        lines = []
        for r in picks.itertuples(index=False):
            arrow = "up" if r.side == "buy" else "down"
            line = f"{r.symbol} {r.rel_volume:.1f}x vol, {arrow}, trigger {r.trigger:.2f}"
            if r.symbol in heads:
                line += f" | {heads[r.symbol][:80]}"
            lines.append(line)
        self.notify.send(f"In play today ({len(picks)}):\n" + "\n".join(lines))

    def _check_triggers(self, now: datetime, acct) -> None:
        if self.watch is None or self.watch.empty:
            return
        pending = self.watch[~self.watch["symbol"].isin(self.state.entered)]
        if pending.empty:
            return
        positions = self.broker.get_positions()
        held = {pos.symbol for pos in positions}
        pending = pending[~pending["symbol"].isin(held)]
        if pending.empty:
            return
        prices = self.broker.latest_prices(pending["symbol"].tolist())
        open_count = len(positions)
        exposure = sum(abs(pos.market_value) for pos in positions)
        p, rp = self.s.in_play, self.s.risk

        for r in pending.itertuples(index=False):
            px = prices.get(r.symbol)
            if px is None:
                continue
            long = r.side == "buy"
            if (long and px < r.trigger) or (not long and px > r.trigger):
                continue  # not triggered yet
            if abs(px - r.trigger) > r.stop_dist:
                log.info("%s: %.2f already %.2f past trigger %.2f, skipping (no chasing)", r.symbol, px,
                         abs(px - r.trigger), r.trigger)
                self.state.entered.add(r.symbol)
                continue
            ok, why = can_open_new_trade(open_count, self.state.trades, rp)
            if not ok:
                log.info("%s triggered but skipped: %s", r.symbol, why)
                break
            stop = px - r.stop_dist if long else px + r.stop_dist
            room = min(acct.buying_power, p.max_leverage * acct.equity - exposure)
            size = position_size(acct.equity, room, px, stop, rp)
            if size.qty < 1:
                log.info("%s: not sized (%s)", r.symbol, size.reason)
                self.state.entered.add(r.symbol)
                continue
            coid = client_order_id(now, r.symbol, self.order_prefix)
            stop_r = round_price(stop)
            try:
                order_id = self.broker.submit_with_stop(r.symbol, r.side, size.qty, stop_r, coid)
            except Exception as exc:
                log.error("%s order rejected: %s", r.symbol, exc)
                self.state.entered.add(r.symbol)
                continue
            self.state.entered.add(r.symbol)
            self.state.trades += 1
            open_count += 1
            exposure += size.qty * px
            self._journal(now, r.symbol, r.side, size.qty, px, stop_r, "", order_id)
            self.notify.send(
                f"{'BUY' if long else 'SHORT'} {size.qty} {r.symbol} @ ~{px:.2f} | stop {stop_r} | "
                f"{r.rel_volume:.1f}x volume | risk ${size.qty * r.stop_dist:,.0f} | exit by 15:55"
                f"{'' if self.s.paper else ' | LIVE'}"
            )
