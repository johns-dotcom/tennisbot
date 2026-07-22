"""The four testrun bots.

Two dimensions, four bots:
  - WHEN it bets:  pre-game (off the model's opening read) vs live (in-play,
    when an advisory clears mid-match)
  - HOW it tunes:  fixed (the hand-tuned v-series policy) vs self-improving
    (re-tunes its OWN thresholds from its OWN settled record)

  bot id   when      tuning
  ------   --------  ---------------
  pre      prematch  fixed
  preSI    prematch  self-improving
  live     advisory  fixed
  liveSI   advisory  self-improving

A self-improving bot nudges its probability floor toward the lowest band where
it actually clears the win target, tightens its edge cap if big-edge bets
underperform, and scales its stake with recent ROI — all bounded, and only once
it has enough settled bets (cold start inherits the fixed v-series policy). It
learns ONLY from its own basis, so the live bots learn from live results and the
pre-game bots from pre-game results. Advisory-only: imaginary bets, never orders.
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

log = get_logger("bots")

# bot registry — id → (basis it bets on, whether it self-improves, label)
#   prematch : pre-game, off the model's opening read (watch loop)
#   advisory : live, in-play when an advisory clears (engine)
#   top5     : the day's 5 highest-salience scenarios only (daily selector)
BOTS: dict[str, dict] = {
    "pre":    {"basis": "prematch", "si": False, "label": "Pre-Game"},
    "preSI":  {"basis": "prematch", "si": True,  "label": "Pre-Game · Self-Improving"},
    "live":   {"basis": "advisory", "si": False, "label": "Live"},
    "liveSI": {"basis": "advisory", "si": True,  "label": "Live · Self-Improving"},
    "top5":   {"basis": "top5",     "si": False, "label": "Top-5 Daily"},
    "top5SI": {"basis": "top5",     "si": True,  "label": "Top-5 Daily · Self-Improving"},
}
SI_BOTS = [b for b, m in BOTS.items() if m["si"]]
TOP5_N = 5  # max bets per day for the Top-5 bots

MIN_BASIS = 15          # settled bets required before a bot adapts at all
TARGET_WIN = 0.70
MIN_BUCKET = 8          # bets needed in a prob band to trust its win rate
FLOOR_MIN, FLOOR_MAX = 0.66, 0.85
EDGE_MAX_TIGHT, EDGE_MAX_LOOSE = 0.15, 0.30
SIZE_MULT_MIN, SIZE_MULT_MAX = 0.7, 1.3


def _state_key(bot_id: str) -> str:
    return f"si:{bot_id}:policy"


def _default_state(bot_id: str) -> dict:
    return {"min_prob": DEFAULT_POLICY.min_prob, "max_edge": DEFAULT_POLICY.max_edge,
            "size_mult": 1.0, "version": f"{bot_id}.0", "n_basis": 0,
            "rationale": "cold start — inheriting the fixed policy until enough "
                         "settled bets to learn from", "history": []}


def _load_state(db: Session, bot_id: str) -> dict:
    row = db.execute(select(IngestState.value).where(
        IngestState.key == _state_key(bot_id))).scalar()
    if not row:
        return _default_state(bot_id)
    try:
        s = json.loads(row)
        for k, v in _default_state(bot_id).items():
            s.setdefault(k, v)
        return s
    except Exception:
        return _default_state(bot_id)


def _save_state(db: Session, bot_id: str, state: dict) -> None:
    now = datetime.now(timezone.utc)
    payload = json.dumps(state)
    db.execute(pg_insert(IngestState).values(
        key=_state_key(bot_id), value=payload, updated_at=now
    ).on_conflict_do_update(index_elements=["key"],
                            set_={"value": payload, "updated_at": now}))
    db.commit()


def bot_policy(db: Session, bot_id: str) -> Policy:
    """A bot's current Policy — DEFAULT for fixed bots, learned for SI bots."""
    if not BOTS.get(bot_id, {}).get("si"):
        return DEFAULT_POLICY
    s = _load_state(db, bot_id)
    return replace(DEFAULT_POLICY, min_prob=s["min_prob"], max_edge=s["max_edge"],
                   size_mult=s["size_mult"], version=s["version"])


def bot_state(db: Session, bot_id: str) -> dict | None:
    """Learned state + history for an SI bot's self-improvement panel (else None)."""
    return _load_state(db, bot_id) if BOTS.get(bot_id, {}).get("si") else None


def iter_bot_policies(db: Session, basis: str) -> list[tuple[str, Policy]]:
    """(bot_id, policy) for the two bots that bet on this basis (prematch/advisory)."""
    return [(bid, bot_policy(db, bid)) for bid, m in BOTS.items()
            if m["basis"] == basis]


def _settled(db: Session, bot_id: str) -> list[dict]:
    rows = db.execute(
        select(PaperBet, KalshiMarket.result)
        .join(KalshiMarket, KalshiMarket.ticker == PaperBet.market_ticker)
        .where(PaperBet.bot == bot_id)).all()
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


