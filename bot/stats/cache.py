"""player_stats_cache maintenance — refreshed at ingest time for active players."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from bot.log import get_logger
from bot.models import Match, Player, PlayerStatsCache
from bot.stats.profile import build_profile

log = get_logger("stats.cache")


def refresh_stats_cache(db: Session, as_of: date | None = None,
                        active_days: int = 30) -> int:
    """Recompute and upsert profiles for players active in the last N days."""
    as_of = as_of or (date.today() + timedelta(days=1))
    cutoff = as_of - timedelta(days=active_days)
    active = db.execute(
        select(Player.id).join(
            Match, (Match.winner_id == Player.id) | (Match.loser_id == Player.id))
        .where(Match.match_date >= cutoff, Match.is_duplicate.is_(False))
        .group_by(Player.id).having(func.count(Match.id) > 0)
    ).scalars().all()
    n = 0
    for pid in active:
        profile = build_profile(db, pid, as_of)
        payload = {
            "form": asdict(profile.form), "deciding": asdict(profile.deciding),
            "trajectory": asdict(profile.trajectory),
            "surfaces": [asdict(s) for s in profile.surfaces],
            "matches_in_db": profile.matches_in_db,
            # set rates + conditionals feed the percentile-within-field signal
            # ("86% set-2 win rate — top 1% of the field")
            "set_rates": {n: asdict(s) for n, s in (profile.set_rates or {}).items()},
            "conditional": asdict(profile.conditional) if profile.conditional else None,
            # serve baselines (ace/DF rate per service point) — the norm the live
            # board's statistical-significance ace/fault flags compare against
            "serve_return": (asdict(profile.serve_return)
                             if profile.serve_return else None),
        }
        db.execute(pg_insert(PlayerStatsCache).values(
            player_id=pid, as_of=as_of, payload=payload,
            computed_at=datetime.now(timezone.utc),
        ).on_conflict_do_update(constraint="uq_stats_cache", set_={
            "payload": payload, "computed_at": datetime.now(timezone.utc)}))
        n += 1
        if n % 200 == 0:
            db.commit()
            log.info("stats cache progress", refreshed=n, total=len(active))
    db.commit()
    log.info("stats cache refreshed", players=n, as_of=str(as_of))
    return n
