"""Retention for market_ticks.

The table had no retention at all — a row per websocket quote, kept forever,
~120M rows / ~39 GB after eight weeks. Memory was 88% of the Railway bill.

Deleting data is the risky kind of fix, so these tests pin the two things that
must not break: only settled markets are touched, and the take-profit question
still answers identically after the ticks are gone."""
import datetime as dt

import bot.web as W
from bot.market.retention import prune_market_ticks


class Row(list):
    pass


class FakeDB:
    """Records the SQL it is asked to run; serves canned rows."""

    def __init__(self, due=(), tick_rows=(), markets=()):
        self.due, self.tick_rows, self.markets = list(due), list(tick_rows), list(markets)
        self.sql = []
        self.commits = 0

    def execute(self, stmt, params=None):
        q = " ".join(str(stmt).split())
        self.sql.append(q)
        if q.startswith("DELETE FROM market_ticks"):
            return type("R", (), {"rowcount": 123})()
        if "max(yes_bid)" in q or "max(ts) FILTER" in q:
            return type("R", (), {"all": lambda _self: self.tick_rows})()
        return type("R", (), {
            "scalars": lambda _self: type("S", (), {
                "__iter__": lambda _s: iter(self.markets),
                "all": lambda _s: self.due})()})()

    def commit(self):
        self.commits += 1


class Mkt:
    def __init__(self, ticker):
        self.ticker = ticker
        self.peak_yes_bid = self.peak_no_bid = None
        self.tp_yes_at = self.tp_no_at = self.ticks_pruned_at = None


def test_nothing_is_deleted_when_nothing_is_due():
    db = FakeDB(due=[])
    s = prune_market_ticks(db)
    assert s["ticks_deleted"] == 0
    assert not any(q.startswith("DELETE") for q in db.sql)


def test_summaries_are_committed_before_the_delete():
    """An interrupted run must never lose the derived facts."""
    db = FakeDB(due=["T1"], markets=[Mkt("T1")],
                tick_rows=[("T1", 93, 40, dt.datetime(2026, 8, 1), None)])
    prune_market_ticks(db)
    order = [i for i, q in enumerate(db.sql)
             if "max(ts) FILTER" in q or q.startswith("DELETE FROM market_ticks")]
    summarize_i = next(i for i in order if "max(ts) FILTER" in db.sql[i])
    delete_i = next(i for i in order if db.sql[i].startswith("DELETE"))
    assert summarize_i < delete_i


def test_only_settled_markets_older_than_the_window_are_selected():
    """A live match must keep the ticks it is still being priced from."""
    db = FakeDB(due=["T1"], markets=[Mkt("T1")],
                tick_rows=[("T1", 93, 40, dt.datetime(2026, 8, 1), None)])
    prune_market_ticks(db, keep_days=30)
    sel = next(q for q in db.sql if "kalshi_markets.ticker" in q and "WHERE" in q)
    assert "result IS NOT NULL" in sel
    assert "ticks_pruned_at IS NULL" in sel
    assert "close_time <" in sel


# --- the fallback must answer the take-profit question identically ------

class M2:
    def __init__(self, ticker, peak_yes, tp_yes_at, pruned=True):
        self.ticker, self.peak_yes_bid = ticker, peak_yes
        self.peak_no_bid, self.tp_no_at = None, None
        self.tp_yes_at = tp_yes_at
        self.ticks_pruned_at = dt.datetime(2026, 8, 1) if pruned else None


class PeakDB:
    def __init__(self, live_rows, markets):
        self.live_rows, self.markets = live_rows, markets

    def execute(self, stmt, params=None):
        q = " ".join(str(stmt).split())
        if "max(yes_bid)" in q:
            return type("R", (), {"all": lambda _s: self.live_rows})()
        # honour the real query's filter, otherwise this stub would pass rows
        # the production SQL would never return and the test proves nothing
        rows = self.markets
        if "ticks_pruned_at IS NOT NULL" in q:
            rows = [m for m in rows if m.ticks_pruned_at is not None]
        return type("R", (), {"scalars": lambda _s: type("S", (), {
            "__iter__": lambda _x: iter(rows)})()})()


BET = dt.datetime(2026, 7, 1)


def test_pruned_market_reports_its_peak_when_the_limit_was_hit_after_the_bet():
    db = PeakDB([], [M2("T", 93, dt.datetime(2026, 7, 15))])   # after BET
    assert W._peak_bids(db, ["T"], BET) == {"T": (93, None)}


def test_pruned_market_withholds_the_peak_when_the_limit_predates_the_bet():
    """The crucial case. The peak alone would wrongly credit a take-profit that
    happened BEFORE the bet was placed; tp_yes_at is what makes it exact."""
    db = PeakDB([], [M2("T", 93, dt.datetime(2026, 6, 1))])    # before BET
    assert W._peak_bids(db, ["T"], BET) == {}


def test_a_market_that_never_hit_the_limit_reports_nothing():
    db = PeakDB([], [M2("T", 71, None)])
    assert W._peak_bids(db, ["T"], BET) == {}


def test_live_ticks_win_over_the_stored_summary():
    db = PeakDB([("T", 88, 30)], [M2("T", 93, dt.datetime(2026, 7, 15))])
    assert W._peak_bids(db, ["T"], BET) == {"T": (88, 30)}


def test_unpruned_markets_are_never_served_from_the_summary():
    """If ticks still exist the live query is authoritative; a market that has
    not been pruned must not silently fall back."""
    db = PeakDB([], [M2("T", 93, dt.datetime(2026, 7, 15), pruned=False)])
    assert W._peak_bids(db, ["T"], BET) == {}


def test_no_tickers_short_circuits():
    assert W._peak_bids(None, [], BET) == {}