def self_improve(db: Session, bot_id: str) -> dict:
    """Recompute an SI bot's policy from its own settled record; persist if changed."""
    if not BOTS.get(bot_id, {}).get("si"):
        return {"changed": False, "reason": "not a self-improving bot"}
    s = _load_state(db, bot_id)
    S = _settled(db, bot_id)
    n = len(S)
    if n < MIN_BASIS:
        return {"changed": False, "n": n,
                "reason": f"only {n}/{MIN_BASIS} settled — holding defaults"}

    # 1) probability floor: the lowest band where the bot clears the target
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

    try:
        ver_n = int(s["version"].split(".")[-1]) + 1
    except ValueError:
        ver_n = 1
    rationale = (f"n={n}, overall {overall:.0%}: floor→{floor:.0%} (lowest band "
                 f"clearing {TARGET_WIN:.0%}), edge cap→{max_edge:.0%}, "
                 f"stake ×{size_mult:.1f} (recent ROI {roi:+.0%})")
    hist = (s.get("history", []) + [{
        "version": s["version"], "min_prob": s["min_prob"],
        "max_edge": s["max_edge"], "size_mult": s["size_mult"],
        "rationale": s.get("rationale", "")}])[-10:]
    new = {"min_prob": floor, "max_edge": max_edge, "size_mult": size_mult,
           "version": f"{bot_id}.{ver_n}", "n_basis": n, "rationale": rationale,
           "history": hist}
    _save_state(db, bot_id, new)
    log.info("bot self-improved", bot=bot_id, version=new["version"],
             min_prob=floor, max_edge=max_edge, size_mult=size_mult, n=n)
    return {"changed": True, "n": n, **new}


def self_improve_all(db: Session) -> dict:
    """Run self-improvement for every SI bot; return {bot_id: result}."""
    return {bid: self_improve(db, bid) for bid in SI_BOTS}


def place_top5_bets(db: Session) -> int:
    """The Top-5 bots: each day, walk the day's scenarios by salience (best
    first) and back the watch side of each that clears the bot's policy, up to
    TOP5_N bets. A concentrated 'best plays of the day' strategy — same gate as
    the other bots, but a candidate universe of only the strongest scenarios.
    Idempotent: one bet per event per bot, capped at TOP5_N/day."""
    from datetime import time
    from sqlalchemy import func, text

    from bot.models import Scenario
    from bot.paper import BetDecision, place_bet, policy_ok, size_units
    from bot.scenarios import SERIES_TIER

    day = db.execute(select(func.max(Scenario.created_for))).scalar()
    if not day:
        return 0
    scen = db.execute(select(Scenario).where(Scenario.created_for == day)
                      .order_by(Scenario.salience.desc())).scalars().all()
    start_today = datetime.combine(datetime.now(timezone.utc).date(),
                                   time.min, tzinfo=timezone.utc)
    placed = 0
    for bot_id in ("top5", "top5SI"):
        policy = bot_policy(db, bot_id)
        done = set(db.execute(select(PaperBet.event_ticker).where(
            PaperBet.bot == bot_id)).scalars())
        today_n = db.execute(select(func.count(PaperBet.id)).where(
            PaperBet.bot == bot_id, PaperBet.created_at >= start_today)).scalar() or 0
        for sc in scen:
            if today_n >= TOP5_N:
                break
            if sc.event_ticker in done:
                continue
            row = db.execute(text(
                "SELECT yes_ask FROM market_ticks WHERE market_ticker = :t "
                "AND kind='quote' AND yes_ask IS NOT NULL "
                "AND ts > now() - interval '45 minutes' ORDER BY ts DESC LIMIT 1"),
                {"t": sc.market_ticker}).first()
            if not row or row[0] is None:
                continue
            price = int(row[0])              # yes_ask = cost to back the watch side
            prob = sc.prematch_prob          # P(watch side wins), pre-play
            edge = prob - price / 100
            tier = next((t for s, t in SERIES_TIER.items()
                         if sc.market_ticker.startswith(s)), "15")
            if not policy_ok(prob, edge, price, tier, policy):
                continue
            conf = (sc.facts or {}).get("model_confidence", 0.7)
            dec = BetDecision(True, side="yes", prob=round(prob, 3),
                              edge=round(edge, 3), price_cents=price,
                              units=size_units(prob, edge, conf, policy.size_mult),
                              reason=f"top-5 daily play (salience {sc.salience:.2f}) "
                                     f"— cleared {policy.version}")
            if place_bet(db, event_ticker=sc.event_ticker,
                         market_ticker=sc.market_ticker, player_id=sc.player_id,
                         decision=dec, confidence=conf, basis="prematch",
                         tier=tier, bot=bot_id, policy_version=policy.version,
                         reasoning={"match": (sc.facts or {}).get("match"),
                                    "prematch_prob": round(prob, 3),
                                    "salience": sc.salience, "top5": True}):
                done.add(sc.event_ticker)
                today_n += 1
                placed += 1
    return placed
