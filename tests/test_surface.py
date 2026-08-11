"""bot.stats.surface — venue normalization + Grand-Slam surface overrides.

The venue→surface MAP needs Sackmann rows (a DB), so these cover the pure
pieces: the normalizer that strips country/level/sponsor noise down to the bare
venue, and the slam overrides (which resolve before any DB lookup)."""
from bot.stats.surface import _venue, resolve_surface


def test_venue_strips_country_and_qualification():
    # "City (Country) - Qualification" → just the city (Sackmann keys by city)
    assert _venue("Jiujiang (China) - Qualification") == "jiujiang"
    assert _venue("Athens (Greece) - Qualification") == "athens"
    assert _venue("Liege (Belgium) - Qualification") == "liege"


def test_venue_strips_tournament_number_and_level():
    assert _venue("Istanbul 2 (Turkey) - Qualification") == "istanbul"
    assert _venue("ATP Challenger Segovia") == "segovia"
    assert _venue("W15 Beograd (Serbia)") == "beograd"
    assert _venue("M25 Nivelles (Belgium)") == "nivelles"


def test_venue_strips_sponsor_and_format_words():
    assert _venue("Livesport Prague Open") == "livesport prague"  # sponsor stays
    # hyphen → space on both feed and Sackmann sides, so they still align
    assert _venue("Winston-Salem Open") == "winston salem"
    assert _venue("Miami Masters") == "miami"


def test_venue_handles_accents():
    assert _venue("Múnich") == "munich"


def test_grand_slams_resolve_by_name_without_db():
    # slams short-circuit before the map lookup, so db is never touched
    assert resolve_surface(None, "French Open") == "Clay"
    assert resolve_surface(None, "French Open Men Singles") == "Clay"
    assert resolve_surface(None, "Roland Garros") == "Clay"
    assert resolve_surface(None, "Wimbledon") == "Grass"
    assert resolve_surface(None, "US Open") == "Hard"
    assert resolve_surface(None, "Australian Open") == "Hard"


def test_slam_override_is_word_bounded():
    # "us open" must NOT fire inside "Aus Open" (Australian Open qualies) — the
    # slam match is word-bounded, so this falls through to the venue map (None
    # here, since there's no DB) rather than being wrongly tagged Hard
    assert resolve_surface(None, "Doha Aus Open Qualies") is None
    assert resolve_surface(None, "Dubai Aus Open Qualies") is None
    # a real "U.S. Open" spelling still resolves
    assert resolve_surface(None, "U.S. Open") == "Hard"


def test_empty_name_is_none():
    assert resolve_surface(None, None) is None
    assert resolve_surface(None, "") is None
