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


def check_mapping(sets_a: int, sets_b: int, result: str | None) -> str:
    """Does our recorded scoreline agree with how the market settled?
    'ok' | 'mismatch' (YES↔competitor flip) | 'unverifiable'."""
    if result not in ("yes", "no") or sets_a == sets_b:
        return "unverifiable"
    our_winner = "yes" if sets_a > sets_b else "no"
    return "ok" if our_winner == result else "mismatch"


def mapping_audit(db: Session) -> dict:
    """Cross-check the YES-side↔competitor mapping against settlements. For each
    settled market with a decided recorded scoreline, the side our scoreline
    says won must equal the side that settled YES. Mismatches mean a flipped
    mapping silently corrupting scorelines / estimator state / bet settlement."""
    from sqlalchemy import text as sqltext

    rows = db.execute(sqltext("""
        SELECT DISTINCT ON (s.market_ticker)
               s.market_ticker, s.sets_a, s.sets_b, k.result, s.scoreline
        FROM match_score_log s
        JOIN kalshi_markets k ON k.ticker = s.market_ticker
        WHERE k.result IN ('yes', 'no')
        ORDER BY s.market_ticker, s.ts DESC""")).all()
    ok = unverifiable = 0
    mismatches = []
    for tk, sa, sb, res, scoreline in rows:
        verdict = check_mapping(sa, sb, res)
        if verdict == "ok":
            ok += 1
        elif verdict == "unverifiable":
            unverifiable += 1
        else:
            mismatches.append({"ticker": tk, "scoreline": scoreline,
                               "sets": f"{sa}-{sb}", "settled": res})
    return {"checked": ok + len(mismatches), "ok": ok,
            "mismatches": mismatches, "unverifiable": unverifiable}


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
