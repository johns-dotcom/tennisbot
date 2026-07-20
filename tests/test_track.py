from bot.track import advisory_outcome, advisory_pnl_cents


def test_yes_side_outcomes():
    assert advisory_outcome("yes", "yes") == "won"
    assert advisory_outcome("yes", "no") == "lost"
    assert advisory_outcome("no", "no") == "won"
    assert advisory_outcome("no", "yes") == "lost"


def test_unsettled_and_void():
    assert advisory_outcome("yes", None) is None
    assert advisory_outcome("yes", "") is None
    assert advisory_outcome("yes", "void") == "void"


def test_pnl_flat_stake():
    assert advisory_pnl_cents("yes", 54, "yes") == 46
    assert advisory_pnl_cents("yes", 54, "no") == -54
    assert advisory_pnl_cents("no", 40, "no") == 60
    assert advisory_pnl_cents("no", 40, "yes") == -40
    assert advisory_pnl_cents("yes", 54, "void") == 0
    assert advisory_pnl_cents("yes", 54, None) is None


def test_clv_beats_close_yes_side():
    from bot.track import clv_cents
    # bought YES at 54¢, market closed at 60¢ → +6¢ CLV (beat the close)
    assert clv_cents("yes", 54, 60) == 6
    assert clv_cents("yes", 60, 54) == -6


def test_clv_no_side_inverts():
    from bot.track import clv_cents
    # bought NO at 40¢; close YES 55 → NO close 45 → +5¢
    assert clv_cents("no", 40, 55) == 5


def test_clv_unknown_close():
    from bot.track import clv_cents
    assert clv_cents("yes", 54, None) is None
