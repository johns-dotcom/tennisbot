"""Discovery of Kalshi's non-match-winner tennis markets — set winner, exact
match score, total games and friends.

STRICTLY READ-ONLY, like every other Kalshi path here (CLAUDE.md rule 1).

These land in `derivative_markets`, never in `kalshi_markets`, because the
probability engine models match winner only and the whole bot pipeline reads
`kalshi_markets`. Nothing here feeds an advisory or a paper bet; the table exists
so a user can log a personal bet on one of these and have it settle.

Series verified against the live API 2026-08-11 (only these exist for tennis;
several are dormant with zero open markets and simply return nothing):

    KXATPSETWINNER / KXWTASETWINNER    'Will X win set N'      2 markets/event
    KXATPEXACTMATCH / KXWTAEXACTMATCH  'X wins 2-1'            4 markets/event
    KXATPGTOTAL / KXWTAGTOTAL          'Over 22.5 games'       N markets/event
    KXATPTOTALSETS                     total sets
    KXATPTIEBREAK                      tiebreak to occur
    KXATPSETSWEEP                      winner without dropping a set
    KXATPANYSET                        any set winner
    KXATPACES / KXWTAACES              aces

Event tickers embed the match key, which is what links a derivative back to the
match we already track:

    KXATPEXACTMATCH-26AUG10DARNAK           -> match key 26AUG10DARNAK
    KXATPSETWINNER-26AUG10DARNAK-2          -> match key 26AUG10DARNAK, set 2
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from bot.log import get_logger
from bot.market.kalshi import TENNIS_SERIES, KalshiClient, dollars_to_cents
from bot.models import DerivativeMarket, KalshiMarket

log = get_logger("market.derivatives")

# series ticker -> (kind, tour). Dormant series are listed so that markets appear
# automatically if Kalshi opens them, at the cost of one cheap empty GET each.
DERIVATIVE_SERIES: dict[str, tuple[str, str]] = {
    "KXATPSETWINNER": ("set_winner", "atp"),
    "KXWTASETWINNER": ("set_winner", "wta"),
    "KXATPEXACTMATCH": ("exact_score", "atp"),
    "KXWTAEXACTMATCH": ("exact_score", "wta"),
    "KXATPGTOTAL": ("total_games", "atp"),
    "KXWTAGTOTAL": ("total_games", "wta"),
    "KXATPTOTALSETS": ("total_sets", "atp"),
    "KXATPTIEBREAK": ("tiebreak", "atp"),
    "KXATPSETSWEEP": ("set_sweep", "atp"),
    "KXATPANYSET": ("any_set", "atp"),
    "KXATPACES": ("aces", "atp"),
    "KXWTAACES": ("aces", "wta"),
}

KIND_LABELS = {
    "set_winner": "Set winner",
    "exact_score": "Exact score",
    "total_games": "Total games",
    "total_sets": "Total sets",
    "tiebreak": "Tiebreak",
    "set_sweep": "Straight sets",
    "any_set": "Any set winner",
    "aces": "Aces",
}

# order the kinds are shown in on the match page
KIND_ORDER = list(KIND_LABELS)


def kind_label(kind: str, set_no: int | None = None) -> str:
    """Human label for a derivative market's kind ('Set 2 winner')."""
    if kind == "set_winner" and set_no:
        return f"Set {set_no} winner"
    return KIND_LABELS.get(kind, kind.replace("_", " ").capitalize())


def match_key(event_ticker: str) -> str | None:
    """The match key shared by every series for one match, e.g. '26AUG10DARNAK'
    out of 'KXATPSETWINNER-26AUG10DARNAK-2'."""
    parts = (event_ticker or "").split("-")
    return parts[1] if len(parts) >= 2 and parts[1] else None


def set_number(event_ticker: str, kind: str) -> int | None:
    """Set winner events carry the set as a third segment: '...-26AUG10DARNAK-2'."""
    if kind != "set_winner":
        return None
    parts = (event_ticker or "").split("-")
    if len(parts) >= 3 and parts[2].isdigit():
        return int(parts[2])
    return None


def _tracked_matches(db: Session) -> tuple[dict, dict]:
    """(tour, match key) -> match event ticker, plus that match's 'A vs B' label,
    over the match-winner markets we already track. Derivatives for anything we
    don't track are dropped — that keeps the crawl bounded and guarantees every
    stored row links back to a real match page."""
    keyed: dict[tuple[str, str], str] = {}
    sides: dict[str, list[str]] = {}
    for m in db.execute(select(KalshiMarket)).scalars().all():
        ev = m.event_ticker or ""
        series = ev.split("-")[0]
        tour = TENNIS_SERIES.get(series)
        k = match_key(ev)
        if not tour or not k:
            continue
        keyed[(tour, k)] = ev
        nm = (m.raw or {}).get("yes_sub_title")
        if nm:
            sides.setdefault(ev, []).append(nm)
    labels = {ev: " vs ".join(sorted(ns)[:2]) for ev, ns in sides.items() if ns}
    return keyed, labels


