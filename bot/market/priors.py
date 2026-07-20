"""Historical set-duration priors — gate when a set boundary is plausible.

Sackmann records total match minutes, not per-set times, so the per-set duration
distribution is estimated as minutes / completed_sets over matches with timing
data, grouped by tier bucket and surface.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from bot.log import get_logger

log = get_logger("market.priors")

TIER_BUCKETS = {
    "G": "slam", "F": "tour", "M": "tour", "A": "tour", "C": "challenger",
}
MIN_SAMPLE = 200


@dataclass(frozen=True)
class SetDurationPriors:
    p05: float  # minutes
    p25: float
    p50: float
    p75: float
    p95: float
    n: int = 0

    def plausibility(self, elapsed_minutes: float) -> float:
        """Multiplier for a boundary candidate given time since last boundary.

        <70% of p05 → 0 (reject); 70%..100% of p05 → 0.75 (penalized);
        otherwise 1.0. Long sets are never penalized — tiebreaks happen.
        """
        if elapsed_minutes < self.p05 * 0.7:
            return 0.0
        if elapsed_minutes < self.p05:
            return 0.75
        return 1.0


DEFAULT_PRIORS = SetDurationPriors(p05=22.0, p25=30.0, p50=38.0, p75=47.0, p95=62.0)


def tier_bucket(tourney_level: str | None) -> str:
    return TIER_BUCKETS.get((tourney_level or "").strip(), "itf")


def load_priors(db: Session, tourney_level: str | None,
                surface: str | None) -> SetDurationPriors:
    """Quantiles of minutes-per-completed-set for (tier bucket, surface)."""
    from bot.models import Match, MatchSet
    from sqlalchemy import func

    bucket = tier_bucket(tourney_level)
    levels = [lvl for lvl, b in TIER_BUCKETS.items() if b == bucket]
    q = (
        select(Match.minutes, func.count(MatchSet.id))
        .join(MatchSet, MatchSet.match_id == Match.id)
        .where(Match.minutes.is_not(None), Match.minutes > 15,
               Match.is_duplicate.is_(False), MatchSet.completed.is_(True))
        .group_by(Match.id, Match.minutes)
    )
    if levels:
        q = q.where(Match.tourney_level.in_(levels))
    else:  # ITF bucket = any level not mapped above
        q = q.where(Match.tourney_level.notin_(list(TIER_BUCKETS)))
    if surface:
        q = q.where(Match.surface == surface)
    per_set = sorted(m / n for m, n in db.execute(q) if n)
    if len(per_set) < MIN_SAMPLE:
        log.warning("thin set-duration sample; using defaults",
                    bucket=bucket, surface=surface, n=len(per_set))
        return DEFAULT_PRIORS

    def pct(p: float) -> float:
        return per_set[min(len(per_set) - 1, int(p * len(per_set)))]

    pri = SetDurationPriors(p05=pct(0.05), p25=pct(0.25), p50=pct(0.50),
                            p75=pct(0.75), p95=pct(0.95), n=len(per_set))
    log.info("set-duration priors", bucket=bucket, surface=surface,
             p05=round(pri.p05, 1), p50=round(pri.p50, 1), p95=round(pri.p95, 1),
             n=pri.n)
    return pri
