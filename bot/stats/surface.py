"""Surface resolver — the live/upcoming feeds (api-tennis, Kalshi) don't carry
court surface, but Sackmann does for ~25k historical tournaments. Most venues
recur, so a venue→surface map built from Sackmann resolves the surface of a
current match by its tournament name. This unlocks surface-specific stats
in-play (the clay/hard split an analyst leans on).

Coverage is ~3/4 of active tournaments; genuinely-new venues stay None (honest).
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select

from bot.models import Tournament

# parenthetical segments carry the country ("Jiujiang (China)") — Sackmann keys
# the venue by city alone, so drop them before matching
_PARENS = re.compile(r"\([^)]*\)")
# anything after " - " on the live feeds is a phase suffix ("- Qualification")
_TAIL = re.compile(r"\s-\s.*$")
# level/prize/qualifier/sponsor/format tokens to strip so only the VENUE remains
_STRIP = re.compile(
    r'\b([wm]\d{2,3}|itf|challenger|ch|atp|wta|men|mens|women|womens|ladies|'
    r'singles|doubles|qualifying|qualification|qual|cup|open|masters|classic|'
    r'international|internationaux|championships?|trophy|final[s]?|round|'
    r'r\d+|q\d+|quarter\w*|semi\w*|prelim\w*|group|of|last)\b|\d+|[^a-z ]')

# Grand Slams: the live feeds name them differently from Sackmann's venue, so
# map the well-known name straight to its (fixed) surface. Word-boundary matched
# so "us open" does NOT fire inside "Aus Open" (Australian Open qualies etc.).
_SLAM = (
    (re.compile(r"\broland garros\b"), "Clay"),
    (re.compile(r"\bfrench open\b"), "Clay"),
    (re.compile(r"\bwimbledon\b"), "Grass"),
    (re.compile(r"\bu\.?s\.? open\b"), "Hard"),
    (re.compile(r"\baustralian open\b"), "Hard"),
)


def _venue(name: str | None) -> str:
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = _PARENS.sub(" ", s)
    s = _TAIL.sub(" ", s)
    s = _STRIP.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


_MAP: dict[str, str] | None = None


def _build_map(db) -> dict[str, str]:
    from collections import Counter, defaultdict
    votes: dict[str, Counter] = defaultdict(Counter)
    for nm, surf in db.execute(
        select(Tournament.name, Tournament.surface)
        .where(Tournament.source == "sackmann", Tournament.surface.is_not(None))
    ):
        v = _venue(nm)
        if v:
            votes[v][surf.capitalize()] += 1  # normalize 'clay'/'Clay'
    return {v: c.most_common(1)[0][0] for v, c in votes.items()}


def surface_map(db, *, refresh: bool = False) -> dict[str, str]:
    """Cached venue→surface map from Sackmann (built once per process)."""
    global _MAP
    if _MAP is None or refresh:
        _MAP = _build_map(db)
    return _MAP


def resolve_surface(db, tournament_name: str | None) -> str | None:
    """Surface ('Hard'|'Clay'|'Grass'|'Carpet') for a tournament by name, or
    None if the venue isn't in the Sackmann history. Grand Slams resolve by
    name (the feeds spell them differently from Sackmann's venue)."""
    if not tournament_name:
        return None
    low = tournament_name.lower()
    for pat, surf in _SLAM:
        if pat.search(low):
            return surf
    if db is None:
        return None  # no venue map available without a session
    return surface_map(db).get(_venue(tournament_name))


def live_match_surface(db, player_a_id, player_b_id, as_of, window_days: int = 3):
    """Surface of the actual match between these two players around `as_of` — the
    scheduled/just-played row carries it. Used to surface-adjust the in-play and
    prematch predictions (the model learns per-surface ratings but only applies
    them when a surface is supplied). None if unknown → the model safely falls
    back to its overall rating. The tight date window keeps it to THIS match, not
    a past head-to-head on a different court."""
    from datetime import timedelta

    from sqlalchemy import text
    if not player_a_id or not player_b_id or as_of is None:
        return None
    return db.execute(text(
        "SELECT surface FROM matches WHERE surface IS NOT NULL "
        "AND is_duplicate = false AND match_date BETWEEN :lo AND :hi "
        "AND ((winner_id = :a AND loser_id = :b) "
        "  OR (winner_id = :b AND loser_id = :a)) "
        "ORDER BY match_date DESC LIMIT 1"),
        {"a": player_a_id, "b": player_b_id,
         "lo": as_of - timedelta(days=window_days),
         "hi": as_of + timedelta(days=window_days)}).scalar()


def backfill_surfaces(db, *, refresh: bool = True) -> int:
    """Fill matches.surface for completed rows missing it, via the venue→surface
    map. Cheap enough to run every ingest, so venues the resolver can now place
    (after a normalizer fix, or once Sackmann has seen them) get filled without a
    manual pass. Returns the number of rows updated."""
    from sqlalchemy import text

    from bot.models import Match, Tournament
    if refresh:
        surface_map(db, refresh=True)  # rebuild from current Sackmann coverage
    # include scheduled rows: surface is a property of the venue, not the
    # result, and upcoming-match surface feeds prematch predictions
    rows = db.execute(
        select(Match.id, Tournament.name)
        .join(Tournament, Tournament.id == Match.tournament_id)
        .where(Match.surface.is_(None), Match.tournament_id.is_not(None))).all()
    ids, surfs = [], []
    for mid, name in rows:
        s = resolve_surface(db, name)
        if s:
            ids.append(mid)
            surfs.append(s)
    if not ids:
        return 0
    db.execute(text(
        "UPDATE matches SET surface = d.surf FROM ("
        "SELECT unnest(CAST(:ids AS bigint[])) AS id, "
        "unnest(CAST(:surfs AS text[])) AS surf) d WHERE matches.id = d.id"),
        {"ids": ids, "surfs": surfs})
    db.commit()
    return len(ids)
