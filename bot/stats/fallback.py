"""Thin-data fallback hierarchy (CLAUDE.md rule 4).

Every derived stat is a Stat carrying its sample size, the window that was
actually used, and how it was obtained:
    full stat at minimum sample size
    → widen window (365d → career)
    → common-opponent proxy (matchup level, applied in profile.py)
    → omit (Stat.omitted() — callers must handle, never fabricate)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stat:
    value: float | None
    n: int  # sample size behind the value
    window: str  # e.g. 'last365', 'career', 'last60', 'common_opponents'
    method: str  # 'direct' | 'widened' | 'proxy' | 'omitted'
    wins: int | None = None  # for record-type stats
    losses: int | None = None

    @property
    def is_omitted(self) -> bool:
        return self.method == "omitted"

    @staticmethod
    def omitted(window: str = "") -> "Stat":
        return Stat(value=None, n=0, window=window, method="omitted")


def rate(wins: int, losses: int, window: str, method: str = "direct") -> Stat:
    n = wins + losses
    return Stat(value=(wins / n) if n else None, n=n, window=window,
                method=method if n else "omitted", wins=wins, losses=losses)


def pick(minimum: int, *candidates: Stat) -> Stat:
    """First candidate meeting the minimum sample size; else omit.

    Candidates are ordered narrow→wide; a widened window is marked as such.
    """
    for i, c in enumerate(candidates):
        if c.n >= minimum and c.value is not None:
            if i == 0:
                return c
            return Stat(value=c.value, n=c.n, window=c.window, method="widened",
                        wins=c.wins, losses=c.losses)
    return Stat.omitted(candidates[0].window if candidates else "")
