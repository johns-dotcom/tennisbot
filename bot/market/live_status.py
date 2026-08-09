"""One place that decides what a raw Kalshi milestone status code means.

Kalshi's milestone `details.status` is an OPEN, provider-defined vocabulary.
Observed live-phase codes already include 'live', 'P', 'CTS', 'S', 'E', and
per-set codes — and more will appear. The old approach kept an ALLOWLIST of
"live" codes, so every code we hadn't seen (‘E’ was the latest) silently fell
through to "starting soon" and a live match vanished from the live board.

The durable fix is to invert it: enumerate only the two SMALL, STABLE
vocabularies — pre-match and ended — and treat any other present code as live.

Why this asymmetry is safe (not just convenient):
  * An ENDED match is caught by THREE authoritative signals independent of the
    status code — a posted settlement result, a final scoreline, and
    discovery-gone. So an ended code we fail to list rarely shows as live; the
    other signals catch it.
  * A LIVE match on a lower tier (ITF/Challenger) often has NO score feed and
    NO settlement yet — the status code is the ONLY evidence it is being
    played. Defaulting an unknown code to "not live" is exactly what loses it.
So: unknown-but-present → live. A new live code never regresses to
"starting soon" again.
"""
from __future__ import annotations

# Pre-match: the match has not begun. Small and stable.
NOT_STARTED = {
    "not_started", "notstarted", "not started", "ns", "sch", "sched",
    "scheduled", "pre", "pre_match", "prematch", "tbd", "upcoming", "delayed",
}

# Terminal: the match is over. Small and stable. (Settlement / final scoreline /
# discovery-gone are checked separately by callers and back this up.) 'ended' is
# the sentinel our own live-status sweep writes when a match leaves the feed.
ENDED = {
    "finished", "complete", "completed", "ended", "end", "closed", "close",
    "cancelled", "canceled", "walkover", "wov", "wo", "abandoned", "retired",
    "ret", "postponed", "final", "settled", "void", "cancel",
}


def status_kind(status: str | None) -> str:
    """Classify a raw milestone status code into one of:
    'none' (absent), 'not_started', 'ended', or 'live' (present and neither
    pre-match nor ended — the durable default for any unrecognised code)."""
    s = (status or "").strip().lower()
    if not s:
        return "none"
    if s in NOT_STARTED:
        return "not_started"
    if s in ENDED:
        return "ended"
    return "live"


def is_live_status(status: str | None) -> bool:
    """True when the code alone indicates play is underway. Callers still let an
    authoritative end signal (settlement/final/gone) outrank this."""
    return status_kind(status) == "live"
