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


def condition_on_state(p_prematch: float, state: MatchState,
                       platt_a: float = 1.0, platt_b: float = 0.0) -> float:
    """P(A wins | current sets score), from A's pre-match win probability."""
    need = state.best_of // 2 + 1
    s = set_prob_from_match_prob(p_prematch, state.best_of)
    p = race_win_prob(s, need - state.sets_a, need - state.sets_b)
    p = min(max(p, 1e-9), 1 - 1e-9)
    if platt_a == 1.0 and platt_b == 0.0:
        return p
    logit = math.log(p / (1 - p))
    return 1 / (1 + math.exp(-(platt_a * logit + platt_b)))
