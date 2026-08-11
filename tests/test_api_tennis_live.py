from bot.market.api_tennis_live import _sb, parse_live_singles

RAW = [
    {"event_type_type": "Itf Women Singles",
     "first_player_key": "111", "second_player_key": "222",
     "event_final_result": "1 - 1", "event_status": "Set 3",
     "scores": [{"score_first": "6", "score_second": "3", "score_set": "1"},
                {"score_first": "3", "score_second": "6", "score_set": "2"},
                {"score_first": "2", "score_second": "1", "score_set": "3"}]},
    {"event_type_type": "Itf Men Doubles",  # doubles must be skipped
     "first_player_key": "9", "second_player_key": "8", "scores": []},
]


def test_parse_skips_doubles_and_reads_sets():
    evs = parse_live_singles(RAW)
    assert len(evs) == 1
    e = evs[0]
    assert e["fk"] == "111" and e["sk"] == "222"
    assert e["sf"] == 1 and e["ss"] == 1 and e["is_final"] is False


def test_orientation_mirrors_correctly():
    e = parse_live_singles(RAW)[0]
    # A = api-tennis "first": a-perspective scoreline
    a = _sb(e, first_is_a=True)
    assert a["scoreline"] == "6-3 3-6 2-1"
    assert a["sets_a"] == 1 and a["sets_b"] == 1
    assert a["games_a"] == 2 and a["games_b"] == 1 and a["set_number"] == 3
    assert a["total_games"] == 6 + 3 + 3 + 6 + 2 + 1
    # A = api-tennis "second": every pair flips
    b = _sb(e, first_is_a=False)
    assert b["scoreline"] == "3-6 6-3 1-2"
    assert b["games_a"] == 1 and b["games_b"] == 2


def test_final_status_detected():
    raw = [{"event_type_type": "Itf Men Singles", "first_player_key": "1",
            "second_player_key": "2", "event_final_result": "2 - 0",
            "event_status": "Finished",
            "scores": [{"score_first": "6", "score_second": "4", "score_set": "1"},
                       {"score_first": "6", "score_second": "2", "score_set": "2"}]}]
    e = parse_live_singles(raw)[0]
    assert e["is_final"] is True and e["sf"] == 2 and e["ss"] == 0
