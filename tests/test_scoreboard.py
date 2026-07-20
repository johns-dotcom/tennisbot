from bot.market.scoreboard import parse_scoreboard

LIVE = {
    "status": "live",
    "competitor1_overall_score": 1,
    "competitor2_overall_score": 0,
    "competitor1_current_round_score": 3,
    "competitor2_current_round_score": 2,
    "competitor1_round_scores": [{"score": 6, "tiebreak_score": None}],
    "competitor2_round_scores": [{"score": 4, "tiebreak_score": None}],
}


def test_live_scoreline_yes_is_competitor1():
    sb = parse_scoreboard(LIVE, yes_is_c1=True)
    assert sb["sets_a"] == 1 and sb["sets_b"] == 0
    assert sb["set_number"] == 2
    assert sb["games_a"] == 3 and sb["games_b"] == 2
    assert sb["scoreline"] == "6-4 3-2"
    assert sb["total_games"] == 6 + 4 + 3 + 2
    assert sb["is_final"] is False


def test_scoreline_flips_when_yes_is_competitor2():
    sb = parse_scoreboard(LIVE, yes_is_c1=False)
    assert sb["sets_a"] == 0 and sb["sets_b"] == 1
    assert sb["games_a"] == 2 and sb["games_b"] == 3
    assert sb["scoreline"] == "4-6 2-3"


def test_tiebreak_notation():
    d = dict(LIVE, competitor1_round_scores=[{"score": 7, "tiebreak_score": 7}],
             competitor2_round_scores=[{"score": 6, "tiebreak_score": 4}])
    sb = parse_scoreboard(d, yes_is_c1=True)
    assert sb["scoreline"].startswith("7-6(4)")


def test_final_match():
    d = dict(LIVE, status="closed", competitor2_overall_score=0,
             competitor1_overall_score=2,
             competitor1_current_round_score=None,
             competitor2_current_round_score=None,
             competitor1_round_scores=[{"score": 6, "tiebreak_score": None},
                                       {"score": 6, "tiebreak_score": None}],
             competitor2_round_scores=[{"score": 4, "tiebreak_score": None},
                                       {"score": 3, "tiebreak_score": None}])
    sb = parse_scoreboard(d, yes_is_c1=True)
    assert sb["is_final"] is True
    assert sb["scoreline"] == "6-4 6-3"


def test_missing_details_returns_none():
    assert parse_scoreboard({}, True) is None
    assert parse_scoreboard({"competitor1_overall_score": "x"}, True) is None
