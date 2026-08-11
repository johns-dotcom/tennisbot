"""KalshiResultsSource — completed match results mined from Kalshi itself.

The Sackmann mirrors froze mid-2026 and the api-tennis feed isn't active yet,
so Kalshi's own settled tennis markets fill the gap: the settled side tells us
who won; the event's milestone live-data gives per-set games for match_sets.
READ-ONLY, like every Kalshi touchpoint (CLAUDE.md rule 1).

Coverage caveat (surfaced on /flags): only matches Kalshi listed — tour-level
is near-complete, ITF partial; surface is unknown; occasional matches lack
usable set detail. Rows carry source='kalshi'.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bot.log import get_logger
from bot.market.kalshi import TENNIS_SERIES, KalshiClient
from bot.matching.market_matcher import PlayerMatcher, normalize_name
from bot.models import Match, MatchSet, Player, Tournament
from bot.sources.base import SyncResult, TennisDataSource

log = get_logger("sources.kalshi_results")

SERIES_LEVEL = {"KXATPMATCH": "A", "KXWTAMATCH": "A", "KXWTAGAME": "A",
                "KXATPCHALLENGERMATCH": "C", "KXITFMATCH": "15", "KXITFWMATCH": "15"}
BACKFILL_START = date(2026, 4, 20)  # WTA mirror ends 2026-04-27; overlap is deduped


def parse_kalshi_sets(details: dict, winner_is_c1: bool) -> tuple[list[dict], int, int, str]:
    """(set rows, sets_won_winner, sets_won_loser, outcome) from live_data
    details' round_scores. Winner-first convention like the Sackmann parser."""
    r1 = details.get("competitor1_round_scores") or []
    r2 = details.get("competitor2_round_scores") or []
    sets, ww, wl = [], 0, 0
    for i, (a, b) in enumerate(zip(r1, r2), start=1):
        try:
            g1, g2 = int(a.get("score")), int(b.get("score"))
        except (TypeError, ValueError):
            continue
        wg, lg = (g1, g2) if winner_is_c1 else (g2, g1)
        if wg == lg:
            continue  # abandoned mid-set fragment
        won = wg > lg
        ww += won
        wl += not won
        tb1, tb2 = a.get("tiebreak_score"), b.get("tiebreak_score")
        tb = tb1 is not None or tb2 is not None
        tb_loser = None
        if tb:
            try:
                pts = sorted(int(x) for x in (tb1, tb2) if x is not None)
                tb_loser = pts[0] if pts else None
            except (TypeError, ValueError):
                tb_loser = None
        sets.append(dict(set_number=i, winner_games=wg, loser_games=lg,
                         set_won_by_match_winner=won, tiebreak=tb,
                         tiebreak_loser_points=tb_loser, completed=True))
    status = (details.get("status") or "").lower()
    outcome = "ret" if "retire" in status else \
        ("wo" if "walk" in status else "completed")
    return sets, ww, wl, outcome


def winner_is_competitor1(details: dict, winner_surname: str,
                          event_title: str) -> bool | None:
    """Map the settled winner onto competitor1/2. Primary: sets majority.
    Fallback: first-named player in the event title is competitor1."""
    try:
        c1 = int(details.get("competitor1_overall_score"))
        c2 = int(details.get("competitor2_overall_score"))
        if c1 != c2:
            first_won = c1 > c2
            # sets majority == match winner except retirements; combine below
            status = (details.get("status") or "").lower()
            if "retire" not in status:
                return first_won
    except (TypeError, ValueError):
        pass
    if " vs " in (event_title or ""):
        first = event_title.split(" vs ")[0].strip().split()[-1]
        if first and winner_surname:
            return winner_surname.lower().startswith(first.lower()[:3]) or \
                first.lower().startswith(winner_surname.lower()[:3])
    return None


