"""SackmannDataSource — historical backfill from GitHub mirrors of the
JeffSackmann/tennis_atp and tennis_wta datasets.

License: CC BY-NC-SA 4.0 — attribution to Jeff Sackmann / Tennis Abstract,
non-commercial, personal research only. Never redistribute the data.

Incremental strategy: GitHub tree API gives a blob SHA per file; each file is
re-downloaded and re-upserted only when its blob SHA changed since the last sync
(watermarks in ingest_state). `full=True` ignores watermarks.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timezone

import httpx
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from bot.config import settings
from bot.ingest.score_parser import parse_score
from bot.log import get_logger
from bot.matching.market_matcher import normalize_name
from bot.models import IngestState, Match, MatchSet, Player, Tournament
from bot.sources.base import SyncResult, TennisDataSource

log = get_logger("sources.sackmann")

STAT_COLS = [
    "w_ace", "w_df", "w_svpt", "w_1stIn", "w_1stWon", "w_2ndWon", "w_SvGms", "w_bpSaved",
    "w_bpFaced", "l_ace", "l_df", "l_svpt", "l_1stIn", "l_1stWon", "l_2ndWon", "l_SvGms",
    "l_bpSaved", "l_bpFaced", "winner_rank", "winner_rank_points", "loser_rank",
    "loser_rank_points",
]
SKIP_LEVELS = {"E", "J"}  # exhibitions, juniors


def _int(v: str | None) -> int | None:
    try:
        return int(float(v)) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


def _yyyymmdd(v: str | None) -> date | None:
    v = (v or "").strip()
    if len(v) == 8 and v.isdigit():
        try:
            return date(int(v[:4]), int(v[4:6]), int(v[6:8]))
        except ValueError:
            return None
    return None


class SackmannDataSource(TennisDataSource):
    name = "sackmann"

    def __init__(self) -> None:
        cfg = settings()
        self.repos = {"atp": cfg.sackmann_atp_repo, "wta": cfg.sackmann_wta_repo}
        self.start_year = cfg.backfill_start_year
        self.client = httpx.Client(timeout=120, follow_redirects=True)

    # ---------- GitHub helpers ----------

    def _tree(self, repo: str) -> dict[str, str]:
        """filename -> blob sha for the repo's default branch."""
        r = self.client.get(f"https://api.github.com/repos/{repo}/git/trees/master")
        r.raise_for_status()
        return {e["path"]: e["sha"] for e in r.json()["tree"] if e["type"] == "blob"}

    def _fetch_csv(self, repo: str, path: str) -> list[dict]:
        r = self.client.get(f"https://raw.githubusercontent.com/{repo}/master/{path}")
        r.raise_for_status()
        return list(csv.DictReader(io.StringIO(r.text)))

    def _wanted_files(self, tour: str) -> list[str]:
        years = range(self.start_year, date.today().year + 1)
        if tour == "atp":
            patterns = ["atp_matches_{y}.csv", "atp_matches_qual_chall_{y}.csv",
                        "atp_matches_futures_{y}.csv"]
        else:
            patterns = ["wta_matches_{y}.csv", "wta_matches_qual_itf_{y}.csv"]
        return [p.format(y=y) for y in years for p in patterns]

    # ---------- watermarks ----------

    @staticmethod
    def _get_watermark(db: Session, key: str) -> str | None:
        row = db.get(IngestState, key)
        return row.value if row else None

    @staticmethod
    def _set_watermark(db: Session, key: str, value: str) -> None:
        stmt = pg_insert(IngestState).values(
            key=key, value=value, updated_at=datetime.now(timezone.utc)
        ).on_conflict_do_update(
            index_elements=["key"],
            set_={"value": value, "updated_at": datetime.now(timezone.utc)},
        )
        db.execute(stmt)

    # ---------- players ----------

    def _sync_players(self, db: Session, tour: str, repo: str, tree: dict[str, str],
                      result: SyncResult, full: bool) -> None:
        fname = f"{tour}_players.csv"
        if fname not in tree:
            result.errors.append(f"{repo}: missing {fname}")
            return
        wm_key = f"sackmann:{tour}:{fname}"
        if not full and self._get_watermark(db, wm_key) == tree[fname]:
            result.skipped_files += 1
            return
        rows = self._fetch_csv(repo, fname)
        by_id: dict[int, dict] = {}
        for r in rows:
            pid = _int(r.get("player_id"))
            if pid is None:
                continue
            first = (r.get("name_first") or "").strip()
            last = (r.get("name_last") or "").strip()
            full_name = f"{first} {last}".strip()
            if not full_name:
                continue
            by_id[pid] = dict(
                tour=tour, sackmann_id=pid, first_name=first or None, last_name=last or None,
                full_name=full_name, normalized_name=normalize_name(full_name),
                hand=(r.get("hand") or None), dob=_yyyymmdd(r.get("dob")),
                ioc=(r.get("ioc") or None), height_cm=_int(r.get("height")),
            )
        batch = list(by_id.values())
        for chunk in _chunks(batch, 2000):
            stmt = pg_insert(Player).values(chunk).on_conflict_do_update(
                constraint="uq_players_tour_sackmann",
                set_={c: getattr(pg_insert(Player).excluded, c)
                      for c in ("first_name", "last_name", "full_name", "normalized_name",
                                "hand", "dob", "ioc", "height_cm")},
            )
            db.execute(stmt)
        result.players_upserted += len(batch)
        self._set_watermark(db, wm_key, tree[fname])
        log.info("players synced", tour=tour, count=len(batch))

    # ---------- matches ----------

    def _player_id_map(self, db: Session, tour: str) -> dict[int, int]:
        rows = db.execute(
            select(Player.sackmann_id, Player.id).where(
                Player.tour == tour, Player.sackmann_id.is_not(None))
        ).all()
        return dict(rows)

    def _ensure_player(self, db: Session, tour: str, sackmann_id: int, name: str,
                       pmap: dict[int, int]) -> int:
        if sackmann_id in pmap:
            return pmap[sackmann_id]
        p = Player(tour=tour, sackmann_id=sackmann_id, full_name=name or f"unknown-{sackmann_id}",
                   normalized_name=normalize_name(name or str(sackmann_id)))
        db.add(p)
        db.flush()
        pmap[sackmann_id] = p.id
        return p.id

    def _sync_matches_file(self, db: Session, tour: str, repo: str, fname: str,
                           pmap: dict[int, int], result: SyncResult) -> None:
        rows = self._fetch_csv(repo, fname)
        # upsert tournaments for this file first
        tkeys: dict[str, dict] = {}
        for r in rows:
            tid = r.get("tourney_id") or ""
            if tid and tid not in tkeys:
                tkeys[tid] = dict(
                    tour=tour, source=self.name, source_key=tid,
                    name=(r.get("tourney_name") or tid), surface=(r.get("surface") or None),
                    level=(r.get("tourney_level") or None), draw_size=_int(r.get("draw_size")),
                    start_date=_yyyymmdd(r.get("tourney_date")),
                )
        if tkeys:
            for chunk in _chunks(list(tkeys.values()), 1000):
                stmt = pg_insert(Tournament).values(chunk).on_conflict_do_update(
                    constraint="uq_tournaments_key",
                    set_={"name": pg_insert(Tournament).excluded.name,
                          "surface": pg_insert(Tournament).excluded.surface,
                          "start_date": pg_insert(Tournament).excluded.start_date},
                )
                db.execute(stmt)
            result.tournaments_upserted += len(tkeys)
        tmap = dict(db.execute(
            select(Tournament.source_key, Tournament.id).where(
                Tournament.tour == tour, Tournament.source == self.name,
                Tournament.source_key.in_(list(tkeys)))
        ).all())

        # keyed by source_key: ITF files occasionally repeat (tourney_id, match_num);
        # last row wins, and one statement must never touch the same key twice
        match_values_by_key: dict[str, dict] = {}
        parsed_by_key: dict[str, object] = {}
        for r in rows:
            level = (r.get("tourney_level") or "").strip()
            if level in SKIP_LEVELS:
                continue
            wid, lid = _int(r.get("winner_id")), _int(r.get("loser_id"))
            tid = r.get("tourney_id") or ""
            mnum = r.get("match_num") or ""
            if wid is None or lid is None or not tid:
                continue
            skey = f"{tour}:{tid}:{mnum}"
            parsed = parse_score(r.get("score"), _int(r.get("best_of")))
            parsed_by_key[skey] = parsed
            stats = {c: _int(r.get(c)) for c in STAT_COLS}
            match_values_by_key[skey] = dict(
                tour=tour, source=self.name, source_key=skey,
                tournament_id=tmap.get(tid),
                winner_id=self._ensure_player(db, tour, wid, r.get("winner_name") or "", pmap),
                loser_id=self._ensure_player(db, tour, lid, r.get("loser_name") or "", pmap),
                match_date=_yyyymmdd(r.get("tourney_date")),
                round=(r.get("round") or None), best_of=_int(r.get("best_of")),
                score_raw=(r.get("score") or None), outcome=parsed.outcome,
                sets_won_winner=parsed.sets_won_winner, sets_won_loser=parsed.sets_won_loser,
                minutes=_int(r.get("minutes")), surface=(r.get("surface") or None),
                tourney_level=level or None,
                stats={k: v for k, v in stats.items() if v is not None} or None,
            )

        match_values = list(match_values_by_key.values())
        id_by_key: dict[str, int] = {}
        for chunk in _chunks(match_values, 1000):
            stmt = pg_insert(Match).values(chunk).on_conflict_do_update(
                constraint="uq_matches_source_key",
                set_={c: getattr(pg_insert(Match).excluded, c)
                      for c in ("tournament_id", "winner_id", "loser_id", "match_date", "round",
                                "best_of", "score_raw", "outcome", "sets_won_winner",
                                "sets_won_loser", "minutes", "surface", "tourney_level", "stats")},
            ).returning(Match.source_key, Match.id)
            id_by_key.update(dict(db.execute(stmt).all()))

        # rewrite set rows for every upserted match (idempotent)
        all_ids = list(id_by_key.values())
        for chunk in _chunks(all_ids, 5000):
            db.execute(delete(MatchSet).where(MatchSet.match_id.in_(chunk)))
        set_values = []
        for skey, mid in id_by_key.items():
            parsed = parsed_by_key.get(skey)
            if not parsed:
                continue
            for s in parsed.sets:
                set_values.append(dict(
                    match_id=mid, set_number=s.set_number, winner_games=s.winner_games,
                    loser_games=s.loser_games, set_won_by_match_winner=s.set_won_by_match_winner,
                    tiebreak=s.tiebreak, tiebreak_loser_points=s.tiebreak_loser_points,
                    is_match_tiebreak=s.is_match_tiebreak, completed=s.completed,
                ))
        for chunk in _chunks(set_values, 5000):
            db.execute(pg_insert(MatchSet).values(chunk))
        result.matches_upserted += len(match_values)
        result.sets_written += len(set_values)
        log.info("file ingested", file=fname, matches=len(match_values), sets=len(set_values))

    # ---------- entry point ----------

    def sync(self, db: Session, *, full: bool = False) -> SyncResult:
        result = SyncResult(source=self.name)
        for tour, repo in self.repos.items():
            try:
                tree = self._tree(repo)
            except httpx.HTTPError as e:
                result.errors.append(f"{repo}: tree fetch failed: {e}")
                continue
            self._sync_players(db, tour, repo, tree, result, full)
            db.commit()
            pmap = self._player_id_map(db, tour)
            for fname in self._wanted_files(tour):
                if fname not in tree:
                    log.warning("file missing in repo", repo=repo, file=fname)
                    continue
                wm_key = f"sackmann:{tour}:{fname}"
                if not full and self._get_watermark(db, wm_key) == tree[fname]:
                    result.skipped_files += 1
                    continue
                before = set(pmap)  # players known before this file
                try:
                    self._sync_matches_file(db, tour, repo, fname, pmap, result)
                    self._set_watermark(db, wm_key, tree[fname])
                    db.commit()
                except Exception as e:  # keep going; one bad file must not kill the run
                    db.rollback()
                    # rollback discarded any players _ensure_player flushed for this
                    # file — evict their now-dangling ids from pmap so a later file
                    # re-creates them instead of referencing a non-existent row
                    for k in set(pmap) - before:
                        pmap.pop(k, None)
                    result.errors.append(f"{fname}: {e}")
                    log.error("file ingest failed", file=fname, error=str(e))
        return result


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
