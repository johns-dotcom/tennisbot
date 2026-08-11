"""Condition a pre-match win probability on the current set score.

Model: sets are iid with per-set win probability s for player A. s is recovered
from the pre-match match-win probability, then the remaining best-of race is
priced from the current state. A logistic calibration hook (Platt-style a, b)
wraps the output so a fitted external model can slot in later without touching
callers.
"""
from __future__ import annotations

import math

from bot.prob.model import MatchState


def race_win_prob(s: float, sets_needed_a: int, sets_needed_b: int) -> float:
    """P(A takes sets_needed_a sets before B takes sets_needed_b), iid sets."""
    if sets_needed_a <= 0:
        return 1.0
    if sets_needed_b <= 0:
        return 0.0
    total = 0.0
    for j in range(sets_needed_b):
        total += math.comb(sets_needed_a - 1 + j, j) * (s ** sets_needed_a) * ((1 - s) ** j)
    return total


def match_prob_from_set_prob(s: float, best_of: int) -> float:
    need = best_of // 2 + 1
    return race_win_prob(s, need, need)


def set_prob_from_match_prob(p: float, best_of: int) -> float:
    """Invert match_prob_from_set_prob by bisection (it is monotone in s)."""
    p = min(max(p, 1e-6), 1 - 1e-6)
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if match_prob_from_set_prob(mid, best_of) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# State-conditioned recalibration (symmetric logit-scaling A per set-score).
# The pure iid race is MIScalibrated at set boundaries: the set leader wins more
# than iid predicts (equivalently, a player down a set is worse than iid says —
# losing a set carries extra bad news the iid assumption can't see). Fitted
# walk-forward on realized outcomes only (never price — CLAUDE.md rule 2), 12mo
# to 2026-07-29: at the 1-set-lead boundary (n=43,093) the model predicted the
# leader at 78.2% vs 82.3% realized, refit to A=1.141 (out-of-sample log-loss
# lift +0.11%). A>1 sharpens. Symmetric (b=0), so the two players still sum to 1.
# Stored for both orientations of each state so it's applied identically to
# either side. Bo5 states had too little data → left at 1.0 (no change). Refit
# when ratings change (same cadence as PLATT_A).
# Per-set-state logit-scaling. EMPTY by default: the set-boundary miscalibration
# is real (the leader wins ~82% where iid predicts ~78%), but correcting it with a
# symmetric logit-sharpen improves Brier while WORSENING out-of-sample log-loss
# (the proper scoring rule we calibrate PLATT_A on) — it over-sharpens the tails
# and isn't robust. So nothing is applied unless a walk-forward refit clears BOTH
# the Brier and log-loss gates (see bot.prob.calibrate.fit_state_scale). The daily
# ingest refits and persists to model_calibration; load_state_calibration()
# overrides this dict on model rebuild. Empty ⇒ pure iid conditioning.
_DEFAULT_STATE_SCALE: dict[tuple[int, int, int], float] = {}
STATE_LOGIT_SCALE: dict[tuple[int, int, int], float] = dict(_DEFAULT_STATE_SCALE)


def load_state_calibration(db) -> None:
    """Refresh STATE_LOGIT_SCALE from the newest model_calibration row (called
    when the model rebuilds). Keeps the current values on any failure, so a
    missing table or a bad read never breaks prediction. Stored keys are
    canonical 'best_of|hi|lo'; both orientations are expanded here so the scale
    is applied identically to either player's perspective."""
    try:
        from sqlalchemy import select

        from bot.models import ModelCalibration
        row = db.execute(select(ModelCalibration)
                         .order_by(ModelCalibration.fitted_at.desc())).scalars().first()
        if not row or not row.state_scale:
            return
        new: dict[tuple[int, int, int], float] = {}
        for k, v in row.state_scale.items():
            bo, sa, sb = (int(x) for x in k.split("|"))
            new[(bo, sa, sb)] = float(v)
            new[(bo, sb, sa)] = float(v)
        if new:
            STATE_LOGIT_SCALE.clear()
            STATE_LOGIT_SCALE.update(new)
    except Exception:
        pass  # keep whatever is currently loaded (defaults or last-good)


def condition_on_state(p_prematch: float, state: MatchState,
                       platt_a: float = 1.0, platt_b: float = 0.0) -> float:
    """P(A wins | current sets score), from A's pre-match win probability."""
    need = state.best_of // 2 + 1
    s = set_prob_from_match_prob(p_prematch, state.best_of)
    p = race_win_prob(s, need - state.sets_a, need - state.sets_b)
    p = min(max(p, 1e-9), 1 - 1e-9)
    # state-conditioned recalibration (fitted); symmetric, so mirror states agree
    a_state = STATE_LOGIT_SCALE.get((state.best_of, state.sets_a, state.sets_b))
    if a_state is not None and a_state != 1.0:
        p = 1 / (1 + math.exp(-a_state * math.log(p / (1 - p))))
        p = min(max(p, 1e-9), 1 - 1e-9)
    if platt_a == 1.0 and platt_b == 0.0:
        return p
    logit = math.log(p / (1 - p))
    return 1 / (1 + math.exp(-(platt_a * logit + platt_b)))
