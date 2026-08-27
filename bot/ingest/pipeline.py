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
    """Mark non-canonical rows duplicated by a more-canonical source (same pair,
    ±10 days). Canonicality order: sackmann (richest stats, verified scores) >
    api_tennis (set-level scores) > kalshi (market settlement only).

    Both live sources (api_tennis, kalshi) are deduped against sackmann; then
    kalshi is deduped against api_tennis, so a post-Sackmann-freeze match covered
    by BOTH live feeds isn't double-counted (it has no Sackmann row to anchor)."""
    # (canonical_source, other_source) pairs, applied in order
    passes = [("sackmann", "api_tennis"), ("sackmann", "kalshi"),
              ("api_tennis", "kalshi")]
    n_total = 0
    for canon_source, other_source in passes:
        canon = aliased(Match)
        other = aliased(Match)
        dupes = db.execute(
            select(other.id).where(
                other.source == other_source,
                other.is_duplicate.is_(False),
                other.outcome != "scheduled",
            ).join(canon, and_(
                canon.source == canon_source,
                canon.is_duplicate.is_(False),
                canon.tour == other.tour,
                canon.winner_id.in_([other.winner_id, other.loser_id]),
                canon.loser_id.in_([other.winner_id, other.loser_id]),
                canon.match_date.between(other.match_date - timedelta(days=10),
                                         other.match_date + timedelta(days=10)),
            ))
        ).scalars().all()
        if dupes:
            db.execute(update(Match).where(Match.id.in_(dupes))
                       .values(is_duplicate=True))
        n_total += len(dupes)
    return n_total


def run_ingest(db: Session, *, full: bool = False, skip_live: bool = False,
               refresh_cache: bool = True) -> list[SyncResult]:
    results = [SackmannDataSource().sync(db, full=full)]
    db.commit()
    try:
        from bot.sources.kalshi_results import KalshiResultsSource

        results.append(KalshiResultsSource().sync(db, full=full))
        db.commit()
    except Exception as e:
        log.error("kalshi results sync failed", error=str(e))
    try:
        from bot.sources.charting import ChartingSource

        results.append(ChartingSource().sync(db, full=full))
        db.commit()
    except Exception as e:
        log.error("charting sync failed", error=str(e))
    if not skip_live:
        results.append(ApiTennisSource().sync(db, full=full))
        db.commit()
    n = dedup_cross_source(db)
    db.commit()
    log.info("dedup complete", marked=n)
    # fill surface on any rows the resolver can now place (feeds the surface-
    # adjusted model + surface-split stats; recent live-feed matches often
    # arrive without a surface)
    try:
        from bot.stats.surface import backfill_surfaces
        filled = backfill_surfaces(db)
        log.info("surface backfill complete", filled=filled)
    except Exception as e:
        db.rollback()
        log.error("surface backfill failed", error=str(e))
    # merge duplicate PLAYER rows so a person's history isn't fragmented across
    # shells (wrecks recency/form/fatigue for the players we bet)
    try:
        from bot.matching.dedup import merge_all_duplicates
        merge_all_duplicates(db)
        db.commit()
    except Exception as e:
        db.rollback()
        log.error("player dedup failed", error=str(e))
    if refresh_cache:
        from bot.stats.cache import refresh_stats_cache

        refresh_stats_cache(db)
    try:
        from bot.scenarios import generate_scenarios

        generate_scenarios(db)
    except Exception as e:
        log.error("scenario generation failed", error=str(e))
    # market_ticks retention. The recorder writes a row per websocket quote and
    # nothing used to delete them: ~120M rows / ~39 GB after eight weeks, which
    # on a memory-billed host was the single largest line on the bill. Only
    # settled markets are pruned, and only after their durable summaries (peak
    # bids, last time each side hit the take-profit limit) are committed.
    try:
        from bot.config import settings as _cfg
        from bot.market.retention import prune_market_ticks
        prune_market_ticks(db, keep_days=_cfg().tick_retention_days)
    except Exception as e:
        db.rollback()
        log.error("tick prune failed", error=str(e))
    # variable-strength monitor: one expanding-window snapshot per ingest so we
    # can watch each candidate variable's lift stabilize as the sample grows
    try:
        from bot.prob.feature_eval import persist_snapshot
        persist_snapshot(db)
    except Exception as e:
        db.rollback()
        log.error("feature-eval snapshot failed", error=str(e))
    # refit the state-conditioned recalibration walk-forward and persist it (the
    # model loads the newest fit on rebuild). Self-gated on OOS lift → safe.
    try:
        from bot.prob.calibrate import refit_and_persist_state_scale
        res = refit_and_persist_state_scale(db)
        log.info("state calibration refit", scale=res.get("scale"))
    except Exception as e:
        db.rollback()
        log.error("state calibration refit failed", error=str(e))
    # refit the global pre-match Platt scalar walk-forward and persist it (the
    # model loads the newest fit on rebuild). Gated on OOS log-loss lift → safe.
    try:
        from bot.prob.calibrate import refit_and_persist_platt
        pres = refit_and_persist_platt(db)
        log.info("platt calibration refit", **pres)
    except Exception as e:
        db.rollback()
        log.error("platt calibration refit failed", error=str(e))
    for r in results:
        log.info("sync result", source=r.source, matches=r.matches_upserted,
                 players=r.players_upserted, tournaments=r.tournaments_upserted,
                 sets=r.sets_written, skipped_files=r.skipped_files, errors=r.errors)
    return results
