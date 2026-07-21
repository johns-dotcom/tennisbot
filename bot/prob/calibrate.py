"""Fit the global Platt calibration (a, b) for SetElo on realized match
outcomes, walk-forward. No market price ever enters — CLAUDE.md rule 2
(no circularity) is preserved: we calibrate predictions against who actually
won, never against a price.

The raw Elo expectation is turned into a probability by p = sigmoid(a·logit(raw)
+ b). We collect each in-window match's raw winner-side logit (using only
ratings from strictly-earlier matches), mirror it as a loser-side sample so the
target is symmetric, and fit (a, b) by Newton's method on log loss.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from bot.prob.backtest import MIN_CONFIDENCE_SCORED
from bot.prob.elo import CONFIDENCE_FULL_SETS, SetElo, _expected


def _sigmoid(z: float) -> float:
    z = min(max(z, -30.0), 30.0)  # clamp to avoid math.exp overflow
    return 1 / (1 + math.exp(-z))


def _collect_raw_logits(db: Session, date_from: date, date_to: date) -> list[float]:
    """Walk-forward raw (pre-Platt) winner-side logits for in-window matches."""
    model = SetElo()
    rows = model._load_matches(db, through=date_to)
    xs: list[float] = []
    for row in rows:
        if row["date"] >= date_from:
            ra = model.ratings.get(row["winner_id"])
            rb = model.ratings.get(row["loser_id"])
            if ra is not None and rb is not None:
                conf = min(ra.sets_seen, rb.sets_seen) / CONFIDENCE_FULL_SETS
                if conf >= MIN_CONFIDENCE_SCORED:
                    raw = _expected(ra.blended(row["surface"]), rb.blended(row["surface"]))
                    raw = min(max(raw, 1e-9), 1 - 1e-9)
                    xs.append(math.log(raw / (1 - raw)))
        model.apply_match(row["winner_id"], row["loser_id"], row["surface"],
                          row["tier"], row["set_results"])
    return xs


def _logloss(data: list[tuple[float, float]], a: float, b: float) -> float:
    s = 0.0
    for x, y in data:
        p = min(max(_sigmoid(a * x + b), 1e-12), 1 - 1e-12)
        s += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return s / len(data)


def fit_platt(xs: list[float]) -> tuple[float, float]:
    """Newton fit of p = sigmoid(a·x + b) on the symmetric {(x,1),(-x,0)} set."""
    data = [(x, 1.0) for x in xs] + [(-x, 0.0) for x in xs]
    a, b = 1.0, 0.0
    for _ in range(100):
        ga = gb = haa = hab = hbb = 0.0
        for x, y in data:
            p = _sigmoid(a * x + b)
            d = p - y
            w = p * (1 - p)
            ga += d * x; gb += d
            haa += w * x * x; hab += w * x; hbb += w
        det = haa * hbb - hab * hab
        if abs(det) < 1e-12:
            break
        da = (ga * hbb - gb * hab) / det
        dbi = (haa * gb - hab * ga) / det
        a -= da; b -= dbi
        if abs(da) + abs(dbi) < 1e-10:
            break
    return a, b


@dataclass
class CalibrationFit:
    n: int
    a_fitted: float
    b_fitted: float
    logloss_raw: float          # a=1, b=0
    logloss_current: float      # current PLATT_A
    logloss_fitted: float

    def render(self) -> str:
        return (f"PLATT CALIBRATION FIT  (n={self.n} in-window predictions)\n"
                f"  fitted a = {self.a_fitted:.3f}   b = {self.b_fitted:+.3f}\n"
                f"  log loss   raw(a=1): {self.logloss_raw:.4f}\n"
                f"             current : {self.logloss_current:.4f}\n"
                f"             fitted  : {self.logloss_fitted:.4f}")


def fit_calibration(db: Session, date_from: date, date_to: date) -> CalibrationFit:
    from bot.prob.elo import PLATT_A

    xs = _collect_raw_logits(db, date_from, date_to)
    data = [(x, 1.0) for x in xs] + [(-x, 0.0) for x in xs]
    a, b = fit_platt(xs)
    return CalibrationFit(
        n=len(xs), a_fitted=a, b_fitted=b,
        logloss_raw=_logloss(data, 1.0, 0.0),
        logloss_current=_logloss(data, PLATT_A, 0.0),
        logloss_fitted=_logloss(data, a, b),
    )
