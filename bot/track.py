"""Track-record math: advisory outcomes and flat-stake P&L.

Pure functions — the web page and tests share them. Staking model for the
record: one contract at the quoted executable price per advisory. This is an
accounting convention for the track record, not betting advice.
"""
from __future__ import annotations


def advisory_outcome(side: str, market_result: str | None) -> str | None:
    """'won' | 'lost' | 'void' | None (unsettled)."""
    if market_result in (None, ""):
        return None
    if market_result == "void":
        return "void"
    if market_result not in ("yes", "no"):
        return None
    return "won" if market_result == side else "lost"


def clv_cents(side: str, entry_cents: int, close_yes_cents: int | None) -> int | None:
    """Closing-line value in cents: our side's closing price minus our entry.
    Positive = we beat the close (got a better price than the sharp line).
    None when the closing line is unknown."""
    if close_yes_cents is None:
        return None
    our_close = close_yes_cents if side == "yes" else 100 - close_yes_cents
    return our_close - entry_cents


def advisory_pnl_cents(side: str, price_cents: int, market_result: str | None) -> int | None:
    """Flat one-contract P&L in cents; None while unsettled, 0 on void."""
    outcome = advisory_outcome(side, market_result)
    if outcome is None:
        return None
    if outcome == "void":
        return 0
    return (100 - price_cents) if outcome == "won" else -price_cents
