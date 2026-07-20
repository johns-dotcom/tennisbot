from datetime import date, timedelta

from bot.stats.fallback import Stat, pick, rate
from bot.stats.profile import (
    compute_deciding_sets,
    compute_form,
    compute_matchup,
    compute_surface,
    compute_trajectory,
)
from bot.stats.types import MatchRow

AS_OF = date(2026, 7, 1)


def mk(days_ago: int, won: bool, *, opp: int = 99, surface: str = "Hard", best_of: int = 3,
       outcome: str = "completed", sets_won: int | None = None, sets_lost: int | None = None,
       reached_decider: bool = False, won_decider: bool | None = None,
       sets: tuple | None = None) -> MatchRow:
    if sets_won is None:
        sets_won = 2 if won else (1 if reached_decider else 0)
    if sets_lost is None:
        sets_lost = (1 if reached_decider else 0) if won else 2
    if sets is None and outcome == "completed":
        if reached_decider:
            sets = ((1, won), (2, not won), (3, bool(won_decider)))
        else:
            sets = ((1, won), (2, won))
    return MatchRow(
        match_date=AS_OF - timedelta(days=days_ago), won=won, opponent_id=opp,
        surface=surface, best_of=best_of, outcome=outcome, sets_won=sets_won,
        sets_lost=sets_lost, reached_decider=reached_decider,
        won_decider=won_decider, tourney_level="A", set_results=sets or (),
    )


def test_form_last_n_and_streak():
    # newest→oldest: W W W L W  → last5 = 4-1, streak W3
    hist = [mk(1, True), mk(3, True), mk(5, True), mk(8, False), mk(12, True)]
    f = compute_form(hist, AS_OF, "Hard")
    assert (f.last5.wins, f.last5.losses) == (4, 1)
    assert f.streak == 3


def test_form_excludes_matches_on_or_after_as_of():
    hist = [mk(0, True), mk(-5, True), mk(2, False)]  # days_ago 0 = as_of itself
    f = compute_form(hist, AS_OF, None)
    # only the match 2 days before as_of counts
    assert f.last5.n == 1 and f.last5.losses == 1


def test_ytd_vs_career_delta():
    hist = [mk(10, True), mk(20, True),  # 2026 YTD: 2-0
            mk(400, False), mk(410, False)]  # prior year: 0-2
    f = compute_form(hist, AS_OF, None)
    assert f.win_rate_ytd.value == 1.0
    assert f.win_rate_career.value == 0.5
    assert abs(f.ytd_vs_career_delta - 0.5) < 1e-9


def test_deciding_set_metrics():
    hist = [
        mk(5, True, reached_decider=True, won_decider=True),
        mk(15, True, reached_decider=True, won_decider=True),
        mk(30, False, reached_decider=True, won_decider=False),
        mk(500, False, reached_decider=True, won_decider=False),
        mk(40, True),  # skunk win, not a decider
    ]
    d = compute_deciding_sets(hist, AS_OF)
    assert (d.career.wins, d.career.losses) == (2, 2)
    assert (d.last365.wins, d.last365.losses) == (2, 1)
    assert d.streak == 2
    assert d.days_since_decider_win == 5
    assert d.last_n_results[0] == {"date": (AS_OF - timedelta(days=5)).isoformat(), "won": True}
    # wins: 3 total (2 deciders + 1 skunk); skunk share = 1/3
    assert abs(d.skunk_share_of_wins_career.value - 1 / 3) < 1e-9


def test_decider_streak_negative():
    hist = [mk(5, False, reached_decider=True, won_decider=False),
            mk(15, False, reached_decider=True, won_decider=False),
            mk(25, True, reached_decider=True, won_decider=True)]
    d = compute_deciding_sets(hist, AS_OF)
    assert d.streak == -2
    assert d.days_since_decider_win == 25


def test_ret_counts_wo_never_reaches_stats():
    # walkovers must be excluded upstream by the loader; RET counts as a played loss
    hist = [mk(5, False, outcome="ret", sets_won=0, sets_lost=1)]
    f = compute_form(hist, AS_OF, None)
    assert f.last5.losses == 1


def test_trajectory():
    hist = [mk(10, True), mk(20, True),  # last 60: 2-0
            mk(100, False), mk(120, False)]  # 60-180d ago: 0-2
    t = compute_trajectory(hist, AS_OF)
    assert t.last60.value == 1.0
    assert t.last180.value == 0.5
    assert abs(t.delta - 0.5) < 1e-9


def test_surface_fallback_widens_to_career():
    # only 2 clay matches in last 365d (< min 5), 6 in career → widened
    hist = [mk(30, True, surface="Clay"), mk(60, True, surface="Clay")] + \
           [mk(400 + i * 10, i % 2 == 0, surface="Clay") for i in range(4)]
    s = compute_surface(hist, AS_OF, "Clay")
    assert s.best.method == "widened"
    assert s.best.n == 6


def test_matchup_h2h_and_common_opponents():
    a = [mk(10, True, opp=2), mk(50, False, opp=2),  # h2h 1-1 vs player 2
         mk(20, True, opp=7), mk(30, True, opp=8), mk(40, False, opp=9)]
    b = [mk(15, False, opp=7), mk(25, False, opp=8), mk(35, True, opp=9),
         mk(10, False, opp=1), mk(50, True, opp=1)]
    m = compute_matchup(a, b, player_b_id=2, player_a_id=1, as_of=AS_OF, surface="Hard")
    assert (m.h2h.wins, m.h2h.losses) == (1, 1)
    assert m.common_opponent_count == 3  # 7, 8, 9
    assert (m.common_opponents.wins, m.common_opponents.losses) == (2, 1)
    assert (m.common_opponents_b.wins, m.common_opponents_b.losses) == (1, 2)
    assert m.common_opponents.method == "proxy"


