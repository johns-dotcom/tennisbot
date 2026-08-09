"""Line movement — how far the price on OUR side has drifted from the market's
opening quote by the time a bot enters. The leading-indicator sibling of CLV
(CLV is entry-vs-close, knowable only after; this is entry-vs-open, knowable AT
the bet, so a bot can gate on it).

Sign convention (cents, on the side we are buying):
    move = our_entry_price - our_open_price
  move < 0 : our side got CHEAPER since the open — the market faded our pick.
             Buying here is betting against a line that moved away from us;
             the losing "adverse selection" slice the backtest flagged.
  move > 0 : our side got MORE expensive — we're paying up / chasing.

The gate uses only the adverse (move < 0) direction, and ONLY pre-match: a
pre-match drift encodes information (someone knows something), but an IN-PLAY
drift just encodes the score (a live underdog is cheap because it's losing, not
because of hidden news) — gating live comeback bets on that would be wrong. So
callers record the move for every bot and gate it for pre-match bets only.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from bot.models import MarketTick

# Pre-match only: skip a bet whose side has fallen at least this many cents since
# the open (deliberately lenient — record everything, block only egregious
# fades, so the sample isn't gutted while we measure).
ADVERSE_MOVE_PREMATCH = 10


def _open_our_side(db: Session, market_ticker: str, side: str) -> int | None:
    """Our-side price at the market's FIRST recorded quote (the open)."""
    row = db.execute(
        select(MarketTick.yes_bid, MarketTick.yes_ask)
        .where(MarketTick.market_ticker == market_ticker,
               MarketTick.kind == "quote",
               MarketTick.yes_ask.is_not(None))
        .order_by(MarketTick.ts.asc()).limit(1)
    ).first()
    if row is None:
        return None
    yes_bid, yes_ask = row
    if side == "yes":
        return yes_ask
    return (100 - yes_bid) if yes_bid is not None else None


def market_move_cents(db: Session, market_ticker: str, side: str,
                      entry_cents: int) -> int | None:
    """Signed cents our side has moved from the open to `entry_cents`. None when
    there's no earlier quote to reference (never raises — a missing reference
    must never block a bet)."""
    try:
        opened = _open_our_side(db, market_ticker, side)
    except Exception:
        return None
    if opened is None:
        return None
    return int(entry_cents) - int(opened)


def adverse_prematch(move: int | None,
                     threshold: int = ADVERSE_MOVE_PREMATCH) -> bool:
    """True when a PRE-MATCH move is adverse enough to skip: our side fell at
    least `threshold`¢ since the open (the market faded our pick)."""
    return move is not None and move <= -threshold
