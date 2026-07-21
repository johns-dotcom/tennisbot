from datetime import datetime, timezone

from bot.scenarios import build_gameflow
from bot.stats.profile import compute_set_rates
from tests.test_advisory import _profiles
from tests.test_stats_profile import AS_OF

START = datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc)


def build(p_a=0.62, fatigue_b=None, model_confidence=0.8):
    a, b, hist_a, hist_b = _profiles()  # A strong in deciders; B decider-poor
    return build_gameflow(
        ticker_a="EV-ADA", ticker_b="EV-ZEL", event_ticker="EV",
        name_a="Julia Adams", name_b="Petra Zelnickova", id_a=1, id_b=2,
        prof_a=a, prof_b=b, hist_a=hist_a, hist_b=hist_b,
        set_rates_a=compute_set_rates(hist_a, AS_OF, min_sample=5),
        set_rates_b=compute_set_rates(hist_b, AS_OF, min_sample=5),
        p_a=p_a, best_of=3, start=START, as_of=AS_OF,
        model_confidence=model_confidence,
        fatigue_b=fatigue_b, event_label="Adams vs Zelnickova")


def test_gameflow_picks_the_stronger_side():
    c = build()
    assert c is not None
    assert c.kind == "gameflow"
    assert c.market_ticker == "EV-ADA"  # Adams: model + decider edge
    assert c.state_key == "plan"


def test_gameflow_sequenced_structure():
    c = build()
    n = c.narrative
    assert "is the play" in n                       # entry thesis
    assert "If it reaches set 3" in n               # decider branch
    assert "straight set 3s" in n                   # opponent decider streak
    assert "drops set 1" in n and "stay away" in n  # risk rule
    assert "%" in n                                 # stat-dense


def test_gameflow_fatigue_cited():
    c = build(fatigue_b={"played": True, "went_distance": True})
    assert "played yesterday and went the distance" in c.narrative
    no_fat = build()
    assert "played yesterday" not in no_fat.narrative


def test_gameflow_facts_payload_complete():
    c = build()
    f = c.facts
    assert f["match"] == "Julia Adams vs Petra Zelnickova"
    assert 1 in f["set_rates_watch"] or "1" in f["set_rates_watch"]
    assert f["decider_watch"] is not None
    assert isinstance(f["support"], list)


def test_gameflow_decider_prob_consistent():
    c = build()
    # watch side is the better decider player: conditional prob should beat 50%
    assert c.p_at_state > 0.5
    assert 0 < c.p_prematch < 1


def test_gameflow_cites_samples_and_confidence():
    c = build(model_confidence=0.8)
    n = c.narrative
    import re
    # every set rate cited with its W-L sample and window
    assert re.search(r"\d+% \(\d+-\d+, (past year|career)\)", n), n
    assert "(past year)" in n or "(career)" in n  # decider records windowed
    assert "model confidence 80%" in n
    assert c.facts["model_confidence"] == 0.8


def test_gameflow_decider_caveat_when_record_contradicts_pick():
    # force the decider-poor player to be the watch side via extreme model lean
    c = build(p_a=0.05)
    assert c.market_ticker == "EV-ZEL"
    assert "caveat" in c.narrative.lower()
    assert "taking profit" in c.narrative


def test_gameflow_supportive_decider_framing():
    c = build()
    # Adams' decider edge rests on a 3-1 record (n=4): honest prose hedges the
    # directional read rather than asserting the distance "favors" him.
    assert "the distance leans toward Adams" in c.narrative
    assert "thin decider samples" in c.narrative


def test_rate_n_flags_thin_and_widened_samples():
    from bot.scenarios import _rate_n
    from bot.stats.fallback import Stat, rate
    # solid recent sample — no flag
    solid = rate(34, 16, "last365")
    assert _rate_n(solid) == "68% (34-16, past year)"
    # thin sample — flagged inline
    thin = rate(3, 1, "last365")
    assert "thin sample" in _rate_n(thin)
    # widened to career (recent too thin) — flagged, not silently passed off
    widened = Stat(value=0.6, n=25, window="career", method="widened",
                   wins=15, losses=10)
    assert "recent sample too thin" in _rate_n(widened)


def test_strongest_set_not_crowned_on_thin_sample():
    from bot.scenarios import _is_thin
    from bot.stats.fallback import rate
    assert _is_thin(rate(2, 1, "last365")) is True     # n=3
    assert _is_thin(rate(34, 16, "last365")) is False  # n=50
    assert _is_thin(rate(0, 0, "last365")) is False    # omitted, not "thin"


def test_gameflow_low_confidence_caveat():
    c = build(model_confidence=0.45)
    assert "size accordingly" in c.narrative
    high = build(model_confidence=0.9)
    assert "size accordingly" not in high.narrative


def test_pct_rank_and_field_floor():
    from bot.scenarios import _pct_rank, MIN_FIELD
    field = [i / 100 for i in range(0, 100)]  # 100 evenly-spaced values 0..0.99
    assert _pct_rank(field, 0.86) == 0.86       # 86 values below 0.86
    assert _pct_rank(field, 0.99) == 0.99
    assert _pct_rank([0.5] * 10, 0.9) is None    # field < MIN_FIELD → no percentile
    assert _pct_rank(field, None) is None
