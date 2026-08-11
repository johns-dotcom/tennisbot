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
from bot.prob.elo import CONFIDENCE_RECENT_FULL, SetElo, _expected


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
                # gate on the SAME recency-based confidence the model uses at
                # predict time (decayed to the match date), so PLATT_A is fitted
                # on the population it will be applied to — not a sets_seen sample.
                conf = min(SetElo._recent_as_of(ra, row["date"]),
                           SetElo._recent_as_of(rb, row["date"])) / CONFIDENCE_RECENT_FULL
                if conf >= MIN_CONFIDENCE_SCORED:
                    raw = _expected(ra.blended(row["surface"]), rb.blended(row["surface"]))
                    raw = min(max(raw, 1e-9), 1 - 1e-9)
                    xs.append(math.log(raw / (1 - raw)))
        model.apply_match(row["winner_id"], row["loser_id"], row["surface"],
                          row["tier"], row["set_results"], day=row.get("date"))
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


def fit_state_scale(db: Session, date_from: date, date_to: date,
                    min_n: int = 2000, min_conf: float = 0.25) -> dict:
    """Fit the symmetric per-set-state logit-scaling A (logit(p_true)=A·logit(p_iid))
    walk-forward on realized outcomes. Returns {"scale": {"bo|hi|lo": A}, "detail":
    {...}} keeping ONLY states with enough samples AND a positive out-of-sample
    log-loss lift — so a bad window simply omits that state (→ falls back to the
    default/last-good), never regresses. Price never enters (rule 2)."""
    from collections import defaultdict

    from bot.prob.feature_eval import _fit_logistic, _score
    from bot.prob.model import MatchState
    from bot.prob.state_adjust import race_win_prob, set_prob_from_match_prob

    def clamp(p):
        return min(max(p, 1e-6), 1 - 1e-6)

    def raw_iid(p_pre, bo, sl, so):
        """RAW iid conditioned prob (leader perspective) — bypasses the current
        STATE_LOGIT_SCALE so the fit recovers the ABSOLUTE scaling, not a residual
        on top of the value already applied."""
        need = bo // 2 + 1
        s = set_prob_from_match_prob(p_pre, bo)
        return clamp(race_win_prob(s, need - sl, need - so))

    model = SetElo()
    walk = model._load_matches(db, through=date_to)
    model = SetElo()
    by_state: dict = defaultdict(list)   # (bo,hi,lo) -> [(date, iid_logit_leader, y)]
    for m in walk:
        d, wid, lid, bo, sr = (m["date"], m["winner_id"], m["loser_id"],
                               m["best_of"], m["set_results"])
        surf, tier = m["surface"], m["tier"]
        if d >= date_from and bo in (3, 5) and sr and len(sr) >= 2:
            try:
                pred = model.predict(wid, lid, surf, tier, MatchState(0, 0, bo), as_of=d)
            except Exception:
                pred = None
            if pred is not None and pred.confidence >= min_conf:
                p_w = clamp(pred.p_a)
                aw = bw = 0
                for k in range(len(sr) - 1):          # every intermediate boundary
                    if sr[k]:
                        aw += 1
                    else:
                        bw += 1
                    if aw == bw:                       # even state: symmetric, skip
                        continue
                    if aw > bw:
                        leader_won, sl, so, p_pre = True, aw, bw, p_w
                    else:
                        leader_won, sl, so, p_pre = False, bw, aw, 1 - p_w
                    try:
                        pm = raw_iid(p_pre, bo, sl, so)
                    except Exception:
                        continue
                    by_state[(bo, sl, so)].append(
                        (d, math.log(pm / (1 - pm)), 1 if leader_won else 0))
        model.apply_match(wid, lid, surf, tier, sr, day=d)

    scale, detail = {}, {}
    for key, S in by_state.items():
        if len(S) < min_n:
            continue
        S.sort(key=lambda r: r[0])
        cut = S[int(len(S) * 0.6)][0]
        tr = [s for s in S if s[0] < cut]
        te = [s for s in S if s[0] >= cut]
        if len(tr) < 200 or len(te) < 200:
            continue
        tr_rows = [([lg], y) for _, lg, y in tr] + [([-lg], 1 - y) for _, lg, y in tr]
        A = _fit_logistic(tr_rows, 1)[0]
        te_rows = [([lg], y) for _, lg, y in te]
        base_br, base_ll = _score(te_rows, [1.0])
        fit_br, fit_ll = _score(te_rows, [A])
        ll_lift, br_lift = base_ll - fit_ll, base_br - fit_br
        k = f"{key[0]}|{key[1]}|{key[2]}"
        detail[k] = {"n": len(S), "A": round(A, 4),
                     "oos_logloss_lift": round(ll_lift, 6),
                     "oos_brier_lift": round(br_lift, 6)}
        # apply ONLY when it improves BOTH proper-scoring metrics out-of-sample —
        # a symmetric sharpen that helps Brier but hurts log-loss is NOT shipped.
        if ll_lift > 0 and br_lift > 0 and abs(A - 1.0) > 0.01:
            scale[k] = round(A, 4)
    return {"scale": scale, "detail": detail}


