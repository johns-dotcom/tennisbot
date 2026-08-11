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
from bot.market.line_move import adverse_prematch, market_move_cents
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
              policy: Policy = DEFAULT_POLICY,
              confidence: float | None = None) -> bool:
    """The single bet gate, shared by the prematch and advisory paths (and all
    bots) so they can never drift: calibrated-band favorite, sane edge/price,
    and — when the caller supplies it — the model-confidence (data-depth) floor.
    Pass `confidence` from every path so the live/top5 bots gate on it too, not
    just the prematch path via decide_bet."""
    return ((confidence is None or confidence >= policy.min_conf)
            and prob >= tier_prob_floor(tier, policy)
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
    """One bet per event per bot. Returns True if placed.

    Records line movement (our side's drift from the open) on EVERY bet, and
    for PRE-MATCH bets applies the adverse-selection gate: skip when our side
    has fallen since the open (the market faded our pick). The chalk control is
    exempt — it must stay a pure market-favorite baseline. In-play bets record
    the move but are not gated (an in-play drift is the score, not news)."""
    exists = db.execute(select(PaperBet.id).where(
        PaperBet.bot == bot, PaperBet.event_ticker == event_ticker)).first()
    if exists:
        return False
    move = market_move_cents(db, market_ticker, decision.side, decision.price_cents)
    if bot != "chalk" and basis == "prematch" and adverse_prematch(move):
        log.info("PAPER BET SKIPPED — adverse pre-match line move", bot=bot,
                 event=event_ticker, side=decision.side, move=move)
        return False
    db.add(PaperBet(
        created_at=datetime.now(timezone.utc), bot=bot, event_ticker=event_ticker,
        market_ticker=market_ticker, player_id=player_id, side=decision.side,
        price_cents=decision.price_cents, model_prob=decision.prob,
        model_confidence=round(confidence, 3), edge=decision.edge, basis=basis,
        units=round(max(1.0, min(MAX_UNITS, decision.units)), 1), tier=tier,
        state_at_placement=state,
        reasoning={**(reasoning or {}), "policy_reason": decision.reason,
                   "policy_version": policy_version, "market_move": move}))
    db.commit()
    log.info("PAPER BET PLACED", bot=bot, event=event_ticker, side=decision.side,
             price=decision.price_cents, prob=decision.prob, edge=decision.edge,
             units=decision.units, basis=basis)
    return True


def _apply_settlement(bet, result: str, source: str) -> bool:
    """Settle one bet from a market/scoreline result. Returns True if the result
    was actionable (yes/no/void) and the bet moved to a settled state; False for
    an unrecognised/empty result (bet left untouched)."""
    outcome = advisory_outcome(bet.side, result)
    if outcome is None:
        return False
    bet.status = outcome if outcome in ("won", "lost") else "void"
    per = advisory_pnl_cents(bet.side, bet.price_cents, result)
    # units is decimal (Float); pnl_cents is Float too, so multi-unit P&L keeps
    # its fractional cents instead of being silently rounded into an int column.
    bet.pnl_cents = round(per * (bet.units or 1), 2) if per is not None else None
    bet.settled_at = datetime.now(timezone.utc)
    bet.reasoning = {**(bet.reasoning or {}), "settled_from": source}
    return True


def settle_open_bets(db: Session) -> int:
    """Settle open paper bets. Kalshi's market result is authoritative, but it
    LAGS the match end (the market sits 'active' with result=None for a while
    after the final point) — so a decided bet would otherwise show 'open' for
    ages. Fallback: settle from the final recorded scoreline (sets_a = the YES
    player of the bet's market), which we have the instant the match ends. When
    the authoritative result later arrives it re-checks a scoreline-settled bet
    and corrects it if they disagree (guards the ~rare mapping flip)."""
    from sqlalchemy import text as _sqltext

    n = 0
    # 1) authoritative — market has a result
    rows = db.execute(
        select(PaperBet, KalshiMarket.result)
        .join(KalshiMarket, KalshiMarket.ticker == PaperBet.market_ticker)
        .where(PaperBet.status == "open", KalshiMarket.result.is_not(None))).all()
    for bet, result in rows:
        if _apply_settlement(bet, result, "kalshi_result"):
            n += 1
            log.info("paper bet settled", event=bet.event_ticker, bot=bet.bot,
                     outcome=bet.status, pnl=bet.pnl_cents)

    # 2) fallback — match finished (final scoreline) but market not yet settled
    open_rest = db.execute(
        select(PaperBet, KalshiMarket.result)
        .join(KalshiMarket, KalshiMarket.ticker == PaperBet.market_ticker)
        .where(PaperBet.status == "open", KalshiMarket.result.is_(None))).all()
    for bet, _ in open_rest:
        fin = db.execute(_sqltext(
            "SELECT sets_a, sets_b FROM match_score_log WHERE market_ticker = :t "
            "AND is_final = true ORDER BY ts DESC LIMIT 1"),
            {"t": bet.market_ticker}).first()
        if not fin or fin[0] == fin[1]:
            continue  # no final score, or an undecided/void-looking line
        result = "yes" if fin[0] > fin[1] else "no"  # sets_a = YES player's sets
        _apply_settlement(bet, result, "scoreline")
        n += 1
        log.info("paper bet settled (scoreline)", event=bet.event_ticker,
                 bot=bet.bot, outcome=bet.status, pnl=bet.pnl_cents)

    # 3) correction — a scoreline-settled bet whose authoritative market result
    # now disagrees. Include "void" (a walkover/void reverses a scoreline win),
    # not just yes/no, so a scoreline-won bet later voided gets corrected too.
    prov = db.execute(
        select(PaperBet, KalshiMarket.result)
        .join(KalshiMarket, KalshiMarket.ticker == PaperBet.market_ticker)
        .where(PaperBet.status.in_(("won", "lost")),
               KalshiMarket.result.in_(("yes", "no", "void")),
               PaperBet.reasoning["settled_from"].astext == "scoreline")).all()
    for bet, result in prov:
        want = advisory_outcome(bet.side, result)  # won|lost|void|None
        if want is not None and want != bet.status:
            _apply_settlement(bet, result, "kalshi_result_corrected")
            log.warning("paper bet corrected on settlement", event=bet.event_ticker,
                        bot=bet.bot, to=bet.status)
    db.commit()  # persist settlements + any step-3 corrections
    return n