def test_fallback_pick_omits_below_minimum():
    narrow = rate(1, 0, "last365")
    wide = rate(2, 1, "career")
    picked = pick(4, narrow, wide)
    assert picked.is_omitted


def test_fallback_pick_widens():
    narrow = rate(1, 0, "last365")
    wide = rate(3, 2, "career")
    picked = pick(4, narrow, wide)
    assert picked.method == "widened" and picked.window == "career"


def _serve(ace, df, svpt, firstin, firstwon, secondwon, svgms, bpsaved, bpfaced):
    return dict(ace=ace, df=df, svpt=svpt, firstin=firstin, firstwon=firstwon,
                secondwon=secondwon, svgms=svgms, bpsaved=bpsaved, bpfaced=bpfaced)


def test_serve_return_aggregation():
    from bot.stats.profile import compute_serve_return

    hist = [mk(10 + i, i % 2 == 0,
               sets=((1, i % 2 == 0), (2, i % 2 == 0))) for i in range(10)]
    for m in hist:
        object.__setattr__(m, "serve",
                           _serve(5, 2, 80, 50, 38, 18, 12, 4, 6))
        object.__setattr__(m, "opp_serve",
                           _serve(3, 3, 78, 45, 30, 15, 12, 6, 10))
    sr = compute_serve_return(hist, AS_OF)
    assert sr.n_matches == 10
    assert abs(sr.ace_pct - 5 / 80) < 1e-9
    assert abs(sr.first_in_pct - 50 / 80) < 1e-9
    assert abs(sr.first_win_pct - 38 / 50) < 1e-9
    assert abs(sr.bp_saved_pct - 4 / 6) < 1e-9
    assert sr.return_pts_win_pct is not None


def test_serve_return_omits_without_stats():
    from bot.stats.profile import compute_serve_return

    hist = [mk(10 + i, True) for i in range(10)]  # no serve dicts
    sr = compute_serve_return(hist, AS_OF)
    assert sr.n_matches == 0 and sr.ace_pct is None


def test_clutch_tiebreaks_and_rank_quality():
    from bot.stats.profile import compute_clutch, compute_deciding_sets

    hist = [
        mk(5, True, opp=1, sets=((1, True), (2, True))),
        mk(10, False, opp=2, sets=((1, False), (2, False))),
        mk(15, True, opp=3, sets=((1, True), (2, True))),
    ]
    object.__setattr__(hist[0], "tiebreaks", ((1, True),))
    object.__setattr__(hist[1], "tiebreaks", ((2, False),))
    object.__setattr__(hist[0], "opp_rank", 12)
    object.__setattr__(hist[1], "opp_rank", 40)
    object.__setattr__(hist[2], "opp_rank", 200)
    c = compute_clutch(hist, AS_OF, compute_deciding_sets(hist, AS_OF).best)
    assert (c.tiebreak.wins, c.tiebreak.losses) == (1, 1)
    assert (c.vs_top50.wins, c.vs_top50.losses) == (1, 1)  # ranks 12 and 40
    assert (c.vs_top20.wins, c.vs_top20.losses) == (1, 0)  # only rank 12


def test_set_number_rates():
    from bot.stats.profile import compute_set_rates

    # 10 matches: wins set 1 in 8, set 2 in 5, decider record 2-1
    hist = ([mk(10 + i, True, sets=((1, True), (2, True))) for i in range(5)] +
            [mk(30 + i, True, sets=((1, True), (2, False), (3, True))) for i in range(2)] +
            [mk(50 + i, False, sets=((1, True), (2, False), (3, False))) for i in range(1)] +
            [mk(60 + i, False, sets=((1, False), (2, False))) for i in range(2)])
    rates = compute_set_rates(hist, AS_OF, min_sample=8)
    assert rates[1].wins == 8 and rates[1].losses == 2
    assert abs(rates[1].value - 0.8) < 1e-9
    assert rates[2].wins == 5 and rates[2].losses == 5
    # only 3 deciders < min_sample 8 in both windows -> omitted, never thin-sampled
    assert rates[3].is_omitted


def test_stat_never_fabricates_on_empty():
    d = compute_deciding_sets([], AS_OF)
    assert d.career.value is None and d.career.method == "omitted"
    assert d.days_since_decider_win is None
    assert d.last_n_results == []


def test_conditional_set1_win_rates():
    from bot.stats.profile import compute_conditional

    # won set 1 in 8, went on to win 7 of those; lost set 1 in 6, won 2,
    # forced a decider in 5 of the 6
    hist = ([mk(10 + i, i < 7, sets=((1, True), (2, i < 7)) if i < 7
             else ((1, True), (2, False), (3, False)),
             reached_decider=(i >= 7)) for i in range(8)] +
            [mk(50 + i, i < 2, reached_decider=(i < 5),
                sets=((1, False), (2, True), (3, i < 2)) if i < 5
                else ((1, False), (2, False))) for i in range(6)])
    c = compute_conditional(hist, AS_OF, min_sample=5)
    assert c.win_given_set1_won.wins == 7 and c.win_given_set1_won.losses == 1
    assert c.win_given_set1_lost.wins == 2 and c.win_given_set1_lost.losses == 4
    assert c.decider_given_set1_lost.wins == 5  # forced decider 5 of 6


def test_conditional_omits_below_min_sample():
    from bot.stats.profile import compute_conditional

    hist = [mk(10, True, sets=((1, True), (2, True)))]
    c = compute_conditional(hist, AS_OF, min_sample=8)
    assert c.win_given_set1_won.is_omitted
