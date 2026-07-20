from bot.stats.profile import compute_charting


def _row(**kw):
    base = dict(winners=30, winners_fh=20, winners_bh=10, unforced=25,
                unforced_fh=15, unforced_bh=10, serve_pts=80, aces=8,
                first_in=48, first_won=38, second_in=32, second_won=18,
                return_pts=70, return_pts_won=28)
    base.update(kw)
    return base


def test_charting_empty_omits():
    ch = compute_charting([])
    assert ch.n_matches == 0
    assert ch.winners_per_match is None and ch.ace_rate is None


def test_charting_aggregates():
    ch = compute_charting([_row(), _row()])
    assert ch.n_matches == 2
    assert ch.winners_per_match == 30.0
    assert ch.unforced_per_match == 25.0
    assert abs(ch.winner_ufe_ratio - 30 / 25) < 1e-9
    assert abs(ch.fh_winner_share - 20 / 30) < 1e-9
    assert abs(ch.ace_rate - 8 / 80) < 1e-9
    assert abs(ch.first_serve_win - 38 / 48) < 1e-9
    assert abs(ch.return_win - 28 / 70) < 1e-9


def test_charting_tolerates_missing_columns():
    ch = compute_charting([_row(unforced=None, winners_fh=None)])
    assert ch.n_matches == 1
    assert ch.winner_ufe_ratio is None  # no UFE total → omitted, not zero-div
    assert ch.winners_per_match == 30.0
