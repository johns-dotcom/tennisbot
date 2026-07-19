"""Parse tennis score strings ("6-4 3-6 7-5") into set-level rows.

Handles retirements (RET), walkovers (W/O), defaults (DEF), abandonments (ABN/ABD),
tiebreak notation "7-6(4)", match tiebreaks "[10-7]", and unparseable garbage
(returns outcome='unknown' rather than guessing).

Convention: in every set tuple the first number is games won by the MATCH winner,
second by the match loser (Sackmann convention: score is written winner-first).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

SET_RE = re.compile(r"^(\d{1,2})-(\d{1,2})(?:\((\d{1,3})\))?$")
MATCH_TB_RE = re.compile(r"^\[(\d{1,3})-(\d{1,3})\]$")

RET_TOKENS = {"RET", "RET.", "RETIRED", "ABANDONED"}
WO_TOKENS = {"W/O", "W.O.", "W.O", "WO", "WALKOVER"}
DEF_TOKENS = {"DEF", "DEF.", "DEFAULT", "DISQ"}
ABN_TOKENS = {"ABN", "ABD", "ABN.", "ABD.", "UNFINISHED", "UNF"}


@dataclass
class ParsedSet:
    set_number: int
    winner_games: int
    loser_games: int
    set_won_by_match_winner: bool
    tiebreak: bool = False
    tiebreak_loser_points: int | None = None
    is_match_tiebreak: bool = False
    completed: bool = True


@dataclass
class ParsedScore:
    outcome: str  # 'completed' | 'ret' | 'wo' | 'def' | 'abandoned' | 'unknown'
    sets: list[ParsedSet] = field(default_factory=list)
    sets_won_winner: int = 0
    sets_won_loser: int = 0


def parse_score(score: str | None, best_of: int | None = None) -> ParsedScore:
    if score is None or not score.strip():
        return ParsedScore(outcome="unknown")

    tokens = score.replace(",", " ").split()
    outcome = "completed"
    sets: list[ParsedSet] = []
    saw_garbage = False

    for tok in tokens:
        up = tok.upper().strip()
        if up in WO_TOKENS:
            outcome = "wo"
            continue
        if up in RET_TOKENS:
            outcome = "ret"
            continue
        if up in DEF_TOKENS:
            outcome = "def"
            continue
        if up in ABN_TOKENS:
            outcome = "abandoned"
            continue

        m = SET_RE.match(tok)
        if m:
            wg, lg, tb = int(m.group(1)), int(m.group(2)), m.group(3)
            if wg > 40 or lg > 40:  # not a plausible games count
                saw_garbage = True
                continue
            sets.append(
                ParsedSet(
                    set_number=len(sets) + 1,
                    winner_games=wg,
                    loser_games=lg,
                    set_won_by_match_winner=wg > lg,
                    tiebreak=tb is not None,
                    tiebreak_loser_points=int(tb) if tb is not None else None,
                )
            )
            continue

        m = MATCH_TB_RE.match(tok)
        if m:
            wg, lg = int(m.group(1)), int(m.group(2))
            sets.append(
                ParsedSet(
                    set_number=len(sets) + 1,
                    winner_games=wg,
                    loser_games=lg,
                    set_won_by_match_winner=wg > lg,
                    tiebreak=True,
                    tiebreak_loser_points=min(wg, lg),
                    is_match_tiebreak=True,
                )
            )
            continue

        saw_garbage = True  # unrecognized token (e.g. Excel-corrupted "Jun-04")

    if saw_garbage and not sets:
        return ParsedScore(outcome="unknown")

    # Walkovers/defaults with no played sets: valid, zero set rows.
    if not sets and outcome in ("wo", "def", "abandoned", "ret"):
        return ParsedScore(outcome=outcome)
    if not sets:
        return ParsedScore(outcome="unknown")

    # A retirement/abandonment mid-set leaves the final set incomplete. Heuristic:
    # last set is incomplete if neither player reached a winning games total
    # (>=6 with margin >=2, or a 7-x / tiebreak finish, or a match tiebreak).
    if outcome in ("ret", "abandoned", "def"):
        last = sets[-1]
        hi, lo = max(last.winner_games, last.loser_games), min(last.winner_games, last.loser_games)
        finished = last.is_match_tiebreak or last.tiebreak or (hi >= 6 and hi - lo >= 2) or hi == 7
        if not finished:
            last.completed = False

    won_w = sum(1 for s in sets if s.set_won_by_match_winner and s.completed)
    won_l = sum(1 for s in sets if not s.set_won_by_match_winner and s.completed)
    return ParsedScore(outcome=outcome, sets=sets, sets_won_winner=won_w, sets_won_loser=won_l)
