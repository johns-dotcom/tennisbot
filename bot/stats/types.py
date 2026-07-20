"""Plain data structures for the stats layer.

The compute functions in profile.py operate on these — never on ORM objects —
so the whole stats engine is unit-testable without a database.

Semantics locked here (see CLAUDE.md vocabulary):
- Walkovers are NOT played matches: excluded from every form/decider stat.
- Retirements and defaults ARE played matches: they count as wins/losses.
- Deciding set = set `best_of` (3 in Bo3, 5 in Bo5), only if completed.
- Skunk = straight-sets win (loser won zero completed sets).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class MatchRow:
    """One played match from one player's perspective."""

    match_date: date
    won: bool
    opponent_id: int
    surface: str | None  # 'Hard' | 'Clay' | 'Grass' | 'Carpet' | None
    best_of: int
    outcome: str  # 'completed' | 'ret' | 'def'
    sets_won: int  # completed sets won by this player
    sets_lost: int
    reached_decider: bool
    won_decider: bool | None  # None if no completed decider
    tourney_level: str | None
    round: str | None = None
    # per completed set from this player's perspective: ((set_number, won), ...)
    set_results: tuple = ()
    # tiebreaks played, player perspective: ((set_number, won), ...)
    tiebreaks: tuple = ()
    # serve stats for this player / their opponent (Sackmann matches only):
    # {ace, df, svpt, firstin, firstwon, secondwon, svgms, bpsaved, bpfaced}
    serve: dict | None = None
    opp_serve: dict | None = None
    opp_rank: int | None = None
    player_rank: int | None = None


PLAYED_OUTCOMES = ("completed", "ret", "def")

# Sackmann dates every match in a tournament at the week's Monday, so within a
# date we order by round to sequence streaks correctly.
ROUND_ORDER = {
    "Q1": 0, "Q2": 1, "Q3": 2, "R128": 3, "R64": 4, "R32": 5, "R16": 6,
    "RR": 6, "QF": 7, "SF": 8, "BR": 9, "F": 10,
}


def round_rank(rnd: str | None) -> int:
    return ROUND_ORDER.get((rnd or "").strip(), 5)


def is_skunk_win(m: MatchRow) -> bool:
    return m.won and m.outcome == "completed" and m.sets_lost == 0 and m.sets_won >= 2
