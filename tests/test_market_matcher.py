from bot.matching.market_matcher import normalize_name


def test_diacritics_stripped():
    assert normalize_name("Bencić") == "bencic"
    assert normalize_name("Muñoz") == "munoz"
    assert normalize_name("Gaël Monfils") == "gael monfils"


def test_punctuation_and_case():
    assert normalize_name("O'Connell, Christopher") == "o connell christopher"
    assert normalize_name("J.J. Wolf") == "j j wolf"


def test_whitespace_collapsed():
    assert normalize_name("  Iga   Świątek ") == "iga swiatek"


def test_empty():
    assert normalize_name("") == ""
    assert normalize_name(None) == ""
