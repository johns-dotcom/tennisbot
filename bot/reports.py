"""inference-report and graduate — estimator accountability."""
from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from bot.config import settings
from bot.models import FeedGap, StateInferenceLog


def inference_report(db: Session) -> str:
    def agg(rows) -> dict:
        n = len(rows)
        hits = sum(1 for r in rows if r.hit)
        leads = [r.lead_time_seconds for r in rows if r.hit and r.lead_time_seconds is not None]
        return {
            "n": n, "hits": hits,
            "hit_rate": hits / n if n else None,
            "false_boundary_rate": (n - hits) / n if n else None,
            "avg_lead_s": sum(leads) / len(leads) if leads else None,
        }

    rows = db.execute(select(StateInferenceLog)).scalars().all()
    clean = [r for r in rows if not r.session_had_gap]
    gappy = [r for r in rows if r.session_had_gap]

    def fmt(label: str, d: dict) -> str:
        if not d["n"]:
            return f"  {label}: no reconciled inferences"
        return (f"  {label}: n={d['n']}  hit rate={d['hit_rate']:.1%}  "
                f"false-boundary rate={d['false_boundary_rate']:.1%}  "
                f"avg lead={d['avg_lead_s']:.0f}s" if d["avg_lead_s"] is not None else
                f"  {label}: n={d['n']}  hit rate={d['hit_rate']:.1%}  "
                f"false-boundary rate={d['false_boundary_rate']:.1%}  avg lead=—")

    gap_by_day = db.execute(
        select(func.date_trunc("day", FeedGap.gap_start).label("day"),
               func.count(FeedGap.id),
               func.coalesce(func.sum(FeedGap.duration_seconds), 0.0))
        .group_by("day").order_by("day")
    ).all()

    lines = [
        "INFERENCE REPORT",
        fmt("all sessions  ", agg(rows)),
        fmt("clean sessions", agg(clean)),
        fmt("gap sessions  ", agg(gappy)),
        "",
        "FEED GAPS (per day)",
    ]
    if gap_by_day:
        for day, n, total_s in gap_by_day:
            lines.append(f"  {day:%Y-%m-%d}: {n} gaps, {total_s:.0f}s total")
    else:
        lines.append("  none recorded")
    return "\n".join(lines)


def graduate_report(db: Session) -> tuple[str, bool]:
    """Check probation graduation thresholds. NEVER flips the flag itself —
    that is a deliberate manual config change (CLAUDE.md rule 5)."""
    cfg = settings()
    rows = db.execute(
        select(StateInferenceLog).where(StateInferenceLog.session_had_gap.is_(False))
    ).scalars().all()
    n = len(rows)
    hits = sum(1 for r in rows if r.hit)
    hit_rate = hits / n if n else 0.0
    false_rate = (n - hits) / n if n else 1.0

    checks = [
        ("confirmed transitions", n, f">= {cfg.graduate_min_confirmed_transitions}",
         n >= cfg.graduate_min_confirmed_transitions),
        ("hit rate (clean sessions)", f"{hit_rate:.1%}",
         f">= {cfg.graduate_min_hit_rate:.0%}", hit_rate >= cfg.graduate_min_hit_rate),
        ("false-boundary rate", f"{false_rate:.1%}",
         f"<= {cfg.graduate_max_false_boundary_rate:.0%}",
         false_rate <= cfg.graduate_max_false_boundary_rate),
    ]
    all_pass = all(ok for *_, ok in checks)
    lines = ["GRADUATION CHECK (probation → live)"]
    for name, val, req, ok in checks:
        lines.append(f"  [{'PASS' if ok else 'FAIL'}] {name}: {val} (required {req})")
    lines.append("")
    lines.append("RESULT: " + ("all thresholds met — you may now manually set "
                               "probation: false in config" if all_pass else
                               "NOT met — probation stays on"))
    return "\n".join(lines), all_pass
