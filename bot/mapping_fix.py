"""YES↔competitor orientation reconciliation (task #14).

The live scoreboard and the settled-results ingest both guess which market
competitor is the YES side from a surname-prefix heuristic, which flips on
shared prefixes / title-vs-payload ordering (~6% of settled markets). But once
a market SETTLES, Kalshi's `result` ('yes'/'no') is authoritative for whether
the YES-side player won. So rather than perfect the guess, we self-heal from
`result`:

  * match_score_log.sets_a is the YES player's set count. If the final row says
    the YES player won (sets_a > sets_b) but the market settled 'no' (or vice
    versa), every row for that ticker is flipped.
  * the settled kalshi Match's winner must be the YES player iff result=='yes';
    if not, winner/loser (and the per-set scores) are swapped.

Run once as a backfill, and per-ticker from the settlement loop so new flips
correct themselves the moment the truth is known. Advisory-only: touches only
recorded history, never any order.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from bot.log import get_logger
from bot.models import KalshiMarket, Match, MatchScoreLog, MatchSet
from bot.reports import check_mapping

log = get_logger("mapping_fix")


def flip_scoreline(s: str) -> str:
    """'6-3 4-6 7-6(4)' -> '3-6 6-4 6-7(4)' (per-segment, tiebreak preserved)."""
    out = []
    for seg in s.split():
        head, paren, tb = seg.partition("(")
        if "-" not in head:
            out.append(seg)
            continue
        g1, _, g2 = head.partition("-")
        out.append(f"{g2}-{g1}" + (f"({tb}" if paren else ""))
    return " ".join(out)


def _flip_score_log(db: Session, ticker: str) -> int:
    rows = db.execute(select(MatchScoreLog).where(
        MatchScoreLog.market_ticker == ticker)).scalars().all()
    for r in rows:
        r.sets_a, r.sets_b = r.sets_b, r.sets_a
        r.games_a, r.games_b = r.games_b, r.games_a
        r.scoreline = flip_scoreline(r.scoreline)
    return len(rows)


def _fix_settled_match(db: Session, mkt: KalshiMarket) -> bool:
    """Ensure the kalshi Match winner is the YES player iff result=='yes'.
    Returns True if it swapped anything."""
    if mkt.result not in ("yes", "no") or mkt.player_a_id is None \
            or mkt.player_b_id is None:
        return False
    match = None
    if mkt.match_id is not None:
        match = db.get(Match, mkt.match_id)
    if match is None or match.source != "kalshi":
        return False
    if match.winner_id is None or match.loser_id is None:
        return False
    yes_pid, no_pid = mkt.player_a_id, mkt.player_b_id
    expect_winner = yes_pid if mkt.result == "yes" else no_pid
    if match.winner_id == expect_winner:
        return False  # already correct
    # only swap if the current winner/loser are exactly this market's two players
    if {match.winner_id, match.loser_id} != {yes_pid, no_pid}:
        return False
    match.winner_id, match.loser_id = match.loser_id, match.winner_id
    match.sets_won_winner, match.sets_won_loser = \
        match.sets_won_loser, match.sets_won_winner
    for s in db.execute(select(MatchSet).where(
            MatchSet.match_id == match.id)).scalars():
        s.winner_games, s.loser_games = s.loser_games, s.winner_games
        if s.set_won_by_match_winner is not None:
            s.set_won_by_match_winner = not s.set_won_by_match_winner
    return True


def reconcile_settled_orientation(db: Session, ticker: str | None = None,
                                  dry_run: bool = False) -> dict:
    """Flip any settled market whose recorded orientation disagrees with the
    authoritative `result`. Scopes to one ticker (settlement-time self-heal) or
    all settled markets (backfill). Commits unless dry_run."""
    q = select(KalshiMarket).where(KalshiMarket.result.in_(("yes", "no")))
    if ticker is not None:
        q = q.where(KalshiMarket.ticker == ticker)
    markets = db.execute(q).scalars().all()

    log_flipped = match_flipped = checked = 0
    flipped_tickers: list[str] = []
    for mkt in markets:
        # latest score-log row decides current orientation
        latest = db.execute(
            select(MatchScoreLog).where(MatchScoreLog.market_ticker == mkt.ticker)
            .order_by(MatchScoreLog.ts.desc()).limit(1)).scalar()
        if latest is not None:
            checked += 1
            if check_mapping(latest.sets_a, latest.sets_b, mkt.result) == "mismatch":
                flipped_tickers.append(mkt.ticker)
                if not dry_run:
                    _flip_score_log(db, mkt.ticker)
                log_flipped += 1
        if not dry_run and _fix_settled_match(db, mkt):
            match_flipped += 1
        elif dry_run and _would_fix_match(db, mkt):
            match_flipped += 1

    if not dry_run:
        db.commit()
    return {"markets": len(markets), "score_logs_checked": checked,
            "score_logs_flipped": log_flipped, "matches_flipped": match_flipped,
            "flipped_tickers": flipped_tickers}


def _would_fix_match(db: Session, mkt: KalshiMarket) -> bool:
    if mkt.result not in ("yes", "no") or mkt.player_a_id is None \
            or mkt.player_b_id is None or mkt.match_id is None:
        return False
    match = db.get(Match, mkt.match_id)
    if match is None or match.source != "kalshi" \
            or match.winner_id is None or match.loser_id is None:
        return False
    expect_winner = mkt.player_a_id if mkt.result == "yes" else mkt.player_b_id
    return match.winner_id != expect_winner \
        and {match.winner_id, match.loser_id} == {mkt.player_a_id, mkt.player_b_id}
