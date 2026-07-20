"""Restart protocol integration test (Phase 6).

Seeds live_match_state, boots estimators through WatchService's reload path,
and asserts: fresh states resume with their confidence, stale states (gap ≥
60s) are quarantined — confidence zeroed, no advising until score re-confirms.

Needs a reachable DATABASE_URL; skipped otherwise.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from bot.models import LiveMatchState

FRESH = "TEST-RESTART-FRESH"
STALE = "TEST-RESTART-STALE"


@pytest.fixture
def db():
    try:
        from bot.db import session

        with session() as s:
            s.execute(delete(LiveMatchState).where(
                LiveMatchState.market_ticker.in_([FRESH, STALE])))
        yield session
        with session() as s:
            s.execute(delete(LiveMatchState).where(
                LiveMatchState.market_ticker.in_([FRESH, STALE])))
    except Exception as e:
        pytest.skip(f"database unavailable: {e}")


def seed(session, ticker: str, state: str, confidence: float, age_s: int):
    now = datetime.now(timezone.utc)
    with session() as s:
        s.add(LiveMatchState(
            market_ticker=ticker, state=state, confidence=confidence,
            last_confirmed_state="0-0", stale=False,
            last_tick_at=now - timedelta(seconds=age_s), updated_at=now))


def test_restart_quarantines_stale_and_resumes_fresh(db):
    from bot.watch import WatchService

    seed(db, FRESH, "1-0", 0.9, age_s=20)    # gap < 60s → resume
    seed(db, STALE, "1-0", 0.9, age_s=300)   # gap ≥ 60s → quarantine

    ws = WatchService()
    est_fresh = ws._estimator(FRESH, "KXITFMATCH")
    est_stale = ws._estimator(STALE, "KXITFMATCH")

    assert est_fresh.state_key == "1-0"
    assert est_fresh.confidence == pytest.approx(0.9)

    assert est_stale.state_key == "1-0"  # state retained for the score snap
    assert est_stale.confidence == 0.0   # but quarantined: no advising
    assert not est_stale.pending

    # score re-confirmation revives the stale match
    est_stale.on_score(datetime.now(timezone.utc), 1, 0)
    assert est_stale.confidence == 1.0
    assert est_stale.last_confirmed == "1-0"


def test_restart_zero_zero_not_quarantined(db):
    seed(db, FRESH, "0-0", 1.0, age_s=300)  # pre-match: nothing to quarantine
    from bot.watch import WatchService

    ws = WatchService()
    est = ws._estimator(FRESH, "KXITFMATCH")
    assert est.state_key == "0-0"
    assert est.confidence == pytest.approx(1.0)
