"""Bot testrun — the paper-betting strategy lab.

The bot places IMAGINARY one-contract bets for itself, selectively (most
matches get no bet), and its settled record is the tuning signal. Target:
≥ 70% winners after month 1; until then the policy below is what gets
iterated on, using the per-bucket breakdowns on the /testrun page.

Policy v2 ("selective favorite value, quality over quantity"): a 70% RECORD
(not ROI) requires picking mostly winners, so bets skew to model favorites —
but only when the market is paying more than the model thinks it should:
  - model prob for the chosen side ≥ PAPER_MIN_PROB
  - edge vs the executable ask ≥ PAPER_MIN_EDGE
  - model confidence ≥ PAPER_MIN_CONF (data depth on both players)
  - sane executable price, one bet per event, ever
Sizing treats an implausibly large edge as a caution flag, not conviction: a
big edge caps at 1u and an absurd one (> EDGE_SUSPECT) is skipped entirely —
2u/3u are reserved for well-supported favorites with a believable edge.

Nothing here touches an order. CLAUDE.md rule 1 holds: this table is fiction
kept honest by settlement against real results.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from bot.log import get_logger
from bot.models import KalshiMarket, PaperBet
from bot.track import advisory_outcome, advisory_pnl_cents

log = get_logger("paper")

# bump whenever any threshold below changes — the testrun timeline annotates
# version changes so before/after records never blend silently
POLICY_VERSION = "v2"

PAPER_MIN_PROB = 0.68
PAPER_MIN_EDGE = 0.03
PAPER_MIN_CONF = 0.60
PAPER_MAX_PRICE = 92  # above this there's nothing to win and one loss wrecks ROI
PAPER_MIN_PRICE = 20

# Quality over quantity (v2): an implausibly large edge at a mid price is far
# more likely model overconfidence or a stale/thin quote than genuine value, so
# a big edge is a CAUTION flag, not a reason to press.
#   - edge above EDGE_SANE_MAX  → never upsize past 1u (don't chase model error)
#   - edge above EDGE_SUSPECT   → don't bet at all (looks like a data problem)
# Real conviction (2u/3u) is reserved for well-supported favorites whose edge is
# in a believable band, not for outliers.
EDGE_SANE_MAX = 0.15
EDGE_SUSPECT = 0.30

# Unit sizing: 1u default; 2u strong conviction; 3u EXTREMELY sparse. Every
# threshold must be met AND the edge must be believable. Never more than 3.
UNITS_2 = {"prob": 0.75, "edge_lo": 0.05, "edge_hi": EDGE_SANE_MAX, "conf": 0.75}
UNITS_3 = {"prob": 0.85, "edge_lo": 0.08, "edge_hi": EDGE_SANE_MAX, "conf": 0.88}


def size_units(prob: float, edge: float, confidence: float) -> int:
    # a suspiciously large edge is a red flag, not conviction — stay at 1u
    if edge > EDGE_SANE_MAX:
        return 1
    if (prob >= UNITS_3["prob"] and UNITS_3["edge_lo"] <= edge <= UNITS_3["edge_hi"]
            and confidence >= UNITS_3["conf"]):
        return 3
    if (prob >= UNITS_2["prob"] and UNITS_2["edge_lo"] <= edge <= UNITS_2["edge_hi"]
            and confidence >= UNITS_2["conf"]):
        return 2
    return 1


@dataclass
class BetDecision:
    place: bool
    side: str | None = None  # 'yes'/'no' relative to the evaluated market
    prob: float = 0.0
    edge: float = 0.0
    price_cents: int = 0
    units: int = 1
    reason: str = ""


def decide_bet(p_yes: float, confidence: float, yes_ask: int | None,
               yes_bid: int | None) -> BetDecision:
    """Pure policy: evaluate one market's two sides, pick at most one."""
    if yes_ask is None or yes_bid is None:
        return BetDecision(False, reason="no quote")
    if confidence < PAPER_MIN_CONF:
        return BetDecision(False, reason=f"model confidence {confidence:.2f} < {PAPER_MIN_CONF}")
    sides = [("yes", p_yes, yes_ask), ("no", 1 - p_yes, 100 - yes_bid)]
    best = None
    for side, prob, price in sides:
        if not (PAPER_MIN_PRICE <= price <= PAPER_MAX_PRICE):
            continue
        edge = prob - price / 100
        if prob >= PAPER_MIN_PROB and edge >= PAPER_MIN_EDGE:
            if best is None or edge > best[3]:
                best = (side, prob, price, edge)
    if best is None:
        return BetDecision(False, reason="no side clears prob+edge gates")
    side, prob, price, edge = best
    # quality over quantity: a monstrous edge at a live price is almost always a
    # stale/thin quote or model error, not free money — pass rather than pile in
    if edge > EDGE_SUSPECT:
        return BetDecision(False, side=side, prob=round(prob, 3), edge=round(edge, 3),
                           price_cents=price,
                           reason=f"edge {edge * 100:.0f}% implausibly large "
                                  f"(> {EDGE_SUSPECT * 100:.0f}%) — likely a stale "
                                  f"quote or model error, skipped")
    units = size_units(prob, edge, confidence)
    return BetDecision(True, side=side, prob=round(prob, 3), edge=round(edge, 3),
                       price_cents=price, units=units,
                       reason=f"prob {prob:.0%} ≥ {PAPER_MIN_PROB:.0%}, "
                              f"edge {edge * 100:.1f}% ≥ {PAPER_MIN_EDGE * 100:.0f}%"
                              f"{f', {units}u conviction' if units > 1 else ''}")


