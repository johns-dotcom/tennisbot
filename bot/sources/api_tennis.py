"""ApiTennisSource — daily sync of completed results newer than the latest
Sackmann coverage, plus the upcoming 48h schedule. api-tennis.com.

Completed results and schedule ONLY. In-play state is never read from here —
that is the market estimator's job (Phase 3.5).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from bot.config import settings
from bot.log import get_logger
from bot.matching.market_matcher import PlayerMatcher, normalize_name
from bot.models import IngestState, Match, MatchSet, Player, Tournament
from bot.sources.base import SyncResult, TennisDataSource

log = get_logger("sources.api_tennis")

# event_type_type → our tour bucket; anything not mapped (doubles, juniors,
# exhibitions) is skipped.
TOUR_MAP = {
    "atp singles": "atp",
    "challenger men singles": "atp",
    "itf men singles": "atp",
    "wta singles": "wta",
    "challenger women singles": "wta",
    "itf women singles": "wta",
}


class ApiTennisSource(TennisDataSource):
    name = "api_tennis"

    def __init__(self) -> None:
        cfg = settings()
        self.key = cfg.api_tennis_key
        self.base = cfg.api_tennis_base
        self.horizon = timedelta(hours=cfg.schedule_horizon_hours)
        self.client = httpx.Client(timeout=60)

    def _get(self, method: str, **params) -> list[dict]:
        r = self.client.get(self.base, params={"method": method, "APIkey": self.key, **params})
        r.raise_for_status()
        body = r.json()
        if body.get("success") != 1:
            raise RuntimeError(f"api-tennis {method} returned {body.get('success')}: "
                               f"{str(body)[:200]}")
        result = body.get("result") or []
        return result if isinstance(result, list) else []

    # ---------- helpers ----------

    @staticmethod
    def _sackmann_frontier(db: Session, tour: str) -> date:
        """Most recent completed-match date Sackmann covers for this tour."""
        d = db.execute(
            select(func.max(Match.match_date)).where(
                Match.source == "sackmann", Match.tour == tour)
        ).scalar()
        # Sackmann dates the whole tournament week at its Monday; step back a week
        # to be safe, dedup handles the overlap.
        return (d - timedelta(days=7)) if d else date.today() - timedelta(days=60)

    def _match_player(self, db: Session, matcher: PlayerMatcher, tour: str,
                      raw_name: str, api_key: str | None, context: dict) -> int | None:
        # fast path: previously linked api_tennis_id
        if api_key:
            pid = db.execute(
                select(Player.id).where(Player.tour == tour, Player.api_tennis_id == str(api_key))
            ).scalar()
            if pid:
                return pid
        res = matcher.match(db, raw_name, source=self.name, context=context)
        if res.player_id and api_key:
            db.get(Player, res.player_id).api_tennis_id = str(api_key)
        if res.player_id is None and res.method == "none" and res.confidence == 0.0:
            # genuinely new player (typical for ITF): create a provisional row
            if context.get("reason") != "ambiguous":
                p = Player(tour=tour, api_tennis_id=str(api_key) if api_key else None,
                           full_name=raw_name, normalized_name=normalize_name(raw_name))
                db.add(p)
                db.flush()
                log.info("provisional player created", name=raw_name, tour=tour)
                return p.id
        return res.player_id

    def _tournament(self, db: Session, tour: str, fixture: dict) -> int | None:
        tkey = str(fixture.get("tournament_key") or "")
        if not tkey:
            return None
        stmt = pg_insert(Tournament).values(
            tour=tour, source=self.name, source_key=tkey,
            name=fixture.get("tournament_name") or tkey,
            surface=None, level=None, start_date=None,
        ).on_conflict_do_nothing(constraint="uq_tournaments_key")
        db.execute(stmt)
        return db.execute(
            select(Tournament.id).where(Tournament.tour == tour, Tournament.source == self.name,
                                        Tournament.source_key == tkey)
        ).scalar()

    @staticmethod
    def _duplicate_of_sackmann(db: Session, tour: str, p1: int, p2: int, d: date) -> bool:
        return db.execute(
            select(Match.id).where(
                Match.source == "sackmann", Match.tour == tour,
                Match.winner_id.in_([p1, p2]), Match.loser_id.in_([p1, p2]),
                Match.match_date.between(d - timedelta(days=10), d + timedelta(days=10)),
            )
        ).first() is not None

    # ---------- fixture ingestion ----------

    def _upsert_fixture(self, db: Session, matcher_by_tour: dict[str, PlayerMatcher],
                        f: dict, result: SyncResult) -> None:
        etype = (f.get("event_type_type") or "").strip().lower()
        tour = TOUR_MAP.get(etype)
        if tour is None:
            return
        p1_name = (f.get("event_first_player") or "").strip()
        p2_name = (f.get("event_second_player") or "").strip()
        if not p1_name or not p2_name or "/" in p1_name:  # doubles guard
            return
        try:
            event_date = datetime.strptime(f.get("event_date", ""), "%Y-%m-%d").date()
        except ValueError:
            return
        status = (f.get("event_status") or "").strip().lower()
        skey = f"{tour}:{f.get('event_key')}"
        ctx = {"tournament": f.get("tournament_name"), "date": str(event_date)}
        matcher = matcher_by_tour[tour]
        p1 = self._match_player(db, matcher, tour, p1_name, f.get("first_player_key"), ctx)
        p2 = self._match_player(db, matcher, tour, p2_name, f.get("second_player_key"), ctx)
        if p1 is None or p2 is None:
            return  # already queued for review
        tid = self._tournament(db, tour, f)

        if status == "finished":
            winner_side = (f.get("event_winner") or "").strip().lower()
            if winner_side not in ("first player", "second player"):
                return
            winner_id, loser_id = (p1, p2) if winner_side == "first player" else (p2, p1)
            sets, ww, wl = self._parse_api_sets(f, winner_is_first=(winner_side == "first player"))
            is_dup = self._duplicate_of_sackmann(db, tour, p1, p2, event_date)
            values = dict(
                tour=tour, source=self.name, source_key=skey, tournament_id=tid,
                winner_id=winner_id, loser_id=loser_id, match_date=event_date,
                round=(f.get("tournament_round") or None), best_of=3,
                score_raw=(f.get("event_final_result") or None),
                outcome="completed", sets_won_winner=ww, sets_won_loser=wl,
                surface=None, tourney_level=None, is_duplicate=is_dup,
                scheduled_start=None,
            )
            stmt = pg_insert(Match).values(values).on_conflict_do_update(
                constraint="uq_matches_source_key",
                set_={k: v for k, v in values.items() if k not in ("source", "source_key")},
            ).returning(Match.id)
            mid = db.execute(stmt).scalar()
            db.execute(MatchSet.__table__.delete().where(MatchSet.match_id == mid))
            for s in sets:
                db.add(MatchSet(match_id=mid, **s))
            result.matches_upserted += 1
            result.sets_written += len(sets)
        elif status in ("", "not started"):
            values = dict(
                tour=tour, source=self.name, source_key=skey, tournament_id=tid,
                winner_id=p1, loser_id=p2,  # side A / side B until completed
                match_date=event_date, round=(f.get("tournament_round") or None),
                outcome="scheduled", scheduled_start=self._event_dt(f),
            )
            stmt = pg_insert(Match).values(values).on_conflict_do_update(
                constraint="uq_matches_source_key",
                set_={"scheduled_start": values["scheduled_start"],
                      "match_date": values["match_date"]},
            )
            db.execute(stmt)
            result.matches_upserted += 1
        # in-play statuses ('set 1', ...) are deliberately ignored

    @staticmethod
    def _event_dt(f: dict) -> datetime | None:
        try:
            return datetime.strptime(
                f"{f.get('event_date')} {f.get('event_time') or '00:00'}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    @staticmethod
    def _parse_api_sets(f: dict, winner_is_first: bool):
        """api-tennis scores: [{'score_first': '6', 'score_second': '4', 'score_set': '1'}]"""
        sets, ww, wl = [], 0, 0
        for s in f.get("scores") or []:
            try:
                g1, g2 = int(s["score_first"]), int(s["score_second"])
                n = int(s["score_set"])
            except (KeyError, ValueError, TypeError):
                continue
            wg, lg = (g1, g2) if winner_is_first else (g2, g1)
            won = wg > lg
            ww += won
            wl += (not won)
            sets.append(dict(set_number=n, winner_games=wg, loser_games=lg,
                             set_won_by_match_winner=won, tiebreak=(max(wg, lg) == 7 and min(wg, lg) == 6),
                             completed=True))
        return sets, ww, wl

    # ---------- entry point ----------

    def sync(self, db: Session, *, full: bool = False) -> SyncResult:
        result = SyncResult(source=self.name)
        if not self.key:
            msg = "API_TENNIS_KEY not set — skipping live-results sync"
            log.warning(msg)
            result.errors.append(msg)
            return result

        frontier = min(self._sackmann_frontier(db, "atp"), self._sackmann_frontier(db, "wta"))
        today = date.today()
        stop = today + timedelta(days=settings().schedule_horizon_hours // 24)
        log.info("api-tennis sync window", start=str(frontier), stop=str(stop))

        matcher_by_tour = {"atp": PlayerMatcher(db, "atp"), "wta": PlayerMatcher(db, "wta")}
        # the API caps ranges; walk in 7-day windows
        cur = frontier
        while cur <= stop:
            win_end = min(cur + timedelta(days=7), stop)
            try:
                fixtures = self._get("get_fixtures", date_start=cur.isoformat(),
                                     date_stop=win_end.isoformat())
            except (httpx.HTTPError, RuntimeError) as e:
                result.errors.append(f"fixtures {cur}..{win_end}: {e}")
                cur = win_end + timedelta(days=1)
                continue
            for f in fixtures:
                try:
                    self._upsert_fixture(db, matcher_by_tour, f, result)
                except Exception as e:
                    result.errors.append(f"event {f.get('event_key')}: {e}")
            db.commit()
            cur = win_end + timedelta(days=1)

        stmt = pg_insert(IngestState).values(
            key="api_tennis:last_sync", value=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc),
        ).on_conflict_do_update(index_elements=["key"], set_={
            "value": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc)})
        db.execute(stmt)
        return result
