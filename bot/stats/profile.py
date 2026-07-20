"""PlayerProfile — the "play script" layer.

All functions take `as_of` and consider only matches strictly BEFORE that date
(no lookahead bias in backtests). Walkovers excluded everywhere; see types.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from bot.config import settings
from bot.stats.fallback import Stat, pick, rate
from bot.stats.types import PLAYED_OUTCOMES, MatchRow, is_skunk_win, round_rank

# ---------------------------------------------------------------------------
# pure compute layer (no DB)
# ---------------------------------------------------------------------------


def _before(history: list[MatchRow], as_of: date) -> list[MatchRow]:
    """History filtered to strictly before as_of, newest first.

    Within a date (Sackmann dates a whole event at its Monday), later rounds
    sort as more recent.
    """
    return sorted((m for m in history if m.match_date < as_of),
                  key=lambda m: (m.match_date, round_rank(m.round)), reverse=True)


def _record(ms: list[MatchRow]) -> tuple[int, int]:
    w = sum(1 for m in ms if m.won)
    return w, len(ms) - w


def _window(ms: list[MatchRow], as_of: date, days: int) -> list[MatchRow]:
    cutoff = as_of - timedelta(days=days)
    return [m for m in ms if m.match_date >= cutoff]


@dataclass
class FormBlock:
    last5: Stat
    last10: Stat
    last20: Stat
    last5_surface: Stat
    last10_surface: Stat
    win_rate_365: Stat
    win_rate_ytd: Stat
    win_rate_career: Stat
    ytd_vs_career_delta: float | None
    streak: int  # +N = winning streak, -N = losing streak, 0 = no matches
    surface: str | None


def compute_form(history: list[MatchRow], as_of: date, surface: str | None) -> FormBlock:
    ms = _before(history, as_of)
    on_surface = [m for m in ms if surface and m.surface == surface]

    def last_n(pool: list[MatchRow], n: int, window: str) -> Stat:
        w, l = _record(pool[:n])
        return rate(w, l, window)

    y365 = _window(ms, as_of, 365)
    ytd = [m for m in ms if m.match_date >= date(as_of.year, 1, 1)]
    career_w, career_l = _record(ms)
    ytd_w, ytd_l = _record(ytd)
    w365, l365 = _record(y365)

    career = rate(career_w, career_l, "career")
    ytd_stat = rate(ytd_w, ytd_l, "ytd")
    delta = (ytd_stat.value - career.value) \
        if ytd_stat.value is not None and career.value is not None else None

    streak = 0
    for m in ms:
        if streak == 0:
            streak = 1 if m.won else -1
        elif m.won and streak > 0:
            streak += 1
        elif not m.won and streak < 0:
            streak -= 1
        else:
            break

    return FormBlock(
        last5=last_n(ms, 5, "last5"), last10=last_n(ms, 10, "last10"),
        last20=last_n(ms, 20, "last20"),
        last5_surface=last_n(on_surface, 5, f"last5_{surface}") if surface else Stat.omitted(),
        last10_surface=last_n(on_surface, 10, f"last10_{surface}") if surface else Stat.omitted(),
        win_rate_365=rate(w365, l365, "last365"), win_rate_ytd=ytd_stat,
        win_rate_career=career, ytd_vs_career_delta=delta, streak=streak, surface=surface,
    )


@dataclass
class DecidingSetBlock:
    career: Stat
    last365: Stat
    best: Stat  # fallback-picked: last365 → career
    last_n_results: list[dict]  # newest first: {date, won}
    streak: int  # +N winning streak in deciders
    days_since_decider_win: int | None
    skunk_share_of_wins_365: Stat  # % of wins that were straight-sets
    skunk_share_of_wins_career: Stat
    last60_vs_prior: tuple[Stat, Stat]  # (last 60d, prior career)
    deciders_played_365: int


def compute_deciding_sets(history: list[MatchRow], as_of: date, last_n: int = 7) -> DecidingSetBlock:
    cfg = settings()
    ms = _before(history, as_of)
    deciders = [m for m in ms if m.reached_decider and m.won_decider is not None]

    def dec_rate(pool: list[MatchRow], window: str) -> Stat:
        w = sum(1 for m in pool if m.won_decider)
        return rate(w, len(pool) - w, window)

    d365 = _window(deciders, as_of, 365)
    d60 = _window(deciders, as_of, 60)
    prior = [m for m in deciders if m not in d60]

    streak = 0
    for m in deciders:
        if streak == 0:
            streak = 1 if m.won_decider else -1
        elif m.won_decider and streak > 0:
            streak += 1
        elif not m.won_decider and streak < 0:
            streak -= 1
        else:
            break

    last_win = next((m for m in deciders if m.won_decider), None)
    days_since = (as_of - last_win.match_date).days if last_win else None

    def skunk_share(pool: list[MatchRow], window: str) -> Stat:
        wins = [m for m in pool if m.won]
        sk = sum(1 for m in wins if is_skunk_win(m))
        # value = share of wins that were skunks; n = number of wins sampled
        return Stat(value=(sk / len(wins)) if wins else None, n=len(wins),
                    window=window, method="direct" if wins else "omitted")

    return DecidingSetBlock(
        career=dec_rate(deciders, "career"),
        last365=dec_rate(d365, "last365"),
        best=pick(cfg.min_sample_decider, dec_rate(d365, "last365"), dec_rate(deciders, "career")),
        last_n_results=[{"date": m.match_date.isoformat(), "won": bool(m.won_decider)}
                        for m in deciders[:last_n]],
        streak=streak,
        days_since_decider_win=days_since,
        skunk_share_of_wins_365=skunk_share(_window(ms, as_of, 365), "last365"),
        skunk_share_of_wins_career=skunk_share(ms, "career"),
        last60_vs_prior=(dec_rate(d60, "last60"), dec_rate(prior, "prior")),
        deciders_played_365=len(d365),
    )


def compute_set_rates(history: list[MatchRow], as_of: date,
                      min_sample: int = 8) -> dict[int, Stat]:
    """Win rate in set N specifically (set 1, set 2, set 3…), the backbone of
    gameflow analysis: 'set 1 is her strongest set at 68%'.

    last-365d window first, widened to career below min_sample, else omitted.
    """
    ms = _before(history, as_of)
    cutoff = as_of - timedelta(days=365)
    career: dict[int, list[int]] = {}
    recent: dict[int, list[int]] = {}
    for m in ms:
        for set_no, won in m.set_results:
            career.setdefault(set_no, [0, 0])[0 if won else 1] += 1
            if m.match_date >= cutoff:
                recent.setdefault(set_no, [0, 0])[0 if won else 1] += 1
    out: dict[int, Stat] = {}
    for n in sorted(career):
        r365 = rate(*recent.get(n, [0, 0]), window=f"set{n}_last365")
        rcar = rate(*career[n], window=f"set{n}_career")
        out[n] = pick(min_sample, r365, rcar)
    return out


@dataclass
class TrajectoryBlock:
    last60: Stat
    last180: Stat
    delta: float | None  # last60 − last180


def compute_trajectory(history: list[MatchRow], as_of: date) -> TrajectoryBlock:
    ms = _before(history, as_of)
    w60, l60 = _record(_window(ms, as_of, 60))
    w180, l180 = _record(_window(ms, as_of, 180))
    s60, s180 = rate(w60, l60, "last60"), rate(w180, l180, "last180")
    delta = (s60.value - s180.value) \
        if s60.value is not None and s180.value is not None else None
    return TrajectoryBlock(last60=s60, last180=s180, delta=delta)


@dataclass
class SurfaceBlock:
    surface: str
    last365: Stat
    career: Stat
    best: Stat


def compute_surface(history: list[MatchRow], as_of: date, surface: str) -> SurfaceBlock:
    cfg = settings()
    ms = [m for m in _before(history, as_of) if m.surface == surface]
    w365, l365 = _record(_window(ms, as_of, 365))
    wc, lc = _record(ms)
    s365, sc = rate(w365, l365, f"last365_{surface}"), rate(wc, lc, f"career_{surface}")
    return SurfaceBlock(surface=surface, last365=s365, career=sc,
                        best=pick(cfg.min_sample_surface, s365, sc))


@dataclass
class MatchupBlock:
    h2h: Stat  # from player A's perspective
    h2h_surface: Stat
    h2h_sets: tuple[int, int]  # completed sets won (a, b) across meetings
    common_opponents: Stat  # A's record vs opponents B also played
    common_opponents_b: Stat  # B's record vs the same shared opponents
    common_opponent_count: int
    decider_rate_a: Stat
    decider_rate_b: Stat
    decider_differential: float | None  # a − b, None if either omitted


def compute_matchup(history_a: list[MatchRow], history_b: list[MatchRow],
                    player_b_id: int, player_a_id: int, as_of: date,
                    surface: str | None) -> MatchupBlock:
    cfg = settings()
    ms_a = _before(history_a, as_of)
    ms_b = _before(history_b, as_of)

    h2h_matches = [m for m in ms_a if m.opponent_id == player_b_id]
    hw, hl = _record(h2h_matches)
    h2h = rate(hw, hl, "h2h")
    hs = [m for m in h2h_matches if surface and m.surface == surface]
    h2h_surface = rate(*_record(hs), window=f"h2h_{surface}") if surface else Stat.omitted()
    sets_a = sum(m.sets_won for m in h2h_matches)
    sets_b = sum(m.sets_lost for m in h2h_matches)

    opps_a = {m.opponent_id for m in ms_a if m.opponent_id != player_b_id}
    opps_b = {m.opponent_id for m in ms_b if m.opponent_id != player_a_id}
    common = opps_a & opps_b
    ca = [m for m in ms_a if m.opponent_id in common]
    cb = [m for m in ms_b if m.opponent_id in common]
    common_a = rate(*_record(ca), window="common_opponents", method="proxy")
    common_b = rate(*_record(cb), window="common_opponents", method="proxy")

    dec_a = compute_deciding_sets(history_a, as_of).best
    dec_b = compute_deciding_sets(history_b, as_of).best
    diff = (dec_a.value - dec_b.value) \
        if dec_a.value is not None and dec_b.value is not None else None

    return MatchupBlock(
        h2h=h2h, h2h_surface=h2h_surface, h2h_sets=(sets_a, sets_b),
        common_opponents=common_a if len(common) >= cfg.min_sample_common_opponents
        else Stat.omitted("common_opponents"),
        common_opponents_b=common_b if len(common) >= cfg.min_sample_common_opponents
        else Stat.omitted("common_opponents"),
        common_opponent_count=len(common),
        decider_rate_a=dec_a, decider_rate_b=dec_b, decider_differential=diff,
    )


@dataclass
class PlayerProfile:
    player_id: int
    player_name: str
    as_of: date
    form: FormBlock
    deciding: DecidingSetBlock
    trajectory: TrajectoryBlock
    surfaces: list[SurfaceBlock] = field(default_factory=list)
    matches_in_db: int = 0


# ---------------------------------------------------------------------------
# DB loading
# ---------------------------------------------------------------------------


def load_history(db: Session, player_id: int) -> list[MatchRow]:
    """All played matches for a player, as MatchRow from their perspective."""
    from bot.models import Match, MatchSet

    q = (
        select(Match.id, Match.match_date, Match.winner_id, Match.loser_id, Match.surface,
               Match.best_of, Match.outcome, Match.sets_won_winner, Match.sets_won_loser,
               Match.tourney_level, Match.round)
        .where(
            ((Match.winner_id == player_id) | (Match.loser_id == player_id)),
            Match.outcome.in_(PLAYED_OUTCOMES),
            Match.is_duplicate.is_(False),
            Match.match_date.is_not(None),
        )
    )
    rows = db.execute(q).all()
    if not rows:
        return []

    # all completed sets in one query: decider outcome + per-set-number results
    match_ids = [r[0] for r in rows]
    best_of_by_id = {r[0]: (r[5] or 3) for r in rows}
    deciders: dict[int, bool] = {}
    sets_by_match: dict[int, list[tuple[int, bool]]] = {}
    for chunk_start in range(0, len(match_ids), 10000):
        chunk = match_ids[chunk_start:chunk_start + 10000]
        for mid, set_no, won_by_winner in db.execute(
            select(MatchSet.match_id, MatchSet.set_number, MatchSet.set_won_by_match_winner)
            .where(MatchSet.match_id.in_(chunk), MatchSet.completed.is_(True))
        ):
            sets_by_match.setdefault(mid, []).append((set_no, won_by_winner))
            if set_no == best_of_by_id[mid]:
                deciders[mid] = won_by_winner

    history = []
    for (mid, mdate, wid, lid, surface, best_of, outcome, sww, swl, level, rnd) in rows:
        won = wid == player_id
        bo = best_of or 3
        dec_won_by_match_winner = deciders.get(mid)
        reached = dec_won_by_match_winner is not None
        history.append(MatchRow(
            match_date=mdate, won=won,
            opponent_id=lid if won else wid,
            surface=surface, best_of=bo, outcome=outcome,
            sets_won=(sww if won else swl) or 0,
            sets_lost=(swl if won else sww) or 0,
            reached_decider=reached,
            won_decider=(dec_won_by_match_winner == won) if reached else None,
            tourney_level=level, round=rnd,
            set_results=tuple((n, wbw == won) for n, wbw in
                              sorted(sets_by_match.get(mid, ()))),
        ))
    return history


def build_profile(db: Session, player_id: int, as_of: date,
                  surface: str | None = None) -> PlayerProfile:
    from bot.models import Player

    player = db.get(Player, player_id)
    history = load_history(db, player_id)
    surfaces_present = sorted({m.surface for m in history if m.surface})
    return PlayerProfile(
        player_id=player_id, player_name=player.full_name, as_of=as_of,
        form=compute_form(history, as_of, surface),
        deciding=compute_deciding_sets(history, as_of),
        trajectory=compute_trajectory(history, as_of),
        surfaces=[compute_surface(history, as_of, s) for s in surfaces_present],
        matches_in_db=len([m for m in history if m.match_date < as_of]),
    )
