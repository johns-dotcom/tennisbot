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
    win_rate_90: Stat
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

    y90 = _window(ms, as_of, 90)
    y365 = _window(ms, as_of, 365)
    ytd = [m for m in ms if m.match_date >= date(as_of.year, 1, 1)]
    career_w, career_l = _record(ms)
    ytd_w, ytd_l = _record(ytd)
    w90, l90 = _record(y90)
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
        win_rate_90=rate(w90, l90, "last90"),
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
    days_since_decider_played: int | None  # rust — last time they were in a decider at all
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
    days_played = (as_of - deciders[0].match_date).days if deciders else None

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
        days_since_decider_played=days_played,
        skunk_share_of_wins_365=skunk_share(_window(ms, as_of, 365), "last365"),
        skunk_share_of_wins_career=skunk_share(ms, "career"),
        last60_vs_prior=(dec_rate(d60, "last60"), dec_rate(prior, "prior")),
        deciders_played_365=len(d365),
    )


# a break of at least this many days reads as a genuine layoff / return, not
# the normal week-to-week tour gap
LAYOFF_MIN_DAYS = 45
RETURN_WINDOW_MATCHES = 6  # only call it a "return" if the gap is this recent


@dataclass
class LayoffBlock:
    """Rust / return-from-break signal — 'first ITF match in a few months',
    'back from physical therapy, lost 3 straight', plus recent deciding-set
    load for the fatigue read ('his fourth set 3 this month')."""
    days_since_last_match: int | None
    return_layoff_days: int | None            # the break they just came back from
    matches_since_return: int | None
    record_since_return: tuple[int, int] | None  # (w, l) since the return
    deciders_last_30d: int
    deciders_last_3d: int                     # back-to-back deciders → acute fatigue


def compute_layoff(history: list[MatchRow], as_of: date) -> LayoffBlock:
    ms = sorted(_before(history, as_of), key=lambda m: m.match_date, reverse=True)
    if not ms:
        return LayoffBlock(None, None, None, None, 0, 0)
    days_since = (as_of - ms[0].match_date).days
    dec30 = sum(1 for m in ms
                if m.reached_decider and (as_of - m.match_date).days <= 30)
    dec3 = sum(1 for m in ms
               if m.reached_decider and (as_of - m.match_date).days <= 3)
    ret_days = ret_n = rec = None
    for i in range(min(RETURN_WINDOW_MATCHES, len(ms) - 1)):
        gap = (ms[i].match_date - ms[i + 1].match_date).days
        if gap >= LAYOFF_MIN_DAYS:
            recent = ms[:i + 1]
            w = sum(1 for m in recent if m.won)
            ret_days, ret_n, rec = gap, len(recent), (w, len(recent) - w)
            break
    return LayoffBlock(days_since, ret_days, ret_n, rec, dec30, dec3)


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


def compute_set_rates_both(history: list[MatchRow],
                           as_of: date) -> dict[int, tuple[Stat, Stat, Stat]]:
    """Three windows per set: {set_no: (last-90d, past-year, career) Stats}. Feeds
    the side-by-side display; unlike compute_set_rates it never collapses to one
    window."""
    ms = _before(history, as_of)
    cut365 = as_of - timedelta(days=365)
    cut90 = as_of - timedelta(days=90)
    career: dict[int, list[int]] = {}
    recent: dict[int, list[int]] = {}
    recent90: dict[int, list[int]] = {}
    for m in ms:
        for set_no, won in m.set_results:
            career.setdefault(set_no, [0, 0])[0 if won else 1] += 1
            if m.match_date >= cut365:
                recent.setdefault(set_no, [0, 0])[0 if won else 1] += 1
            if m.match_date >= cut90:
                recent90.setdefault(set_no, [0, 0])[0 if won else 1] += 1
    return {n: (rate(*recent90.get(n, [0, 0]), window=f"set{n}_last90"),
                rate(*recent.get(n, [0, 0]), window=f"set{n}_last365"),
                rate(*career[n], window=f"set{n}_career"))
            for n in sorted(career)}


