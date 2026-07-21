from bot.web import _opponent_surname, postgame_analysis


def test_opponent_surname_from_title():
    t = "Will Edison Ambarzumjan win the Ambarzumjan vs Stroemberg: M25 Round of 32 match?"
    assert _opponent_surname(t, "Ambarzumjan") == "Stroemberg"
    assert _opponent_surname(t, "Stroemberg") == "Ambarzumjan"
    assert _opponent_surname(None, "X") == "the opponent"


def test_won_straight_sets():
    a = postgame_analysis("Swiatek", "Gauff", "yes", "yes", "6-3 6-2", 2, 0,
                          False, 60, 40, "took_profit", 30, 5, "Swiatek is the play")
    assert "Won as planned" in a and "straight sets" in a
    assert "6-3 6-2" in a


def test_lost_but_salvaged_by_take_profit():
    # backed YES, lost, but our side touched 90 mid-match -> TP salvages
    a = postgame_analysis("Rune", "Sinner", "yes", "no", "6-4 3-6 4-6", 1, 2,
                          True, 55, -55, "took_profit", 35, -2, "Rune is the play")
    assert "led first" in a and "take-profit salvaged" in a
    assert "+35" in a and "−55" in a


def test_no_scoreline_returns_empty():
    assert postgame_analysis("A", "B", "yes", "yes", None, None, None,
                             False, 50, 50, "won", None, None, "") == ""


def test_no_side_perspective_flips_scoreline():
    # backed NO -> pick is the second player; YES-oriented "6-2 6-1" is a loss
    # for YES, so from the NO pick's view it reads "2-6 1-6" ... won
    a = postgame_analysis("Kyrgios", "Nadal", "no", "no", "6-2 6-1", 2, 0,
                          False, 45, 55, "took_profit", 45, 3, "")
    assert "2-6 1-6" in a  # flipped to the NO pick's perspective
