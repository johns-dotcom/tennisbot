"""Deciding-set signal — the separate decider flag/weighting."""
from bot.prob.decider import decider_read, set_form_signal


def test_signal_symmetry_and_direction():
    # equal deciding-set rates → coin flip; stronger decider player → favored
    assert abs(set_form_signal(0.6, 0.6) - 0.5) < 1e-9
    assert set_form_signal(0.7, 0.4) > 0.5
    assert set_form_signal(0.4, 0.7) < 0.5


def test_missing_data_falls_back_to_base():
    p, sig = decider_read(0.62, None, 0.5)
    assert p == 0.62 and sig is None


def test_weight_pulls_toward_set_form():
    # base says 0.50; A is the far better decider player → weighted read > 0.50,
    # but stays between base and the pure set-form signal
    base = 0.50
    p, sig = decider_read(base, 0.68, 0.42, weight=0.35)
    assert sig is not None and sig > 0.5
    assert base < p < sig


def test_weight_zero_is_base_and_one_is_signal():
    p0, _ = decider_read(0.55, 0.7, 0.4, weight=0.0)
    p1, sig = decider_read(0.55, 0.7, 0.4, weight=1.0)
    assert abs(p0 - 0.55) < 1e-9
    assert abs(p1 - sig) < 1e-9


def test_symmetry_two_players_sum_to_one():
    a, _ = decider_read(0.6, 0.65, 0.45, weight=0.4)
    b, _ = decider_read(0.4, 0.45, 0.65, weight=0.4)
    assert abs(a + b - 1.0) < 1e-9