@dataclass
class ServeReturnBlock:
    """Aggregated serve/return rates from matches carrying Sackmann stats.
    None-valued fields mean insufficient stat coverage (never fabricated)."""
    n_matches: int
    ace_pct: float | None            # aces / service points
    df_pct: float | None             # double faults / service points
    first_in_pct: float | None       # first serves in / service points
    first_win_pct: float | None      # first-serve points won / first in
    second_win_pct: float | None     # second-serve points won / second serves
    hold_pct: float | None           # 1 - break_points_converted_against
    bp_saved_pct: float | None       # break points saved / faced
    return_pts_win_pct: float | None # points won on opponent's serve
    break_pct: float | None          # opponent service games broken


def compute_serve_return(history: list[MatchRow], as_of: date,
                         days: int = 365, min_matches: int = 8) -> ServeReturnBlock:
    ms = [m for m in _before(history, as_of) if m.serve]
    ms = [m for m in ms if m.match_date >= as_of - timedelta(days=days)] or \
        [m for m in _before(history, as_of) if m.serve]  # widen to career if thin
    if len(ms) < min_matches:
        return ServeReturnBlock(len(ms), *([None] * 9))

    def agg(key, over, src="serve"):
        num = sum((m.serve if src == "serve" else m.opp_serve)[key] for m in ms)
        den = sum((m.serve if src == "serve" else m.opp_serve)[over] for m in ms)
        return (num / den) if den else None

    ace = agg("ace", "svpt")
    df = agg("df", "svpt")
    first_in = agg("firstin", "svpt")
    first_win = (sum(m.serve["firstwon"] for m in ms) /
                 s if (s := sum(m.serve["firstin"] for m in ms)) else None)
    second_srv = sum(m.serve["svpt"] - m.serve["firstin"] for m in ms)
    second_win = (sum(m.serve["secondwon"] for m in ms) / second_srv
                  if second_srv else None)
    bp_faced = sum(m.serve["bpfaced"] for m in ms)
    bp_saved = sum(m.serve["bpsaved"] for m in ms)
    bp_saved_pct = (bp_saved / bp_faced) if bp_faced else None
    svgms = sum(m.serve["svgms"] for m in ms)
    # games held ≈ service games minus games where a break point was converted
    holds = svgms - (bp_faced - bp_saved)  # approx: each faced-and-lost bp = a break
    hold_pct = (holds / svgms) if svgms else None
    # return side (opponent serve)
    opp_svpt = sum(m.opp_serve["svpt"] for m in ms)
    opp_first_won = sum(m.opp_serve["firstwon"] for m in ms)
    opp_second_won = sum(m.opp_serve["secondwon"] for m in ms)
    ret_win = (1 - (opp_first_won + opp_second_won) / opp_svpt) if opp_svpt else None
    opp_bp_faced = sum(m.opp_serve["bpfaced"] for m in ms)
    opp_bp_saved = sum(m.opp_serve["bpsaved"] for m in ms)
    break_pct = ((opp_bp_faced - opp_bp_saved) / sum(m.opp_serve["svgms"] for m in ms)
                 if sum(m.opp_serve["svgms"] for m in ms) else None)
    return ServeReturnBlock(
        len(ms), ace, df, first_in, first_win, second_win, hold_pct,
        bp_saved_pct, ret_win, break_pct)


def serve_conditional_winrate(history: list[MatchRow], as_of: date, *,
                              key: str, side: str, thresh: int,
                              min_n: int = 6) -> Stat:
    """Win rate over past matches (those carrying Sackmann serve stats) where a
    serve count meets a threshold — the historical read behind a LIVE count, e.g.
    "when this player serves 9+ aces, they win X%".

    key   : 'ace' | 'df'
    side  : 'self' (this player's serve stat) | 'opp' (their opponent's)
    thresh: >= thresh matches (or exactly 0 when thresh <= 0, i.e. a clean count)

    Omitted below `min_n` matches — a thin split is not reported (CLAUDE.md)."""
    ms = _before(history, as_of)

    def val(m: MatchRow):
        d = m.serve if side == "self" else m.opp_serve
        return d.get(key) if d else None

    if thresh <= 0:
        pool = [m for m in ms if val(m) == 0]
    else:
        pool = [m for m in ms if (v := val(m)) is not None and v >= thresh]
    w = sum(1 for m in pool if m.won)
    s = rate(w, len(pool) - w, f"{side}_{key}_{'0' if thresh <= 0 else f'ge{thresh}'}")
    return s if s.n >= min_n else Stat.omitted(s.window)