def place_bet(db: Session, *, event_ticker: str, market_ticker: str,
              player_id: int | None, decision: BetDecision, confidence: float,
              basis: str, tier: str | None, state: str = "0-0",
              reasoning: dict | None = None) -> bool:
    """One bet per event, ever. Returns True if placed."""
    exists = db.execute(select(PaperBet.id).where(
        PaperBet.event_ticker == event_ticker)).first()
    if exists:
        return False
    db.add(PaperBet(
        created_at=datetime.now(timezone.utc), event_ticker=event_ticker,
        market_ticker=market_ticker, player_id=player_id, side=decision.side,
        price_cents=decision.price_cents, model_prob=decision.prob,
        model_confidence=round(confidence, 3), edge=decision.edge, basis=basis,
        units=max(1, min(3, decision.units)), tier=tier, state_at_placement=state,
        reasoning={**(reasoning or {}), "policy_reason": decision.reason,
                   "policy_version": POLICY_VERSION}))
    db.commit()
    log.info("PAPER BET PLACED", event=event_ticker, side=decision.side,
             price=decision.price_cents, prob=decision.prob, edge=decision.edge,
             units=decision.units, basis=basis)
    return True


def settle_open_bets(db: Session) -> int:
    """Settle open paper bets whose market has a result. Returns settled count."""
    rows = db.execute(
        select(PaperBet, KalshiMarket.result)
        .join(KalshiMarket, KalshiMarket.ticker == PaperBet.market_ticker)
        .where(PaperBet.status == "open", KalshiMarket.result.is_not(None))
    ).all()
    n = 0
    for bet, result in rows:
        outcome = advisory_outcome(bet.side, result)
        if outcome is None:
            continue
        bet.status = outcome if outcome in ("won", "lost") else "void"
        per_contract = advisory_pnl_cents(bet.side, bet.price_cents, result)
        bet.pnl_cents = per_contract * (bet.units or 1) \
            if per_contract is not None else None
        bet.settled_at = datetime.now(timezone.utc)
        n += 1
        log.info("paper bet settled", event=bet.event_ticker, outcome=bet.status,
                 pnl=bet.pnl_cents)
    if n:
        db.commit()
    return n