def refresh_derivatives(db: Session, client: KalshiClient) -> dict:
    """Crawl the derivative series and upsert everything belonging to a match we
    already track, then chase settlement on rows that have dropped out of the
    open crawl.

    Same three-phase shape as discover_markets: a short DB read, then all the
    HTTP with no transaction open (serverless Postgres drops connections that
    idle through a crawl), then a short DB write."""
    stats = {"seen": 0, "kept": 0, "new": 0, "settled": 0}

    # ---- pass 1 (DB, short) ----
    keyed, labels = _tracked_matches(db)
    existing = {r.ticker: r for r in db.execute(
        select(DerivativeMarket)).scalars().all()}
    # unsettled rows we may need to chase individually if they stop being open
    chase = [t for t, r in existing.items() if r.result in (None, "")]
    db.commit()   # release the transaction for the duration of the crawl

    if not keyed:
        return stats

    # ---- pass 2 (HTTP only) ----
    fetched: list[tuple[str, str, str, dict]] = []   # series, kind, tour, market
    for series, (kind, tour) in DERIVATIVE_SERIES.items():
        try:
            for m in client.markets(series):
                fetched.append((series, kind, tour, m))
        except Exception as e:
            log.error("derivative discovery failed", series=series, error=str(e))
    stats["seen"] = len(fetched)

    seen_tickers = {m.get("ticker") for _, _, _, m in fetched}
    chased: list[dict] = []
    for t in chase:
        if t in seen_tickers:
            continue      # still open — the crawl already has the fresh copy
        try:
            chased.append(client.market(t))   # already unwrapped to the market dict
        except Exception as e:
            log.warning("derivative settlement chase failed", ticker=t, error=str(e))

    # ---- pass 3 (DB, short) ----
    now = datetime.now(timezone.utc)
    for series, kind, tour, m in fetched:
        ev = m.get("event_ticker") or ""
        k = match_key(ev)
        match_ev = keyed.get((tour, k)) if k else None
        if not match_ev:
            continue      # a match we don't track — skip rather than orphan it
        stats["kept"] += 1
        if _upsert(db, existing, m, series, kind, ev, match_ev,
                   labels.get(match_ev), now):
            stats["new"] += 1
    for m in chased:
        if not m:
            continue
        row = existing.get(m.get("ticker"))
        if row is None:
            continue
        _apply_result(row, m, now)
        if row.result:
            stats["settled"] += 1
    db.commit()
    log.info("derivative refresh", **stats)
    return stats


def _apply_result(row: DerivativeMarket, m: dict, now: datetime) -> None:
    """Copy status/result off a Kalshi market payload. `result` is '' until the
    market settles, so an empty value must not be stored as a settled outcome."""
    row.status = m.get("status") or row.status
    res = (m.get("result") or "").strip().lower()
    if res in ("yes", "no", "void") and row.result != res:
        row.result = res
        row.settled_at = now
    row.last_seen_at = now


def _upsert(db: Session, existing: dict, m: dict, series: str, kind: str,
            ev: str, match_ev: str, match_label: str | None,
            now: datetime) -> bool:
    """Insert or update one derivative market. Returns True if newly inserted."""
    ticker = m.get("ticker")
    if not ticker:
        return False
    row = existing.get(ticker)
    fresh = row is None
    if fresh:
        row = DerivativeMarket(ticker=ticker, first_seen_at=now)
        db.add(row)
        existing[ticker] = row
    row.event_ticker = ev
    row.series_ticker = series
    row.kind = kind
    row.match_event_ticker = match_ev
    row.set_no = set_number(ev, kind)
    row.label = (m.get("yes_sub_title") or "")[:160] or None
    row.match_label = (match_label or "")[:160] or None
    row.title = m.get("title")
    row.close_time = _ts(m.get("close_time"))
    row.yes_bid_cents = dollars_to_cents(m.get("yes_bid_dollars"))
    row.yes_ask_cents = dollars_to_cents(m.get("yes_ask_dollars"))
    row.last_price_cents = dollars_to_cents(m.get("last_price_dollars"))
    row.raw = m
    _apply_result(row, m, now)
    return fresh


def _ts(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
