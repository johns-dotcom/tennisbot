"""The durable live/ended/pre-match classification of raw Kalshi milestone
status codes. Regression guard for 'a live match shows as starting soon'."""
from bot.market.live_status import ENDED, NOT_STARTED, is_live_status, status_kind


def test_known_live_codes():
    for s in ("live", "P", "CTS", "S", "started", "1st_set", "2nd_set", "interrupted"):
        assert status_kind(s) == "live", s
        assert is_live_status(s) is True


def test_unknown_present_code_is_live_not_soon():
    # THE regression: 'E' (and any future unseen live code) must classify as live,
    # never fall through to 'starting soon'. This is the whole point of the
    # blocklist-not-allowlist design.
    assert status_kind("E") == "live"
    assert is_live_status("E") is True
    for made_up in ("X", "Q3", "tb", "changeover", "some_new_code"):
        assert status_kind(made_up) == "live", made_up


def test_pre_match_codes():
    for s in ("not_started", "NOT_STARTED", "sch", "scheduled", "ns", "tbd", "delayed"):
        assert status_kind(s) == "not_started", s
        assert is_live_status(s) is False


def test_ended_codes():
    for s in ("finished", "ended", "closed", "retired", "walkover", "final",
              "settled", "void", "postponed"):
        assert status_kind(s) == "ended", s
        assert is_live_status(s) is False


def test_absent_status():
    assert status_kind(None) == "none"
    assert status_kind("") == "none"
    assert status_kind("   ") == "none"
    assert is_live_status(None) is False


def test_case_and_whitespace_insensitive():
    assert status_kind("  Live ") == "live"
    assert status_kind("FINISHED") == "ended"


def test_vocabularies_disjoint():
    # a code must never be both pre-match and ended
    assert NOT_STARTED.isdisjoint(ENDED)
