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
POLICY_VERSION = "v4"  # v4: selectivity — only the calibrated band, sane edges

# v4 selectivity (from the CLV/edge dig on 54 settled bets): the bot was betting
# ~indiscriminately (54 in ~1.5 days) and the losers were concentrated in three
# buckets, so we simply stop betting them:
#   - model < 82%      → skip. The 68-82% band won only ~57% and lost money;
#     82-90% was the only well-calibrated, profitable band.
#   - edge > 15%       → skip. 15-30% went ~50%, >30% went ~38% — a huge "edge"
#     is model error / a stale quote, not value (was merely down-sized in v2).
#   - Challenger tier  → demoted: needs a stronger favorite (>=86%) to bet, since
#     the tier ran 50% overall.
PAPER_MIN_PROB = 0.82
PAPER_MIN_EDGE = 0.03
PAPER_MAX_EDGE = 0.15
PAPER_MIN_CONF = 0.60
PAPER_MAX_PRICE = 92  # above this there's nothing to win and one loss wrecks ROI
PAPER_MIN_PRICE = 20
CHALLENGER_MIN_PROB = 0.86

# kept for sizing (2u/3u require a believable edge band, not an outlier)
EDGE_SANE_MAX = PAPER_MAX_EDGE


def tier_prob_floor(tier: str | None) -> float:
    """Challenger is demoted — it needs a stronger favorite to clear."""
    return CHALLENGER_MIN_PROB if tier == "C" else PAPER_MIN_PROB


def policy_ok(prob: float, edge: float, price: int, tier: str | None) -> bool:
    """The single v4 gate, shared by the prematch and advisory bet paths so they
    can never drift: calibrated-band favorite, sane edge, sane price."""
    return (prob >= tier_prob_floor(tier)
            and PAPER_MIN_EDGE <= edge <= PAPER_MAX_EDGE
            and PAPER_MIN_PRICE <= price <= PAPER_MAX_PRICE)

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
               yes_bid: int | None, tier: str | None = None) -> BetDecision:
    """Pure policy: evaluate one market's two sides, pick at most one."""
    if yes_ask is None or yes_bid is None:
        return BetDecision(False, reason="no quote")
    if confidence < PAPER_MIN_CONF:
        return BetDecision(False, reason=f"model confidence {confidence:.2f} < {PAPER_MIN_CONF}")
    floor = tier_prob_floor(tier)
    best = None
    for side, prob, price in [("yes", p_yes, yes_ask), ("no", 1 - p_yes, 100 - yes_bid)]:
        edge = prob - price / 100
        if policy_ok(prob, edge, price, tier) and (best is None or edge > best[3]):
            best = (side, prob, price, edge)
    if best is None:
        return BetDecision(False, reason=f"no side clears the calibrated-band policy "
                                         f"(prob ≥ {floor:.0%}, edge "
                                         f"{PAPER_MIN_EDGE:.0%}–{PAPER_MAX_EDGE:.0%})")
    side, prob, price, edge = best
    units = size_units(prob, edge, confidence)
    chal = " (Challenger bar)" if tier == "C" else ""
    return BetDecision(True, side=side, prob=round(prob, 3), edge=round(edge, 3),
                       price_cents=price, units=units,
                       reason=f"prob {prob:.0%} ≥ {floor:.0%}{chal}, edge "
                              f"{edge * 100:.1f}% in [{PAPER_MIN_EDGE * 100:.0f}–"
                              f"{PAPER_MAX_EDGE * 100:.0f}]%"
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
