"""Merge duplicate player rows into one canonical id.

The ingest created the same person multiple times (no id to dedupe on), so a
player's matches — especially recent lower-tier ones — get fragmented across
several rows. That wrecks recency stats (form/fatigue/streak) for exactly the
players we bet: e.g. 'Andres Martin' had his 191-match history on one id and
single recent matches scattered across four empty shells.

The matcher now resolves NEW matches to a canonical, but the already-fragmented
history has to be consolidated. This repoints every player foreign key from the
duplicate shells to the canonical (the row with the deepest history) and deletes
the shells — safe because two distinct pros never share an identical normalized
name on one tour.
"""
from __future__ import annotations

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from bot.log import get_logger
from bot.models import Match, Player

log = get_logger("matching.dedup")

# (table, column) for every foreign key that points at players.id
_FK = [
    ("matches", "winner_id"), ("matches", "loser_id"),
    ("kalshi_markets", "player_a_id"), ("kalshi_markets", "player_b_id"),
    ("paper_bets", "player_id"), ("advisories", "recommended_player_id"),
    ("charting_stats", "player_id"), ("player_aliases", "player_id"),
    ("scenarios", "player_id"), ("scenarios", "opponent_id"),
    ("match_review_queue", "resolved_player_id"),
]
# unique (player_id, as_of) — drop the shells' rows rather than repoint-and-collide
_DROP_FIRST = ["player_rankings", "player_stats_cache"]


def merge_all_duplicates(db: Session, tour: str | None = None) -> dict:
    """Consolidate every same-(tour, normalized_name) player group into one
    canonical row. Bulk SQL — a couple of scans to plan, then one mapping-join
    UPDATE per foreign key — so it's a handful of statements, not one per group.
    Returns {groups, players_removed}."""
    from collections import defaultdict
    rows = db.execute(select(Player.id, Player.tour, Player.normalized_name,
                             Player.sackmann_id, Player.api_tennis_id)).all()
    groups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        if tour and r.tour != tour:
            continue
        # space-insensitive key so joined-vs-spaced romanization shells
        # ('YeXin Ma') group with the source-backed row ('Ye Xin Ma')
        groups[(r.tour, (r.normalized_name or "").replace(" ", ""))].append(r)
    dup_groups = []
    for v in groups.values():
        if len(v) <= 1:
            continue
        # ≥2 SOURCE-BACKED rows collapsing to one key are likely DISTINCT
        # players (not a spelling variant) — never auto-merge; leave for review.
        # Merging shells (no source id) into the one canonical stays safe.
        if sum(1 for r in v if r.sackmann_id is not None
               or r.api_tennis_id is not None) >= 2:
            continue
        dup_groups.append(v)
    if not dup_groups:
        return {"groups": 0, "players_removed": 0}

    cand = [r.id for v in dup_groups for r in v]
    wc = dict(db.execute(select(Match.winner_id, func.count()).where(
        Match.winner_id.in_(cand)).group_by(Match.winner_id)).all())
    lc = dict(db.execute(select(Match.loser_id, func.count()).where(
        Match.loser_id.in_(cand)).group_by(Match.loser_id)).all())

    def mc(pid):
        return (wc.get(pid, 0) or 0) + (lc.get(pid, 0) or 0)

    mapping: dict[int, int] = {}        # dup id -> canonical id
    id_xfer: list[tuple] = []           # (canon_id, sackmann_id, api_tennis_id)
    for v in dup_groups:
        canon = max(v, key=lambda r: (r.sackmann_id is not None, mc(r.id), -r.id))
        sack, api = canon.sackmann_id, canon.api_tennis_id
        for r in v:
            if r.id == canon.id:
                continue
            mapping[r.id] = canon.id
            if sack is None and r.sackmann_id is not None:
                sack = r.sackmann_id
            if api is None and r.api_tennis_id is not None:
                api = r.api_tennis_id
        if (sack, api) != (canon.sackmann_id, canon.api_tennis_id):
            id_xfer.append((canon.id, sack, api))

    dups = list(mapping.keys())
    canons = [mapping[d] for d in dups]
    # free the dups' unique source-id slots, then hand any needed id to the canon
    db.execute(text("UPDATE players SET sackmann_id=NULL, api_tennis_id=NULL "
                    "WHERE id = ANY(:d)"), {"d": dups})
    for canon_id, sack, api in id_xfer:
        db.execute(text("UPDATE players SET sackmann_id=:s, api_tennis_id=:a "
                        "WHERE id=:c"), {"s": sack, "a": api, "c": canon_id})
    # (player_id, as_of) is unique — drop the shells' rows rather than collide
    for tbl in _DROP_FIRST:
        db.execute(text(f"DELETE FROM {tbl} WHERE player_id = ANY(:d)"), {"d": dups})
    # one mapping-join UPDATE per foreign key
    for tbl, col in _FK:
        db.execute(text(
            f"UPDATE {tbl} t SET {col} = m.canon FROM ("
            f"SELECT unnest(CAST(:d AS bigint[])) AS dup, "
            f"unnest(CAST(:c AS bigint[])) AS canon) m "
            f"WHERE t.{col} = m.dup"), {"d": dups, "c": canons})
    db.execute(text("DELETE FROM players WHERE id = ANY(:d)"), {"d": dups})
    db.commit()
    log.info("player dedup complete", groups=len(dup_groups), removed=len(dups))
    return {"groups": len(dup_groups), "players_removed": len(dups)}


def merge_player_into(db: Session, dup_id: int, canon_id: int) -> dict:
    """Repoint every player foreign key from one row to another and delete the
    shell. For TARGETED fixes of fragmentation the exact-name bulk dedup can't
    catch — e.g. a spelling variant ('YeXin Ma' shell) split from the
    source-backed row ('Ye Xin Ma'). The canonical keeps its own source ids."""
    if dup_id == canon_id:
        return {"merged": 0, "reason": "same id"}
    db.execute(text("UPDATE players SET sackmann_id=NULL, api_tennis_id=NULL "
                    "WHERE id=:d"), {"d": dup_id})
    for tbl in _DROP_FIRST:  # unique (player_id, as_of) — drop rather than collide
        db.execute(text(f"DELETE FROM {tbl} WHERE player_id=:d"), {"d": dup_id})
    for tbl, col in _FK:
        db.execute(text(f"UPDATE {tbl} SET {col}=:c WHERE {col}=:d"),
                   {"c": canon_id, "d": dup_id})
    db.execute(text("DELETE FROM players WHERE id=:d"), {"d": dup_id})
    db.commit()
    log.info("player merged", dup=dup_id, canon=canon_id)
    return {"merged": 1, "dup": dup_id, "canon": canon_id}
