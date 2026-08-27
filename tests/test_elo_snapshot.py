"""Persisting the fitted ratings so only the daily ingest replays history.

fit_from_db materialises ~875k match rows plus per-match set results — ~330 MB
peak, measured. The web process did that every 30 minutes to render two
spotlight bands, and CPython keeps most of a freed heap resident, so the cost
was permanent, not transient. These tests guard the replacement."""
import datetime as dt

from bot.prob.elo import SetElo
from bot.prob.model import MatchState


def fitted():
    """A model with the full range of per-player state: surface ratings, a
    provisional player, an inactive one, and a never-seen id."""
    m = SetElo()
    day = dt.date(2026, 6, 1)
    m.apply_match(1, 2, "hard", "A", [True, False, True], day=day)
    m.apply_match(1, 3, "clay", "G", [True, True], day=day + dt.timedelta(days=2))
    m.apply_match(3, 2, "grass", "M", [True, False, True],
                  day=day + dt.timedelta(days=9))
    m.trained_through = dt.date(2026, 6, 15)
    return m


def test_a_round_trip_preserves_every_rating_field_exactly():
    a = fitted()
    b = SetElo()
    b.load_snapshot(a.to_snapshot(), a.trained_through)
    assert set(b.ratings) == set(a.ratings)
    for pid, ra in a.ratings.items():
        rb = b.ratings[pid]
        assert ra.overall == rb.overall          # exact, not rounded
        assert ra.sets_seen == rb.sets_seen
        assert ra.recent == rb.recent
        assert ra.last_day == rb.last_day
        assert ra.by_surface == rb.by_surface
    assert b.trained_through == a.trained_through


def test_predictions_are_identical_after_a_round_trip():
    """The whole point: the web service must serve the same numbers it would
    have computed by replaying history."""
    a = fitted()
    b = SetElo()
    b.load_snapshot(a.to_snapshot(), a.trained_through)
    for surface in ("hard", "clay", "grass", None):
        st = MatchState()
        pa = a.predict(1, 2, surface, "A", st)
        pb = b.predict(1, 2, surface, "A", st)
        # EXACT, not approximately equal — a 4dp round trip moved these in
        # the 8th decimal, which is exactly the kind of unexplainable drift
        # between services that is worth spending a few hundred kB to avoid.
        assert pa.p_a == pb.p_a, surface
        assert pa.confidence == pb.confidence, surface


def test_an_unknown_player_still_falls_back_to_a_base_rating():
    b = SetElo()
    b.load_snapshot(fitted().to_snapshot())
    p = b.predict(1, 999_999, "hard", "A", MatchState())  # never appeared
    assert 0.0 < p.p_a < 1.0


def test_the_snapshot_is_json_safe_and_far_smaller_than_the_replay():
    import json
    snap = fitted().to_snapshot()
    blob = json.dumps(snap)          # must survive a JSONB round trip
    assert json.loads(blob).keys() == snap.keys()
    # per player the payload is a compact list, not a dict of repeated key names
    (first,) = list(snap.values())[:1]
    assert isinstance(first, list) and len(first) == 5


def test_an_empty_snapshot_loads_without_error():
    m = SetElo()
    assert m.load_snapshot({}) == 0
    assert m.predict(1, 2, "hard", "A", MatchState()).p_a == 0.5


# --- the services must stay off the replay path -------------------------

def test_the_web_service_never_replays_the_match_history():
    """The whole point of the snapshot. If a future change reintroduces
    fit_from_db into bot/web.py the memory cost comes straight back, and it
    would not be obvious from behaviour — only from the bill."""
    import inspect

    import bot.web as W
    src = inspect.getsource(W._fit_live_model)
    assert "from_snapshot_db" in src
    assert "fit_from_db(" not in src


def test_the_worker_prefers_the_snapshot_and_only_fits_as_a_fallback():
    import inspect

    from bot.watch import WatchService
    src = inspect.getsource(WatchService._fit_model)
    assert src.index("from_snapshot_db") < src.index("fit_from_db(")


def test_the_ingest_persists_a_snapshot_after_fitting():
    import inspect

    import bot.scenarios as S
    src = inspect.getsource(S.generate_scenarios)
    assert "fit_from_db" in src and "save_snapshot" in src
