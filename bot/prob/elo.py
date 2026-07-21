"""Surface-adjusted Elo with set-level updates — the baseline WinProbabilityModel.

Deliberately simple: a five-component external Elo with walk-forward retraining
and Platt scaling will replace this behind the same interface. The interface and
the state-conditioning are the investment; this is the placeholder engine.

No market data of any kind is visible from this module (CLAUDE.md rule 2).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from bot.log import get_logger
from bot.prob.model import MatchState, Prediction, WinProbabilityModel
from bot.prob.state_adjust import condition_on_state

log = get_logger("prob.elo")

BASE_RATING = 1500.0
K_SET = 16.0  # per completed set (roughly K=32 per match split across sets)
SURFACE_BLEND = 0.3  # weight of surface-specific rating in the blend
TIER_K_MULT = {"G": 1.2, "F": 1.1, "M": 1.1, "A": 1.0, "C": 0.9}
DEFAULT_TIER_MULT = 0.8  # ITF/Futures and everything else
PROVISIONAL_1, PROVISIONAL_2 = 30, 100  # set-count thresholds for K boosts
CONFIDENCE_FULL_SETS = 60  # sets seen for full rating confidence
# Global calibration for the raw Elo expectation, fitted by walk-forward log
# loss against realized outcomes only (never price — CLAUDE.md rule 2), via
# bot.prob.calibrate. The prior 1.65 (fit on 2023-24) over-sharpened the tail:
# the 2026 walk-forward (n=28,782) showed favorites over-predicting by 2-3 pts
# per bucket and refit to 1.437 (log loss 0.5981 → 0.5966). b stays 0 — a
# two-player model must be symmetric, so no bias term. Refit when ratings change.
PLATT_A = 1.437


@dataclass
class _Rating:
    overall: float = BASE_RATING
    by_surface: dict[str, float] = field(default_factory=dict)
    sets_seen: int = 0

    def blended(self, surface: str | None) -> float:
        if surface and surface in self.by_surface:
            return (1 - SURFACE_BLEND) * self.overall + SURFACE_BLEND * self.by_surface[surface]
        return self.overall


def _expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


class SetElo(WinProbabilityModel):
    def __init__(self) -> None:
        self.ratings: dict[int, _Rating] = {}
        self.trained_through: date | None = None

    def _get(self, pid: int) -> _Rating:
        r = self.ratings.get(pid)
        if r is None:
            r = self.ratings[pid] = _Rating()
        return r

    # ---------- training ----------

    def _k(self, rating: _Rating, tier: str | None) -> float:
        k = K_SET * TIER_K_MULT.get(tier or "", DEFAULT_TIER_MULT)
        if rating.sets_seen < PROVISIONAL_1:
            return k * 2.0
        if rating.sets_seen < PROVISIONAL_2:
            return k * 1.5
        return k

    def update_set(self, winner_of_set: int, loser_of_set: int,
                   surface: str | None, tier: str | None) -> None:
        """Apply one completed set result."""
        rw, rl = self._get(winner_of_set), self._get(loser_of_set)
        e = _expected(rw.blended(surface), rl.blended(surface))
        kw, kl = self._k(rw, tier), self._k(rl, tier)
        rw.overall += kw * (1 - e)
        rl.overall -= kl * (1 - e)
        if surface:
            sw = rw.by_surface.get(surface, rw.overall)
            sl = rl.by_surface.get(surface, rl.overall)
            es = _expected(sw, sl)
            rw.by_surface[surface] = sw + kw * (1 - es)
            rl.by_surface[surface] = sl - kl * (1 - es)
        rw.sets_seen += 1
        rl.sets_seen += 1

    def apply_match(self, winner_id: int, loser_id: int, surface: str | None,
                    tier: str | None, set_results: list[bool]) -> None:
        """set_results: per completed set, True if won by the MATCH winner."""
        for won_by_match_winner in set_results:
            if won_by_match_winner:
                self.update_set(winner_id, loser_id, surface, tier)
            else:
                self.update_set(loser_id, winner_id, surface, tier)

    def fit_from_db(self, db: Session, through: date | None = None) -> int:
        """Replay history in chronological order. Returns matches applied."""
        rows = self._load_matches(db, through)
        n = 0
        for row in rows:
            self.apply_match(row["winner_id"], row["loser_id"], row["surface"],
                             row["tier"], row["set_results"])
            n += 1
        self.trained_through = through or (rows[-1]["date"] if rows else None)
        log.info("elo fitted", matches=n, through=str(self.trained_through))
        return n

    @staticmethod
    def _load_matches(db: Session, through: date | None = None,
                      start: date | None = None) -> list[dict]:
        """Played matches with per-set outcomes, chronological (date, round)."""
        from bot.models import Match, MatchSet
        from bot.stats.types import PLAYED_OUTCOMES, round_rank

        q = select(Match.id, Match.match_date, Match.winner_id, Match.loser_id,
                   Match.surface, Match.tourney_level, Match.round, Match.best_of).where(
            Match.outcome.in_(PLAYED_OUTCOMES), Match.is_duplicate.is_(False),
            Match.match_date.is_not(None))
        if through:
            q = q.where(Match.match_date < through)
        if start:
            q = q.where(Match.match_date >= start)
        matches = db.execute(q).all()
        by_id: dict[int, list[tuple[int, bool]]] = {m[0]: [] for m in matches}
        ids = list(by_id)
        for i in range(0, len(ids), 50000):
            for mid, set_no, by_winner in db.execute(
                select(MatchSet.match_id, MatchSet.set_number,
                       MatchSet.set_won_by_match_winner)
                .where(MatchSet.match_id.in_(ids[i:i + 50000]),
                       MatchSet.completed.is_(True))
            ):
                by_id[mid].append((set_no, by_winner))
        out = []
        for (mid, mdate, wid, lid, surface, tier, rnd, best_of) in matches:
            sets = [w for _, w in sorted(by_id[mid])]
            out.append({"id": mid, "date": mdate, "winner_id": wid, "loser_id": lid,
                        "surface": surface, "tier": tier, "round": rnd,
                        "best_of": best_of or 3, "set_results": sets})
        out.sort(key=lambda r: (r["date"], round_rank(r["round"])))
        return out

    # ---------- prediction ----------

    def predict(self, player_a: int, player_b: int, surface: str | None,
                tier: str | None, match_state: MatchState) -> Prediction:
        ra = self.ratings.get(player_a, _Rating())
        rb = self.ratings.get(player_b, _Rating())
        raw = _expected(ra.blended(surface), rb.blended(surface))
        # PLATT_A was fitted on pre-match predictions: sharpen first, then condition
        raw = min(max(raw, 1e-9), 1 - 1e-9)
        z = PLATT_A * math.log(raw / (1 - raw))
        p_prematch = 1 / (1 + math.exp(-z))
        p = condition_on_state(p_prematch, match_state)
        confidence = min(ra.sets_seen, rb.sets_seen) / CONFIDENCE_FULL_SETS
        return Prediction(p_a=p, confidence=min(1.0, confidence))
