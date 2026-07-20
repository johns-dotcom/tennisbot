from datetime import datetime, timedelta, timezone

from bot.market.estimator import SetBoundaryEstimator
from bot.market.priors import SetDurationPriors

T0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
PRIORS = SetDurationPriors(p05=20.0, p25=28.0, p50=36.0, p75=45.0, p95=60.0)


def build(**kw):
    events = {"snapshots": [], "logs": [], "conflicts": []}
    est = SetBoundaryEstimator(
        "TESTMKT", priors=PRIORS,
        persist=lambda s: events["snapshots"].append(s),
        log_inference=lambda d: events["logs"].append(d),
        on_conflict=lambda t: events["conflicts"].append(t), **kw)
    return est, events


def feed_boundary(est, start, mid_from=60, mid_to=72, trades=6):
    """A volume-confirmed discontinuity at `start`."""
    est.on_quote(start, mid_from - 1, mid_from + 1)
    est.on_trade(start + timedelta(seconds=10), (mid_from + mid_to) // 2, trades)
    est.on_quote(start + timedelta(seconds=20), mid_to - 1, mid_to + 1)


def test_favorite_set_win_detected():
    est, ev = build()
    est.on_trade(T0, 60, 10)  # anchor: match underway
    feed_boundary(est, T0 + timedelta(minutes=30))
    assert est.state_key == "1-0"
    assert 0.5 < est.confidence < 1.0
    assert len(est.pending) == 1
    assert ev["snapshots"][-1].state == "1-0"  # persisted after transition


def test_quote_only_jump_is_noise():
    est, _ = build()
    est.on_trade(T0, 60, 10)
    start = T0 + timedelta(minutes=30)
    est.on_quote(start, 59, 61)
    est.on_quote(start + timedelta(seconds=20), 71, 73)  # no trades in window
    assert est.state_key == "0-0"


def test_degraded_ticks_never_detect():
    est, _ = build()
    est.on_trade(T0, 60, 10)
    start = T0 + timedelta(minutes=30)
    est.on_quote(start, 59, 61, degraded=True)
    est.on_trade(start + timedelta(seconds=10), 66, 8, degraded=True)
    est.on_quote(start + timedelta(seconds=20), 71, 73, degraded=True)
    assert est.state_key == "0-0"


def test_too_early_boundary_rejected_by_priors():
    est, _ = build()
    est.on_trade(T0, 60, 10)
    feed_boundary(est, T0 + timedelta(minutes=5))  # < 0.7 * p05(20) = 14 min
    assert est.state_key == "0-0"


def test_underdog_set_win_needs_bigger_jump():
    est, _ = build()
    est.on_trade(T0, 65, 10)
    start = T0 + timedelta(minutes=30)
    # 6-cent move toward the underdog: below the full 8c threshold → no transition
    est.on_quote(start, 64, 66)
    est.on_trade(start + timedelta(seconds=10), 60, 8)
    est.on_quote(start + timedelta(seconds=20), 58, 60)
    assert est.state_key == "0-0"


def test_asymmetry_favorite_direction_passes_at_six_cents():
    est, _ = build()
    est.on_trade(T0, 65, 10)
    start = T0 + timedelta(minutes=30)
    # same 6-cent move toward the favorite: 8 * 0.7 = 5.6c threshold → transition
    est.on_quote(start, 64, 66)
    est.on_trade(start + timedelta(seconds=10), 68, 8)
    est.on_quote(start + timedelta(seconds=20), 70, 72)
    assert est.state_key == "1-0"


def test_underdog_set_win_detected_on_big_jump():
    est, _ = build()
    est.on_trade(T0, 65, 10)
    feed_boundary(est, T0 + timedelta(minutes=30), mid_from=65, mid_to=50, trades=8)
    assert est.state_key == "0-1"


def test_score_confirms_inference_hit_logged():
    est, ev = build()
    est.on_trade(T0, 60, 10)
    feed_boundary(est, T0 + timedelta(minutes=30))
    score_ts = T0 + timedelta(minutes=32)
    res = est.on_score(score_ts, 1, 0)
    assert res.changed and not res.conflict
    assert est.confidence == 1.0
    assert est.last_confirmed == "1-0"
    assert not est.pending
    hit = ev["logs"][-1]
    assert hit["hit"] is True
    assert 0 < hit["lead_time_seconds"] <= 120
    assert not ev["conflicts"]


def test_score_conflict_kills_and_snaps():
    est, ev = build()
    est.on_trade(T0, 60, 10)
    feed_boundary(est, T0 + timedelta(minutes=30))  # inferred 1-0
    res = est.on_score(T0 + timedelta(minutes=32), 0, 1)  # truth: 0-1
    assert res.conflict
    assert est.state_key == "0-1"
    assert est.confidence == 1.0
    assert ev["conflicts"] == ["TESTMKT"]
    assert ev["logs"][-1]["hit"] is False


def test_missed_boundary_logged():
    est, ev = build()
    est.on_trade(T0, 60, 10)
    res = est.on_score(T0 + timedelta(minutes=40), 1, 0)  # we never inferred it
    assert res.changed and est.state_key == "1-0"
    assert ev["logs"][-1]["detail"]["missed_boundary"] is True


def test_quarantine_zeroes_confidence_and_blocks_detection():
    est, ev = build()
    est.on_trade(T0, 60, 10)
    est.quarantine(T0 + timedelta(minutes=10), "feed gap")
    assert est.confidence == 0.0
    assert ev["conflicts"] == ["TESTMKT"]
    feed_boundary(est, T0 + timedelta(minutes=40))
    assert est.state_key == "0-0"  # no detection while quarantined
    # score re-confirmation revives
    est.on_score(T0 + timedelta(minutes=45), 1, 0)
    assert est.confidence == 1.0 and est.state_key == "1-0"


def test_match_reaches_final():
    est, _ = build()
    est.on_trade(T0, 60, 10)
    feed_boundary(est, T0 + timedelta(minutes=30))
    feed_boundary(est, T0 + timedelta(minutes=65), mid_from=75, mid_to=92)
    assert est.state_key == "final"
    assert est.final


def test_restore_roundtrip():
    est, _ = build()
    est.on_trade(T0, 60, 10)
    feed_boundary(est, T0 + timedelta(minutes=30))
    snap = est.snapshot()
    est2, _ = build()
    est2.restore(snap.state, snap.confidence, snap.last_confirmed_state, snap.last_tick_at)
    assert est2.state_key == "1-0"
    assert est2.last_confirmed == "0-0"


def test_repeated_same_score_is_noop():
    est, ev = build()
    est.on_score(T0, 0, 0)
    n_logs = len(ev["logs"])
    est.on_score(T0 + timedelta(minutes=1), 0, 0)
    assert len(ev["logs"]) == n_logs
    assert est.state_key == "0-0"
