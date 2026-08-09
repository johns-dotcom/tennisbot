"""Offline feature-strength evaluation.

Answers "which VARIABLES actually improve the model?" without waiting on paper
bets. Walks every played match in date order (no lookahead), builds the base
surface-Elo prediction plus a set of as-of candidate features, then — on a
held-out slice — measures how much each feature reduces out-of-sample Brier /
log-loss ON TOP OF a recalibrated Elo baseline.

Both the baseline and each feature model get the same recalibration freedom
(a + b·elo_logit), so the delta is attributable to the feature, not to fitting.
Pure Python (no numpy): a small Newton-Raphson logistic solver.

CIRCULARITY: features come only from match history/state, never market price.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from bot.log import get_logger
from bot.prob.elo import SetElo
from bot.prob.model import MatchState

log = get_logger("prob.feature_eval")

# candidate features (as-of, history-only). Each is a per-player scalar; the
# model sees the winner-minus-opponent DIFFERENCE, oriented by the row label.
FEATURES = ("rest_days", "form_last10", "fatigue_min14d", "h2h_margin",
            "career_matches", "surface_form",
            "serve_ace", "serve_1stwin", "serve_bpsaved", "age_gap",
            # set-statistic candidates (career-accumulated, as-of, history-only):
            # set-1 win rate (slow starter), deciding-set win rate (resilience),
            # and share of wins that were straight-sets (dominance).
            "set1_winrate", "decider_winrate", "skunk_rate")
# h2h_margin has no per-side value (it's already a pairing margin); everything
# else is a per-player scalar diffed winner-minus-opponent
_PLAYER_FEATS = tuple(f for f in FEATURES if f != "h2h_margin")


def _sig(z: float) -> float:
    if z < -35:
        return 1e-15
    if z > 35:
        return 1 - 1e-15
    return 1.0 / (1.0 + math.exp(-z))


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gauss-Jordan solve a·x=b for small systems."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        if abs(m[c][c]) < 1e-12:
            m[c][c] = 1e-12
        pv = m[c][c]
        for j in range(c, n + 1):
            m[c][j] /= pv
        for r in range(n):
            if r != c and m[r][c]:
                f = m[r][c]
                for j in range(c, n + 1):
                    m[r][j] -= f * m[c][j]
    return [m[i][n] for i in range(n)]


def _fit_logistic(rows: list[tuple[list[float], int]], p: int,
                  iters: int = 25, ridge: float = 1e-3) -> list[float]:
    """Newton-Raphson logistic regression. rows = (x_vector[len p], y). Returns
    coefficients (x already includes a leading 1 for the intercept). Ridge keeps
    the Hessian invertible on collinear features."""
    beta = [0.0] * p
    for _ in range(iters):
        grad = [0.0] * p
        hess = [[ridge if i == j else 0.0 for j in range(p)] for i in range(p)]
        for x, y in rows:
            z = sum(beta[k] * x[k] for k in range(p))
            mu = _sig(z)
            w = mu * (1 - mu)
            r = mu - y
            for i in range(p):
                grad[i] += r * x[i]
                for j in range(p):
                    hess[i][j] += w * x[i] * x[j]
        for i in range(p):
            grad[i] += ridge * beta[i]
        step = _solve(hess, grad)
        maxstep = max(abs(s) for s in step)
        if maxstep > 4:  # damp
            step = [s * 4 / maxstep for s in step]
        beta = [beta[i] - step[i] for i in range(p)]
        if maxstep < 1e-6:
            break
    return beta


def _score(rows: list[tuple[list[float], int]], beta: list[float]) -> tuple[float, float]:
    brier = ll = 0.0
    for x, y in rows:
        p = _sig(sum(beta[k] * x[k] for k in range(len(beta))))
        brier += (p - y) ** 2
        ll += -(y * math.log(max(p, 1e-15)) + (1 - y) * math.log(max(1 - p, 1e-15)))
    n = len(rows)
    return brier / n, ll / n


@dataclass
class _PState:
    last_date: date | None = None
    results: deque = field(default_factory=lambda: deque(maxlen=10))   # (won bool)
    surf_results: dict = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=10)))
    minutes: deque = field(default_factory=lambda: deque(maxlen=40))   # (date, minutes)
    serve: deque = field(default_factory=lambda: deque(maxlen=12))     # serve lines
    career: int = 0
    # set-statistic accumulators (career counts, updated as-of after each match)
    set1_w: int = 0
    set1_n: int = 0
    dec_w: int = 0
    dec_n: int = 0
    straight_w: int = 0
    wins_n: int = 0


def evaluate_features(db: Session, *, eval_from: date, eval_to: date,
                      train_frac: float = 0.6, min_conf: float = 0.25,
                      combos: tuple = ()) -> dict:
    """Walk history, collect as-of feature rows for matches in [eval_from,
    eval_to), split by date into train/test, and report per-feature +
    all-features out-of-sample Brier/log-loss lift over recalibrated Elo, plus
    per-feature data coverage (fraction of rows with real, non-imputed values)."""
    from bot.models import Match, Player
    from bot.stats.types import PLAYED_OUTCOMES, round_rank
    rows = db.execute(
        select(Match.match_date, Match.winner_id, Match.loser_id, Match.surface,
               Match.tourney_level, Match.round, Match.best_of, Match.minutes,
               Match.stats)
        .where(Match.outcome.in_(PLAYED_OUTCOMES), Match.is_duplicate.is_(False),
               Match.match_date.is_not(None), Match.match_date < eval_to)).all()
    rows = sorted(rows, key=lambda r: (r[0], round_rank(r[5])))
    dob = dict(db.execute(select(Player.id, Player.dob)
                          .where(Player.dob.is_not(None))).all())

    elo = SetElo()
    walk = elo._load_matches(db, through=eval_to)
    walk_by_key = defaultdict(list)
    for m in walk:
        walk_by_key[(m["date"], m["winner_id"], m["loser_id"])].append(m)

    st: dict[int, _PState] = defaultdict(_PState)
    h2h: dict[tuple, list] = defaultdict(lambda: [0, 0])
    samples = []           # (date, elo_logit_winner, feat_diff dict)
    cover = {f: 0 for f in FEATURES}   # rows with real data on BOTH sides

    def feats(pid: int, surf: str | None, d: date) -> tuple[dict, set]:
        """(values, present) — `present` is the set of features with real
        (non-default) data for this player right now."""
        s = st[pid]
        present = set()
        rest = (d - s.last_date).days if s.last_date else 14
        form = (sum(s.results) / len(s.results)) if s.results else 0.5
        sf = s.surf_results.get(surf)
        surf_form = (sum(sf) / len(sf)) if sf else 0.5
        fat = sum(mn for (dd, mn) in s.minutes if mn and (d - dd).days <= 14)
        set1 = s.set1_w / s.set1_n if s.set1_n else 0.5
        dec = s.dec_w / s.dec_n if s.dec_n else 0.5
        skunk = s.straight_w / s.wins_n if s.wins_n else 0.5
        v = {"rest_days": min(rest, 30), "form_last10": form,
             "fatigue_min14d": fat, "career_matches": min(s.career, 200),
             "surface_form": surf_form,
             "serve_ace": 0.0, "serve_1stwin": 0.0, "serve_bpsaved": 0.0,
             "age_gap": 26.0,
             "set1_winrate": set1, "decider_winrate": dec, "skunk_rate": skunk}
        if s.results:
            present |= {"form_last10", "career_matches"}
        if s.last_date:
            present.add("rest_days")
        if sf:
            present.add("surface_form")
        if any(mn for _, mn in s.minutes):
            present.add("fatigue_min14d")
        # set stats need a minimum sample to count as real (thin per-set rates
        # on a handful of matches are noise — rule 4)
        if s.set1_n >= 10:
            present.add("set1_winrate")
        if s.dec_n >= 6:
            present.add("decider_winrate")
        if s.wins_n >= 10:
            present.add("skunk_rate")
        if s.serve:
            svpt = sum(x[1] for x in s.serve) or 1
            fin = sum(x[2] for x in s.serve) or 1
            bpf = sum(x[4] for x in s.serve) or 1
            v["serve_ace"] = sum(x[0] for x in s.serve) / svpt
            v["serve_1stwin"] = sum(x[3] for x in s.serve) / fin
            v["serve_bpsaved"] = sum(x[5] for x in s.serve) / bpf
            present |= {"serve_ace", "serve_1stwin", "serve_bpsaved"}
        if dob.get(pid):
            v["age_gap"] = (d - dob[pid]).days / 365.25
            present.add("age_gap")
        return v, present

    def _serveline(stats, pref):
        if not stats:
            return None
        try:
            return (float(stats[f"{pref}_ace"]), float(stats[f"{pref}_svpt"]),
                    float(stats[f"{pref}_1stIn"]), float(stats[f"{pref}_1stWon"]),
                    float(stats[f"{pref}_bpFaced"]), float(stats[f"{pref}_bpSaved"]))
        except (KeyError, TypeError, ValueError):
            return None

    for (mdate, wid, lid, surface, tier, rnd, best_of, minutes, stats) in rows:
        if mdate >= eval_from:
            try:
                pred = elo.predict(wid, lid, surface, tier,
                                   MatchState(0, 0, best_of if best_of in (3, 5) else 3),
                                   as_of=mdate)
            except Exception:
                pred = None
            if pred is not None and pred.confidence >= min_conf:
                fw, pw = feats(wid, surface, mdate)
                fl, pl = feats(lid, surface, mdate)
                pair = (min(wid, lid), max(wid, lid))
                hw = h2h[pair][0 if wid < lid else 1] - h2h[pair][1 if wid < lid else 0]
                diff = {k: fw[k] - fl[k] for k in fw}
                diff["h2h_margin"] = float(hw)
                both = pw & pl
                for f in _PLAYER_FEATS:
                    if f in both:
                        cover[f] += 1
                if hw != 0:
                    cover["h2h_margin"] += 1
                p = min(max(pred.p_a, 1e-6), 1 - 1e-6)
                samples.append((mdate, math.log(p / (1 - p)), diff))
        # ---- apply match to state AFTER predicting (no lookahead) ----
        sets = None
        for m in walk_by_key.get((mdate, wid, lid), []):
            if m["set_results"]:
                elo.apply_match(wid, lid, surface, tier, m["set_results"], day=mdate)
                if sets is None:
                    sets = m["set_results"]
        # per-match set facts (set_results[i] = True if won by the MATCH winner)
        need = (best_of if best_of in (3, 5) else 3) // 2 + 1
        n_sets = len(sets) if sets else 0
        reached_dec = n_sets == 2 * need - 1
        straight = n_sets == need  # winner took it in the minimum number of sets
        wl, ll_ = _serveline(stats, "w"), _serveline(stats, "l")
        for pid, won, sl in ((wid, True, wl), (lid, False, ll_)):
            s = st[pid]
            s.last_date = mdate
            s.results.append(1 if won else 0)
            s.surf_results[surface].append(1 if won else 0)
            if minutes:
                s.minutes.append((mdate, minutes))
            if sl:
                s.serve.append(sl)
            s.career += 1
            if sets:
                s.set1_n += 1
                s.set1_w += (sets[0] == won)          # did THIS player win set 1
                if reached_dec:
                    s.dec_n += 1
                    s.dec_w += won                    # match winner won the decider
                if won:
                    s.wins_n += 1
                    s.straight_w += straight
        pair = (min(wid, lid), max(wid, lid))
        h2h[pair][0 if wid < lid else 1] += 1

    if len(samples) < 200:
        return {"error": f"only {len(samples)} eval samples"}

    samples.sort(key=lambda s: s[0])
    cut = samples[int(len(samples) * train_frac)][0]
    train_raw = [s for s in samples if s[0] < cut]
    test_raw = [s for s in samples if s[0] >= cut]

    def build(raw, feats_used):
        # balanced +/- rows from RAW diffs (negate the raw value for the mirror,
        # THEN standardize — so orientation is exact and the balanced set is
        # symmetric about 0)
        out = []
        for _, logit, d in raw:
            fx = [d[f] for f in feats_used]
            out.append(([1.0, logit] + fx, 1))                    # winner won
            out.append(([1.0, -logit] + [-v for v in fx], 0))     # mirror
        return out

    def run(feats_used):
        p = 2 + len(feats_used)
        tr, te = build(train_raw, feats_used), build(test_raw, feats_used)
        if feats_used:  # standardize feature columns (idx 2..) on train, apply to both
            k = len(feats_used)
            mean = [sum(x[2 + i] for x, _ in tr) / len(tr) for i in range(k)]
            std = [(sum((x[2 + i] - mean[i]) ** 2 for x, _ in tr) / len(tr)) ** 0.5 or 1.0
                   for i in range(k)]
            for rows in (tr, te):
                for x, _ in rows:
                    for i in range(k):
                        x[2 + i] = (x[2 + i] - mean[i]) / std[i]
        beta = _fit_logistic(tr, p)
        return _score(te, beta), beta

    n_ev = len(samples)
    (base_brier, base_ll), _ = run([])
    per = {}
    for f in FEATURES:
        (b, ll), beta = run([f])
        per[f] = {"d_brier": base_brier - b, "d_logloss": base_ll - ll,
                  "beta": round(beta[2], 4), "coverage": round(cover[f] / n_ev, 3)}
    (all_b, all_ll), _ = run(list(FEATURES))
    combo_out = {}
    for combo in combos:
        (cb, cll), _ = run(list(combo))
        combo_out["+".join(combo)] = {"d_brier": base_brier - cb,
                                      "d_logloss": base_ll - cll}
    return {
        "n_train": len(train_raw), "n_test": len(test_raw),
        "base_brier": base_brier, "base_logloss": base_ll,
        "per_feature": per,
        "all_features": {"d_brier": base_brier - all_b, "d_logloss": base_ll - all_ll},
        "combos": combo_out,
    }


# --------------------------------------------------------------------------- #
# production coefficient fit — learns raw β for the two proven features with the
# CALIBRATED Elo logit as a FIXED offset (Platt untouched), symmetric (no
# intercept). Returns coefficients to bake into bot.prob.elo + the OOS gate.
# --------------------------------------------------------------------------- #
def fit_adjustment(db: Session, *, eval_from: date, eval_to: date,
                   train_frac: float = 0.6, min_conf: float = 0.25) -> dict:
    from bot.models import Match
    from bot.stats.types import PLAYED_OUTCOMES, round_rank
    rows = db.execute(
        select(Match.match_date, Match.winner_id, Match.loser_id, Match.surface,
               Match.tourney_level, Match.round, Match.best_of)
        .where(Match.outcome.in_(PLAYED_OUTCOMES), Match.is_duplicate.is_(False),
               Match.match_date.is_not(None), Match.match_date < eval_to)).all()
    rows = sorted(rows, key=lambda r: (r[0], round_rank(r[4])))
    elo = SetElo()
    walk = elo._load_matches(db, through=eval_to)
    wk = defaultdict(list)
    for m in walk:
        wk[(m["date"], m["winner_id"], m["loser_id"])].append(m)

    career: dict[int, int] = defaultdict(int)
    surf: dict[int, dict] = defaultdict(lambda: defaultdict(lambda: deque(maxlen=10)))
    samples = []  # (date, elo_logit, career_diff, sf_diff)

    def sf(pid, s):
        d = surf[pid].get(s)
        return (sum(d) / len(d)) if d else 0.5

    for (mdate, wid, lid, surface, tier, rnd, best_of) in rows:
        if mdate >= eval_from:
            try:
                pred = elo.predict(wid, lid, surface, tier,
                                   MatchState(0, 0, best_of if best_of in (3, 5) else 3),
                                   as_of=mdate)
            except Exception:
                pred = None
            if pred is not None and pred.confidence >= min_conf:
                cd = min(career[wid], 200) - min(career[lid], 200)
                sd = sf(wid, surface) - sf(lid, surface)
                p = min(max(pred.p_a, 1e-6), 1 - 1e-6)
                samples.append((mdate, math.log(p / (1 - p)), float(cd), sd))
        for m in wk.get((mdate, wid, lid), []):
            if m["set_results"]:
                elo.apply_match(wid, lid, surface, tier, m["set_results"], day=mdate)
        career[wid] += 1
        career[lid] += 1
        surf[wid][surface].append(1)
        surf[lid][surface].append(0)

    if len(samples) < 200:
        return {"error": f"only {len(samples)} samples"}
    samples.sort(key=lambda s: s[0])
    cut = samples[int(len(samples) * train_frac)][0]

    def rows_for(sset):
        # features = [calibrated_elo_logit, career_diff, sf_diff]; balanced +/-,
        # no intercept (symmetric two-player model)
        out = []
        for _, lg, cd, sd in sset:
            out.append(([lg, cd, sd], 1))
            out.append(([-lg, -cd, -sd], 0))
        return out
    tr = rows_for([s for s in samples if s[0] < cut])
    te = rows_for([s for s in samples if s[0] >= cut])

    # joint fit: Elo-logit coefficient is FREE (so the features capture only the
    # residual, not base miscalibration — this is what keeps surface_form's sign
    # correct). c1≈1 confirms the existing Platt is fine on this window.
    joint = _fit_logistic(tr, 3)
    c1, c_car, c_sf = joint
    # Platt-only: recalibrate the Elo scale alone, no features
    platt = _fit_logistic([([x[0]], y) for x, y in tr], 1)
    base_b, base_ll = _score(te, [1.0, 0.0, 0.0])              # current production
    platt_b, platt_ll = _score([([x[0]], y) for x, y in te], platt)  # recalibrated only
    joint_b, joint_ll = _score(te, joint)                     # recalibrated + features
    return {"c1_elo": c1, "platt_scale": platt[0],
            "beta_career": c_car, "beta_surface_form": c_sf,
            "n_test": len(te) // 2, "base_brier": base_b,
            "recal_d_brier": base_b - platt_b,          # gain from recalibration alone
            "recal_d_logloss": base_ll - platt_ll,
            "feature_marginal_d_brier": platt_b - joint_b,   # TRUE feature gain beyond recal
            "feature_marginal_d_logloss": platt_ll - joint_ll,
            "total_d_brier": base_b - joint_b}


# --------------------------------------------------------------------------- #
# recurring monitor — an EXPANDING window (fixed anchor → today), so the test
# sample grows every day and we can watch each variable's strength stabilize.
# Snapshots persist in ingest_state; the /features page reads them.
# --------------------------------------------------------------------------- #
MONITOR_ANCHOR = date(2026, 1, 1)   # start of the expanding eval window


def snapshot(db: Session, anchor: date = MONITOR_ANCHOR) -> dict:
    """One expanding-window eval, compacted for storage/display."""
    r = evaluate_features(db, eval_from=anchor, eval_to=date.today())
    if "error" in r:
        return {"as_of": date.today().isoformat(), "error": r["error"]}
    return {
        "as_of": date.today().isoformat(),
        "anchor": anchor.isoformat(),
        "n_test": r["n_test"], "base_brier": round(r["base_brier"], 5),
        "features": {f: {"d_brier": round(v["d_brier"], 6),
                         "d_logloss": round(v["d_logloss"], 6),
                         "beta": v["beta"], "coverage": v["coverage"]}
                     for f, v in r["per_feature"].items()},
    }


def _kv_put(db: Session, key: str, value: str) -> None:
    from datetime import datetime, timezone

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from bot.models import IngestState
    now = datetime.now(timezone.utc)
    db.execute(pg_insert(IngestState).values(key=key, value=value, updated_at=now)
               .on_conflict_do_update(index_elements=["key"],
                                      set_={"value": value, "updated_at": now}))


def _kv_get(db: Session, key: str):
    from bot.models import IngestState
    return db.execute(select(IngestState.value).where(IngestState.key == key)).scalar()


def persist_snapshot(db: Session, anchor: date = MONITOR_ANCHOR, keep: int = 90) -> dict:
    """Compute today's snapshot and store it (idempotent per day). Returns it."""
    import json
    snap = snapshot(db, anchor)
    day = snap["as_of"]
    _kv_put(db, f"feateval:{day}", json.dumps(snap))
    idx = json.loads(_kv_get(db, "feateval:index") or "[]")
    if day not in idx:
        idx.append(day)
    idx = sorted(set(idx))[-keep:]
    _kv_put(db, "feateval:index", json.dumps(idx))
    db.commit()
    log.info("feature-eval snapshot stored", as_of=day,
             n_test=snap.get("n_test"), error=snap.get("error"))
    return snap


def load_snapshots(db: Session, limit: int = 40) -> list[dict]:
    """Recent snapshots, oldest→newest, for the monitor page."""
    import json
    idx = json.loads(_kv_get(db, "feateval:index") or "[]")
    out = []
    for day in idx[-limit:]:
        raw = _kv_get(db, f"feateval:{day}")
        if raw:
            try:
                out.append(json.loads(raw))
            except Exception:
                pass
    return out
