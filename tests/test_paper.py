from bot.paper import PAPER_MAX_EDGE, PAPER_MIN_PROB, decide_bet


def test_calibrated_favorite_with_sane_edge_bets_yes():
    # 85% favorite priced 75¢ → 10% edge, in the calibrated band
    d = decide_bet(p_yes=0.85, confidence=0.8, yes_ask=75, yes_bid=73)
    assert d.place and d.side == "yes"
    assert d.price_cents == 75
    assert 0.03 <= d.edge <= PAPER_MAX_EDGE


def test_no_side_when_the_underdog_is_the_calibrated_favorite():
    # p_yes 15% → NO side prob 85%; NO price = 100 - 25 = 75, edge 10%
    d = decide_bet(p_yes=0.15, confidence=0.8, yes_ask=27, yes_bid=25)
    assert d.place and d.side == "no"
    assert d.price_cents == 75


def test_no_bet_without_edge():
    d = decide_bet(p_yes=0.85, confidence=0.8, yes_ask=84, yes_bid=82)  # priced to model
    assert not d.place


def test_no_bet_without_quote():
    assert not decide_bet(p_yes=0.9, confidence=0.9, yes_ask=None, yes_bid=None).place


def test_no_bet_on_low_confidence():
    d = decide_bet(p_yes=0.9, confidence=0.3, yes_ask=75, yes_bid=73)
    assert not d.place and "confidence" in d.reason


# --- v6 selectivity (floor re-based to the recalibrated scale) -----------

def test_v6_floor_is_a_favorite_band_not_a_coinflip():
    # v6 floor is 0.68 (recalibrated & honest); a 78% favorite with value now
    # clears, but a coin-flip / underdog is still skipped (record-focused)
    assert 0.65 <= PAPER_MIN_PROB <= 0.75
    assert decide_bet(p_yes=0.78, confidence=0.9, yes_ask=70, yes_bid=68).place
    assert not decide_bet(p_yes=0.55, confidence=0.9, yes_ask=42, yes_bid=40).place


def test_v7_allows_value_edges_but_skips_absurd():
    # v7: a model-favorite the market underprices (22% edge) is a value bet now
    assert decide_bet(p_yes=0.90, confidence=1.0, yes_ask=68, yes_bid=66).place
    # but a >30% edge is still skipped as an implausible / stale quote
    assert not decide_bet(p_yes=0.90, confidence=1.0, yes_ask=55, yes_bid=53).place


def test_v6_challenger_is_demoted():
    # 70% favorite clears the general 68% floor but not the Challenger 72% bar
    assert decide_bet(p_yes=0.70, confidence=0.9, yes_ask=65, yes_bid=63).place
    assert not decide_bet(p_yes=0.70, confidence=0.9, yes_ask=65, yes_bid=63,
                          tier="C").place
    # a stronger Challenger favorite still clears
    assert decide_bet(p_yes=0.78, confidence=0.9, yes_ask=70, yes_bid=68,
                      tier="C").place


def test_sizing_is_continuous_confidence_driven_and_sparing():
    from bot.paper import MAX_UNITS, size_units

    # decimals, in-range
    v = size_units(0.90, 0.05, 1.0)
    assert isinstance(v, float) and 1.0 < v < MAX_UNITS
    # monotone in probability (more confidence the pick wins → bigger stake)
    assert (size_units(0.85, 0.05, 1.0) < size_units(0.90, 0.05, 1.0)
            < size_units(0.95, 0.05, 1.0) <= MAX_UNITS)
    # multi-unit is sparing: a near-floor favorite stays close to 1u
    assert size_units(0.70, 0.05, 1.0) < 1.3
    # gated by data depth: thin data shrinks the same strong pick toward 1u
    assert size_units(0.93, 0.05, 0.62) < size_units(0.93, 0.05, 1.0)
    # floor and cap, and a suspicious edge is never pressed
    assert size_units(0.68, 0.03, 0.60) == 1.0
    assert size_units(0.99, 0.05, 1.0) == MAX_UNITS
    assert size_units(0.95, 0.35, 1.0) == 1.0  # edge beyond the cap → not pressed


def test_shared_gate_used_by_both_paths():
    # policy_ok is the single gate for the prematch and advisory bet paths
    from bot.paper import policy_ok

    assert policy_ok(0.85, 0.10, 75, None)
    assert not policy_ok(0.60, 0.10, 70, None)      # below 68% floor
    assert policy_ok(0.90, 0.25, 65, None)          # 25% value edge allowed (v7)
    assert not policy_ok(0.90, 0.35, 55, None)      # edge > 30% skipped
    assert not policy_ok(0.70, 0.05, 65, "C")       # Challenger needs 72%
    assert policy_ok(0.75, 0.05, 70, "C")


def test_policy_parameterization_changes_the_gate():
    # a self-improved policy with a lower floor / higher edge cap lets through a
    # bet the default policy would reject — proving both bots share one gate
    from bot.paper import DEFAULT_POLICY, decide_bet
    from dataclasses import replace
    loose = replace(DEFAULT_POLICY, min_prob=0.60, version="t2.3")
    # 63% favorite at 58¢ = 5% edge: below the 68% default floor, above 60%
    assert not decide_bet(0.63, 0.9, 58, 56).place
    assert decide_bet(0.63, 0.9, 58, 56, policy=loose).place
    # a tighter policy rejects a bet the default would take, naming its version
    tight = replace(DEFAULT_POLICY, min_prob=0.90, version="t2.7")
    rej = decide_bet(0.85, 0.9, 75, 73, policy=tight)
    assert not rej.place and "t2.7" in rej.reason


def test_size_mult_scales_the_stake():
    from bot.paper import size_units
    base = size_units(0.90, 0.05, 1.0)
    assert size_units(0.90, 0.05, 1.0, size_mult=1.3) > base
    assert size_units(0.90, 0.05, 1.0, size_mult=0.7) < base
