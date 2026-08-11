"""advisory_gate_ok — the single-variable experiment bots each pass ONLY when
their one indicator condition holds."""
from bot.t2 import BOTS, MOVE_THRESH, advisory_gate_ok


def _ok(bot, **kw):
    base = dict(at_decider=False, confidence=0.7, tier="C", move=0, tour="atp")
    base.update(kw)
    return advisory_gate_ok(BOTS[bot], **base)


def test_ungated_mid_always_passes():
    # `mid` has no experiment gate → passes regardless
    assert _ok("mid", tier="A", confidence=0.1, move=-50) is True


def test_tier_splits():
    assert _ok("tmain", tier="A") is True
    assert _ok("tmain", tier="C") is False
    assert _ok("tchal", tier="C") is True
    assert _ok("tchal", tier="A") is False
    assert _ok("titf", tier="15") is True
    assert _ok("titf", tier="25") is True
    assert _ok("titf", tier="C") is False


def test_gender_splits():
    assert _ok("men", tour="atp") is True
    assert _ok("men", tour="wta") is False
    assert _ok("women", tour="wta") is True
    assert _ok("women", tour="atp") is False


def test_high_confidence_gate():
    assert _ok("hiconf", confidence=0.85) is True
    assert _ok("hiconf", confidence=0.79) is False
    assert _ok("hiconf", confidence=None) is False


def test_follow_needs_line_toward_pick():
    assert _ok("follow", move=MOVE_THRESH) is True        # our side rose enough
    assert _ok("follow", move=MOVE_THRESH + 10) is True
    assert _ok("follow", move=MOVE_THRESH - 1) is False    # not enough
    assert _ok("follow", move=-20) is False                # moved against us
    assert _ok("follow", move=None) is False               # no reference


def test_fade_needs_line_against_pick():
    assert _ok("fade", move=-MOVE_THRESH) is True          # our side fell enough
    assert _ok("fade", move=MOVE_THRESH) is False          # moved toward us
    assert _ok("fade", move=0) is False


def test_decider_only_still_gated():
    assert _ok("dec", at_decider=True) is True
    assert _ok("dec", at_decider=False) is False


def _dip(**kw):
    # a clear favorite on the YES side, down 0-1, advisory recommends YES, Bo3
    base = dict(at_decider=False, confidence=0.7, tier="A", move=0, tour="atp",
                sets=(0, 1), best_of=3, fav_side="yes", fav_prob=0.66,
                recommended_side="yes")
    base.update(kw)
    return advisory_gate_ok(BOTS["dip"], **base)


def test_dip_fires_only_for_favorite_down_a_set():
    assert _dip() is True                                  # canonical case
    # YES favorite but hasn't dropped a set yet (still 0-0)
    assert _dip(sets=(0, 0)) is False
    # YES favorite already won set 1 → not the dip
    assert _dip(sets=(1, 0)) is False
    # NO-side favorite that dropped set 1 (YES won it) → sets 1-0, recommend NO
    assert _dip(fav_side="no", recommended_side="no", sets=(1, 0)) is True
    assert _dip(fav_side="no", recommended_side="no", sets=(0, 1)) is False


def test_dip_requires_clear_favorite_and_backs_only_the_favorite():
    assert _dip(fav_prob=0.58) is False                    # not a clear favorite
    assert _dip(fav_prob=None) is False
    # advisory recommends the OTHER side → we never back the opponent's move
    assert _dip(recommended_side="no") is False


def test_dip_is_bo3_only():
    assert _dip(best_of=5) is False


def test_each_gated_bot_isolates_one_variable():
    # every experiment bot declares at most ONE gate key (clean attribution)
    keys = {"decider_only", "tiers", "tour", "min_conf", "move", "dropped_set1"}
    for bid in ("tmain", "tchal", "titf", "men", "women", "hiconf", "follow",
                "fade", "dip"):
        declared = keys & set(BOTS[bid])
        assert len(declared) == 1, f"{bid} declares {declared}, want exactly 1"