def refit_and_persist_state_scale(db: Session, window_days: int = 365) -> dict:
    """Refit the state scaling over the trailing window and store a new
    model_calibration row (only if at least one state passed the OOS gate). Called
    by the daily ingest. Returns the fit summary."""
    from datetime import timedelta

    from bot.models import ModelCalibration
    to = date.today()
    res = fit_state_scale(db, to - timedelta(days=window_days), to)
    if res["scale"]:
        db.add(ModelCalibration(window_days=window_days,
                                state_scale=res["scale"], detail=res["detail"]))
        db.commit()
    return res


PLATT_A_MIN, PLATT_A_MAX = 0.7, 1.8   # clamp the persisted scalar to a sane range
PLATT_REFIT_MIN_N = 5000              # don't trust a fit on a thin window


def refit_and_persist_platt(db: Session, window_days: int = 365) -> dict:
    """Refit the global pre-match Platt scalar over the trailing window and store a
    new model_calibration row — only when the fit clears the sample-size gate and
    strictly improves out-of-sample log loss over the value currently in force.
    Called by the daily ingest so the calibration tracks the ratings instead of
    drifting. Returns a summary dict."""
    from datetime import timedelta

    from bot.models import ModelCalibration
    to = date.today()
    fit = fit_calibration(db, to - timedelta(days=window_days), to)
    a = min(max(fit.a_fitted, PLATT_A_MIN), PLATT_A_MAX)
    improved = fit.n >= PLATT_REFIT_MIN_N and fit.logloss_fitted < fit.logloss_current
    summary = {"n": fit.n, "a_fitted": round(fit.a_fitted, 4), "a_stored": round(a, 4),
               "logloss_current": round(fit.logloss_current, 6),
               "logloss_fitted": round(fit.logloss_fitted, 6), "persisted": bool(improved)}
    if improved:
        db.add(ModelCalibration(window_days=window_days, platt_a=a, detail=summary))
        db.commit()
    return summary


def load_platt_calibration(db: Session) -> float | None:
    """Newest persisted global Platt scalar (or None). Called on model rebuild to
    override the elo.py default; any failure keeps whatever is currently in force."""
    try:
        from sqlalchemy import select

        from bot.models import ModelCalibration
        row = db.execute(
            select(ModelCalibration).where(ModelCalibration.platt_a.is_not(None))
            .order_by(ModelCalibration.fitted_at.desc())).scalars().first()
        return float(row.platt_a) if row and row.platt_a else None
    except Exception:
        return None


def fit_calibration(db: Session, date_from: date, date_to: date) -> CalibrationFit:
    from bot.prob.elo import PLATT_A

    xs = _collect_raw_logits(db, date_from, date_to)
    if not xs:  # empty window / all below the confidence gate — nothing to fit
        return CalibrationFit(n=0, a_fitted=1.0, b_fitted=0.0,
                              logloss_raw=0.0, logloss_current=0.0, logloss_fitted=0.0)
    data = [(x, 1.0) for x in xs] + [(-x, 0.0) for x in xs]
    a, b = fit_platt(xs)
    return CalibrationFit(
        n=len(xs), a_fitted=a, b_fitted=b,
        logloss_raw=_logloss(data, 1.0, 0.0),
        logloss_current=_logloss(data, PLATT_A, 0.0),
        logloss_fitted=_logloss(data, a, b),
    )
