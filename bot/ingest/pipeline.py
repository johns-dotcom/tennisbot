"""Ingest orchestration: Sackmann backfill/incremental → api-tennis gap-fill →
cross-source dedup. Stats-cache refresh hooks in at Phase 2."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import and_, select, update
from sqlalchemy.orm import Session, aliased

from bot.log import get_logger
from bot.models import Match
from bot.sources.api_tennis import ApiTennisSource
from bot.sources.base import SyncResult
from bot.sources.sackmann import SackmannDataSource

log = get_logger("ingest")


def dedup_cross_source(db: Session) -> int:
    """Mark api_tennis matches duplicated by a Sackmann match (same pair, ±10 days).

    Sackmann is canonical: richer stats, verified scores.
    """
    sack = aliased(Match)
    apit = aliased(Match)
    dupes = db.execute(
        select(apit.id).where(
            apit.source == "api_tennis",
            apit.is_duplicate.is_(False),
            apit.outcome != "scheduled",
        ).join(sack, and_(
            sack.source == "sackmann",
            sack.tour == apit.tour,
            sack.winner_id.in_([apit.winner_id, apit.loser_id]),
            sack.loser_id.in_([apit.winner_id, apit.loser_id]),
            sack.match_date.between(apit.match_date - timedelta(days=10),
                                    apit.match_date + timedelta(days=10)),
        ))
    ).scalars().all()
    if dupes:
        db.execute(update(Match).where(Match.id.in_(dupes)).values(is_duplicate=True))
    return len(dupes)


def run_ingest(db: Session, *, full: bool = False, skip_live: bool = False,
               refresh_cache: bool = True) -> list[SyncResult]:
    results = [SackmannDataSource().sync(db, full=full)]
    db.commit()
    if not skip_live:
        results.append(ApiTennisSource().sync(db, full=full))
        db.commit()
    n = dedup_cross_source(db)
    db.commit()
    log.info("dedup complete", marked=n)
    if refresh_cache:
        from bot.stats.cache import refresh_stats_cache

        refresh_stats_cache(db)
    try:
        from bot.scenarios import generate_scenarios

        generate_scenarios(db)
    except Exception as e:
        log.error("scenario generation failed", error=str(e))
    for r in results:
        log.info("sync result", source=r.source, matches=r.matches_upserted,
                 players=r.players_upserted, tournaments=r.tournaments_upserted,
                 sets=r.sets_written, skipped_files=r.skipped_files, errors=r.errors)
    return results