@dataclass
class ClutchBlock:
    tiebreak: Stat            # tiebreak win record
    deciding_set: Stat        # reuse of decider best
    vs_top50: Stat            # record vs opponents ranked ≤ 50
    vs_top20: Stat
    by_level: dict            # tourney_level -> Stat


def compute_clutch(history: list[MatchRow], as_of: date,
                   decider_best: Stat) -> ClutchBlock:
    ms = _before(history, as_of)
    tb_w = tb_l = 0
    for m in ms:
        for _n, won in m.tiebreaks:
            tb_w += won
            tb_l += not won
    top50 = [m for m in ms if m.opp_rank and m.opp_rank <= 50]
    top20 = [m for m in ms if m.opp_rank and m.opp_rank <= 20]
    levels: dict[str, list] = {}
    for m in ms:
        if m.tourney_level:
            levels.setdefault(m.tourney_level, []).append(m)
    return ClutchBlock(
        tiebreak=rate(tb_w, tb_l, "tiebreaks"),
        deciding_set=decider_best,
        vs_top50=rate(*_record(top50), window="vs_top50"),
        vs_top20=rate(*_record(top20), window="vs_top20"),
        by_level={lv: rate(*_record(mm), window=f"level_{lv}")
                  for lv, mm in sorted(levels.items())},
    )


@dataclass
class ScheduleBlock:
    """Strength of schedule — how good were the opponents behind the form.
    Guards against 'won 7 of 10' meaning nothing against a weak field. Ranks
    come from Sackmann data; Kalshi-mined/charted matches lack them."""
    n_ranked: int
    avg_opp_rank: float | None
    vs_top100: Stat
    field: str  # 'elite' | 'strong' | 'mid' | 'weak' | 'unknown'


def compute_schedule(history: list[MatchRow], as_of: date, days: int = 365,
                     min_ranked: int = 8) -> ScheduleBlock:
    recent = [m for m in _before(history, as_of)
              if m.opp_rank and m.match_date >= as_of - timedelta(days=days)]
    ms = recent if len(recent) >= min_ranked else \
        [m for m in _before(history, as_of) if m.opp_rank]
    if not ms:
        return ScheduleBlock(0, None, Stat.omitted("vs_top100"), "unknown")
    avg = sum(m.opp_rank for m in ms) / len(ms)
    top = [m for m in ms if m.opp_rank <= 100]
    field = ("elite" if avg <= 50 else "strong" if avg <= 150
             else "mid" if avg <= 400 else "weak")
    return ScheduleBlock(len(ms), round(avg, 0), rate(*_record(top), "vs_top100"),
                         field)


def schedule_in_window(history: list[MatchRow], as_of: date,
                       days: int | None) -> ScheduleBlock:
    """Strength of schedule STRICTLY within a window (no cross-window fallback),
    so each timeframe reflects its own opponents. days=None → career. Returns an
    'unknown' block when there are no ranked opponents in that window."""
    ms = [m for m in _before(history, as_of) if m.opp_rank]
    if days is not None:
        cutoff = as_of - timedelta(days=days)
        ms = [m for m in ms if m.match_date >= cutoff]
    if not ms:
        return ScheduleBlock(0, None, Stat.omitted("vs_top100"), "unknown")
    avg = sum(m.opp_rank for m in ms) / len(ms)
    top = [m for m in ms if m.opp_rank <= 100]
    field = ("elite" if avg <= 50 else "strong" if avg <= 150
             else "mid" if avg <= 400 else "weak")
    return ScheduleBlock(len(ms), round(avg, 0), rate(*_record(top), "vs_top100"),
                         field)


