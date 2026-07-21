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


# --- v4 selectivity ------------------------------------------------------

def test_v4_probability_floor_is_the_calibrated_band():
    assert PAPER_MIN_PROB >= 0.82
    # 78% favorite with a big edge is now skipped — below the calibrated band
    d = decide_bet(p_yes=0.78, confidence=0.9, yes_ask=60, yes_bid=58)
    assert not d.place


def test_v4_skips_big_edges_entirely():
    # the Kayo Nishimura case: 90% model vs 65¢ = +25% edge. v2 sized it 1u;
    # v4 skips it outright (edge > 15% is model error, not value).
    d = decide_bet(p_yes=0.90, confidence=1.0, yes_ask=65, yes_bid=63)
    assert not d.place


def test_v4_challenger_is_demoted():
    # 83% favorite: clears the general 82% floor, but not the Challenger 86% bar
    ok = decide_bet(p_yes=0.83, confidence=0.9, yes_ask=72, yes_bid=70)
    assert ok.place
    chal = decide_bet(p_yes=0.83, confidence=0.9, yes_ask=72, yes_bid=70, tier="C")
    assert not chal.place
    # a stronger Challenger favorite still clears
    strong = decide_bet(p_yes=0.88, confidence=0.9, yes_ask=78, yes_bid=76, tier="C")
    assert strong.place


def test_sizing_is_continuous_confidence_driven_and_sparing():
    from bot.paper import MAX_UNITS, size_units

    # decimals, in-range
    v = size_units(0.90, 0.05, 1.0)
    assert isinstance(v, float) and 1.0 < v < MAX_UNITS
    # monotone in probability (more confidence the pick wins → bigger stake)
    assert (size_units(0.85, 0.05, 1.0) < size_units(0.90, 0.05, 1.0)
            < size_units(0.95, 0.05, 1.0) <= MAX_UNITS)
    # multi-unit is sparing: a middling calibrated favorite stays near 1u
    assert size_units(0.85, 0.05, 1.0) < 1.5
    # gated by data depth: thin data shrinks the same strong pick toward 1u
    assert size_units(0.93, 0.05, 0.62) < size_units(0.93, 0.05, 1.0)
    # floor and cap, and a suspicious edge is never pressed
    assert size_units(0.82, 0.03, 0.60) == 1.0
    assert size_units(0.99, 0.05, 1.0) == MAX_UNITS
    assert size_units(0.95, 0.25, 1.0) == 1.0


def test_shared_gate_used_by_both_paths():
    # policy_ok is the single gate for the prematch and advisory bet paths
    from bot.paper import policy_ok

    assert policy_ok(0.85, 0.10, 75, None)
    assert not policy_ok(0.80, 0.10, 75, None)      # below 82% floor
    assert not policy_ok(0.90, 0.25, 65, None)      # edge > 15%
    assert not policy_ok(0.85, 0.10, 75, "C")       # Challenger needs 86%
    assert policy_ok(0.88, 0.10, 78, "C")
