from bot.sources.kalshi_results import parse_kalshi_sets, winner_is_competitor1

DETAILS = {
    "status": "complete",
    "competitor1_overall_score": 2,
    "competitor2_overall_score": 1,
    "competitor1_round_scores": [
        {"outcome": "loser", "score": 6, "tiebreak_score": 3},
        {"outcome": "winner", "score": 7, "tiebreak_score": 7},
        {"outcome": "winner", "score": 6},
    ],
    "competitor2_round_scores": [
        {"outcome": "winner", "score": 7, "tiebreak_score": 7},
        {"outcome": "loser", "score": 6, "tiebreak_score": 4},
        {"outcome": "loser", "score": 4},
    ],
}


def test_winner_mapping_by_sets_majority():
    assert winner_is_competitor1(DETAILS, "Anybody", "X vs Y") is True


def test_winner_mapping_falls_back_to_title_on_retirement():
    d = dict(DETAILS, status="retired")
    assert winner_is_competitor1(d, "Rocha", "Rocha vs Martinez") is True
    assert winner_is_competitor1(d, "Martinez", "Rocha vs Martinez") is False


def test_parse_sets_winner_first_convention():
    sets, ww, wl, outcome = parse_kalshi_sets(DETAILS, winner_is_c1=True)
    assert outcome == "completed"
    assert (ww, wl) == (2, 1)
    assert [(s["winner_games"], s["loser_games"]) for s in sets] == \
        [(6, 7), (7, 6), (6, 4)]
    assert sets[0]["set_won_by_match_winner"] is False
    assert sets[0]["tiebreak"] and sets[0]["tiebreak_loser_points"] == 3
    assert sets[2]["tiebreak"] is False


def test_parse_sets_retirement_status():
    d = dict(DETAILS, status="retired")
    _, _, _, outcome = parse_kalshi_sets(d, winner_is_c1=True)
    assert outcome == "ret"


def test_parse_sets_handles_missing_scores():
    sets, ww, wl, _ = parse_kalshi_sets({"competitor1_round_scores": [],
                                         "competitor2_round_scores": []}, True)
    assert sets == [] and (ww, wl) == (0, 0)
