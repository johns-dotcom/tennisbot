"""A named confidence scale for the model's data-depth confidence.

The model's `confidence` (0..1) measures how much history it has on the two
players — min(sets_seen)/CONFIDENCE_FULL_SETS, capped at 1. That is NOT the
probability of being right; it is how well-grounded the estimate is. Shown as a
bare "100%" it reads like certainty, so this maps it onto a labelled 5-band
scale with a plain-English meaning and a status tier for colouring.

Bands (by confidence value):
    < 0.25  Minimal   — barely any shared history; treat as a guess
    < 0.45  Low       — thin data; wide error bars
    < 0.65  Fair      — enough to lean on, not to trust blindly
    < 0.85  Good      — solid history on both players
    >=0.85  Strong    — deep history; the estimate is well-grounded
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfidenceBand:
    label: str
    tier: str   # maps to UI colour: critical | warn | neutral | good
    note: str
    lo: float   # inclusive lower bound of the band


# ordered high → low so the first match wins
CONFIDENCE_BANDS: list[ConfidenceBand] = [
    ConfidenceBand("Strong", "good", "deep history on both players — well-grounded", 0.85),
    ConfidenceBand("Good", "good", "solid history on both players", 0.65),
    ConfidenceBand("Fair", "neutral", "enough to lean on, not to trust blindly", 0.45),
    ConfidenceBand("Low", "warn", "thin data — wide error bars", 0.25),
    ConfidenceBand("Minimal", "critical", "barely any shared history — treat as a guess", 0.0),
]


def confidence_band(conf: float | None) -> ConfidenceBand:
    """Map a 0..1 confidence onto its named band (Minimal→Strong)."""
    c = conf if conf is not None else 0.0
    for band in CONFIDENCE_BANDS:
        if c >= band.lo:
            return band
    return CONFIDENCE_BANDS[-1]


def confidence_label(conf: float | None) -> str:
    """'Good (78%)' — the band name with the raw value in parentheses."""
    b = confidence_band(conf)
    return f"{b.label} ({(conf or 0.0):.0%})"
