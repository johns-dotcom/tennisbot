"""Human-readable play-script rendering for `python -m bot profile`."""
from __future__ import annotations

from bot.stats.fallback import Stat
from bot.stats.profile import PlayerProfile


def _rec(s: Stat) -> str:
    if s.is_omitted:
        return "— (omitted: insufficient sample)"
    pct = f"{s.value:.0%}" if s.value is not None else "—"
    rec = f"{s.wins}-{s.losses}" if s.wins is not None else f"n={s.n}"
    tag = "" if s.method == "direct" else f" [{s.method}:{s.window}]"
    return f"{rec} ({pct}){tag}"


def render_profile(p: PlayerProfile) -> str:
    f, d, t = p.form, p.deciding, p.trajectory
    lines = [
        f"PLAY SCRIPT — {p.player_name}  (as of {p.as_of}, {p.matches_in_db} matches in DB)",
        "",
        "FORM",
        f"  last 5:  {_rec(f.last5)}    last 10: {_rec(f.last10)}    last 20: {_rec(f.last20)}",
        f"  win rate: 365d {_rec(f.win_rate_365)} | YTD {_rec(f.win_rate_ytd)} | career {_rec(f.win_rate_career)}",
    ]
    if f.ytd_vs_career_delta is not None:
        lines.append(f"  YTD vs career delta: {f.ytd_vs_career_delta:+.1%}")
    lines.append(f"  active streak: {'W' if f.streak > 0 else 'L'}{abs(f.streak)}"
                 if f.streak else "  active streak: none")
    if f.surface:
        lines.append(f"  on {f.surface}: last 5 {_rec(f.last5_surface)} | last 10 {_rec(f.last10_surface)}")

    lines += [
        "",
        "DECIDING SETS",
        f"  career: {_rec(d.career)}    last 365d: {_rec(d.last365)}    (used: {_rec(d.best)})",
        f"  last {len(d.last_n_results)} deciders: "
        + (" ".join(("W" if r["won"] else "L") + f"({r['date']})" for r in d.last_n_results) or "none"),
        f"  decider streak: {('W' if d.streak > 0 else 'L') + str(abs(d.streak)) if d.streak else 'none'}"
        f"    days since last decider win: {d.days_since_decider_win if d.days_since_decider_win is not None else '—'}",
        f"  skunk share of wins: 365d "
        + (f"{d.skunk_share_of_wins_365.value:.0%} of {d.skunk_share_of_wins_365.n} wins"
           if not d.skunk_share_of_wins_365.is_omitted else "—")
        + " | career "
        + (f"{d.skunk_share_of_wins_career.value:.0%} of {d.skunk_share_of_wins_career.n} wins"
           if not d.skunk_share_of_wins_career.is_omitted else "—"),
        f"  last 60d vs prior: {_rec(d.last60_vs_prior[0])} vs {_rec(d.last60_vs_prior[1])}",
        "",
        "TRAJECTORY",
        f"  last 60d {_rec(t.last60)} vs last 180d {_rec(t.last180)}"
        + (f"  (delta {t.delta:+.1%})" if t.delta is not None else ""),
    ]
    if p.surfaces:
        lines += ["", "SURFACES"]
        for s in p.surfaces:
            lines.append(f"  {s.surface:8s} 365d {_rec(s.last365)} | career {_rec(s.career)}")
    return "\n".join(lines)
