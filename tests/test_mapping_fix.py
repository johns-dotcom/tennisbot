from bot.mapping_fix import flip_scoreline
from bot.reports import check_mapping


def test_flip_scoreline_basic():
    assert flip_scoreline("6-3 4-6 2-1") == "3-6 6-4 1-2"


def test_flip_scoreline_preserves_tiebreak():
    assert flip_scoreline("7-6(4) 6-7(2)") == "6-7(4) 7-6(2)"


def test_flip_scoreline_double_flip_is_identity():
    for s in ("6-3 4-6 7-6(10)", "2-6 6-4 6-3", "6-0 6-0"):
        assert flip_scoreline(flip_scoreline(s)) == s


def test_flip_scoreline_handles_sets_summary_fallback():
    # non "g-g" tokens pass through untouched
    assert flip_scoreline("0-2 sets") == "2-0 sets"


def test_check_mapping_detects_the_flip_direction():
    # YES player recorded winning 2-0 but market settled NO -> mismatch
    assert check_mapping(2, 0, "no") == "mismatch"
    assert check_mapping(2, 0, "yes") == "ok"
    assert check_mapping(1, 1, "yes") == "unverifiable"
    assert check_mapping(0, 2, "no") == "ok"
