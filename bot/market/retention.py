"""Retention for market_ticks.

The recorder writes one row per websocket quote for every watched market and
nothing ever deleted them: ~120M rows / ~39 GB after about eight weeks. On a
memory-billed host that is the dominant cost — Postgres holds a working set
proportional to the data it is asked about, and memory was 88% of the bill.

Nothing is deleted until the facts that outlive the ticks are stored on the
market row:

  * close_yes_cents — the closing line for CLV, already written at match start
  * peak_yes_bid / peak_no_bid — best bid ever seen
  * tp_yes_at / tp_no_at — the LAST moment each side was at/above the
    take-profit limit, which is what answers "did it reach 90¢ after I bet?"
    exactly. A peak alone cannot: the peak may predate the bet while a later
    qualifying moment still exists.

Only SETTLED markets are pruned, and only after their summaries are stored, so
a live match can never lose the ticks it is still being priced from.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from bot.log import get_logger
from bot.models import KalshiMarket

log = get_logger("market.retention")

TP_LIMIT = 90          # take-profit limit price (cents); mirrors bot.web.TP_LIMIT
DEFAULT_KEEP_DAYS = 30


def summarize_market_ticks(db: Session, tickers: list[str]) -> int:
    """Store the durable summaries for these markets. Returns rows updated."""
    if not tickers:
        return 0
    rows = db.execute(text("""
        SELECT market_ticker,
               max(yes_bid) AS peak_yes,
               max(no_bid)  AS peak_no,
               max(ts) FILTER (WHERE yes_bid >= :tp) AS tp_yes_at,
               max(ts) FILTER (WHERE no_bid  >= :tp) AS tp_no_at
          FROM market_ticks
         WHERE market_ticker = ANY(:t) AND kind = 'quote'
         GROUP BY market_ticker"""), {"t": tickers, "tp": TP_LIMIT}).all()
    by_ticker = {r[0]: r for r in rows}
    n = 0
    for m in db.execute(select(KalshiMarket).where(
            KalshiMarket.ticker.in_(tickers))).scalars():
        r = by_ticker.get(m.ticker)
        if r is None:
            continue
        m.peak_yes_bid, m.peak_no_bid = r[1], r[2]
        m.tp_yes_at, m.tp_no_at = r[3], r[4]
        n += 1
    db.commit()
    return n


def prune_market_ticks(db: Session, keep_days: int = DEFAULT_KEEP_DAYS,
                       batch: int = 50) -> dict:
    """Summarize then delete ticks for settled markets older than `keep_days`.

    Deliberately conservative:
      * only markets with a settled result are touched — a live or upcoming
        match keeps every tick it is still priced from;
      * summaries are written and committed BEFORE the delete, so an
        interrupted run can never lose the derived facts;
      * markets are processed in small batches so one run cannot hold a huge
        delete open against the database."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    stats = {"markets": 0, "summarized": 0, "ticks_deleted": 0}

    due = list(db.execute(select(KalshiMarket.ticker).where(
        KalshiMarket.result.is_not(None),
        KalshiMarket.ticks_pruned_at.is_(None),
        KalshiMarket.close_time.is_not(None),
        KalshiMarket.close_time < cutoff)).scalars())
    db.commit()
    if not due:
        log.info("tick prune: nothing due", keep_days=keep_days)
        return stats

    now = datetime.now(timezone.utc)
    for i in range(0, len(due), batch):
        chunk = due[i:i + batch]
        stats["summarized"] += summarize_market_ticks(db, chunk)
        deleted = db.execute(
            text("DELETE FROM market_ticks WHERE market_ticker = ANY(:t)"),
            {"t": chunk}).rowcount or 0
        for m in db.execute(select(KalshiMarket).where(
                KalshiMarket.ticker.in_(chunk))).scalars():
            m.ticks_pruned_at = now
        db.commit()
        stats["ticks_deleted"] += deleted
        stats["markets"] += len(chunk)
    log.info("tick prune", keep_days=keep_days, **stats)
    return stats
