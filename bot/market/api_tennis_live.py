"""api-tennis live-score BACKUP.

Kalshi's milestone feed scores live matches game-by-game mainly for ATP/WTA; most
ITF (and some Challenger) live matches come back with no in-match score. This
fills match_score_log for those from api-tennis's get_livescore, so the live
board's score grid isn't blank on the lower tiers.

Orientation (which side is the market's YES player) is resolved ONLY via the
api_tennis_id → player_id mapping — never fuzzy names — so a mis-map can't flip a
scoreline; an unresolved match is simply skipped. Rows are tagged
detail.src='api_tennis' and never override a fresh Kalshi-sourced score.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from bot.log import get_logger
from bot.models import KalshiMarket, MatchScoreLog, Player

log = get_logger("market.api_tennis_live")


def parse_live_singles(result: list[dict]) -> list[dict]:
    """api-tennis get_livescore rows → normalized singles events (doubles skipped)."""
    out = []
    for e in result or []:
        if "singles" not in (e.get("event_type_type") or "").lower():
            continue
        fk, sk = e.get("first_player_key"), e.get("second_player_key")
        if not fk or not sk:
            continue
        per = []
        for s in e.get("scores") or []:
            try:  # "6", "6.4" (tiebreak) → games only
                g1 = int(str(s.get("score_first")).split(".")[0])
                g2 = int(str(s.get("score_second")).split(".")[0])
            except (TypeError, ValueError):
                continue
            per.append((g1, g2))
        fr = (e.get("event_final_result") or "").replace(" ", "").split("-")
        try:
            sf, ss = int(fr[0]), int(fr[1])
        except (IndexError, ValueError):
            sf = sum(1 for a, b in per if a > b)
            ss = sum(1 for a, b in per if b > a)
        status = (e.get("event_status") or "").lower()
        is_final = any(w in status for w in ("finish", "ended", "complete",
                                             "ret", "walk", "w.o", "aband"))
        out.append({"fk": str(fk), "sk": str(sk), "per": per,
                    "sf": sf, "ss": ss, "is_final": is_final})
    return out


def _sb(e: dict, first_is_a: bool) -> dict | None:
    """MatchScoreLog fields in the A(=YES) player's perspective."""
    per = e["per"] if first_is_a else [(b, a) for a, b in e["per"]]
    if not per:
        return None
    ga, gb = per[-1]
    return {
        "scoreline": (" ".join(f"{a}-{b}" for a, b in per))[:96],
        "sets_a": e["sf"] if first_is_a else e["ss"],
        "sets_b": e["ss"] if first_is_a else e["sf"],
        "set_number": len(per),
        "games_a": ga, "games_b": gb,
        "total_games": sum(a + b for a, b in per),
        "is_final": e["is_final"],
    }


def backfill(db: Session, events: list[dict], watched_events: set[str]) -> int:
    """Write api-tennis score rows for watched live events that lack a fresh
    Kalshi score. Returns rows written."""
    if not events or not watched_events:
        return 0
    keys = {e["fk"] for e in events} | {e["sk"] for e in events}
    idmap = dict(db.execute(
        select(Player.api_tennis_id, Player.id)
        .where(Player.api_tennis_id.in_(list(keys)))).all())
    by_pair: dict[frozenset, tuple] = {}
    for e in events:
        a, b = idmap.get(e["fk"]), idmap.get(e["sk"])
        if a and b:
            by_pair[frozenset((a, b))] = (e, a)  # a = our id of api-tennis "first"

    written = 0
    for evt in watched_events:
        sides = db.execute(select(KalshiMarket).where(
            KalshiMarket.event_ticker == evt,
            KalshiMarket.player_a_id.is_not(None))
            .order_by(KalshiMarket.ticker)).scalars().all()
        if len(sides) < 2:
            continue
        A, B = sides[0], sides[1]
        hit = by_pair.get(frozenset((A.player_a_id, B.player_a_id)))
        if not hit:
            continue
        e, first_id = hit
        # never override a live KALSHI-sourced score (only fill gaps)
        fresh_kalshi = db.execute(text(
            "SELECT 1 FROM match_score_log WHERE market_ticker = :t "
            "AND ts > now() - interval '5 minutes' "
            "AND (detail->>'src') IS DISTINCT FROM 'api_tennis' LIMIT 1"),
            {"t": A.ticker}).first()
        if fresh_kalshi:
            continue
        sb = _sb(e, first_is_a=(first_id == A.player_a_id))
        if sb is None:
            continue
        last = db.execute(select(MatchScoreLog).where(
            MatchScoreLog.market_ticker == A.ticker)
            .order_by(MatchScoreLog.ts.desc()).limit(1)).scalar()
        if (last and last.total_games == sb["total_games"]
                and last.is_final == sb["is_final"]
                and last.sets_a == sb["sets_a"] and last.sets_b == sb["sets_b"]):
            continue  # unchanged since last poll
        db.add(MatchScoreLog(market_ticker=A.ticker, event_ticker=evt,
                             ts=datetime.now(timezone.utc),
                             detail={"src": "api_tennis"}, **sb))
        written += 1
    if written:
        db.commit()
        log.info("api-tennis score backfill", rows=written)
    return written
