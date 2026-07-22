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
POLICY_VERSION = "v9"  # v9: recency-aware model confidence (stale ratings discounted)

# v8 — fit to 78 settled bets across the fixed-scale run (the bots' own data):
#   * By probability band the bet population is badly overconfident BELOW ~82%
#     (68-82% won ~40-47% despite 71-78% model prob — adverse selection: the
#     market fades a mediocre favorite for a reason) and only genuinely wins at
#     90%+ (n=20, 100%). So the floor goes back UP to 0.82 on the recalibrated
#     scale — there IS volume there (31 of 78 bets), unlike the pre-recalibration
#     v4 era. Confidence is data-depth, this is the calibrated-value band.
#   * Big edges LOST: 15-30% edge went 23% (n=13). v7's raised cap was wrong —
#     a large gap vs the market is adverse selection, not free value. Back to 15%.
#   * Challenger stays demoted (stronger favorite required).
# The probability MODEL itself is well-calibrated on the full population (the
# 28k walk-forward); this is a selection effect in the bet universe, so the fix
# is policy selectivity, not re-recalibrating the model.
PAPER_MIN_PROB = 0.82
PAPER_MIN_EDGE = 0.03
PAPER_MAX_EDGE = 0.15
PAPER_MIN_CONF = 0.60
PAPER_MAX_PRICE = 92  # above this there's nothing to win and one loss wrecks ROI
PAPER_MIN_PRICE = 20
CHALLENGER_MIN_PROB = 0.85

# kept for sizing (multi-unit requires a believable edge, not an outlier)
EDGE_SANE_MAX = PAPER_MAX_EDGE

# Confidence-driven decimal sizing (v5). Stake in [1.0, MAX_UNITS], one decimal.
# Conviction = how far into the strong-favorite range the pick is, GATED by how
# well the read is supported by data — the two are multiplied, so a multi-unit
# stake needs BOTH a strong calibrated probability AND deep data. GAMMA > 1 and
# a high saturation point keep multi-unit sparing: most bets sit near 1u and
# only genuine standouts approach the cap.
MAX_UNITS = 3.0
SIZING_GAMMA = 1.7
PROB_SATURATION = 0.97  # prob at which the stake reaches MAX_UNITS


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


@dataclass(frozen=True)
class Policy:
    """The tunable thresholds a bot bets by. T1 uses DEFAULT_POLICY (fixed,
    hand-tuned v5); T2 supplies its own self-adapted Policy (see bot/t2.py).
    size_mult scales the conviction stake (T2 presses/eases with its results)."""
    min_prob: float = PAPER_MIN_PROB
    min_edge: float = PAPER_MIN_EDGE
    max_edge: float = PAPER_MAX_EDGE
    min_conf: float = PAPER_MIN_CONF
    min_price: int = PAPER_MIN_PRICE
    max_price: int = PAPER_MAX_PRICE
    challenger_min_prob: float = CHALLENGER_MIN_PROB
    size_mult: float = 1.0
    version: str = POLICY_VERSION


DEFAULT_POLICY = Policy()


def tier_prob_floor(tier: str | None, policy: Policy = DEFAULT_POLICY) -> float:
    """Challenger is demoted — it needs a stronger favorite to clear."""
    return policy.challenger_min_prob if tier == "C" else policy.min_prob


def policy_ok(prob: float, edge: float, price: int, tier: str | None,
              policy: Policy = DEFAULT_POLICY) -> bool:
    """The single bet gate, shared by the prematch and advisory paths (and both
    bots) so they can never drift: calibrated-band favorite, sane edge/price."""
    return (prob >= tier_prob_floor(tier, policy)
            and policy.min_edge <= edge <= policy.max_edge
            and policy.min_price <= price <= policy.max_price)


