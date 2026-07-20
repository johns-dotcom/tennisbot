"""Replay a recorded market session through the estimator, deterministically.

The recorder's market_ticks rows are the ground truth; replay rebuilds one
estimator per market and feeds ticks in order, writing live_match_state and
state_inference_log exactly as the live loop would (rows tagged with the
session_id and replay=True in detail).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from bot.log import get_logger
from bot.market.estimator import EstimatorSnapshot, SetBoundaryEstimator
from bot.market.priors import DEFAULT_PRIORS
from bot.models import FeedGap, LiveMatchState, MarketTick, StateInferenceLog

log = get_logger("market.replay")


@dataclass
class ReplaySummary:
    session_id: str
    markets: dict[str, dict] = field(default_factory=dict)

    def render(self) -> str:
        lines = [f"REPLAY {self.session_id}"]
        for ticker, m in self.markets.items():
            lines.append(
                f"  {ticker}: ticks={m['ticks']} transitions={m['transitions']} "
                f"reconciliations={m['reconciliations']} hits={m['hits']} "
                f"misses={m['misses']} final_state={m['state']}")
        return "\n".join(lines)


def persist_snapshot(db: Session, snap: EstimatorSnapshot) -> None:
    now = datetime.now(timezone.utc)
    db.execute(pg_insert(LiveMatchState).values(
        market_ticker=snap.market_ticker, state=snap.state,
        confidence=snap.confidence, last_confirmed_state=snap.last_confirmed_state,
        stale=snap.stale, last_tick_at=snap.last_tick_at, updated_at=now,
    ).on_conflict_do_update(index_elements=["market_ticker"], set_=dict(
        state=snap.state, confidence=snap.confidence,
        last_confirmed_state=snap.last_confirmed_state, stale=snap.stale,
        last_tick_at=snap.last_tick_at, updated_at=now)))


def replay_session(db: Session, session_id: str) -> ReplaySummary:
    ticks = db.execute(
        select(MarketTick).where(MarketTick.session_id == session_id)
        .order_by(MarketTick.ts, MarketTick.id)
    ).scalars().all()
    if not ticks:
        raise SystemExit(f"no ticks recorded for session {session_id}")

    had_gap = db.execute(
        select(FeedGap.id).where(FeedGap.session_id == session_id)
    ).first() is not None

    summary = ReplaySummary(session_id=session_id)
    estimators: dict[str, SetBoundaryEstimator] = {}
    stats: dict[str, dict] = {}

    def make_logger(ticker: str):
        def log_inference(row: dict) -> None:
            detail = dict(row.get("detail") or {})
            detail["replay"] = True
            db.add(StateInferenceLog(
                market_ticker=row["market_ticker"], session_id=session_id,
                inferred_state=row["inferred_state"], inferred_at=row["inferred_at"],
                confirmed_state=row["confirmed_state"], confirmed_at=row["confirmed_at"],
                lead_time_seconds=row["lead_time_seconds"], hit=row["hit"],
                session_had_gap=had_gap, detail=detail))
            stats[ticker]["hits" if row["hit"] else "misses"] += 1
        return log_inference

    for t in ticks:
        est = estimators.get(t.market_ticker)
        if est is None:
            stats[t.market_ticker] = dict(ticks=0, transitions=0, reconciliations=0,
                                          hits=0, misses=0, state="0-0")
            est = SetBoundaryEstimator(
                t.market_ticker, priors=DEFAULT_PRIORS,
                persist=lambda snap, _db=db: persist_snapshot(_db, snap),
                log_inference=make_logger(t.market_ticker))
            estimators[t.market_ticker] = est
        s = stats[t.market_ticker]
        s["ticks"] += 1
        before = est.state_key
        if t.kind == "quote":
            est.on_quote(t.ts, t.yes_bid, t.yes_ask, degraded=t.degraded)
        elif t.kind == "trade":
            est.on_trade(t.ts, t.trade_price or 0, t.trade_count or 0,
                         degraded=t.degraded)
        elif t.kind == "score":
            raw = t.raw or {}
            if "sets_a" in raw and "sets_b" in raw:
                est.on_score(t.ts, int(raw["sets_a"]), int(raw["sets_b"]))
                s["reconciliations"] += 1
        if est.state_key != before and t.kind != "score":
            s["transitions"] += 1
        s["state"] = est.state_key

    db.commit()
    summary.markets = stats
    log.info("replay complete", session=session_id, markets=len(stats))
    return summary
