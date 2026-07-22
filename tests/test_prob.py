import math

from bot.prob.elo import SetElo, _expected
from bot.prob.model import MatchState, Prediction
from bot.prob.state_adjust import (
    condition_on_state,
    match_prob_from_set_prob,
    race_win_prob,
    set_prob_from_match_prob,
)


def test_race_win_prob_basics():
    assert race_win_prob(0.5, 1, 1) == 0.5
    assert race_win_prob(1.0, 2, 2) == 1.0
    assert math.isclose(race_win_prob(0.6, 2, 2), 0.6 ** 2 * (3 - 2 * 0.6))


def test_set_prob_inversion_roundtrip():
    for p in (0.1, 0.35, 0.5, 0.72, 0.9):
        for bo in (3, 5):
            s = set_prob_from_match_prob(p, bo)
            assert math.isclose(match_prob_from_set_prob(s, bo), p, abs_tol=1e-6)


def test_condition_on_state_directions():
    # leading raises win prob, trailing lowers it, decider ~= set prob
    p0 = 0.6
    up = condition_on_state(p0, MatchState(1, 0, 3))
    down = condition_on_state(p0, MatchState(0, 1, 3))
    decider = condition_on_state(p0, MatchState(1, 1, 3))
    assert up > p0 > down
    s = set_prob_from_match_prob(p0, 3)
    assert math.isclose(decider, s, abs_tol=1e-9)


def test_condition_even_match_even_state_is_half():
    assert math.isclose(condition_on_state(0.5, MatchState(1, 1, 3)), 0.5, abs_tol=1e-6)


def test_impossible_state_rejected():
    import pytest

    with pytest.raises(ValueError):
        MatchState(2, 0, 3)


def test_elo_update_moves_ratings():
    m = SetElo()
    for _ in range(6):
        m.update_set(1, 2, "Hard", "A")
    ra, rb = m.ratings[1], m.ratings[2]
    assert ra.overall > 1500 > rb.overall
    assert ra.by_surface["Hard"] > rb.by_surface["Hard"]
    pred = m.predict(1, 2, "Hard", "A", MatchState())
    assert pred.p_a > 0.5


def test_elo_prediction_is_symmetric():
    m = SetElo()
    for _ in range(10):
        m.update_set(1, 2, "Clay", "A")
    p_ab = m.predict(1, 2, "Clay", "A", MatchState()).p_a
    p_ba = m.predict(2, 1, "Clay", "A", MatchState()).p_a
    assert math.isclose(p_ab + p_ba, 1.0, abs_tol=1e-9)


def test_unseen_players_default_even_zero_confidence():
    m = SetElo()
    pred = m.predict(111, 222, "Hard", None, MatchState())
    assert math.isclose(pred.p_a, 0.5, abs_tol=1e-9)
    assert pred.confidence == 0.0


def test_confidence_grows_with_recent_activity():
    import datetime as _dt
    m = SetElo()
    base = _dt.date(2026, 1, 1)
    # 30 recent matches (3 sets each) → high confidence
    for i in range(30):
        m.apply_match(1, 2, None, None, [True, False, True],
                      day=base + _dt.timedelta(days=i * 2))
    p = m.predict(1, 2, None, None, MatchState())
    assert 0.4 < p.confidence <= 1.0


def test_stale_rating_has_low_confidence():
    import datetime as _dt
    m = SetElo()
    # player 1 played a lot LONG ago, then a returnee's handful of recent matches;
    # player 3 has been steadily active recently
    for i in range(40):
        m.apply_match(1, 2, None, None, [True, False, True],
                      day=_dt.date(2024, 1, 1) + _dt.timedelta(days=i * 3))
    for i in range(3):  # returnee: only 3 matches in 2026 after a long gap
        m.apply_match(1, 2, None, None, [True, False, True],
                      day=_dt.date(2026, 7, 1) + _dt.timedelta(days=i))
    for i in range(30):  # steadily active opponent 3
        m.apply_match(3, 4, None, None, [True, True],
                      day=_dt.date(2026, 3, 1) + _dt.timedelta(days=i * 3))
    stale = m.predict(1, 3, None, None, MatchState()).confidence
    active = m.predict(3, 4, None, None, MatchState()).confidence
    # the returnee (huge lifetime sets, thin recent) drags confidence down
    assert stale < active
    assert m.ratings[1].sets_seen > m.ratings[3].sets_seen  # lifetime: returnee has more
    assert m.ratings[1].recent < m.ratings[3].recent        # recent: far less


def test_apply_match_set_results_order():
    m = SetElo()
    # winner dropped the middle set: 3 updates, 2 for winner, 1 for loser
    m.apply_match(1, 2, "Hard", "A", [True, False, True])
    assert m.ratings[1].sets_seen == 3
    assert m.ratings[1].overall > m.ratings[2].overall


def test_prediction_dataclass():
    p = Prediction(p_a=0.7, confidence=0.5)
    assert p.p_a == 0.7


def test_expected_symmetry():
    assert math.isclose(_expected(1500, 1500), 0.5)
    assert math.isclose(_expected(1600, 1400) + _expected(1400, 1600), 1.0)
