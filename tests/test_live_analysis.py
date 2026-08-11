"""live_analysis_html — the in-play synthesis shown below the read."""
from bot.web import live_analysis_html

TRIGS = [{"kind": "set1", "state": "1-0", "prob": 0.90},
         {"kind": "drop1", "state": "0-1", "prob": 0.55},
         {"kind": "decider", "state": "1-1", "prob": 0.74}]

DETAIL = {"yes_is_c1": True,
          "competitor1_statistics": {"aces": 7, "double_faults": 2,
                                     "first_serve_points_won": 36,
                                     "first_serve_successful": 48,
                                     "breakpoints_won": 3, "total_breakpoints": 7,
                                     "points_won_from_last_10": 7},
          "competitor2_statistics": {"aces": 3, "double_faults": 0,
                                     "first_serve_points_won": 45,
                                     "first_serve_successful": 57,
                                     "breakpoints_won": 2, "total_breakpoints": 9,
                                     "points_won_from_last_10": 3}}


def _call(**kw):
    base = dict(is_live=True, is_final=False, sets_watch=1, sets_opp=1,
                scoreline="3-6 7-6 1-2", st_state="1-1", prematch=0.84,
                dec_prob=0.74, triggers=TRIGS, watch_cents=52,
                player="Chia Yi Tsao", opp_name="Natsuho Arakawa", detail=DETAIL)
    base.update(kw)
    return live_analysis_html(**base)


def test_prematch_shows_placeholder():
    h = live_analysis_html(is_live=False, is_final=False, sets_watch=None,
                           sets_opp=None, scoreline=None, st_state=None,
                           prematch=0.84, dec_prob=0.74, triggers=TRIGS,
                           watch_cents=None, player="A", opp_name="B", detail=None)
    assert "Live play analysis" in h and "awaiting play" in h
    assert "model now" not in h  # nothing live yet


def test_final_shows_result():
    h = _call(is_final=True, scoreline="3-6 7-6 6-3", watch_cents=None, detail=None)
    assert "Match final" in h and "3-6 7-6 6-3" in h


def test_live_model_now_from_state_trigger():
    # at the decider (1-1) the model-now is the decider trigger's prob, not prematch
    h = _call()
    assert "74%" in h            # model now
    assert "at 1-1" in h
    assert "84%" in h            # prematch reference shown alongside


def test_decider_in_band_is_entry_live():
    h = _call(watch_cents=52)
    assert "ENTRY LIVE" in h


def test_out_of_band_when_priced_outside_35_65():
    h = _call(watch_cents=80)
    assert "OUT OF BAND" in h and "ENTRY LIVE" not in h


def test_down_a_set_holds_over_the_trigger():
    # dropped set 1 (0-1): risk rule outranks the drop1 trigger — never chase
    h = _call(sets_watch=0, sets_opp=1, st_state="0-1", watch_cents=40)
    assert "HOLD" in h and "stay away" in h
    assert "ENTRY LIVE" not in h


def test_serve_takeaway_oriented_to_yes_side():
    # the full chart moved to the combined "Live match stats" section (built in
    # _match_view); here we only assert the serve-battle sentence is distilled
    # into the written read, oriented to the YES side.
    h = _call()
    assert "Tsao is serving bigger" in h   # YES=c1, 7 aces to Arakawa's 3
    assert "7 aces to 3" in h
    # the bar chart itself no longer renders inside this panel
    assert "#5b8def" not in h


def test_serve_takeaway_absent_without_orientation():
    # no yes_is_c1 stored (old rows) → no misattributed stats, no serve sentence
    h = _call(detail={"competitor1_statistics": {"aces": 7}})
    assert "serving bigger" not in h


def test_written_read_present_when_live():
    h = _call()
    # the prose read narrates state → model → value → verdict
    assert "class=\"prose\"" in h
    assert "level at 1-1" in h.lower() or "deciding set" in h.lower()
    assert "prematch" in h.lower()


def test_live_value_vs_price():
    h = _call(watch_cents=52)     # model 74% vs 52¢ → +22% value
    assert "value" in h
    h2 = _call(watch_cents=76)    # 74% vs 76¢ → no edge
    assert "no edge" in h2