# --- style archetypes from Match Charting shot data ---

def style_profile(ch) -> dict | None:
    """Characterize a player's game from charting aggregates: aggression
    (winners vs errors), serve dominance, return strength. None if uncharted."""
    if ch is None or not ch.n_matches:
        return None
    tags = []
    r = ch.winner_ufe_ratio
    if r is not None:
        if r >= 1.1 and (ch.winners_per_match or 0) >= 22:
            tags.append("aggressor")
        elif r <= 0.75:
            tags.append("error-prone")
        elif (ch.winners_per_match or 0) < 18 and r >= 0.9:
            tags.append("consistent/grinder")
    if ch.ace_rate is not None and ch.ace_rate >= 0.10:
        tags.append("big serve")
    if ch.first_serve_win is not None and ch.first_serve_win >= 0.75:
        tags.append("dominant 1st serve")
    if ch.return_win is not None and ch.return_win >= 0.42:
        tags.append("strong return")
    elif ch.return_win is not None and ch.return_win <= 0.33:
        tags.append("weak return")
    return {"tags": tags or ["balanced"], "n": ch.n_matches,
            "ace_rate": ch.ace_rate, "first_serve_win": ch.first_serve_win,
            "return_win": ch.return_win, "wr": ch.winner_ufe_ratio,
            "winners": ch.winners_per_match}


def style_matchup(a_ch, b_ch, name_a: str, name_b: str) -> list[str]:
    """Edge notes from two style profiles — the human 'big server vs weak
    returner' read. Empty if either player is uncharted."""
    a, b = style_profile(a_ch), style_profile(b_ch)
    if not a or not b:
        return []
    notes = []
    # serve vs return mismatches, both directions
    for srv, ret, sname, rname in ((a, b, name_a, name_b), (b, a, name_b, name_a)):
        if (srv.get("ace_rate") or 0) >= 0.10 and (ret.get("return_win") or 1) <= 0.35:
            notes.append(f"{sname}'s serve ({srv['ace_rate']:.0%} aces, "
                         f"{(srv.get('first_serve_win') or 0):.0%} 1st-serve won) meets "
                         f"{rname}'s weak return ({ret['return_win']:.0%} return points) "
                         f"— holds should dominate; lean {sname} on serve and tiebreaks.")
    if "aggressor" in a["tags"] and "consistent/grinder" in b["tags"]:
        notes.append(f"{name_a} the aggressor vs {name_b} the grinder — higher "
                     f"variance; {name_a} lives on winners, {name_b} on rallies.")
    if "aggressor" in b["tags"] and "consistent/grinder" in a["tags"]:
        notes.append(f"{name_b} the aggressor vs {name_a} the grinder — higher "
                     f"variance; {name_b} lives on winners, {name_a} on rallies.")
    return notes


@dataclass
class ChartingBlock:
    """Shot-level aggregates from the Match Charting Project. None when the
    player has no charted matches (coverage is ~5000 matches, not universal)."""
    n_matches: int
    winners_per_match: float | None
    unforced_per_match: float | None
    winner_ufe_ratio: float | None      # aggression efficiency
    fh_winner_share: float | None       # forehand share of winners
    bh_winner_share: float | None
    fh_ufe_share: float | None          # which wing leaks errors
    ace_rate: float | None              # aces / serve points
    first_serve_win: float | None
    second_serve_win: float | None
    return_win: float | None


