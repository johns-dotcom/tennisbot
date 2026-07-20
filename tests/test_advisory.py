from datetime import date, timedelta

from bot.advisory.facts import Fact, build_fact_block, build_facts
from bot.advisory.template import render_template
from bot.advisory.validate import extract_numbers, validate_numbers
from bot.prob.model import MatchState
from bot.stats.profile import build_profile  # noqa: F401  (import path sanity)
from tests.test_stats_profile import AS_OF, mk


def _profiles():
    from bot.stats import profile as P

    hist_a = ([mk(5, True, reached_decider=True, won_decider=True),
               mk(15, True, reached_decider=True, won_decider=True),
               mk(25, True, reached_decider=True, won_decider=True),
               mk(35, False, reached_decider=True, won_decider=False)] +
              [mk(45 + i * 5, True) for i in range(8)])
    hist_b = ([mk(6, False, reached_decider=True, won_decider=False),
               mk(16, False, reached_decider=True, won_decider=False),
               mk(26, False, reached_decider=True, won_decider=False),
               mk(31, False, reached_decider=True, won_decider=False)] +
              [mk(36 + i * 5, True, sets_lost=0) for i in range(7)])

    class Prof:
        pass

    a = Prof()
    a.player_id, a.player_name = 1, "Julia Adams"
    a.form = P.compute_form(hist_a, AS_OF, None)
    a.deciding = P.compute_deciding_sets(hist_a, AS_OF)
    a.trajectory = P.compute_trajectory(hist_a, AS_OF)
    b = Prof()
    b.player_id, b.player_name = 2, "Petra Zelnickova"
    b.form = P.compute_form(hist_b, AS_OF, None)
    b.deciding = P.compute_deciding_sets(hist_b, AS_OF)
    b.trajectory = P.compute_trajectory(hist_b, AS_OF)
    return a, b, hist_a, hist_b


def test_number_extraction():
    assert extract_numbers("won 5 of 7, at 54¢ — edge 8.2%") == ["5", "7", "54", "8.2"]
    assert extract_numbers("edge 8.20%") == ["8.2"]


def test_validator_accepts_only_fact_numbers():
    allowed = {"5", "7", "54", "8.2", "8"}
    ok, bad = validate_numbers("won 5 of 7 at 54¢, edge 8.2%", allowed)
    assert ok and not bad
    ok, bad = validate_numbers("won 6 of 7 at 54¢", allowed)
    assert not ok and bad == ["6"]


def test_fact_block_decider_boost_at_one_one():
    a, b, ha, hb = _profiles()
    facts_neutral = build_facts(a, b, ha, hb, MatchState(0, 0, 3), AS_OF)
    facts_decider = build_facts(a, b, ha, hb, MatchState(1, 1, 3), AS_OF)
    top_decider_keys = [f.key for f in facts_decider[:3]]
    assert any("decider" in k for k in top_decider_keys)
    dec_sal_11 = max(f.salience for f in facts_decider if "decider" in f.key)
    dec_sal_00 = max(f.salience for f in facts_neutral if "decider" in f.key)
    assert dec_sal_11 > dec_sal_00


def test_fact_block_allowed_numbers_cover_template():
    a, b, ha, hb = _profiles()
    facts = build_facts(a, b, ha, hb, MatchState(1, 1, 3), AS_OF)
    block = build_fact_block(
        market_ticker="MKT", name_a="Julia Adams", name_b="Petra Zelnickova",
        side="yes", facts=facts, model_prob=0.71, model_confidence=0.8,
        price_cents=54, volume=800, state=MatchState(1, 1, 3),
        state_confidence=0.91, state_confirmed=False, probation=True)
    text = render_template(block)
    ok, bad = validate_numbers(text, block.allowed_numbers)
    assert ok, f"template used non-fact numbers: {bad}"
    assert "ADAMS ML @ 54¢" in text.upper()
    assert "awaiting score confirmation" in text


def test_skunk_fact_flags_untested_player():
    a, b, ha, hb = _profiles()
    facts = build_facts(a, b, ha, hb, MatchState(1, 1, 3), AS_OF)
    assert any(f.key == "skunk_share_b" for f in facts), \
        "all of B's wins are skunks — must be flagged"