class KalshiResultsSource(TennisDataSource):
    name = "kalshi"

    def __init__(self) -> None:
        self.client = KalshiClient()

    def _settled_markets(self, series: str, min_close: datetime) -> list[dict]:
        out, cursor = [], None
        for _ in range(40):
            params = {"series_ticker": series, "status": "settled", "limit": 200,
                      "min_close_ts": int(min_close.timestamp())}
            if cursor:
                params["cursor"] = cursor
            try:
                d = self.client._get("/markets", **params)
            except Exception as e:
                log.warning("settled fetch failed", series=series, error=str(e))
                time.sleep(2)
                continue
            out.extend(d.get("markets", []))
            cursor = d.get("cursor")
            if not cursor:
                break
        return out

    def sync(self, db: Session, *, full: bool = False) -> SyncResult:
        result = SyncResult(source=self.name)
        start = datetime.combine(BACKFILL_START, datetime.min.time(),
                                 tzinfo=timezone.utc)
        if not full:
            latest = db.execute(select(func.max(Match.match_date)).where(
                Match.source == self.name)).scalar()
            if latest:
                start = datetime.combine(latest - timedelta(days=3),
                                         datetime.min.time(), tzinfo=timezone.utc)

        existing = set(db.execute(select(Match.source_key).where(
            Match.source == self.name)).scalars().all())
        matchers = {t: PlayerMatcher(db, t) for t in ("atp", "wta")}
        db.commit()  # no open transaction across the crawl

        for series, tour in TENNIS_SERIES.items():
            markets = self._settled_markets(series, start)
            events: dict[str, list[dict]] = {}
            for m in markets:
                events.setdefault(m.get("event_ticker"), []).append(m)
            log.info("settled events fetched", series=series, events=len(events))
            for ev_ticker, sides in events.items():
                skey = f"{tour}:kalshi:{ev_ticker}"
                # full=True re-ingests already-seen events so a row first written
                # during a degraded window (null sets) gets repaired; incremental
                # skips them.
                if not ev_ticker or (skey in existing and not full):
                    continue
                try:
                    n = self._ingest_event(db, matchers[tour], tour, series,
                                           ev_ticker, sides, skey)
                    if n:
                        result.matches_upserted += 1
                        result.sets_written += n - 1
                        db.commit()
                except Exception as e:
                    db.rollback()
                    result.errors.append(f"{ev_ticker}: {e}")
                time.sleep(0.15)  # polite pacing: ~2 API calls per event
        return result

    def _ingest_event(self, db: Session, matcher: PlayerMatcher, tour: str,
                      series: str, ev_ticker: str, sides: list[dict],
                      skey: str) -> int:
        """Returns 1 + number of set rows written, 0 if skipped."""
        winner_m = next((m for m in sides if (m.get("result") or "") == "yes"), None)
        loser_m = next((m for m in sides if (m.get("result") or "") == "no"), None)
        if winner_m is None or loser_m is None:
            return 0  # void / unsettled pair
        w_name = (winner_m.get("yes_sub_title") or "").strip()
        l_name = (loser_m.get("yes_sub_title") or "").strip()
        if not w_name or not l_name:
            return 0

        def player_id(name: str) -> int | None:
            res = matcher.match(db, name, source="kalshi_results",
                                context={"event": ev_ticker})
            if res.player_id is not None:
                return res.player_id
            p = Player(tour=tour, full_name=name,
                       normalized_name=normalize_name(name))
            db.add(p)
            db.flush()
            return p.id

        # --- all network I/O FIRST, before any DB transaction is opened ---
        # milestone → date, tournament, best_of, per-set score. Holding a
        # connection open across these HTTP calls is what let Neon drop it
        # mid-transaction (#9); do the slow work while nothing is checked out.
        ms = self.client.milestones_for_event(ev_ticker)
        details, mdate, tname, best_of = {}, None, None, 3
        if ms:
            det = ms[0].get("details") or {}
            tname = det.get("tournament_name")
            best_of = int(det.get("best_of") or 3)
            sd = ms[0].get("start_date")
            if sd:
                mdate = datetime.fromisoformat(sd.replace("Z", "+00:00")).date()
            try:
                details = (self.client.live_data(ms[0]["id"]) or {}).get("details") or {}
            except Exception:
                details = {}
        if mdate is None:
            ct = winner_m.get("close_time")
            mdate = datetime.fromisoformat(ct.replace("Z", "+00:00")).date() \
                if ct else date.today()

        w_surname = w_name.split()[-1]
        is_c1 = winner_is_competitor1(
            details, w_surname,
            (winner_m.get("title") or "").replace("Will ", "").split(" win")[0])
        sets, ww, wl, outcome = ([], None, None, "completed") if is_c1 is None \
            else parse_kalshi_sets(details, is_c1)

        # --- now the DB writes, in one short burst (no network in between) ---
        wid, lid = player_id(w_name), player_id(l_name)
        tid = None
        if tname:
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            db.execute(pg_insert(Tournament).values(
                tour=tour, source=self.name, source_key=tname[:64], name=tname,
                surface=None, level=SERIES_LEVEL.get(series),
                start_date=mdate).on_conflict_do_nothing(
                constraint="uq_tournaments_key"))
            tid = db.execute(select(Tournament.id).where(
                Tournament.tour == tour, Tournament.source == self.name,
                Tournament.source_key == tname[:64])).scalar()

        match = Match(
            tour=tour, source=self.name, source_key=skey, tournament_id=tid,
            winner_id=wid, loser_id=lid, match_date=mdate, best_of=best_of,
            outcome=outcome, sets_won_winner=ww, sets_won_loser=wl,
            surface=None, tourney_level=SERIES_LEVEL.get(series),
            score_raw=None, is_duplicate=False)
        db.add(match)
        db.flush()
        for s in sets:
            db.add(MatchSet(match_id=match.id, **s))
        return 1 + len(sets)
