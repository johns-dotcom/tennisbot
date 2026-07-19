from bot.ingest.score_parser import parse_score


def test_straight_sets():
    p = parse_score("6-4 6-3")
    assert p.outcome == "completed"
    assert len(p.sets) == 2
    assert (p.sets_won_winner, p.sets_won_loser) == (2, 0)
    assert p.sets[0].winner_games == 6 and p.sets[0].loser_games == 4
    assert all(s.set_won_by_match_winner for s in p.sets)


def test_three_setter_with_dropped_set():
    p = parse_score("6-4 3-6 7-5")
    assert (p.sets_won_winner, p.sets_won_loser) == (2, 1)
    assert p.sets[1].set_won_by_match_winner is False
    assert p.sets[1].winner_games == 3 and p.sets[1].loser_games == 6


def test_tiebreak_notation():
    p = parse_score("7-6(4) 6-7(10) 6-3")
    s1, s2, s3 = p.sets
    assert s1.tiebreak and s1.tiebreak_loser_points == 4
    assert s2.tiebreak and s2.tiebreak_loser_points == 10
    assert not s2.set_won_by_match_winner
    assert not s3.tiebreak
    assert (p.sets_won_winner, p.sets_won_loser) == (2, 1)


def test_retirement_mid_set():
    p = parse_score("6-4 3-1 RET")
    assert p.outcome == "ret"
    assert len(p.sets) == 2
    assert p.sets[0].completed is True
    assert p.sets[1].completed is False
    # incomplete set counts for neither side
    assert (p.sets_won_winner, p.sets_won_loser) == (1, 0)


def test_retirement_between_sets():
    p = parse_score("6-4 RET")
    assert p.outcome == "ret"
    assert p.sets[0].completed is True
    assert (p.sets_won_winner, p.sets_won_loser) == (1, 0)


def test_retirement_mid_set_completed_second():
    p = parse_score("7-6(3) 4-6 2-0 RET")
    assert p.outcome == "ret"
    assert p.sets[2].completed is False
    assert (p.sets_won_winner, p.sets_won_loser) == (1, 1)


def test_walkover_variants():
    for s in ("W/O", "W.O.", "Walkover"):
        p = parse_score(s)
        assert p.outcome == "wo"
        assert p.sets == []


def test_default():
    p = parse_score("DEF")
    assert p.outcome == "def"
    assert p.sets == []


def test_default_mid_match():
    p = parse_score("6-3 4-4 DEF")
    assert p.outcome == "def"
    assert p.sets[1].completed is False
    assert (p.sets_won_winner, p.sets_won_loser) == (1, 0)


def test_abandoned():
    p = parse_score("2-1 ABN")
    assert p.outcome == "abandoned"
    assert p.sets[0].completed is False


def test_match_tiebreak_third_set():
    p = parse_score("6-3 3-6 [10-7]")
    assert (p.sets_won_winner, p.sets_won_loser) == (2, 1)
    last = p.sets[2]
    assert last.is_match_tiebreak and last.tiebreak
    assert last.set_won_by_match_winner


def test_empty_and_garbage():
    assert parse_score("").outcome == "unknown"
    assert parse_score(None).outcome == "unknown"
    assert parse_score("Jun-04").outcome == "unknown"  # Excel-corrupted legacy rows


def test_garbage_token_amid_valid_sets_keeps_valid():
    p = parse_score("6-4 Jun-04 6-3")
    assert len(p.sets) == 2
    assert (p.sets_won_winner, p.sets_won_loser) == (2, 0)


def test_five_setter():
    p = parse_score("6-7(5) 6-4 3-6 7-6(2) 6-4", best_of=5)
    assert (p.sets_won_winner, p.sets_won_loser) == (3, 2)
    assert len(p.sets) == 5


def test_seven_five_no_tiebreak_is_complete_on_ret():
    p = parse_score("7-5 RET")
    assert p.sets[0].completed is True
