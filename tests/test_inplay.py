"""Hierarchical in-play win-probability model — correctness vs known values."""
import math

from bot.prob.inplay import (
    game_win_prob,
    anchor_serve_gap,
    inplay_win_prob,
    match_win_prob,
    set_win_prob,
)


def test_game_win_prob_known_values():
    assert abs(game_win_prob(0.5) - 0.5) < 1e-9           # coin-flip server
    # a 62%-point server holds ~78% (matches published tennis tables)
    assert 0.76 < game_win_prob(0.62) < 0.79
    # extremes
    assert game_win_prob(0.0) == 0.0
    assert game_win_prob(1.0) == 1.0
    assert game_win_prob(0.9) > 0.99


def test_game_win_prob_monotonic():
    xs = [game_win_prob(p) for p in (0.4, 0.5, 0.6, 0.7, 0.8)]
    assert all(b > a for a, b in zip(xs, xs[1:]))


def test_set_and_match_symmetry_equal_servers():
    # equal serve skill → a fresh set/match is a coin flip within a whisker
    s = set_win_prob(0.62, 0.62, 0, 0, True)
    assert abs(s - 0.5) < 0.02
    m = match_win_prob(0.62, 0.62, best_of=3, sets_a=0, sets_b=0, games_a=0,
                       games_b=0, pts_a=0, pts_b=0, a_serving=True)
    assert abs(m - 0.5) < 0.02


def test_match_prob_bounds_and_lead_helps():
    # A up a set should be more likely to win than at level
    base = match_win_prob(0.63, 0.61, best_of=3, sets_a=0, sets_b=0, games_a=0,
                          games_b=0, pts_a=0, pts_b=0, a_serving=True)
    up_set = match_win_prob(0.63, 0.61, best_of=3, sets_a=1, sets_b=0, games_a=0,
                            games_b=0, pts_a=0, pts_b=0, a_serving=True)
    down_set = match_win_prob(0.63, 0.61, best_of=3, sets_a=0, sets_b=1, games_a=0,
                              games_b=0, pts_a=0, pts_b=0, a_serving=True)
    assert down_set < base < up_set
    assert 0.0 <= down_set and up_set <= 1.0


def test_match_point_is_near_certain():
    # A serving at 1-0 sets, 5-0 games, 40-0 (triple match point) → ~1.0
    p = match_win_prob(0.62, 0.62, best_of=3, sets_a=1, sets_b=0, games_a=5,
                       games_b=0, pts_a=3, pts_b=0, a_serving=True)
    assert p > 0.99


def test_anchor_reproduces_prematch():
    # inplay at 0-0 must equal the Elo prematch number it's anchored to
    for pm in (0.35, 0.5, 0.62, 0.8):
        live = inplay_win_prob(pm, best_of=3, sets_a=0, sets_b=0, games_a=0,
                               games_b=0, a_serving=True)
        assert abs(live - pm) < 0.01, (pm, live)


def test_implied_gap_direction():
    assert anchor_serve_gap(0.5, 3) < 1e-3            # even → no gap
    assert anchor_serve_gap(0.8, 3) > anchor_serve_gap(0.62, 3) > 0


def test_favorite_dropping_first_set_falls_below_half():
    # a 70% favorite that loses the opening set is now an underdog
    live = inplay_win_prob(0.70, best_of=3, sets_a=0, sets_b=1, games_a=0,
                           games_b=0, a_serving=True)
    assert live < 0.5


def test_bo5_vs_bo3_favorite_more_secure():
    # a favorite is safer over best-of-5 (variance ↓)
    p3 = inplay_win_prob(0.65, best_of=3, sets_a=0, sets_b=0, games_a=0, games_b=0)
    p5 = inplay_win_prob(0.65, best_of=5, sets_a=0, sets_b=0, games_a=0, games_b=0)
    assert p5 >= p3 - 0.005      # anchored equal at 0-0 (both ~0.65)
    # but down a set, Bo5 favorite recovers more of its edge than Bo3
    d3 = inplay_win_prob(0.65, best_of=3, sets_a=0, sets_b=1, games_a=0, games_b=0)
    d5 = inplay_win_prob(0.65, best_of=5, sets_a=0, sets_b=1, games_a=0, games_b=0)
    assert d5 > d3
