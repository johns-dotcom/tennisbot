"""Deciding-set signal — a SEPARATE, opt-in flag, NOT the calibrated model.

When a match reaches (or is heading to) a deciding set, this weights the two
players' deciding-set win rates more heavily than the base model does and
produces an alternative "decider read".

It is deliberately ISOLATED from bot.prob.elo and the bet pipeline: our
walk-forward test found deciding-set win rate non-predictive on its own, so this
must never silently drive calibrated bets. It is a clearly-labelled lens for the
UI (and any explicitly decider-only experiment), tunable by DECIDER_SET_WEIGHT.

No market price ever enters (CLAUDE.md rule 2). Pure functions.
"""
from __future__ import annotations

import math

# how hard the set-form (deciding-set win-rate) signal pulls the base model read
# at a decider. 0 = ignore set form (pure model); 1 = set form only.
DECIDER_SET_WEIGHT = 0.35
# minimum deciders behind a win rate for it to count (thin rates are noise)
DECIDER_MIN_N = 6


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sig(z: float) -> float:
    z = min(max(z, -30.0), 30.0)
    return 1 / (1 + math.exp(-z))


def set_form_signal(dec_wr_a: float | None, dec_wr_b: float | None) -> float | None:
    """P(A wins the decider) from the two deciding-set win rates ALONE, via a
    Bradley–Terry ratio of their 'wins-deciders' strengths. None if either is
    unknown (caller then falls back to the base model unchanged)."""
    if dec_wr_a is None or dec_wr_b is None:
        return None
    a = min(max(dec_wr_a, 1e-3), 1 - 1e-3)
    b = min(max(dec_wr_b, 1e-3), 1 - 1e-3)
    oa, ob = a / (1 - a), b / (1 - b)
    return oa / (oa + ob)


def decider_read(base_prob: float, dec_wr_a: float | None, dec_wr_b: float | None,
                 weight: float = DECIDER_SET_WEIGHT) -> tuple[float, float | None]:
    """Blend the base model decider prob with the set-form signal in logit space
    by `weight`. Returns (blended_prob, signal_prob) — signal_prob is None (and
    blended == base) when set-form data is missing, so it degrades to the model."""
    sig = set_form_signal(dec_wr_a, dec_wr_b)
    if sig is None:
        return base_prob, None
    z = (1 - weight) * _logit(base_prob) + weight * _logit(sig)
    return _sig(z), sig
