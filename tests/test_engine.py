"""Gate, debounce, and hold/release/kill logic — all I/O stubbed."""
from types import SimpleNamespace

import pytest

from bot.engine import AdvisoryEngine, edge_band
from bot.prob.model import Prediction


class FakeModel:
    def __init__(self, p=0.70, conf=0.9):
        self.p, self.conf = p, conf

    def predict(self, a, b, surface, tier, state):
        return Prediction(p_a=self.p, confidence=self.conf)


class FakeEst(SimpleNamespace):
    pass


def make_engine(p=0.70, conf=0.9, monkeypatch=None):
    eng = AdvisoryEngine(db_session_factory=None, model=FakeModel(p, conf))
    eng.ctx["MKT"] = {"player_a_id": 1, "player_b_id": 2, "name_a": "A Player",
                      "name_b": "B Player", "best_of": 3, "tier": "15"}
    calls = {"fired": [], "held": [], "killed": []}
    eng._fire = lambda ticker, est, **kw: calls["fired"].append(
        (ticker, est.state_key, kw.get("side"), kw.get("release_of"))) or \
        eng.last_advised.__setitem__(ticker, (est.state_key, kw.get("band", 0)))
    eng._hold = lambda ticker, est, *a, **kw: (
        calls["held"].append((ticker, est.state_key)),
        eng.pending.__setitem__(ticker, {"advisory_id": 99, "state": est.state_key,
                                         "block": None}))
    eng._kill = lambda adv_id, reason: calls["killed"].append((adv_id, reason))
    return eng, calls


def est(state="0-0", confidence=0.9, last_confirmed="0-0", final=False):
    return FakeEst(state_key=state, confidence=confidence,
                   last_confirmed=last_confirmed, final=final)


def test_fires_on_confirmed_state_with_edge():
    eng, calls = make_engine(p=0.70)
    # yes_ask 60 → implied .60, model .70 → edge .10; volume ok
    eng.on_quote("MKT", est(), 58, 60, 500)
    assert len(calls["fired"]) == 1
    assert calls["fired"][0][2] == "yes"


def test_no_fire_below_edge_threshold():
    eng, calls = make_engine(p=0.63)
    eng.on_quote("MKT", est(), 58, 60, 500)  # edge .03 < .06
    assert not calls["fired"] and not calls["held"]


def test_no_fire_below_volume_floor():
    eng, calls = make_engine(p=0.75)
    eng.on_quote("MKT", est(), 58, 60, 20)  # volume 20 < 100
    assert not calls["fired"] and not calls["held"]


def test_no_fire_low_model_confidence():
    eng, calls = make_engine(p=0.75, conf=0.2)
    eng.on_quote("MKT", est(), 58, 60, 500)
    assert not calls["fired"] and not calls["held"]


def test_no_side_picked_when_underdog_value():
    # model says A only 30% but yes_ask 60 → NO side: model .70, price 100-58=42 → edge .28
    eng, calls = make_engine(p=0.30)
    eng.on_quote("MKT", est(), 58, 60, 500)
    assert calls["fired"] and calls["fired"][0][2] == "no"


def test_unconfirmed_high_confidence_fires():
    eng, calls = make_engine(p=0.72)
    e = est(state="1-0", confidence=0.90, last_confirmed="0-0")
    eng.on_quote("MKT", e, 58, 60, 500)
    assert len(calls["fired"]) == 1


def test_unconfirmed_low_confidence_holds_then_releases():
    eng, calls = make_engine(p=0.72)
    e = est(state="1-0", confidence=0.75, last_confirmed="0-0")
    eng.on_quote("MKT", e, 58, 60, 500)
    assert calls["held"] == [("MKT", "1-0")]
    assert not calls["fired"]
    # score confirms the same state → release path fires
    e2 = est(state="1-0", confidence=1.0, last_confirmed="1-0")
    eng.on_confirmed_state("MKT", e2)
    assert any(f[3] == 99 for f in calls["fired"])  # release_of=99


def test_unconfirmed_low_confidence_holds_then_killed_on_conflict():
    eng, calls = make_engine(p=0.72)
    e = est(state="1-0", confidence=0.75, last_confirmed="0-0")
    eng.on_quote("MKT", e, 58, 60, 500)
    assert eng.pending
    e2 = est(state="0-1", confidence=1.0, last_confirmed="0-1")
    eng.on_confirmed_state("MKT", e2)
    assert calls["killed"] and calls["killed"][0][0] == 99


def test_kill_pending_external():
    eng, calls = make_engine(p=0.72)
    e = est(state="1-0", confidence=0.75, last_confirmed="0-0")
    eng.on_quote("MKT", e, 58, 60, 500)
    eng.kill_pending("MKT", "market suspended")
    assert calls["killed"] == [(99, "market suspended")]
    assert not eng.pending


def test_debounce_same_state_same_band():
    eng, calls = make_engine(p=0.70)
    eng.on_quote("MKT", est(), 58, 60, 500)
    eng.on_quote("MKT", est(), 58, 60, 500)  # identical
    assert len(calls["fired"]) == 1


def test_debounce_rearms_on_new_state():
    eng, calls = make_engine(p=0.70)
    eng.on_quote("MKT", est(), 58, 60, 500)
    e2 = est(state="1-0", confidence=1.0, last_confirmed="1-0")
    eng.on_quote("MKT", e2, 58, 60, 500)
    assert len(calls["fired"]) == 2


def test_debounce_rearms_on_band_crossing():
    eng, calls = make_engine(p=0.70)
    eng.on_quote("MKT", est(), 58, 60, 500)   # edge .10 → band 1
    eng.model.p = 0.79
    eng.on_quote("MKT", est(), 58, 60, 500)   # edge .19 → band 2
    assert len(calls["fired"]) == 2


def test_never_advises_final():
    eng, calls = make_engine(p=0.9)
    eng.on_quote("MKT", est(state="final", final=True), 58, 60, 500)
    assert not calls["fired"] and not calls["held"]


def test_edge_band_boundaries():
    assert edge_band(0.05) == -1
    assert edge_band(0.06) == 0
    assert edge_band(0.10) == 1
    assert edge_band(0.16) == 2
