"""ChartingSource — Jeff Sackmann's Match Charting Project (still public).

Shot-by-shot hand-charted data: winners, unforced errors, FH/BH splits — depth
no commercial feed sells. We ingest the match index + the Overview 'Total' rows
(per-player aggregates); the 30-56 MB point-by-point files are skipped (not
needed for player profiles). Names are matched to our players table; unmatched
rows are still stored (player_id NULL) so nothing is lost.

Data © Tennis Abstract Match Charting Project (CC BY-NC-SA 4.0) — attribution
required, non-commercial, personal research only. READ-ONLY fetch.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from bot.log import get_logger
from bot.matching.market_matcher import PlayerMatcher
from bot.models import ChartingStat, IngestState
from bot.sources.base import SyncResult, TennisDataSource

log = get_logger("sources.charting")

REPO = "JeffSackmann/tennis_MatchChartingProject"
RAW = f"https://raw.githubusercontent.com/{REPO}/master"
STAT_COLS = ["serve_pts", "aces", "dfs", "first_in", "first_won", "second_in",
             "second_won", "bk_pts", "bp_saved", "return_pts", "return_pts_won",
             "winners", "winners_fh", "winners_bh", "unforced", "unforced_fh",
             "unforced_bh"]


def _int(v) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


def _mdate(v: str) -> date | None:
    v = (v or "").strip()
    if len(v) == 8 and v.isdigit():
        try:
            return date(int(v[:4]), int(v[4:6]), int(v[6:8]))
        except ValueError:
            return None
    return None


class ChartingSource(TennisDataSource):
    name = "charting"

    def __init__(self) -> None:
        self.client = httpx.Client(timeout=180, follow_redirects=True)

    def _blob_sha(self, path: str) -> str | None:
        try:
            r = self.client.get(
                f"https://api.github.com/repos/{REPO}/contents/{path}")
            return r.json().get("sha") if r.status_code == 200 else None
        except httpx.HTTPError:
            return None

    def _csv(self, fname: str) -> list[dict]:
        r = self.client.get(f"{RAW}/{fname}")
        r.raise_for_status()
        return list(csv.DictReader(io.StringIO(r.text)))

    def sync(self, db: Session, *, full: bool = False) -> SyncResult:
        result = SyncResult(source=self.name)
        for gender, tour in (("m", "atp"), ("w", "wta")):
            ov_file = f"charting-{gender}-stats-Overview.csv"
            wm_key = f"charting:{gender}:overview"
            sha = self._blob_sha(ov_file)
            if not full and sha and db.get(IngestState, wm_key) \
                    and db.get(IngestState, wm_key).value == sha:
                result.skipped_files += 1
                continue
            try:
                self._sync_gender(db, gender, tour, ov_file, result)
            except Exception as e:
                result.errors.append(f"{gender}: {e}")
                log.error("charting sync failed", gender=gender, error=str(e))
                continue
            if sha:
                db.execute(pg_insert(IngestState).values(
                    key=wm_key, value=sha, updated_at=datetime.now(timezone.utc))
                    .on_conflict_do_update(index_elements=["key"],
                    set_={"value": sha, "updated_at": datetime.now(timezone.utc)}))
            db.commit()
        return result

    def _sync_gender(self, db: Session, gender: str, tour: str, ov_file: str,
                     result: SyncResult) -> None:
        # match metadata (date, tournament, surface) keyed by match_id
        meta: dict[str, dict] = {}
        for r in self._csv(f"charting-{gender}-matches.csv"):
            mid = r.get("match_id")
            if mid:
                meta[mid] = {"date": _mdate(r.get("Date")),
                             "tournament": (r.get("Tournament") or "").strip() or None,
                             "surface": (r.get("Surface") or "").strip() or None}

        matcher = PlayerMatcher(db, tour)
        name_cache: dict[str, int | None] = {}

        def match_name(name: str) -> int | None:
            if name not in name_cache:
                res = matcher.match(db, name, source="charting",
                                    queue_on_miss=False)
                name_cache[name] = res.player_id
            return name_cache[name]

        rows = self._csv(ov_file)
        batch: list[dict] = []
        for r in rows:
            if (r.get("set") or "").strip() != "Total":  # aggregate rows only
                continue
            mid, name = r.get("match_id"), (r.get("player") or "").strip()
            if not mid or not name:
                continue
            m = meta.get(mid, {})
            rec = {"match_id": mid, "tour": tour, "player_name": name,
                   "player_id": match_name(name), "match_date": m.get("date"),
                   "tournament": m.get("tournament"), "surface": m.get("surface")}
            for c in STAT_COLS:
                rec[c] = _int(r.get(c))
            batch.append(rec)

        for i in range(0, len(batch), 1000):
            chunk = batch[i:i + 1000]
            stmt = pg_insert(ChartingStat).values(chunk).on_conflict_do_update(
                constraint="uq_charting_match_player",
                set_={c: getattr(pg_insert(ChartingStat).excluded, c)
                      for c in ["player_id", "match_date", "tournament", "surface",
                                *STAT_COLS]})
            db.execute(stmt)
            db.commit()
        matched = sum(1 for b in batch if b["player_id"] is not None)
        result.matches_upserted += len(batch)
        log.info("charting ingested", tour=tour, player_lines=len(batch),
                 matched=matched)
