"""Sanity checks for the pure-Python logistic solver behind feature_eval."""
import math

from bot.prob.feature_eval import _fit_logistic, _score, _sig


def test_logistic_recovers_known_coefficients():
    # generate y ~ sigmoid(0.5 + 1.5*x) deterministically (no RNG in tests)
    rows = []
    for i in range(4000):
        x = -3 + 6 * (i / 4000)          # sweep x in [-3, 3]
        p = _sig(0.5 + 1.5 * x)
        # place proportional positives/negatives at this x via a fine grid
        y = 1 if (i * 2654435761 % 1000) / 1000 < p else 0
        rows.append(([1.0, x], y))
    b = _fit_logistic(rows, 2)
    assert abs(b[0] - 0.5) < 0.25 and abs(b[1] - 1.5) < 0.3


def test_score_perfect_and_chance():
    perfect = [([1.0], 1)] * 50  # sigmoid(large) ~1 vs y=1
    # a strongly positive intercept → p≈1, brier≈0, on all-ones labels
    brier, ll = _score([([10.0], 1)] * 50, [1.0])
    assert brier < 0.01 and ll < 0.05
    chance, _ = _score([([0.0], 1), ([0.0], 0)], [1.0])  # p=0.5
    assert abs(chance - 0.25) < 1e-9
