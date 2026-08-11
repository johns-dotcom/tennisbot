"""The bot's own game-by-game scoring record.

Kalshi's milestone live-data carries per-set games and the in-progress set's
game count. We snapshot it every poll and persist a new match_score_log row
whenever the game score changes — giving a complete scoreline history per match
that we own, richer than the set-level estimator and useful as labeled data for
improving it. Recording only.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from bot.log import get_logger
from bot.models import MatchScoreLog

log = get_logger("market.scoreboard")


def parse_scoreboard(details: dict, yes_is_c1: bool) -> dict | None:
    """Full game-level state from a milestone live_data 'details' block,
    mapped so 'a' is the YES-side player of the market. None if unusable."""
    if not details:
        return None
    try:
        c1_sets = int(details.get("competitor1_overall_score"))
        c2_sets = int(details.get("competitor2_overall_score"))
    except (TypeError, ValueError):
        return None
    c1_cur = details.get("competitor1_current_round_score")
    c2_cur = details.get("competitor2_current_round_score")
    c1_rounds = details.get("competitor1_round_scores") or []
    c2_rounds = details.get("competitor2_round_scores") or []
    status = (details.get("status") or "").lower()
    is_final = (status in ("closed", "ended", "complete", "finished")
                or "retire" in status or "walk" in status)

    # completed-set game pairs, competitor-1 perspective
    completed = []
    for a, b in zip(c1_rounds, c2_rounds):
        try:
            g1, g2 = int(a.get("score")), int(b.get("score"))
        except (TypeError, ValueError):
            continue
        tb1, tb2 = a.get("tiebreak_score"), b.get("tiebreak_score")
        completed.append((g1, g2, tb1, tb2))

    def cur(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    cur1, cur2 = cur(c1_cur), cur(c2_cur)
    in_progress = not is_final and (cur1 is not None or cur2 is not None) \
        and (cur1 or 0) + (cur2 or 0) >= 0

    # build scoreline in competitor-1 perspective, then flip if YES is c2
    parts = []
    for g1, g2, tb1, tb2 in completed:
        seg = f"{g1}-{g2}"
        if tb1 is not None or tb2 is not None:
            loser_tb = min(int(tb1 or 0), int(tb2 or 0))
            seg += f"({loser_tb})"
        parts.append(seg)
    set_number = len(completed) + 1
    if in_progress:
        parts.append(f"{cur1 or 0}-{cur2 or 0}")
    else:
        set_number = max(1, len(completed))

    def flip_seg(seg: str) -> str:
        # "6-3(4)" -> "3-6(4)"
        head, _, tb = seg.partition("(")
        g1, g2 = head.split("-")
        return f"{g2}-{g1}" + (f"({tb}" if tb else "")

    if yes_is_c1:
        sets_a, sets_b = c1_sets, c2_sets
        cur_a, cur_b = (cur1 or 0), (cur2 or 0)
        scoreline = " ".join(parts)
    else:
        sets_a, sets_b = c2_sets, c1_sets
        cur_a, cur_b = (cur2 or 0), (cur1 or 0)
        scoreline = " ".join(flip_seg(p) for p in parts)

    games_a = cur_a if in_progress else (
        completed[-1][0 if yes_is_c1 else 1] if completed else 0)
    games_b = cur_b if in_progress else (
        completed[-1][1 if yes_is_c1 else 0] if completed else 0)
    # total games across the whole match = the change detector
    total = sum(g1 + g2 for g1, g2, _, _ in completed) + (cur_a + cur_b if in_progress else 0)
    return {
        "sets_a": sets_a, "sets_b": sets_b, "set_number": set_number,
        "games_a": games_a, "games_b": games_b,
        "scoreline": scoreline[:96] or f"{sets_a}-{sets_b} sets",
        "total_games": total, "is_final": is_final,
    }


def record_snapshot(db: Session, market_ticker: str, event_ticker: str | None,
                    details: dict, yes_is_c1: bool) -> bool:
    """Write a match_score_log row only when the game score advanced.
    Returns True if a new row was written."""
    sb = parse_scoreboard(details, yes_is_c1)
    if sb is None:
        return False
    last = db.execute(
        select(MatchScoreLog).where(MatchScoreLog.market_ticker == market_ticker)
        .order_by(MatchScoreLog.ts.desc()).limit(1)).scalar()
    if last is not None and last.total_games == sb["total_games"] \
            and last.sets_a == sb["sets_a"] and last.sets_b == sb["sets_b"] \
            and last.is_final == sb["is_final"]:
        return False  # nothing changed since last poll
    from datetime import timezone

    # store the live serve/return stats ALREADY ORIENTED to our sides
    # (yes = the market's YES player, opp = the other) so the UI never has to
    # re-derive the competitor↔player mapping — the raw competitor1/2 labels
    # carry no reliable orientation once persisted.
    c1 = details.get("competitor1_statistics")
    c2 = details.get("competitor2_statistics")
    stats = {}
    if c1 is not None and c2 is not None:
        stats["yes_statistics"] = c1 if yes_is_c1 else c2
        stats["opp_statistics"] = c2 if yes_is_c1 else c1
    if details.get("advantage") is not None:
        stats["advantage"] = details["advantage"]
    db.add(MatchScoreLog(
        market_ticker=market_ticker, event_ticker=event_ticker,
        ts=datetime.now(timezone.utc), detail=stats or None, **sb))
    db.commit()
    log.info("game recorded", ticker=market_ticker, score=sb["scoreline"],
             sets=f"{sb['sets_a']}-{sb['sets_b']}", final=sb["is_final"])
    return True
