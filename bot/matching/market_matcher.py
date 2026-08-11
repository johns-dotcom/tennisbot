"""Player-name matching across Kalshi titles, api-tennis, and Sackmann data.

Strategy, in order of trust:
  1. manual override table (player_aliases)
  2. exact normalized-name match
  3. "surname, first-initial" match (handles "S. Williams" / "Williams S.")
  4. rapidfuzz fuzzy match over the candidate pool
Anything below the confidence threshold lands in match_review_queue —
never silently dropped.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

from rapidfuzz import fuzz, process
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bot.config import settings
from bot.log import get_logger
from bot.models import Match, MatchReviewQueue, Player, PlayerAlias

log = get_logger("matching")

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")
_PAREN_RE = re.compile(r"\([^)]*\)")  # Kalshi disambiguators: "Cezar Cretu (b. 2001)"


def normalize_name(name: str) -> str:
    """Lowercase, strip parentheticals/diacritics/punctuation, collapse whitespace."""
    s = _PAREN_RE.sub(" ", name or "")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _PUNCT_RE.sub(" ", s.lower())
    return _WS_RE.sub(" ", s).strip()


def _surname_ok(query_norm: str, cand_norm: str) -> bool:
    """Guard the fuzzy path against different-family-name matches. A high
    whole-name score can still hide a distinct surname when the first names
    coincide — "Alex Hernandez" vs "Alex Fernandez" scores 93% but they are two
    different people. Require the surnames (last token) to actually align: same
    first letter and a strong token ratio. A failed guard routes to review, not a
    wrong match — a wrong player identity is worse than an unmatched market."""
    q = query_norm.split()
    c = cand_norm.split()
    if not q or not c:
        return False
    qs, cs = q[-1], c[-1]
    if qs[0] != cs[0]:
        return False  # different family name (Hernandez ≠ Fernandez)
    return fuzz.ratio(qs, cs) >= 88


@dataclass
class MatchResult:
    player_id: int | None
    confidence: float  # 0-1
    method: str  # 'alias' | 'exact' | 'initial' | 'fuzzy' | 'none'


class PlayerMatcher:
    """Caches the player pool per instance; rebuild by constructing a new one."""

    def __init__(self, db: Session, tour: str | None = None):
        q = select(Player.id, Player.normalized_name, Player.first_name, Player.last_name,
                   Player.tour)
        if tour:
            q = q.where(Player.tour == tour)
        self._pool = db.execute(q).all()
        self._by_norm: dict[str, list[int]] = {}
        # space-insensitive index: catches joined-vs-spaced romanizations
        # ("YeXin Ma" vs "Ye Xin Ma"), common for CJK names, which otherwise
        # fragment into a shell player separate from the source-backed row
        self._by_collapsed: dict[str, list[int]] = {}
        self._names: list[str] = []
        self._ids: list[int] = []
        for pid, norm, first, last, _t in self._pool:
            self._by_norm.setdefault(norm, []).append(pid)
            self._by_collapsed.setdefault(norm.replace(" ", ""), []).append(pid)
            self._names.append(norm)
            self._ids.append(pid)
        self.tour = tour

    def match(self, db: Session, raw_name: str, *, source: str = "unknown",
              context: dict | None = None, queue_on_miss: bool = True) -> MatchResult:
        cfg = settings()
        norm = normalize_name(raw_name)
        if not norm:
            return MatchResult(None, 0.0, "none")

        # 1. manual alias (source-scoped beats global)
        alias = db.execute(
            select(PlayerAlias).where(PlayerAlias.alias_normalized == norm)
            .order_by(PlayerAlias.source.is_(None))  # non-null (scoped) first
        ).scalars().first()
        if alias and (alias.source is None or alias.source == source):
            return MatchResult(alias.player_id, 1.0, "alias")

        # 2. exact
        exact = self._by_norm.get(norm, [])
        if len(exact) == 1:
            return MatchResult(exact[0], 1.0, "exact")
        if len(exact) > 1:
            # Multiple rows share this exact normalized name. Usually that's not a
            # real ambiguity — it's DUPLICATE shells of one person (ingest created
            # the same player more than once because it had no id to dedupe on).
            # Collapse them to a canonical rather than leaving the market
            # unmatched (which 404s its match-data page). Only a genuine
            # collision — two or more DISTINCT, id-bearing players — still queues.
            canonical = self._resolve_duplicates(db, exact)
            if canonical is not None:
                return MatchResult(canonical, 0.9, "exact-dedup")
            self._queue(db, raw_name, source,
                        {"reason": "ambiguous exact", "candidates": exact, **(context or {})},
                        queue_on_miss)
            return MatchResult(None, 0.0, "ambiguous")

        # 2.5 space-insensitive exact — joined vs spaced romanization
        # ("YeXin Ma" ↔ "Ye Xin Ma"). High precision: an identical name once
        # internal spaces are removed. Resolves multi-hits through the same
        # conservative duplicate logic (which queues genuinely-distinct players).
        col = norm.replace(" ", "")
        collapsed = [pid for pid in dict.fromkeys(self._by_collapsed.get(col, []))
                     if pid not in exact]
        if len(collapsed) == 1:
            return MatchResult(collapsed[0], 0.97, "collapsed")
        if len(collapsed) > 1:
            canonical = self._resolve_duplicates(db, collapsed)
            if canonical is not None:
                return MatchResult(canonical, 0.9, "collapsed-dedup")

        # 3. surname + first-initial ("s williams", "williams s")
        toks = norm.split()
        if len(toks) >= 2 and (len(toks[0]) == 1 or len(toks[-1]) == 1):
            initial, surname = (toks[0], toks[-1]) if len(toks[0]) == 1 else (toks[-1], toks[0])
            hits = [pid for pid, n, first, last, _t in self._pool
                    if last and normalize_name(last) == surname
                    and first and normalize_name(first)[:1] == initial]
            if len(hits) == 1:
                return MatchResult(hits[0], 0.95, "initial")
            if len(hits) > 1:
                self._queue(db, raw_name, source,
                            {"reason": "ambiguous initial", "candidates": hits, **(context or {})},
                            queue_on_miss)
                return MatchResult(None, 0.0, "ambiguous")

        # 4. fuzzy
        if self._names:
            found = process.extract(norm, self._names, scorer=fuzz.token_sort_ratio, limit=3)
            if found:
                best_name, best_score, best_idx = found[0]
                runner_up = found[1][1] if len(found) > 1 else 0
                if (best_score >= cfg.fuzzy_match_threshold
                        and best_score - runner_up >= 3
                        and _surname_ok(norm, best_name)):
                    return MatchResult(self._ids[best_idx], best_score / 100, "fuzzy")
                if best_score >= cfg.fuzzy_review_threshold:
                    self._queue(db, raw_name, source, {
                        "reason": "low-confidence fuzzy",
                        "candidates": [{"name": n, "score": s, "player_id": self._ids[i]}
                                       for n, s, i in found],
                        **(context or {}),
                    }, queue_on_miss)
                    return MatchResult(None, best_score / 100, "none")

        self._queue(db, raw_name, source, {"reason": "no candidate", **(context or {})},
                    queue_on_miss)
        return MatchResult(None, 0.0, "none")

    def _resolve_duplicates(self, db: Session, ids: list[int]) -> int | None:
        """Collapse same-normalized-name candidates to ONE canonical id.

        Multiple rows with an IDENTICAL normalized name in the same tour are
        duplicates of one person, not a real ambiguity — two distinct pros with
        the exact same full name on one tour is effectively nonexistent (near
        namesakes like 'Martin Damm' / 'Martin Damm Jr' normalize differently).
        Leaving them unmatched dead-ends the whole match-data page, which is
        strictly worse than picking the best row. So always resolve, to the
        RICHEST record — most match history first (that's the real player's
        row), then one carrying a source id, then the lowest id — so the picked
        player has the deepest profile. Returns None only when no rows exist.
        """
        rows = db.execute(
            select(Player.id, Player.sackmann_id, Player.api_tennis_id)
            .where(Player.id.in_(ids))
        ).all()
        if not rows:
            return None
        cand = [r.id for r in rows]
        has_id = {r.id: (r.sackmann_id is not None or r.api_tennis_id is not None)
                  for r in rows}
        wc = dict(db.execute(
            select(Match.winner_id, func.count()).where(Match.winner_id.in_(cand))
            .group_by(Match.winner_id)).all())
        lc = dict(db.execute(
            select(Match.loser_id, func.count()).where(Match.loser_id.in_(cand))
            .group_by(Match.loser_id)).all())
        matches = {pid: wc.get(pid, 0) + lc.get(pid, 0) for pid in cand}
        # rank: most matches, then id-bearing, then lowest id (negate for max)
        return max(cand, key=lambda pid: (matches[pid], has_id[pid], -pid))

    def _queue(self, db: Session, raw_name: str, source: str, context: dict,
               enabled: bool) -> None:
        if not enabled:
            return
        exists = db.execute(
            select(MatchReviewQueue.id).where(
                MatchReviewQueue.raw_name == raw_name,
                MatchReviewQueue.source == source,
                MatchReviewQueue.resolved.is_(False))
        ).first()
        if exists:
            return
        db.add(MatchReviewQueue(created_at=datetime.now(timezone.utc), source=source,
                                raw_name=raw_name, context=context))
        log.warning("name sent to review queue", name=raw_name, source=source,
                    reason=context.get("reason"))