def size_units(prob: float, edge: float, confidence: float,
               size_mult: float = 1.0) -> float:
    """Continuous, confidence-driven stake in [1.0, MAX_UNITS] (one decimal).

    conviction = prob_score · conf_score, where prob_score is how far the pick
    reaches into the strong-favorite range and conf_score is data depth beyond
    the floor. Multiplicative → both must be high, so multi-unit stays sparing.
    A suspicious (too-large) edge never presses the size. size_mult lets a bot
    scale its aggression (T2's self-improvement)."""
    if edge > EDGE_SANE_MAX:
        return 1.0
    prob_score = _clip01((prob - PAPER_MIN_PROB) / (PROB_SATURATION - PAPER_MIN_PROB))
    conf_score = _clip01((confidence - PAPER_MIN_CONF) / (1.0 - PAPER_MIN_CONF))
    conviction = prob_score * conf_score
    units = 1.0 + (MAX_UNITS - 1.0) * conviction ** SIZING_GAMMA
    return round(min(MAX_UNITS, max(1.0, units * size_mult)), 1)


@dataclass
class BetDecision:
    place: bool
    side: str | None = None  # 'yes'/'no' relative to the evaluated market
    prob: float = 0.0
    edge: float = 0.0
    price_cents: int = 0
    units: float = 1.0
    reason: str = ""


def decide_bet(p_yes: float, confidence: float, yes_ask: int | None,
               yes_bid: int | None, tier: str | None = None,
               policy: Policy = DEFAULT_POLICY) -> BetDecision:
    """Pure policy: evaluate one market's two sides, pick at most one."""
    if yes_ask is None or yes_bid is None:
        return BetDecision(False, reason="no quote")
    if confidence < policy.min_conf:
        return BetDecision(False, reason=f"model confidence {confidence:.2f} < {policy.min_conf}")
    floor = tier_prob_floor(tier, policy)
    best = None
    for side, prob, price in [("yes", p_yes, yes_ask), ("no", 1 - p_yes, 100 - yes_bid)]:
        edge = prob - price / 100
        if policy_ok(prob, edge, price, tier, policy) and (best is None or edge > best[3]):
            best = (side, prob, price, edge)
    if best is None:
        return BetDecision(False, reason=f"no side clears the {policy.version} policy "
                                         f"(prob ≥ {floor:.0%}, edge "
                                         f"{policy.min_edge:.0%}–{policy.max_edge:.0%})")
    side, prob, price, edge = best
    units = size_units(prob, edge, confidence, policy.size_mult)
    chal = " (Challenger bar)" if tier == "C" else ""
    return BetDecision(True, side=side, prob=round(prob, 3), edge=round(edge, 3),
                       price_cents=price, units=units,
                       reason=f"prob {prob:.0%} ≥ {floor:.0%}{chal}, edge "
                              f"{edge * 100:.1f}% in [{policy.min_edge * 100:.0f}–"
                              f"{policy.max_edge * 100:.0f}]%"
                              f"{f', {units:.1f}u conviction' if units > 1 else ''}")


def place_bet(db: Session, *, event_ticker: str, market_ticker: str,
              player_id: int | None, decision: BetDecision, confidence: float,
              basis: str, tier: str | None, state: str = "0-0",
              reasoning: dict | None = None, bot: str = "pre",
              policy_version: str = POLICY_VERSION) -> bool:
    """One bet per event per bot. Returns True if placed."""
    exists = db.execute(select(PaperBet.id).where(
        PaperBet.bot == bot, PaperBet.event_ticker == event_ticker)).first()
    if exists:
        return False
    db.add(PaperBet(
        created_at=datetime.now(timezone.utc), bot=bot, event_ticker=event_ticker,
        market_ticker=market_ticker, player_id=player_id, side=decision.side,
        price_cents=decision.price_cents, model_prob=decision.prob,
        model_confidence=round(confidence, 3), edge=decision.edge, basis=basis,
        units=round(max(1.0, min(MAX_UNITS, decision.units)), 1), tier=tier,
        state_at_placement=state,
        reasoning={**(reasoning or {}), "policy_reason": decision.reason,
                   "policy_version": policy_version}))
    db.commit()
    log.info("PAPER BET PLACED", bot=bot, event=event_ticker, side=decision.side,
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
