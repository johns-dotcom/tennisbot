"""Line-movement signal: our side's drift from the open, and the pre-match
adverse-selection gate."""
from bot.market.line_move import (ADVERSE_MOVE_PREMATCH, adverse_prematch,
                                  market_move_cents)


class _FakeDB:
    """Minimal stand-in: db.execute(...).first() -> the opening (yes_bid, yes_ask)."""
    def __init__(self, row):
        self._row = row

    def execute(self, *_a, **_k):
        return self

    def first(self):
        return self._row


def test_yes_side_move_signed_from_open():
    # opened yes_ask=40, entering yes at 52 -> our side got 12¢ MORE expensive
    db = _FakeDB((38, 40))  # (yes_bid, yes_ask)
    assert market_move_cents(db, "T", "yes", 52) == +12
    # entering yes at 33 -> our side got 7¢ CHEAPER (market faded us)
    assert market_move_cents(db, "T", "yes", 33) == -7


def test_no_side_uses_the_complement_of_yes_bid():
    # opened yes_bid=60 -> our NO side opened at 100-60 = 40
    db = _FakeDB((60, 62))
    assert market_move_cents(db, "T", "no", 45) == +5   # NO got pricier
    assert market_move_cents(db, "T", "no", 34) == -6   # NO faded


def test_none_when_no_prior_quote():
    assert market_move_cents(_FakeDB(None), "T", "yes", 50) is None


def test_none_never_raises_on_bad_db():
    class _Boom:
        def execute(self, *a, **k):
            raise RuntimeError("db down")
    # a missing/broken reference must never block a bet
    assert market_move_cents(_Boom(), "T", "yes", 50) is None


def test_adverse_prematch_only_on_big_downward_move():
    assert adverse_prematch(-ADVERSE_MOVE_PREMATCH) is True     # exactly the threshold
    assert adverse_prematch(-(ADVERSE_MOVE_PREMATCH + 5)) is True
    assert adverse_prematch(-(ADVERSE_MOVE_PREMATCH - 1)) is False  # not far enough
    assert adverse_prematch(+20) is False   # our side ROSE — not adverse
    assert adverse_prematch(0) is False
    assert adverse_prematch(None) is False  # unknown reference never gates
