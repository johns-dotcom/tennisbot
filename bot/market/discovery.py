"""Scheduled discovery of open Kalshi tennis markets + player matching.

Each match event has two complementary markets (one per player). Both are
upserted into kalshi_markets; the YES-side player of each market is matched via
market_matcher (unmatched names land in the review queue, never dropped).

Structure: all HTTP happens between two short DB passes — serverless Postgres
closes connections that idle while we crawl the API, so no transaction may
span the crawl.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from bot.log import get_logger
from bot.market.kalshi import TENNIS_SERIES, KalshiClient
from bot.matching.market_matcher import PlayerMatcher
from bot.models import KalshiMarket

log = get_logger("market.discovery")


def discover_markets(db: Session, client: KalshiClient) -> dict:
    stats = {"seen": 0, "new": 0, "matched": 0, "unmatched": 0}

    # ---- pass 1 (DB, short): existing state + matcher pools ----
    existing: dict[str, dict] = {}
    for r in db.execute(select(KalshiMarket)).scalars().all():
        existing[r.ticker] = {
            "status": r.status,
            "milestone": (r.raw or {}).get("_milestone_id"),
            "matched": r.player_a_id is not None,
        }
    matchers = {tour: PlayerMatcher(db, tour) for tour in ("atp", "wta")}
    db.commit()  # release the connection's transaction during the crawl

    # ---- pass 2 (HTTP only): crawl series + milestones ----
    fetched: list[tuple[str, dict]] = []
    for series in TENNIS_SERIES:
        try:
            for m in client.markets(series):
                fetched.append((series, m))
        except Exception as e:
            log.error("series discovery failed", series=series, error=str(e))
    stats["seen"] = len(fetched)

    milestone_by_event: dict[str, str] = {}
    need_milestone = set()
    for series, m in fetched:
        ev = m.get("event_ticker")
        prev = existing.get(m["ticker"])
        if ev and not (prev and prev["milestone"]) and ev not in milestone_by_event:
            need_milestone.add(ev)
    best_of_by_event: dict[str, int] = {}
    for ev in need_milestone:
        try:
            ms = client.milestones_for_event(ev)
            if ms:
                milestone_by_event[ev] = ms[0]["id"]
                bo = (ms[0].get("details") or {}).get("best_of")
                if bo:
                    best_of_by_event[ev] = int(bo)
        except Exception as e:
            log.warning("milestone lookup failed", event=ev, error=str(e))

    # ---- pass 3 (DB): upsert + match, committed per market so no transaction
    # ever spans the CPU-heavy fuzzy matching. Unchanged rows are skipped and
    # only get a bulk last_seen_at touch at the end. ----
    now = datetime.now(timezone.utc)
    unchanged: list[str] = []
    for series, m in fetched:
        prev = existing.get(m["ticker"])
        if prev and prev["matched"] and prev["milestone"] \
                and prev["status"] == m.get("status"):
            unchanged.append(m["ticker"])
            stats["seen_unchanged"] = stats.get("seen_unchanged", 0) + 1
            continue
        try:
            _upsert_one(db, matchers, series, m, milestone_by_event,
                        best_of_by_event, now, stats)
            db.commit()
        except OperationalError:
            db.rollback()
            _upsert_one(db, matchers, series, m, milestone_by_event,
                        best_of_by_event, now, stats)
            db.commit()
    for i in range(0, len(unchanged), 500):
        db.execute(update(KalshiMarket)
                   .where(KalshiMarket.ticker.in_(unchanged[i:i + 500]))
                   .values(last_seen_at=now))
    db.commit()
    log.info("discovery complete", **stats)
    return stats


def _upsert_one(db: Session, matchers: dict, series: str, m: dict,
                milestone_by_event: dict[str, str], best_of_by_event: dict[str, int],
                now: datetime, stats: dict) -> None:
    tour = TENNIS_SERIES[series]
    ticker = m["ticker"]
    row = db.execute(
        select(KalshiMarket).where(KalshiMarket.ticker == ticker)
    ).scalar()
    if row is None:
        row = KalshiMarket(ticker=ticker, first_seen_at=now)
        db.add(row)
        stats["new"] += 1
    row.event_ticker = m.get("event_ticker")
    row.title = m.get("title")
    row.status = m.get("status")
    row.last_seen_at = now
    close = m.get("close_time")
    if close:
        row.close_time = datetime.fromisoformat(close.replace("Z", "+00:00"))
    raw = dict(m)
    raw["_series"] = series
    prev = row.raw or {}
    raw["_milestone_id"] = prev.get("_milestone_id") or milestone_by_event.get(row.event_ticker)
    raw["_best_of"] = prev.get("_best_of") or best_of_by_event.get(row.event_ticker, 3)
    row.raw = raw

    if row.player_a_id is None:
        yes_name = (m.get("yes_sub_title") or "").strip()
        if yes_name:
            res = matchers[tour].match(
                db, yes_name, source="kalshi",
                context={"ticker": ticker, "title": m.get("title")})
            if res.player_id is not None:
                row.player_a_id = res.player_id
                row.match_confidence = res.confidence
                row.matched_at = now
                stats["matched"] += 1
            else:
                stats["unmatched"] += 1
