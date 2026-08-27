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
# recency-aware confidence (v9): confidence is now RECENT activity, not lifetime.
# A ~150-day half-life; ~45 decayed sets ⇒ full confidence (a normally-active
# player clears it; a returnee / long-inactive player does not).
RECENCY_HALFLIFE_DAYS = 150
CONFIDENCE_RECENT_FULL = 45
# Global calibration for the raw Elo expectation, fitted by walk-forward log
# loss against realized outcomes only (never price — CLAUDE.md rule 2), via
# bot.prob.calibrate. History: 1.65 (2023-24) over-sharpened; 1.437 (2026
# walk-forward, n=28,782). By 2026-08 the ratings had drifted the fit down again
# — the trailing-365d walk-forward (n=46,322) refit to ~1.20 (log loss 0.6027 →
# 0.6001, Brier 0.2071 → 0.2066), i.e. the model had crept back toward
# over-confident favorites. b stays 0 — a two-player model must be symmetric.
# This value is now only the DEFAULT: the daily ingest refits and persists a new
# scalar (calibrate.refit_and_persist_platt), and load_platt_calibration()
# overrides this on model rebuild, so it can't silently go stale again.
PLATT_A = 1.201


@dataclass
class _Rating:
    overall: float = BASE_RATING
    by_surface: dict[str, float] = field(default_factory=dict)
    sets_seen: int = 0
    recent: float = 0.0          # time-decayed set count → RECENCY-aware confidence
    last_day: date | None = None

    def blended(self, surface: str | None) -> float:
        if surface and surface in self.by_surface:
            return (1 - SURFACE_BLEND) * self.overall + SURFACE_BLEND * self.by_surface[surface]
        return self.overall

    def touch(self, day: date | None, sets: int) -> None:
        """Add `sets` of activity on `day`, decaying prior activity by its age.
        A player who's played little recently (a returnee like Kokkinakis: huge
        lifetime sets_seen but few in 2026) ends with low `recent` → low
        confidence, even though the rating itself is high."""
        if day is not None and self.last_day is not None:
            dd = (day - self.last_day).days
            if dd > 0:
                self.recent *= 0.5 ** (dd / RECENCY_HALFLIFE_DAYS)
        self.recent += sets
        if day is not None:
            self.last_day = day


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
        pre_w, pre_l = rw.overall, rl.overall  # seed a new surface from PRE-update overall
        rw.overall += kw * (1 - e)
        rl.overall -= kl * (1 - e)
        if surface:
            sw = rw.by_surface.get(surface, pre_w)
            sl = rl.by_surface.get(surface, pre_l)
            es = _expected(sw, sl)
            rw.by_surface[surface] = sw + kw * (1 - es)
            rl.by_surface[surface] = sl - kl * (1 - es)
        rw.sets_seen += 1
        rl.sets_seen += 1

    def apply_match(self, winner_id: int, loser_id: int, surface: str | None,
                    tier: str | None, set_results: list[bool],
                    day: date | None = None) -> None:
        """set_results: per completed set, True if won by the MATCH winner."""
        for won_by_match_winner in set_results:
            if won_by_match_winner:
                self.update_set(winner_id, loser_id, surface, tier)
            else:
                self.update_set(loser_id, winner_id, surface, tier)
        # recency activity: one touch per match, on its date
        ns = len(set_results) or 1
        self._get(winner_id).touch(day, ns)
        self._get(loser_id).touch(day, ns)

    def fit_from_db(self, db: Session, through: date | None = None) -> int:
        """Replay history in chronological order. Returns matches applied."""
        rows = self._load_matches(db, through)
        n = 0
        for row in rows:
            self.apply_match(row["winner_id"], row["loser_id"], row["surface"],
                             row["tier"], row["set_results"], day=row.get("date"))
            n += 1
        self.trained_through = through or (rows[-1]["date"] if rows else None)
        # refresh the state-conditioned recalibration from the persisted fit
        # (daily-refit) whenever the model is rebuilt; falls back to defaults
        from bot.prob.state_adjust import load_state_calibration
        load_state_calibration(db)
        # refresh the global pre-match Platt scalar from the newest persisted refit
        # so calibration tracks the current ratings instead of a stale constant
        from bot.prob.calibrate import load_platt_calibration
        _pa = load_platt_calibration(db)
        if _pa is not None:
            global PLATT_A
            PLATT_A = _pa
        log.info("elo fitted", matches=n, through=str(self.trained_through),
                 platt_a=PLATT_A)
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

    @staticmethod
    def _recent_as_of(r: _Rating, as_of: date | None) -> float:
        """r.recent decayed forward from the player's last match to `as_of`,
        WITHOUT mutating the rating. This is what makes recency awareness bite
        at prediction time: a returnee whose last match was long ago has their
        idle stretch decayed in here, not only after their next match applies."""
        val = r.recent
        if as_of is not None and r.last_day is not None:
            dd = (as_of - r.last_day).days
            if dd > 0:
                val *= 0.5 ** (dd / RECENCY_HALFLIFE_DAYS)
        return val

    # ---------- persistence ----------
    # Ratings only change on the daily ingest, so the fit is done once there and
    # every other process loads the result. This is what keeps the web service
    # off the ~330 MB replay path.

    def to_snapshot(self) -> dict:
        """Compact, JSON-safe form of the fitted state.

        Floats are stored at full precision, NOT rounded. Rounding ratings to
        4dp shifted predictions in the 8th decimal — invisible on screen, but it
        would mean the web service and the ingest disagree about the same match
        for no reason anyone could later explain. Python's float repr is
        shortest-round-trip, so this is exact and costs only a few hundred kB
        across the whole player pool."""
        return {str(pid): [r.overall, r.sets_seen, r.recent,
                           r.last_day.isoformat() if r.last_day else None,
                           dict(r.by_surface)]
                for pid, r in self.ratings.items()}

    def load_snapshot(self, payload: dict, through: date | None = None) -> int:
        """Restore fitted state. Returns the number of players loaded."""
        self.ratings = {}
        for pid, v in (payload or {}).items():
            r = _Rating()
            r.overall, r.sets_seen, r.recent = float(v[0]), int(v[1]), float(v[2])
            r.last_day = date.fromisoformat(v[3]) if v[3] else None
            r.by_surface = {k: float(x) for k, x in (v[4] or {}).items()}
            self.ratings[int(pid)] = r
        self.trained_through = through
        return len(self.ratings)

    def save_snapshot(self, db: Session, n_matches: int | None = None,
                      keep: int = 3) -> None:
        """Persist the fit and prune older rows."""
        from sqlalchemy import select as _select

        from bot.models import EloSnapshot
        payload = self.to_snapshot()
        db.add(EloSnapshot(trained_through=self.trained_through,
                           n_matches=n_matches, n_players=len(payload),
                           ratings=payload))
        db.commit()
        old = list(db.execute(_select(EloSnapshot.id)
                              .order_by(EloSnapshot.fitted_at.desc())
                              .offset(keep)).scalars())
        if old:
            for oid in old:
                db.delete(db.get(EloSnapshot, oid))
            db.commit()
        log.info("elo snapshot saved", players=len(payload),
                 through=str(self.trained_through))

    @classmethod
    def from_snapshot_db(cls, db: Session):
        """The newest persisted fit, or None if there is none yet."""
        from sqlalchemy import select as _select

        from bot.models import EloSnapshot
        row = db.execute(_select(EloSnapshot)
                         .order_by(EloSnapshot.fitted_at.desc())
                         .limit(1)).scalars().first()
        if row is None:
            return None
        m = cls()
        m.load_snapshot(row.ratings, row.trained_through)
        from bot.prob.state_adjust import load_state_calibration
        load_state_calibration(db)
        log.info("elo loaded from snapshot", players=len(m.ratings),
                 through=str(m.trained_through))
        return m

    def predict(self, player_a: int, player_b: int, surface: str | None,
                tier: str | None, match_state: MatchState,
                as_of: date | None = None) -> Prediction:
        ra = self.ratings.get(player_a, _Rating())
        rb = self.ratings.get(player_b, _Rating())
        raw = _expected(ra.blended(surface), rb.blended(surface))
        # PLATT_A was fitted on pre-match predictions: sharpen first, then condition
        raw = min(max(raw, 1e-9), 1 - 1e-9)
        z = PLATT_A * math.log(raw / (1 - raw))
        p_prematch = 1 / (1 + math.exp(-z))
        p = condition_on_state(p_prematch, match_state)
        # confidence = RECENT activity (v9), decayed to the prediction date so a
        # high but stale rating (a returnee) no longer reads as high confidence
        confidence = min(self._recent_as_of(ra, as_of),
                         self._recent_as_of(rb, as_of)) / CONFIDENCE_RECENT_FULL
        return Prediction(p_a=p, confidence=min(1.0, confidence))
