from collections import namedtuple

from bot.matching.market_matcher import PlayerMatcher, normalize_name

_Row = namedtuple("_Row", "id sackmann_id api_tennis_id")


class _FakeExec:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    """execute() returns each supplied result-set in order: the resolver queries
    player rows, then winner match-counts, then loser match-counts."""
    def __init__(self, *result_sets):
        self._sets = list(result_sets)

    def execute(self, *_a, **_k):
        rows = self._sets.pop(0) if self._sets else []
        return _FakeExec(rows)


def _matcher():
    # skip the DB-backed __init__; we only exercise _resolve_duplicates
    return PlayerMatcher.__new__(PlayerMatcher)


def test_duplicate_shells_collapse_to_lowest_id():
    # identical "Andres Martin" shells, no ids, no match history → lowest id
    players = [_Row(138798, None, None), _Row(137431, None, None),
               _Row(137462, None, None), _Row(138481, None, None)]
    db = _FakeDB(players, [], [])  # players, winner-counts, loser-counts
    assert _matcher()._resolve_duplicates(db, [r.id for r in players]) == 137431


def test_single_substantive_candidate_wins_over_shells():
    # no match history → the id-bearing row beats the shells
    players = [_Row(500, None, None), _Row(42, 214581, None), _Row(700, None, None)]
    db = _FakeDB(players, [], [])
    assert _matcher()._resolve_duplicates(db, [500, 42, 700]) == 42


def test_richest_match_history_wins_even_over_lower_id():
    # two id-bearing dup rows: the one with real match history is the canonical,
    # even though it has the higher id (this is the Andres Martin case)
    players = [_Row(42, 111, None), _Row(63590, 222, None)]
    db = _FakeDB(players, [(63590, 40)], [(63590, 12)])  # 63590 has 52 matches
    assert _matcher()._resolve_duplicates(db, [42, 63590]) == 63590


def test_always_resolves_when_rows_exist():
    # never returns None when candidates exist — that's the whole point
    players = [_Row(9, None, None), _Row(3, None, None)]
    assert _matcher()._resolve_duplicates(_FakeDB(players, [], []), [9, 3]) == 3


def test_no_rows_is_none():
    assert _matcher()._resolve_duplicates(_FakeDB([]), [1, 2]) is None


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


def test_collapsed_space_insensitive_match():
    # "YeXin Ma" (joined, from the live feed) must resolve to the source-backed
    # "Ye Xin Ma" instead of fragmenting into a new shell
    m = PlayerMatcher.__new__(PlayerMatcher)
    m._pool, m._names, m._ids, m.tour = [], ["ye xin ma"], [82812], "wta"
    m._by_norm = {"ye xin ma": [82812]}
    m._by_collapsed = {"yexinma": [82812]}

    class _NoAlias:
        def scalars(self):
            return self

        def first(self):
            return None

    class _DB:
        def execute(self, *a, **k):
            return _NoAlias()

    r = m.match(_DB(), "YeXin Ma", source="kalshi", queue_on_miss=False)
    assert r.player_id == 82812 and r.method == "collapsed"
    # a direct exact spelling still takes the exact path
    r2 = m.match(_DB(), "Ye Xin Ma", source="kalshi", queue_on_miss=False)
    assert r2.player_id == 82812 and r2.method == "exact"


def test_surname_guard_blocks_different_family_names():
    from bot.matching.market_matcher import _surname_ok
    # the real bug: shared first name, one-letter surname diff, DIFFERENT people
    assert _surname_ok("alex hernandez", "alex fernandez") is False
    # legit fuzzy cases still pass: same surname, first-name variance / order
    assert _surname_ok("alex hernandez", "alejandro hernandez") is True
    assert _surname_ok("n djokovic", "novak djokovic") is True
    # identical surname always allowed
    assert _surname_ok("carlos alcaraz", "carlos alcaraz") is True
    # weak surname similarity → rejected (routes to review, not a wrong match),
    # even when the first letter matches
    assert _surname_ok("john smith", "john smyth") is False
    assert _surname_ok("john smith", "john blyth") is False
