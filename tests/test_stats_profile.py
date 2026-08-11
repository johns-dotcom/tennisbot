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


def test_serve_conditional_winrate():
    from bot.stats.profile import serve_conditional_winrate

    # 10 matches: when the player serves 10 aces they win 4 of 6; serving few
    # aces (2) they win 1 of 4. Opponent DFs: opp had 5 DFs in the 6 the player
    # won, 0 in the 4 they lost.
    hist = []
    for i in range(10):
        won = i < 6
        m = mk(10 + i, won, sets=((1, won), (2, won)))
        object.__setattr__(m, "serve", _serve(10 if won else 2, 3, 80, 50, 38, 18, 12, 4, 6))
        object.__setattr__(m, "opp_serve", _serve(4, 5 if won else 0, 78, 45, 30, 15, 12, 6, 10))
        hist.append(m)

    # self serving 10+ aces → the 6 wins only (they served 10 there)
    s = serve_conditional_winrate(hist, AS_OF, key="ace", side="self", thresh=10)
    assert (s.wins, s.losses) == (6, 0) and s.value == 1.0
    # facing an opponent with 5+ double faults → those same 6 (opp DF=5 in wins)
    o = serve_conditional_winrate(hist, AS_OF, key="df", side="opp", thresh=5)
    assert (o.wins, o.losses) == (6, 0)
    # a thin split is omitted (min_n): only 4 matches at <=2 aces via exact-0? use
    # a threshold nothing meets → 0 matches → omitted
    z = serve_conditional_winrate(hist, AS_OF, key="ace", side="self", thresh=99)
    assert z.value is None and z.method == "omitted"
    # min_n gate: 6 wins is fine at default 6, but raise the bar and it omits
    hi = serve_conditional_winrate(hist, AS_OF, key="ace", side="self",
                                   thresh=10, min_n=8)
    assert hi.value is None


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


def test_schedule_strength():
    from bot.stats.profile import compute_schedule

    hist = [mk(10 + i, i % 2 == 0) for i in range(10)]
    for i, m in enumerate(hist):
        object.__setattr__(m, "opp_rank", 30 + i * 5)  # avg ~52 → strong/elite
    s = compute_schedule(hist, AS_OF, min_ranked=5)
    assert s.n_ranked == 10
    assert s.field in ("elite", "strong")
    assert s.vs_top100.wins + s.vs_top100.losses == 10  # all ranked <=100


def test_schedule_weak_field():
    from bot.stats.profile import compute_schedule

    hist = [mk(10 + i, True) for i in range(8)]
    for m in hist:
        object.__setattr__(m, "opp_rank", 600)
    s = compute_schedule(hist, AS_OF, min_ranked=5)
    assert s.field == "weak"


def test_style_matchup_big_server_vs_weak_returner():
    from bot.stats.profile import ChartingBlock, style_matchup

    server = ChartingBlock(n_matches=20, winners_per_match=25, unforced_per_match=18,
                           winner_ufe_ratio=1.4, fh_winner_share=0.6, bh_winner_share=0.4,
                           fh_ufe_share=0.5, ace_rate=0.14, first_serve_win=0.78,
                           second_serve_win=0.55, return_win=0.40)
    weak_ret = ChartingBlock(n_matches=20, winners_per_match=15, unforced_per_match=20,
                             winner_ufe_ratio=0.75, fh_winner_share=0.5, bh_winner_share=0.5,
                             fh_ufe_share=0.5, ace_rate=0.04, first_serve_win=0.65,
                             second_serve_win=0.45, return_win=0.30)
    notes = style_matchup(server, weak_ret, "Server", "Weakret")
    assert any("serve" in n and "weak return" in n for n in notes)


def test_style_matchup_empty_when_uncharted():
    from bot.stats.profile import style_matchup

    assert style_matchup(None, None, "A", "B") == []


def test_conditional_set3_after_losing_set2():
    from bot.stats.profile import compute_conditional
    # 10 matches where player lost set 2 (won set 1, forced to set 3);
    # won set 3 in 8 of them
    hist = [mk(10 + i, i < 8, sets=((1, True), (2, False), (3, i < 8)))
            for i in range(10)]
    c = compute_conditional(hist, AS_OF, min_sample=5)
    assert c.set3_given_lost_set2.wins == 8 and c.set3_given_lost_set2.losses == 2


def test_days_since_decider_played():
    from bot.stats.profile import compute_deciding_sets
    # last decider was a loss 30 days ago; last decider WIN 60 days ago
    hist = [mk(30, False, reached_decider=True, won_decider=False),
            mk(60, True, reached_decider=True, won_decider=True)]
    d = compute_deciding_sets(hist, AS_OF)
    assert d.days_since_decider_played == 30   # most recent decider (a loss)
    assert d.days_since_decider_win == 60       # most recent decider WIN


def test_compute_layoff_detects_return_from_break():
    from bot.stats.profile import compute_layoff
    # 3 recent matches, then a 90-day gap before older history → just returned
    hist = [mk(2, True), mk(6, False), mk(11, True),
            mk(101, False), mk(115, True)]
    lb = compute_layoff(hist, AS_OF)
    assert lb.days_since_last_match == 2
    assert lb.return_layoff_days >= 45
    assert lb.matches_since_return == 3
    assert lb.record_since_return == (2, 1)


def test_compute_layoff_recent_decider_load():
    from bot.stats.profile import compute_layoff
    hist = [mk(1, True, reached_decider=True, won_decider=True),
            mk(2, False, reached_decider=True, won_decider=False),
            mk(20, True, reached_decider=True, won_decider=True)]
    lb = compute_layoff(hist, AS_OF)
    assert lb.deciders_last_3d == 2   # two deciders in the last 72h
    assert lb.deciders_last_30d == 3


def test_compute_layoff_no_history():
    from bot.stats.profile import compute_layoff
    lb = compute_layoff([], AS_OF)
    assert lb.days_since_last_match is None and lb.return_layoff_days is None


def test_win_given_won_a_set_conditional():
    from bot.stats.profile import compute_conditional
    # won a set in 4 matches, took the match in 3 of them
    hist = [
        mk(2, True, sets=((1, True), (2, True))),
        mk(4, True, sets=((1, False), (2, True), (3, True)), reached_decider=True,
           won_decider=True),
        mk(6, True, sets=((1, True), (2, True))),
        mk(8, False, sets=((1, True), (2, False), (3, False)), reached_decider=True,
           won_decider=False),
        mk(10, False, sets=((1, False), (2, False))),  # won no set → excluded
    ]
    c = compute_conditional(hist, AS_OF, min_sample=3)
    assert c.win_given_won_a_set.wins == 3 and c.win_given_won_a_set.losses == 1
