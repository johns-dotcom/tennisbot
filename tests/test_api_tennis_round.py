from bot.sources.api_tennis import _norm_round


def test_long_names_map_to_short_codes():
    assert _norm_round("Round of 16") == "R16"
    assert _norm_round("1/8-finals") == "R16"
    assert _norm_round("Quarter-final") == "QF"
    assert _norm_round("Semi-finals") == "SF"
    assert _norm_round("Final") == "F"
    assert _norm_round("Round of 128") == "R128"


def test_qualifying():
    assert _norm_round("Qualification Round 2") == "Q2"
    assert _norm_round("Qualifying") == "Q"


def test_none_and_clamp():
    assert _norm_round(None) is None
    assert _norm_round("") is None
    # unknown value must never exceed the varchar(8) column
    assert len(_norm_round("Something Very Long Round Name")) <= 8


def test_short_codes_passthrough():
    assert _norm_round("QF") == "QF"
    assert _norm_round("R32") == "R32"
