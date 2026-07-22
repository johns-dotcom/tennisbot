"""T2 — the self-improving testrun bot.

Same market read as T1, but T2 re-tunes its OWN betting thresholds from its OWN
settled record — the automated version of the manual v3/v4/v5 tuning:
  - probability floor nudges toward the lowest band where T2 actually wins
    (>= TARGET_WIN with enough bets), so it loosens when winning low, tightens
    when losing;
  - the edge cap tightens if big-edge bets underperform its overall rate;
  - the stake multiplier scales with recent ROI (bounded).
All bounded, and only once it has enough settled bets — cold start inherits
T1's fixed v5 policy. Advisory-only: still imaginary bets, never orders.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from bot.log import get_logger
from bot.models import IngestState, KalshiMarket, PaperBet
from bot.paper import DEFAULT_POLICY, Policy
from bot.track import advisory_outcome

log = get_logger("t2")

STATE_KEY = "t2:policy"
MIN_BASIS = 15          # settled bets required before T2 adapts at all
TARGET_WIN = 0.70
MIN_BUCKET = 8          # bets needed in a prob band to trust its win rate
FLOOR_MIN, FLOOR_MAX = 0.66, 0.85
EDGE_MAX_TIGHT, EDGE_MAX_LOOSE = 0.15, 0.30
SIZE_MULT_MIN, SIZE_MULT_MAX = 0.7, 1.3


def _default_state() -> dict:
    return {"min_prob": DEFAULT_POLICY.min_prob, "max_edge": DEFAULT_POLICY.max_edge,
            "size_mult": 1.0, "version": "t2.0", "n_basis": 0,
            "rationale": "cold start — inheriting T1's v5 policy until enough "
                         "settled bets to learn from", "history": []}


def _load_state(db: Session) -> dict:
    row = db.execute(select(IngestState.value).where(
        IngestState.key == STATE_KEY)).scalar()
    if not row:
        return _default_state()
    try:
        s = json.loads(row)
        _default_state().keys()  # ensure all keys present
        for k, v in _default_state().items():
            s.setdefault(k, v)
        return s
    except Exception:
        return _default_state()


def _save_state(db: Session, state: dict) -> None:
    now = datetime.now(timezone.utc)
    payload = json.dumps(state)
    db.execute(pg_insert(IngestState).values(
        key=STATE_KEY, value=payload, updated_at=now
    ).on_conflict_do_update(index_elements=["key"],
                            set_={"value": payload, "updated_at": now}))
    db.commit()


def t2_policy(db: Session) -> Policy:
    """T2's current adaptive Policy (falls back to T1 defaults at cold start)."""
    s = _load_state(db)
    return replace(DEFAULT_POLICY, min_prob=s["min_prob"], max_edge=s["max_edge"],
                   size_mult=s["size_mult"], version=s["version"])


def t2_state(db: Session) -> dict:
    """Raw learned state + history, for the T2 page's self-improvement panel."""
    return _load_state(db)


def iter_bot_policies(db: Session) -> list[tuple[str, Policy]]:
    """(bot_id, policy) for every bot that should evaluate each opportunity."""
    return [("t1", DEFAULT_POLICY), ("t2", t2_policy(db))]


def _settled(db: Session) -> list[dict]:
    rows = db.execute(
        select(PaperBet, KalshiMarket.result)
        .join(KalshiMarket, KalshiMarket.ticker == PaperBet.market_ticker)
        .where(PaperBet.bot == "t2")).all()
    out = []
    for b, res in rows:
        o = advisory_outcome(b.side, res)
        if o in ("won", "lost"):
            out.append({"won": o == "won", "prob": b.model_prob, "edge": b.edge,
                        "price": b.price_cents, "units": b.units or 1.0,
                        "pnl": b.pnl_cents or 0, "at": b.created_at})
    return out


def _wr(pool: list[dict]):
    return sum(x["won"] for x in pool) / len(pool) if pool else None


def t2_self_improve(db: Session) -> dict:
    """Recompute T2's policy from its own settled record; persist if it changed."""
    s = _load_state(db)
    S = _settled(db)
    n = len(S)
    if n < MIN_BASIS:
        return {"changed": False, "n": n,
                "reason": f"only {n}/{MIN_BASIS} settled — holding defaults"}

    # 1) probability floor: the lowest band where T2 clears the target
    floor = FLOOR_MAX
    f = FLOOR_MIN
    while f <= FLOOR_MAX + 1e-9:
        pool = [x for x in S if x["prob"] >= f]
        wr = _wr(pool)
        if len(pool) >= MIN_BUCKET and wr is not None and wr >= TARGET_WIN:
            floor = round(f, 2)
            break
        f += 0.02

    # 2) edge cap: tighten if the big-edge slice underperforms overall
    overall = _wr(S) or 0.0
    big = [x for x in S if x["edge"] > 0.10]
    bw = _wr(big)
    max_edge = (EDGE_MAX_TIGHT if len(big) >= 5 and bw is not None
                and bw < overall - 0.10 else EDGE_MAX_LOOSE)

    # 3) stake multiplier: scale with recent ROI (last 20 settled)
    recent = sorted(S, key=lambda x: x["at"])[-20:]
    stake = sum(x["price"] * x["units"] for x in recent)
    roi = (sum(x["pnl"] for x in recent) / stake) if stake else 0.0
    size_mult = s["size_mult"]
    if roi > 0.05:
        size_mult = min(SIZE_MULT_MAX, round(size_mult + 0.1, 2))
    elif roi < -0.05:
        size_mult = max(SIZE_MULT_MIN, round(size_mult - 0.1, 2))

    if (floor == s["min_prob"] and max_edge == s["max_edge"]
            and size_mult == s["size_mult"]):
        return {"changed": False, "n": n, "reason": "no adjustment warranted"}

    ver_n = int(s["version"].split(".")[1]) + 1
    rationale = (f"n={n}, overall {overall:.0%}: floor→{floor:.0%} (lowest band "
                 f"clearing {TARGET_WIN:.0%}), edge cap→{max_edge:.0%}, "
                 f"stake ×{size_mult:.1f} (recent ROI {roi:+.0%})")
    hist = (s.get("history", []) + [{
        "version": s["version"], "min_prob": s["min_prob"],
        "max_edge": s["max_edge"], "size_mult": s["size_mult"],
        "rationale": s.get("rationale", "")}])[-10:]
    new = {"min_prob": floor, "max_edge": max_edge, "size_mult": size_mult,
           "version": f"t2.{ver_n}", "n_basis": n, "rationale": rationale,
           "history": hist}
    _save_state(db, new)
    log.info("T2 self-improved", version=new["version"], min_prob=floor,
             max_edge=max_edge, size_mult=size_mult, n=n)
    return {"changed": True, "n": n, **new}
