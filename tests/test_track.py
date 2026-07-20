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
