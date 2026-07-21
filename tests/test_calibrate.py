from bot.prob.calibrate import _logloss, fit_platt


def test_fit_platt_reduces_logloss_and_stays_symmetric():
    # winner logits with a mix of strong favorites and upsets (non-separable)
    xs = [2.0, 1.5, 1.0, 0.5, 0.2, -0.4, -1.0]
    a, b = fit_platt(xs)
    data = [(x, 1.0) for x in xs] + [(-x, 0.0) for x in xs]
    assert _logloss(data, a, b) <= _logloss(data, 1.0, 0.0) + 1e-9
    assert abs(b) < 1e-6          # symmetric target → no bias term
    assert a > 0


def test_fit_platt_shrinks_overconfident_input():
    # if raw logits are too large for the outcomes (many upsets among big favs),
    # the fit should pull a below 1 (shrink), not sharpen
    xs = [3.0, 3.0, 3.0, -3.0, 3.0, -3.0]  # big-magnitude preds, frequent upsets
    a, _ = fit_platt(xs)
    assert a < 1.0
