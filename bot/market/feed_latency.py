"""Feed-vs-market latency analysis — the make-or-break test for in-play edge.

The in-play edge thesis is "lower-tier markets reprice slowly." That only pays
if OUR score feed is FASTER than the market. Both are already timestamped in the
DB — market_ticks (every quote) and match_score_log (every observed game/set
change) — so we can measure, per score event, whether the market's price
reaction lands BEFORE or AFTER we observed the score.

For each score change we logged at time E, we find the market's first material
mid-price move (vs the price ~window before) in [E-window, E+window]:
    delta = react_ts - E
    delta > 0  → market moved AFTER we saw the score  → we're AHEAD (edge possible)
    delta < 0  → market moved BEFORE we saw it        → we LAG   (no edge from this feed)

Caveat surfaced in the output: our score feed is POLLED (~25s), so this can only
prove gross lag, not sub-poll lead. If we lag here, a faster/push feed is
required before any in-play edge is realistic.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

_TIER = {"KXATPCHALLENGERMATCH": "challenger", "KXWTACHALLENGERMATCH": "challenger",
         "KXATPMATCH": "main", "KXWTAMATCH": "main", "KXWTAGAME": "main"}


def _tier(ticker: str) -> str:
    for pre, t in _TIER.items():
        if ticker.startswith(pre):
            return t
    if ticker.startswith("KXITF"):
        return "itf"
    return "other"


def analyze_feed_latency(db: Session, *, since_days: int = 30, window_s: int = 90,
                         move_thresh: int = 2, min_quotes: int = 3) -> dict:
    """Return per-tier and overall latency stats of our score feed vs the market.
    Reads only (market_ticks + match_score_log)."""
    win = timedelta(seconds=window_s)
    rows = db.execute(text(
        "SELECT market_ticker, ts, sets_a, sets_b, games_a, games_b "
        "FROM match_score_log WHERE ts > now() - make_interval(days => :d) "
        "AND is_final = false ORDER BY market_ticker, ts"),
        {"d": since_days}).all()

    events: dict[str, list] = defaultdict(list)   # ticker -> [event_ts]
    cadence: list[float] = []                      # seconds between our observations
    prev_key: dict[str, tuple] = {}
    prev_ts: dict[str, object] = {}
    for tk, ts, sa, sb, ga, gb in rows:
        key = (sa, sb, ga, gb)
        if tk in prev_key:
            if prev_key[tk] != key:
                events[tk].append(ts)
                cadence.append((ts - prev_ts[tk]).total_seconds())
        prev_key[tk], prev_ts[tk] = key, ts

    by_tier: dict[str, list] = defaultdict(list)   # tier -> [delta seconds]
    for tk, evs in events.items():
        if not evs:
            continue
        quotes = db.execute(text(
            "SELECT ts, yes_bid, yes_ask FROM market_ticks WHERE market_ticker=:t "
            "AND kind='quote' AND yes_bid IS NOT NULL AND yes_ask IS NOT NULL "
            "AND ts BETWEEN :lo AND :hi ORDER BY ts"),
            {"t": tk, "lo": min(evs) - win, "hi": max(evs) + win}).all()
        if len(quotes) < min_quotes:
            continue
        qs = [(q[0], (q[1] + q[2]) / 2) for q in quotes]
        tier = _tier(tk)
        for e in evs:
            wq = [(ts, mid) for ts, mid in qs if e - win <= ts <= e + win]
            if len(wq) < 2:
                continue
            baseline = wq[0][1]
            react = next((ts for ts, mid in wq if abs(mid - baseline) >= move_thresh), None)
            if react is None:
                continue                            # no material move → event didn't matter
            by_tier[tier].append((react - e).total_seconds())

    def summarize(deltas: list[float]) -> dict:
        if not deltas:
            return {"n": 0}
        deltas.sort()
        n = len(deltas)
        ahead = sum(1 for d in deltas if d > 0)
        return {"n": n, "median_delta_s": round(statistics.median(deltas), 1),
                "mean_delta_s": round(statistics.mean(deltas), 1),
                "pct_ahead": round(100 * ahead / n, 1),
                "p25": round(deltas[n // 4], 1), "p75": round(deltas[3 * n // 4], 1)}

    all_deltas = [d for ds in by_tier.values() for d in ds]
    return {
        "since_days": since_days, "window_s": window_s, "move_thresh": move_thresh,
        "poll_cadence_median_s": round(statistics.median(cadence), 1) if cadence else None,
        "overall": summarize(all_deltas),
        "by_tier": {t: summarize(ds) for t, ds in sorted(by_tier.items())},
    }
