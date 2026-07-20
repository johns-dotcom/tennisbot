"""Market recorder — the deterministic test substrate for the estimator.

EVERY websocket tick, trade print, and delayed-score update for a watched market
is written to market_ticks, tagged with a session_id per websocket session.
`python -m bot replay <session>` feeds a recorded session back through the
estimator. Built before the estimator, per PLAN.md.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from bot.log import get_logger
from bot.models import MarketTick

log = get_logger("market.recorder")

FLUSH_EVERY = 50  # buffered rows
FLUSH_SECONDS = 2.0


def new_session_id() -> str:
    return f"{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}"


class MarketRecorder:
    """Buffers tick rows briefly and bulk-inserts. Call flush() on shutdown —
    the watch loop's SIGTERM handler must do this (restart protocol)."""

    def __init__(self, db: Session, session_id: str | None = None):
        self.db = db
        self.session_id = session_id or new_session_id()
        self._buf: list[dict] = []
        self._last_flush = datetime.now(timezone.utc)

    # ---------- record ----------

    def quote(self, ticker: str, ts: datetime, yes_bid: int | None, yes_ask: int | None,
              no_bid: int | None, no_ask: int | None, volume: int | None = None,
              degraded: bool = False, raw: dict | None = None) -> None:
        self._add(dict(kind="quote", market_ticker=ticker, ts=ts, yes_bid=yes_bid,
                       yes_ask=yes_ask, no_bid=no_bid, no_ask=no_ask, volume=volume,
                       degraded=degraded, raw=raw))

    def trade(self, ticker: str, ts: datetime, price: int, count: int,
              degraded: bool = False, raw: dict | None = None) -> None:
        self._add(dict(kind="trade", market_ticker=ticker, ts=ts, trade_price=price,
                       trade_count=count, degraded=degraded, raw=raw))

    def score(self, ticker: str, ts: datetime, sets_a: int, sets_b: int,
              raw: dict | None = None) -> None:
        """Kalshi's delayed score update, normalized to sets won (YES side first)."""
        payload = dict(raw or {})
        payload.update({"sets_a": sets_a, "sets_b": sets_b})
        self._add(dict(kind="score", market_ticker=ticker, ts=ts, raw=payload))

    def lifecycle(self, ticker: str, ts: datetime, event: str,
                  raw: dict | None = None) -> None:
        payload = dict(raw or {})
        payload["event"] = event
        self._add(dict(kind="lifecycle", market_ticker=ticker, ts=ts, raw=payload))

    # ---------- plumbing ----------

    def _add(self, row: dict) -> None:
        row["session_id"] = self.session_id
        self._buf.append(row)
        age = (datetime.now(timezone.utc) - self._last_flush).total_seconds()
        if len(self._buf) >= FLUSH_EVERY or age >= FLUSH_SECONDS:
            self.flush()

    def flush(self) -> int:
        if not self._buf:
            return 0
        n = len(self._buf)
        self.db.bulk_insert_mappings(MarketTick, self._buf)
        self.db.commit()
        self._buf.clear()
        self._last_flush = datetime.now(timezone.utc)
        return n