def compute_charting(rows: list[dict]) -> ChartingBlock:
    """rows: charting_stats dicts for one player (each a match 'Total' line)."""
    if not rows:
        return ChartingBlock(0, *([None] * 10))

    def total(k):
        return sum(r[k] for r in rows if r.get(k) is not None)

    n = len(rows)
    w, ufe = total("winners"), total("unforced")
    wfh, wbh = total("winners_fh"), total("winners_bh")
    ufh, ubh = total("unforced_fh"), total("unforced_bh")
    svpt, aces = total("serve_pts"), total("aces")
    first_in, first_won = total("first_in"), total("first_won")
    second_in, second_won = total("second_in"), total("second_won")
    ret, ret_won = total("return_pts"), total("return_pts_won")
    return ChartingBlock(
        n_matches=n,
        winners_per_match=w / n if n else None,
        unforced_per_match=ufe / n if n else None,
        winner_ufe_ratio=(w / ufe) if ufe else None,
        fh_winner_share=(wfh / w) if w else None,
        bh_winner_share=(wbh / w) if w else None,
        fh_ufe_share=(ufh / ufe) if ufe else None,
        ace_rate=(aces / svpt) if svpt else None,
        first_serve_win=(first_won / first_in) if first_in else None,
        second_serve_win=(second_won / second_in) if second_in else None,
        return_win=(ret_won / ret) if ret else None,
    )


@dataclass
class ConditionalBlock:
    """Gameflow conditionals — win probability given how set 1 goes, plus the
    set-2→set-3 recovery. 'if he takes set 1 it's over' / 'don't panic if he
    drops set 1' / 'loses set 2 but still takes set 3'."""
    win_given_set1_won: Stat        # P(win match | won set 1)
    win_given_set1_lost: Stat       # P(win match | lost set 1)
    decider_given_set1_lost: Stat   # P(reached a deciding set | lost set 1)
    set3_given_lost_set2: Stat      # P(won set 3 | lost set 2 and reached set 3)
    win_given_won_a_set: Stat       # P(win match | won at least one set) — "gets a foothold, closes"


def compute_conditional(history: list[MatchRow], as_of: date,
                        min_sample: int = 8) -> ConditionalBlock:
    ms = _before(history, as_of)
    won1 = [m for m in ms if any(n == 1 and w for n, w in m.set_results)]
    lost1 = [m for m in ms if any(n == 1 and not w for n, w in m.set_results)]
    cutoff = as_of - timedelta(days=365)

    def winrate(pool, window):
        w = sum(1 for m in pool if m.won)
        return rate(w, len(pool) - w, window)

    def decider_rate(pool, window):
        r = sum(1 for m in pool if m.reached_decider)
        return rate(r, len(pool) - r, window)

    def set3_rate(pool, window):
        played3 = [m for m in pool if any(n == 3 for n, _ in m.set_results)]
        w = sum(1 for m in played3 if any(n == 3 and won for n, won in m.set_results))
        return rate(w, len(played3) - w, window)

    def best(pool, fn):
        recent = [m for m in pool if m.match_date >= cutoff]
        return pick(min_sample, fn(recent, "last365"), fn(pool, "career"))

    lost2 = [m for m in ms if any(n == 2 and not w for n, w in m.set_results)]
    won_a_set = [m for m in ms if any(w for _, w in m.set_results)]
    return ConditionalBlock(
        win_given_set1_won=best(won1, winrate),
        win_given_set1_lost=best(lost1, winrate),
        decider_given_set1_lost=best(lost1, decider_rate),
        set3_given_lost_set2=best(lost2, set3_rate),
        win_given_won_a_set=best(won_a_set, winrate),
    )


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
    serve_return: ServeReturnBlock | None = None
    clutch: ClutchBlock | None = None
    set_rates: dict = field(default_factory=dict)
    conditional: ConditionalBlock | None = None
    schedule: ScheduleBlock | None = None
    schedule_windows: dict = field(default_factory=dict)  # 'last90'|'last365'|'career' -> ScheduleBlock
    age: float | None = None
    layoff: LayoffBlock | None = None
    recent_load: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# DB loading
# ---------------------------------------------------------------------------


