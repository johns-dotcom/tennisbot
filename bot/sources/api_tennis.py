"""ApiTennisSource — daily sync of completed results newer than the latest
Sackmann coverage, plus the upcoming 48h schedule. api-tennis.com.

Completed results and schedule ONLY. In-play state is never read from here —
that is the market estimator's job (Phase 3.5).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from bot.config import settings
from bot.log import get_logger
from bot.matching.market_matcher import PlayerMatcher, normalize_name
from bot.models import (IngestState, KalshiMarket, Match, MatchSet, Player,
                        PlayerRanking, Tournament)
from bot.sources.base import SyncResult, TennisDataSource

# get_players is billed per player; only refresh an active player's bio this
# often, and cap how many we fetch per ingest run.
BIO_REFRESH_DAYS = 30
BIO_MAX_PER_RUN = 60

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

# api-tennis uses long round names ("Round of 16"); Sackmann — and every
# consumer of Match.round (deciding-set / final detection in scenarios) —
# expects short codes. Normalise to those. The column is varchar(8), so the
# fallback also clamps: never emit a value that would overflow.
_ROUND_MAP = {
    "final": "F", "the final": "F",
    "semi-final": "SF", "semi-finals": "SF", "semifinal": "SF", "1/2-finals": "SF",
    "quarter-final": "QF", "quarter-finals": "QF", "quarterfinal": "QF", "1/4-finals": "QF",
    "round of 16": "R16", "1/8-finals": "R16",
    "round of 32": "R32", "1/16-finals": "R32",
    "round of 64": "R64", "1/32-finals": "R64",
    "round of 128": "R128", "1/64-finals": "R128",
}


def _int(v) -> int | None:
    """Parse an api-tennis stat scalar ('53', '68%', '', None) to int or None."""
    if v is None:
        return None
    s = str(v).strip().rstrip("%")
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return None


def _parse_bday(raw) -> date | None:
    """api-tennis player_bday is 'DD.MM.YYYY' (or empty)."""
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw).strip(), "%d.%m.%Y").date()
    except ValueError:
        return None


def _parse_surface_stats(stats) -> dict | None:
    """Career singles win/loss per surface from get_players `stats` (a list of
    per-season rows). Doubles rows are skipped. Returns
    {"hard": {"w", "l"}, "clay": {...}, "grass": {...}} with any positive total,
    else None."""
    if not isinstance(stats, list):
        return None
    agg = {s: {"w": 0, "l": 0} for s in ("hard", "clay", "grass")}
    for row in stats:
        if (row.get("type") or "").lower() != "singles":
            continue
        for surf in agg:
            w = _int(row.get(f"{surf}_won"))
            l = _int(row.get(f"{surf}_lost"))
            if w:
                agg[surf]["w"] += w
            if l:
                agg[surf]["l"] += l
    agg = {s: v for s, v in agg.items() if v["w"] + v["l"] > 0}
    return agg or None


def _side_serve(by_stat: dict[tuple[str, str], dict]) -> dict | None:
    """Build the Sackmann-schema serve line ({ace, df, svpt, 1stIn, 1stWon,
    2ndWon, SvGms, bpSaved, bpFaced}) for ONE player from that player's
    match-period api-tennis statistics, keyed (stat_type, stat_name)→row.

    Returns None unless the core serve-point data is present, so partial rows
    (a retirement with no serve feed) are absent rather than half-populated —
    the profile treats a partial serve line as no line at all.

    Break points are defaulted to 0 when the row is absent: a player who faced
    zero break points legitimately has no 'Break Points Saved' stat, and that
    is real data (0 faced, 0 saved), not a gap. svpt is DERIVED as
    1stIn + 2nd-serve-points so first_in/svpt reproduces api-tennis's own
    reported 1st-serve %; the raw 'Service Points Won' total is noisier
    (counts lets/retired points inconsistently) and would break that identity.
    """
    def won(t, n):
        return _int((by_stat.get((t, n)) or {}).get("stat_won"))

    def total(t, n):
        return _int((by_stat.get((t, n)) or {}).get("stat_total"))

    def val(t, n):
        return _int((by_stat.get((t, n)) or {}).get("stat_value"))

    first_in = total("Service", "1st serve points won")
    first_won = won("Service", "1st serve points won")
    second_pts = total("Service", "2nd serve points won")
    second_won = won("Service", "2nd serve points won")
    svgms = total("Games", "Service games won")
    # core serve-point data must be present and non-degenerate
    if None in (first_in, first_won, second_pts, second_won, svgms):
        return None
    if first_in <= 0 or svgms <= 0:
        return None
    return {
        "ace": val("Service", "Aces") or 0,
        "df": val("Service", "Double Faults") or 0,
        "svpt": first_in + second_pts,       # derived; see docstring
        "1stIn": first_in,
        "1stWon": first_won,
        "2ndWon": second_won,
        "SvGms": svgms,
        "bpSaved": won("Service", "Break Points Saved") or 0,
        "bpFaced": total("Service", "Break Points Saved") or 0,
    }


def parse_serve_stats(f: dict, winner_key, loser_key) -> dict | None:
    """Winner/loser serve line from a finished fixture's `statistics`, in the
    same w_*/l_* schema Sackmann writes to Match.stats — so the serve/return
    profile picks these matches up with no new table or aggregator. None when
    either side lacks a usable serve line (both sides required, since the
    profile drops any match missing one side)."""
    stats = f.get("statistics")
    if not isinstance(stats, list) or not stats:
        return None
    by_player: dict[str, dict] = {}
    for s in stats:
        if (s.get("stat_period") or "").lower() != "match":
            continue
        pk = str(s.get("player_key"))
        by_player.setdefault(pk, {})[(s.get("stat_type"), s.get("stat_name"))] = s
    w = _side_serve(by_player.get(str(winner_key), {}))
    l = _side_serve(by_player.get(str(loser_key), {}))
    if w is None or l is None:
        return None
    out = {f"w_{k}": v for k, v in w.items()}
    out.update({f"l_{k}": v for k, v in l.items()})
    return out


def _norm_round(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip()
    key = s.lower()
    if key in _ROUND_MAP:
        return _ROUND_MAP[key]
    # qualifying rounds → Q1/Q2/Q3
    if key.startswith("qualif"):
        digits = "".join(c for c in s if c.isdigit())
        return f"Q{digits[:1]}" if digits else "Q"
    # already a short code, or anything else — clamp to fit varchar(8)
    return s[:8]


class ApiTennisSource(TennisDataSource):
    name = "api_tennis"

    def __init__(self) -> None:
        cfg = settings()
        self.key = cfg.api_tennis_key
        self.base = cfg.api_tennis_base
        self.horizon = timedelta(hours=cfg.schedule_horizon_hours)
        self.client = httpx.Client(timeout=60)
        self._pk_cache: dict[tuple, int] = {}  # (tour, player_key) -> our player_id

    def _get(self, method: str, **params) -> list[dict]:
        r = self.client.get(self.base, params={"method": method, "APIkey": self.key, **params})
        r.raise_for_status()
        body = r.json()
        if body.get("success") != 1:
            raise RuntimeError(f"api-tennis {method} returned {body.get('success')}: "
                               f"{str(body)[:200]}")
        result = body.get("result") or []
        return result if isinstance(result, list) else []

    def live_scores(self) -> list[dict]:
        """Normalized live singles events (for the live-score backup). Empty if
        no key configured — never raises to the caller's loop."""
        if not self.key:
            return []
        from bot.market.api_tennis_live import parse_live_singles
        try:
            return parse_live_singles(self._get("get_livescore"))
        except Exception as e:
            log.warning("api-tennis livescore fetch failed", error=str(e))
            return []

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
        # in-run cache by api-tennis player key — a player appears in many
        # fixtures across a 3-month backfill; resolve (incl. the expensive fuzzy)
        # once, not per fixture
        ck = (tour, str(api_key)) if api_key else None
        if ck and ck in self._pk_cache:
            return self._pk_cache[ck]
        if api_key:  # persisted link from a prior run
            pid = db.execute(
                select(Player.id).where(Player.tour == tour,
                                        Player.api_tennis_id == str(api_key))).scalar()
            if pid:
                self._pk_cache[ck] = pid
                return pid
        # queue_on_miss=False: backfill creates a provisional on miss, so the
        # per-miss review-queue query+insert (2 round-trips) is pure overhead
        res = matcher.match(db, raw_name, source=self.name, context=context,
                            queue_on_miss=False)
        resolved = res.player_id
        if resolved and api_key:
            db.get(Player, resolved).api_tennis_id = str(api_key)
        # Create a provisional ONLY on a genuine no-candidate miss — never on an
        # ambiguous match (method "ambiguous"), which would spawn a phantom third
        # player and misattribute every future fixture to it.
        if resolved is None and res.method == "none" and res.confidence == 0.0:
            p = Player(tour=tour, api_tennis_id=str(api_key) if api_key else None,
                       full_name=raw_name, normalized_name=normalize_name(raw_name))
            db.add(p)
            db.flush()
            resolved = p.id
        if ck and resolved:
            self._pk_cache[ck] = resolved
        return resolved

    def _tournament(self, db: Session, tour: str, fixture: dict) -> int | None:
        tkey = str(fixture.get("tournament_key") or "")
        if not tkey:
            return None
        from bot.stats.surface import resolve_surface
        name = fixture.get("tournament_name") or tkey
        # api-tennis carries no surface; resolve it by venue from Sackmann history
        surface = resolve_surface(db, name)
        stmt = pg_insert(Tournament).values(
            tour=tour, source=self.name, source_key=tkey, name=name,
            surface=surface, level=None, start_date=None,
        ).on_conflict_do_update(
            constraint="uq_tournaments_key",
            set_={"surface": surface} if surface else {},
        ) if surface else pg_insert(Tournament).values(
            tour=tour, source=self.name, source_key=tkey, name=name,
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
            winner_is_first = winner_side == "first player"
            winner_id, loser_id = (p1, p2) if winner_is_first else (p2, p1)
            winner_key, loser_key = (
                (f.get("first_player_key"), f.get("second_player_key"))
                if winner_is_first
                else (f.get("second_player_key"), f.get("first_player_key")))
            serve_stats = parse_serve_stats(f, winner_key, loser_key)
            sets, ww, wl = self._parse_api_sets(f, winner_is_first=winner_is_first)
            is_dup = self._duplicate_of_sackmann(db, tour, p1, p2, event_date)
            # best_of: a completed win to 3 sets is best-of-5 (Grand Slam men's),
            # otherwise best-of-3 — don't hardcode 3 or slams misclassify their
            # deciding set. outcome: detect ret/walkover so they aren't recorded
            # as clean completions.
            best_of = 5 if ww >= 3 else 3
            _st = f"{(f.get('event_status') or '').lower()} {(f.get('event_final_result') or '').lower()}"
            outcome = ("ret" if "ret" in _st
                       else "wo" if ("walk" in _st or "w/o" in _st) else "completed")
            from bot.stats.surface import resolve_surface
            surface = resolve_surface(db, f.get("tournament_name"))
            values = dict(
                tour=tour, source=self.name, source_key=skey, tournament_id=tid,
                winner_id=winner_id, loser_id=loser_id, match_date=event_date,
                round=_norm_round(f.get("tournament_round")), best_of=best_of,
                score_raw=(f.get("event_final_result") or None),
                outcome=outcome, sets_won_winner=ww, sets_won_loser=wl,
                surface=surface, tourney_level=None, is_duplicate=is_dup,
                scheduled_start=None, stats=serve_stats,
            )
            update_cols = {k: v for k, v in values.items()
                           if k not in ("source", "source_key")}
            # never overwrite stored serve stats with a null re-fetch
            if serve_stats is None:
                update_cols.pop("stats", None)
            stmt = pg_insert(Match).values(values).on_conflict_do_update(
                constraint="uq_matches_source_key", set_=update_cols,
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
                match_date=event_date, round=_norm_round(f.get("tournament_round")),
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

    # ---------- rankings & bios (cheap; get_players is per-player, so gated) ----------

    def sync_rankings(self, db: Session, result: SyncResult) -> None:
        """Refresh current ATP/WTA rankings from get_standings (two calls) and
        snapshot them for history. Only players already in our DB are updated —
        standings never create players (they'd have no match history)."""
        as_of = date.today()
        for league, tour in (("ATP", "atp"), ("WTA", "wta")):
            try:
                rows = self._get("get_standings", event_type=league)
            except (httpx.HTTPError, RuntimeError) as e:
                result.errors.append(f"standings {league}: {e}")
                continue
            # resolve the whole tour pool once (avoid a query per standings row):
            # by api-tennis key first, then a UNIQUE exact normalized name.
            by_key: dict[str, int] = {}
            by_norm: dict[str, list[int]] = {}
            for pid, norm, akey in db.execute(
                select(Player.id, Player.normalized_name, Player.api_tennis_id)
                .where(Player.tour == tour)
            ):
                by_norm.setdefault(norm, []).append(pid)
                if akey:
                    by_key[akey] = pid
            n = 0
            for r in rows:
                rank = _int(r.get("place"))
                if rank is None:
                    continue
                pts = _int(r.get("points"))
                pk = r.get("player_key")
                pid = by_key.get(str(pk)) if pk is not None else None
                if pid is None:  # fall back to a UNIQUE exact-name match
                    ids = by_norm.get(normalize_name(r.get("player") or ""), [])
                    if len(ids) == 1:
                        pid = ids[0]
                if pid is None:
                    continue
                db.execute(update(Player).where(Player.id == pid).values(
                    rank=rank, rank_points=pts, rank_date=as_of))
                db.execute(pg_insert(PlayerRanking).values(
                    player_id=pid, tour=tour, as_of=as_of, rank=rank, points=pts
                ).on_conflict_do_update(
                    constraint="uq_player_ranking",
                    set_={"rank": rank, "points": pts}))
                n += 1
            db.commit()
            log.info("api-tennis rankings synced", tour=tour, updated=n)

    def _active_player_ids(self, db: Session) -> list[int]:
        """Players in an upcoming/live match or an unsettled Kalshi market —
        the set worth spending a per-player get_players call on."""
        now = datetime.now(timezone.utc)
        lo, hi = now - timedelta(days=1), now + self.horizon
        ids: set[int] = set()
        for wid, lid in db.execute(
            select(Match.winner_id, Match.loser_id).where(
                Match.outcome == "scheduled",
                Match.scheduled_start.is_not(None),
                Match.scheduled_start >= lo, Match.scheduled_start <= hi)
        ):
            ids.update((wid, lid))
        for a, b in db.execute(
            select(KalshiMarket.player_a_id, KalshiMarket.player_b_id).where(
                KalshiMarket.result.is_(None))
        ):
            ids.update((a, b))
        ids.discard(None)
        return list(ids)

    def sync_bios(self, db: Session, result: SyncResult) -> None:
        """Fill dob + career surface splits for ACTIVE players via get_players
        (billed per player). Capped and staleness-gated so a run costs a few
        dozen calls at most. Handedness is not exposed by get_players — it
        stays Sackmann-sourced."""
        active = self._active_player_ids(db)
        if not active:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=BIO_REFRESH_DAYS)
        due = db.execute(
            select(Player.id, Player.api_tennis_id).where(
                Player.id.in_(active),
                Player.api_tennis_id.is_not(None),
                or_(Player.bio_synced_at.is_(None), Player.bio_synced_at < cutoff))
            .limit(BIO_MAX_PER_RUN)
        ).all()
        n = 0
        for pid, pk in due:
            try:
                rows = self._get("get_players", player_key=pk)
            except (httpx.HTTPError, RuntimeError) as e:
                result.errors.append(f"players {pk}: {e}")
                continue
            vals = {"bio_synced_at": datetime.now(timezone.utc)}
            if rows:
                p = rows[0]
                dob = _parse_bday(p.get("player_bday"))
                if dob is not None:
                    vals["dob"] = dob
                surf = _parse_surface_stats(p.get("stats"))
                if surf:
                    vals["surface_stats"] = surf
            db.execute(update(Player).where(Player.id == pid).values(**vals))
            db.commit()
            n += 1
        log.info("api-tennis bios synced", players=n, due=len(due), active=len(active))

    # ---------- entry point ----------

    def sync(self, db: Session, *, full: bool = False) -> SyncResult:
        result = SyncResult(source=self.name)
        if not self.key:
            # Not an error until the live source is activated (Phase 5.5):
            # the daily cron must exit 0 on a Sackmann-only sync.
            log.warning("API_TENNIS_KEY not set — skipping live-results sync")
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
                    db.commit()  # per-fixture: no long transaction for Neon to drop
                except Exception as e:
                    db.rollback()
                    result.errors.append(f"event {f.get('event_key')}: {e}")
            log.info("api-tennis window done", start=str(cur), stop=str(win_end),
                     matches=result.matches_upserted)
            cur = win_end + timedelta(days=1)

        # rankings (2 calls) and, for active players only, bios/surface splits
        try:
            self.sync_rankings(db, result)
        except Exception as e:
            db.rollback()
            result.errors.append(f"rankings: {e}")
        try:
            self.sync_bios(db, result)
        except Exception as e:
            db.rollback()
            result.errors.append(f"bios: {e}")

        stmt = pg_insert(IngestState).values(
            key="api_tennis:last_sync", value=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc),
        ).on_conflict_do_update(index_elements=["key"], set_={
            "value": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc)})
        db.execute(stmt)
        return result
