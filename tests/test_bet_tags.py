"""A bet can be tailed from several sources, so its tag field holds a comma-joined
list. These cover parsing it and normalizing it down to what the column can hold."""
from bot.web import TAG_MAX_LEN, TAGS_MAX_LEN, _bet_tags, _norm_tags


def test_parsing_splits_trims_and_dedupes_case_insensitively():
    assert _bet_tags("blvr") == ["blvr"]
    assert _bet_tags("blvr, clutch") == ["blvr", "clutch"]
    assert _bet_tags(" a , b ,, c ") == ["a", "b", "c"]
    assert _bet_tags("blvr,clutch , Blvr") == ["blvr", "clutch"]   # order preserved
    assert _bet_tags(None) == [] and _bet_tags("") == [] and _bet_tags(" , ") == []


def test_normalizing_round_trips_through_the_stored_form():
    assert _norm_tags("blvr,  clutch ,BLVR") == "blvr, clutch"
    assert _bet_tags(_norm_tags("blvr, clutch, sharp")) == ["blvr", "clutch", "sharp"]


def test_untagged_stays_null_rather_than_an_empty_string():
    for raw in (None, "", "   ", ",", " , , "):
        assert _norm_tags(raw) is None


def test_overflow_drops_whole_tags_instead_of_slicing_one_mid_word():
    # the old behaviour sliced the JOINED string, so a 6th tag landed as "pinnacl"
    # — a phantom that then polluted autocomplete and per-tag performance
    tags = ["tennisinsider", "clutchplays", "blvr", "sharpmoney", "modeltail",
            "pinnacle"]
    stored = _norm_tags(", ".join(tags), limit=64)
    # the first five join to 55 chars; adding ", pinnacle" would hit 65, so the
    # whole sixth tag is dropped rather than stored as "pinnacl"
    assert stored == "tennisinsider, clutchplays, blvr, sharpmoney, modeltail"
    assert len(stored) <= 64
    assert "pinnacl" not in _bet_tags(stored)
    # every tag that survived is intact — none is a prefix of what was asked for
    assert set(_bet_tags(stored)) <= set(tags)


def test_a_single_overlong_tag_is_capped_not_dropped():
    long = "x" * (TAG_MAX_LEN + 50)
    assert _norm_tags(long) == "x" * TAG_MAX_LEN


def test_many_tags_fit_within_the_widened_column():
    tags = [f"tail{i:02d}" for i in range(20)]
    stored = _norm_tags(", ".join(tags))
    assert len(stored) <= TAGS_MAX_LEN
    # 20 short tags is ~140 chars — comfortably inside 256, so none is dropped
    assert _bet_tags(stored) == tags


def test_the_stored_form_never_exceeds_the_column_width():
    stored = _norm_tags(", ".join(f"averylongtagname{i}" for i in range(40)))
    assert stored is not None and len(stored) <= TAGS_MAX_LEN
