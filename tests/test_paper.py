from bot.paper import PAPER_MIN_PROB, decide_bet


def test_strong_favorite_with_edge_bets_yes():
    d = decide_bet(p_yes=0.78, confidence=0.8, yes_ask=70, yes_bid=68)
    assert d.place and d.side == "yes"
    assert d.price_cents == 70
    assert d.edge >= 0.03


def test_underdog_value_bets_no_side():
    # A only 25% → NO side prob 75%; NO exec price = 100 - yes_bid = 30... too
    # cheap? edge = .75 - .70 with yes_bid 30 → no price 70, edge 5%
    d = decide_bet(p_yes=0.25, confidence=0.8, yes_ask=32, yes_bid=30)
    assert d.place and d.side == "no"
    assert d.price_cents == 70


def test_no_bet_without_edge():
    # 80% favorite priced at 82 — great player, no value
    d = decide_bet(p_yes=0.80, confidence=0.8, yes_ask=82, yes_bid=80)
    assert not d.place


def test_no_bet_below_probability_floor():
    # 55% side with big edge still skipped — record target favors winners
    d = decide_bet(p_yes=0.55, confidence=0.9, yes_ask=42, yes_bid=40)
    assert not d.place
    assert "prob" in d.reason or "gates" in d.reason


def test_no_bet_on_low_confidence():
    d = decide_bet(p_yes=0.85, confidence=0.3, yes_ask=70, yes_bid=68)
    assert not d.place
    assert "confidence" in d.reason


def test_no_bet_on_extreme_price():
    d = decide_bet(p_yes=0.985, confidence=0.9, yes_ask=95, yes_bid=93)
    assert not d.place  # nothing to win at 95¢


def test_no_bet_without_quote():
    d = decide_bet(p_yes=0.9, confidence=0.9, yes_ask=None, yes_bid=None)
    assert not d.place


def test_selectivity_probability_floor_is_high():
    # the policy exists to hit a 70% record: floor must be near/above that
    assert PAPER_MIN_PROB >= 0.65


def test_unit_sizing_default_one():
    from bot.paper import size_units

    assert size_units(0.70, 0.04, 0.65) == 1


def test_unit_sizing_two_needs_all_thresholds():
    from bot.paper import size_units

    assert size_units(0.76, 0.07, 0.80) == 2
    assert size_units(0.76, 0.07, 0.65) == 1  # confidence short
    assert size_units(0.76, 0.04, 0.80) == 1  # edge short


def test_unit_sizing_three_is_extreme_and_capped():
    from bot.paper import size_units, decide_bet

    assert size_units(0.85, 0.12, 0.90) == 3
    assert size_units(0.85, 0.12, 0.80) == 2  # one threshold short of 3u
    assert size_units(0.99, 0.30, 0.99) == 3  # never above 3
    d = decide_bet(p_yes=0.85, confidence=0.9, yes_ask=73, yes_bid=71)
    assert d.place and d.units == 3 and "3u" in d.reason
