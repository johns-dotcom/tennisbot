"""parse_serve_stats: api-tennis match statistics → Sackmann w_*/l_* serve
schema, so finished api-tennis matches feed the serve/return profile with no
new table."""
from bot.sources.api_tennis import parse_serve_stats


def _stat(pk, t, n, value=None, won=None, total=None, period="match"):
    return {"player_key": pk, "stat_period": period, "stat_type": t,
            "stat_name": n, "stat_value": value, "stat_won": won,
            "stat_total": total}


def _player_stats(pk, *, aces, dfs, first_won, first_in, second_won,
                  second_pts, svgms, bp_saved=None, bp_faced=None):
    rows = [
        _stat(pk, "Service", "Aces", value=aces),
        _stat(pk, "Service", "Double Faults", value=dfs),
        _stat(pk, "Service", "1st serve points won", won=first_won, total=first_in),
        _stat(pk, "Service", "2nd serve points won", won=second_won, total=second_pts),
        _stat(pk, "Games", "Service games won", won=svgms, total=svgms),
    ]
    if bp_faced is not None:
        rows.append(_stat(pk, "Service", "Break Points Saved",
                          won=bp_saved, total=bp_faced))
    return rows


def _fixture(winner_rows, loser_rows, wk="10", lk="20"):
    return {"first_player_key": wk, "second_player_key": lk,
            "statistics": winner_rows + loser_rows}


def test_parses_both_sides_into_sackmann_schema():
    f = _fixture(
        _player_stats("10", aces=3, dfs=1, first_won=53, first_in=78,
                      second_won=22, second_pts=43, svgms=10, bp_saved=6, bp_faced=9),
        _player_stats("20", aces=1, dfs=2, first_won=22, first_in=72,
                      second_won=28, second_pts=45, svgms=10, bp_saved=3, bp_faced=12),
    )
    s = parse_serve_stats(f, "10", "20")
    assert s is not None
    # every key the profile's side_stats() reads must be present for both sides
    for prefix in ("w", "l"):
        for k in ("ace", "df", "svpt", "1stIn", "1stWon", "2ndWon",
                  "SvGms", "bpSaved", "bpFaced"):
            assert f"{prefix}_{k}" in s, f"missing {prefix}_{k}"
    assert s["w_ace"] == 3 and s["w_df"] == 1
    assert s["w_1stIn"] == 78 and s["w_1stWon"] == 53
    assert s["w_bpSaved"] == 6 and s["w_bpFaced"] == 9


def test_svpt_derived_so_first_serve_pct_matches_api():
    # api reports 1st-serve% = 1stIn / (1stIn + 2nd-serve-points), so svpt must
    # be the derived sum, not the noisier 'Service Points Won' total.
    f = _fixture(
        _player_stats("10", aces=4, dfs=1, first_won=53, first_in=78,
                      second_won=22, second_pts=43, svgms=10),
        _player_stats("20", aces=0, dfs=0, first_won=30, first_in=60,
                      second_won=20, second_pts=40, svgms=10),
    )
    s = parse_serve_stats(f, "10", "20")
    assert s["w_svpt"] == 78 + 43
    assert abs(s["w_1stIn"] / s["w_svpt"] - 0.644) < 0.01   # ~64%
    # second serves = svpt - 1stIn, exactly the 2nd-serve-points total
    assert s["w_svpt"] - s["w_1stIn"] == 43


def test_missing_break_points_default_to_zero_not_dropped():
    # a player who faced no break points has no 'Break Points Saved' row —
    # that's real 0-faced data, the match should still parse.
    f = _fixture(
        _player_stats("10", aces=5, dfs=0, first_won=40, first_in=50,
                      second_won=15, second_pts=25, svgms=9),  # no bp keys
        _player_stats("20", aces=2, dfs=3, first_won=30, first_in=55,
                      second_won=18, second_pts=30, svgms=9, bp_saved=1, bp_faced=4),
    )
    s = parse_serve_stats(f, "10", "20")
    assert s is not None
    assert s["w_bpFaced"] == 0 and s["w_bpSaved"] == 0


def test_returns_none_when_a_side_lacks_serve_data():
    # loser has no serve statistics at all (e.g. a retirement feed) → the whole
    # match is None, because the profile drops any match missing one side.
    f = _fixture(
        _player_stats("10", aces=5, dfs=0, first_won=40, first_in=50,
                      second_won=15, second_pts=25, svgms=9),
        [],  # loser: nothing
    )
    assert parse_serve_stats(f, "10", "20") is None


def test_returns_none_without_statistics():
    assert parse_serve_stats({"statistics": None}, "10", "20") is None
    assert parse_serve_stats({}, "10", "20") is None


def test_ignores_non_match_period_rows():
    # set-level rows must not be mistaken for the match total
    rows = _player_stats("10", aces=5, dfs=0, first_won=40, first_in=50,
                         second_won=15, second_pts=25, svgms=9)
    set_row = _stat("10", "Service", "Aces", value=99, period="set 1")
    f = _fixture(rows + [set_row],
                 _player_stats("20", aces=1, dfs=1, first_won=30, first_in=55,
                               second_won=18, second_pts=30, svgms=9))
    s = parse_serve_stats(f, "10", "20")
    assert s["w_ace"] == 5   # match total, not the set-1 row's 99


# ---- bio / surface parsers (get_players) ----
from bot.sources.api_tennis import _parse_bday, _parse_surface_stats


def test_parse_bday():
    assert _parse_bday("16.08.2001").isoformat() == "2001-08-16"
    assert _parse_bday("") is None
    assert _parse_bday(None) is None
    assert _parse_bday("garbage") is None


def test_parse_surface_stats_singles_only():
    stats = [
        {"season": "2024", "type": "singles", "hard_won": "40", "hard_lost": "10",
         "clay_won": "15", "clay_lost": "8", "grass_won": "5", "grass_lost": "2"},
        {"season": "2023", "type": "singles", "hard_won": "30", "hard_lost": "12",
         "clay_won": "", "clay_lost": "", "grass_won": "3", "grass_lost": "1"},
        {"season": "2024", "type": "doubles", "hard_won": "99", "hard_lost": "99"},  # ignored
    ]
    s = _parse_surface_stats(stats)
    assert s["hard"] == {"w": 70, "l": 22}      # summed across singles seasons
    assert s["clay"] == {"w": 15, "l": 8}
    assert s["grass"] == {"w": 8, "l": 3}
    assert "doubles" not in str(s) and s["hard"]["w"] == 70  # doubles excluded


def test_parse_surface_stats_empty():
    assert _parse_surface_stats(None) is None
    assert _parse_surface_stats([]) is None
    assert _parse_surface_stats([{"type": "doubles", "hard_won": "5"}]) is None
