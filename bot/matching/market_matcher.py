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
from sqlalchemy import select
from sqlalchemy.orm import Session

from bot.config import settings
from bot.log import get_logger
from bot.models import MatchReviewQueue, Player, PlayerAlias

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
        self._names: list[str] = []
        self._ids: list[int] = []
        for pid, norm, first, last, _t in self._pool:
            self._by_norm.setdefault(norm, []).append(pid)
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
            self._queue(db, raw_name, source,
                        {"reason": "ambiguous exact", "candidates": exact, **(context or {})},
                        queue_on_miss)
            return MatchResult(None, 0.0, "none")

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
                return MatchResult(None, 0.0, "none")

        # 4. fuzzy
        if self._names:
            found = process.extract(norm, self._names, scorer=fuzz.token_sort_ratio, limit=3)
            if found:
                best_name, best_score, best_idx = found[0]
                runner_up = found[1][1] if len(found) > 1 else 0
                if best_score >= cfg.fuzzy_match_threshold and best_score - runner_up >= 3:
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
