"""Partial cash-out on the My Bets ledger: selling part of a position splits it
into a realized slice + a still-open remainder, and undoing folds it back."""
from datetime import datetime, timezone

from bot.models import UserBet
from bot.web import cash_out_bet, uncash_bet

PLACED = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


class FakeDB:
    """Enough of a Session for the split/merge logic: add(), delete(), get()."""

    def __init__(self, *rows):
        self.rows = {r.id: r for r in rows}
        self._next = max(self.rows, default=0) + 1

    def add(self, obj):
        obj.id = self._next
        self._next += 1
        self.rows[obj.id] = obj

    def delete(self, obj):
        self.rows.pop(obj.id, None)

    def get(self, _cls, oid):
        return self.rows.get(oid)

    def open_bets(self):
        return [b for b in self.rows.values() if b.exit_price_cents is None]

    def cashed(self):
        return [b for b in self.rows.values() if b.exit_price_cents is not None]


def _bet(**kw):
    b = UserBet(user_id=7, event_ticker="EV", market_ticker="MT", side="yes",
                player_name="Swiatek", opponent_name="Sabalenka",
                entry_price_cents=kw.pop("entry", 40), shares=kw.pop("shares", 100),
                unit_usd=500, tag="blvr", created_at=PLACED, **kw)
    b.id = 1
    return b


def test_selling_part_splits_off_a_realized_slice_and_leaves_the_rest_open():
    b = _bet(shares=100, entry=40)
    db = FakeDB(b)
    cash_out_bet(db, b, 72, 40)

    assert b.shares == 60 and b.exit_price_cents is None   # remainder still runs
    (slice_,) = db.cashed()
    assert (slice_.shares, slice_.exit_price_cents) == (40, 72)
    assert slice_.parent_bet_id == b.id
    # the slice inherits the position's identity so the ledger stays coherent
    assert (slice_.entry_price_cents, slice_.unit_usd) == (40, 500)
    assert (slice_.tag, slice_.market_ticker, slice_.created_at) == (
        "blvr", "MT", PLACED)
    # no shares invented or lost by the split
    assert b.shares + slice_.shares == 100


def test_selling_the_whole_position_exits_in_place_without_splitting():
    b = _bet(shares=100)
    db = FakeDB(b)
    cash_out_bet(db, b, 80, 100)
    assert len(db.rows) == 1
    assert b.exit_price_cents == 80 and b.shares == 100 and b.parent_bet_id is None

    b2 = _bet(shares=100)
    db2 = FakeDB(b2)
    cash_out_bet(db2, b2, 80)          # shares omitted → the whole thing
    assert len(db2.rows) == 1 and b2.exit_price_cents == 80


def test_share_count_is_clamped_to_what_is_actually_held():
    b = _bet(shares=30)
    db = FakeDB(b)
    cash_out_bet(db, b, 55, 999)       # over-sell → full exit, never negative
    assert len(db.rows) == 1 and b.shares == 30 and b.exit_price_cents == 55

    b2 = _bet(shares=30)
    db2 = FakeDB(b2)
    cash_out_bet(db2, b2, 55, 0)       # under-1 → at least one share
    assert b2.shares == 29 and db2.cashed()[0].shares == 1


def test_repeated_part_sells_each_keep_the_price_they_sold_at():
    b = _bet(shares=100)
    db = FakeDB(b)
    cash_out_bet(db, b, 60, 25)
    cash_out_bet(db, b, 85, 25)
    assert b.shares == 50
    assert sorted((s.shares, s.exit_price_cents) for s in db.cashed()) == [
        (25, 60), (25, 85)]
    # every slice points back at the ORIGINAL position, not at the previous slice
    assert {s.parent_bet_id for s in db.cashed()} == {b.id}


def test_undoing_a_slice_folds_its_shares_back_into_the_open_position():
    b = _bet(shares=100)
    db = FakeDB(b)
    cash_out_bet(db, b, 72, 40)
    (slice_,) = db.cashed()
    uncash_bet(db, slice_)
    assert b.shares == 100 and db.cashed() == []
    assert list(db.rows) == [b.id]      # the slice is gone, not left dangling


def test_undoing_a_slice_reopens_it_alone_when_the_position_is_gone_or_exited():
    # position since fully cashed out → the slice must not merge into a closed row
    b = _bet(shares=100)
    db = FakeDB(b)
    cash_out_bet(db, b, 72, 40)
    (slice_,) = db.cashed()
    cash_out_bet(db, b, 90)             # remainder exits too
    uncash_bet(db, slice_)
    assert slice_.exit_price_cents is None and slice_.shares == 40
    assert b.shares == 60 and b.exit_price_cents == 90

    # position deleted entirely → slice just reopens on its own
    b2 = _bet(shares=100)
    db2 = FakeDB(b2)
    cash_out_bet(db2, b2, 72, 40)
    (slice2,) = db2.cashed()
    db2.delete(b2)
    uncash_bet(db2, slice2)
    assert slice2.exit_price_cents is None and slice2.shares == 40


def test_a_repriced_position_does_not_swallow_the_slice_at_the_wrong_basis():
    b = _bet(shares=100, entry=40)
    db = FakeDB(b)
    cash_out_bet(db, b, 72, 40)
    (slice_,) = db.cashed()
    b.entry_price_cents = 55            # user corrected/added onto the position
    uncash_bet(db, slice_)
    assert slice_.exit_price_cents is None and slice_.entry_price_cents == 40
    assert b.shares == 60               # untouched — no silent re-basing


def test_undoing_a_plain_full_cashout_just_reverts_it_to_held():
    b = _bet(shares=100)
    db = FakeDB(b)
    cash_out_bet(db, b, 80)
    uncash_bet(db, b)
    assert b.exit_price_cents is None and b.exit_at is None and b.shares == 100