def load_history(db: Session, player_id: int) -> list[MatchRow]:
    """All played matches for a player, as MatchRow from their perspective."""
    from bot.models import Match, MatchSet

    q = (
        select(Match.id, Match.match_date, Match.winner_id, Match.loser_id, Match.surface,
               Match.best_of, Match.outcome, Match.sets_won_winner, Match.sets_won_loser,
               Match.tourney_level, Match.round, Match.stats, Match.minutes)
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
    tbs_by_match: dict[int, list[tuple[int, bool]]] = {}
    for chunk_start in range(0, len(match_ids), 10000):
        chunk = match_ids[chunk_start:chunk_start + 10000]
        for mid, set_no, won_by_winner, tb in db.execute(
            select(MatchSet.match_id, MatchSet.set_number,
                   MatchSet.set_won_by_match_winner, MatchSet.tiebreak)
            .where(MatchSet.match_id.in_(chunk), MatchSet.completed.is_(True))
        ):
            sets_by_match.setdefault(mid, []).append((set_no, won_by_winner))
            if tb:
                tbs_by_match.setdefault(mid, []).append((set_no, won_by_winner))
            if set_no == best_of_by_id[mid]:
                deciders[mid] = won_by_winner

    SERVE_KEYS = (("ace", "ace"), ("df", "df"), ("svpt", "svpt"),
                  ("1stIn", "firstin"), ("1stWon", "firstwon"),
                  ("2ndWon", "secondwon"), ("SvGms", "svgms"),
                  ("bpSaved", "bpsaved"), ("bpFaced", "bpfaced"))

    def side_stats(stats: dict | None, prefix: str) -> dict | None:
        if not stats:
            return None
        out = {}
        for src, dst in SERVE_KEYS:
            v = stats.get(f"{prefix}_{src}")
            if v is None:
                return None  # partial rows are worse than absent ones
            out[dst] = v
        return out

    history = []
    for (mid, mdate, wid, lid, surface, best_of, outcome, sww, swl, level, rnd,
         mstats, minutes) in rows:
        won = wid == player_id
        bo = best_of or 3
        dec_won_by_match_winner = deciders.get(mid)
        reached = dec_won_by_match_winner is not None
        me, opp = ("w", "l") if won else ("l", "w")
        rank_key, opp_rank_key = (("winner_rank", "loser_rank") if won
                                  else ("loser_rank", "winner_rank"))
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
            tiebreaks=tuple((n, wbw == won) for n, wbw in
                            sorted(tbs_by_match.get(mid, ()))),
            serve=side_stats(mstats, me),
            opp_serve=side_stats(mstats, opp),
            minutes=minutes,
            opp_rank=(mstats or {}).get(opp_rank_key),
            player_rank=(mstats or {}).get(rank_key),
        ))
    return history


def build_profile(db: Session, player_id: int, as_of: date,
                  surface: str | None = None) -> PlayerProfile:
    from bot.models import Player

    player = db.get(Player, player_id)
    history = load_history(db, player_id)
    surfaces_present = sorted({m.surface for m in history if m.surface})
    deciding = compute_deciding_sets(history, as_of)
    # quantified fatigue: match/set/minute load over the last 7 days
    _r7 = [m for m in history if m.match_date and as_of - timedelta(days=7) <= m.match_date < as_of]
    recent_load = {"m": len(_r7),
                   "sets": sum(len(m.set_results) for m in _r7),
                   "min": sum(m.minutes or 0 for m in _r7)}
    return PlayerProfile(
        player_id=player_id, player_name=player.full_name, as_of=as_of,
        form=compute_form(history, as_of, surface),
        deciding=deciding,
        trajectory=compute_trajectory(history, as_of),
        surfaces=[compute_surface(history, as_of, s) for s in surfaces_present],
        matches_in_db=len([m for m in history if m.match_date < as_of]),
        serve_return=compute_serve_return(history, as_of),
        clutch=compute_clutch(history, as_of, deciding.best),
        set_rates=compute_set_rates(history, as_of),
        conditional=compute_conditional(history, as_of),
        schedule=compute_schedule(history, as_of),
        schedule_windows={
            "last90": schedule_in_window(history, as_of, 90),
            "last365": schedule_in_window(history, as_of, 365),
            "career": schedule_in_window(history, as_of, None),
        },
        age=round((as_of - player.dob).days / 365.25, 1) if player.dob else None,
        layoff=compute_layoff(history, as_of),
        recent_load=recent_load,
    )
