from datetime import datetime, timezone

from bot.scenarios import build_candidates_for_match
from tests.test_advisory import _profiles

START = datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc)


def build(p_a=0.55):
    a, b, _, _ = _profiles()  # A strong in deciders; B all-skunk wins, decider-poor
    return build_candidates_for_match(
        ticker_a="EV-ADA", ticker_b="EV-ZEL", event_ticker="EV",
        name_a="Julia Adams", name_b="Petra Zelnickova", id_a=1, id_b=2,
        prof_a=a, prof_b=b, p_a=p_a, best_of=3, start=START,
        event_label="Adams vs Zelnickova")


def test_decider_edge_generated_for_divergent_records():
    cands = build()
    dec = [c for c in cands if c.kind == "decider_edge"]
    assert len(dec) == 1
    c = dec[0]
    assert c.market_ticker == "EV-ADA"  # Adams is the better decider side
    assert c.state_key == "1-1"
    assert c.p_at_state > 0.5  # conditional prob favors her once there
    assert "set 3" in c.narrative
    assert "straight" in c.narrative  # B's losing decider streak cited


def test_resilient_favorite_needs_strong_prematch():
    weak = [c for c in build(p_a=0.55) if c.kind == "resilient_favorite"]
    assert not weak
    strong = [c for c in build(p_a=0.72) if c.kind == "resilient_favorite"]
    assert len(strong) == 1
    c = strong[0]
    assert c.market_ticker == "EV-ADA"
    assert c.state_key == "0-1"
    assert 0.40 <= c.p_at_state < c.p_prematch


def test_narrative_numbers_come_from_facts():
    import re

    for c in build(p_a=0.72):
        nums = set(re.findall(r"\d+", c.narrative))
        allowed = set()
        for v in (c.facts.get("decider_a", []) + c.facts.get("decider_b", [])):
            allowed.add(str(v))
        for k in ("diff", "prematch", "down_a_set"):
            if k in c.facts:
                allowed.add(str(int(round(abs(c.facts[k]) * 100))))
        allowed.add(str(int(round(c.p_at_state * 100))))
        allowed.add(str(int(round(c.p_prematch * 100))))
        allowed.add("3")  # "set 3"
        # streak/skunk numbers cited in prose come from the profiles directly
        allowed |= {"4", "5", "80", "100", "86", "88"}
        assert nums <= allowed, f"unexpected numbers {nums - allowed} in: {c.narrative}"
