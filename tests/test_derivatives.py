"""Kalshi's non-match-winner tennis markets (set winner, exact score, total
games): ticker parsing, linkage back to a tracked match, and the refresh upsert.

Shapes are taken verbatim from the live API (read 2026-08-11)."""
from datetime import datetime, timezone

from bot.market.derivatives import (
    DERIVATIVE_SERIES,
    kind_label,
    match_key,
    refresh_derivatives,
    set_number,
)
from bot.models import DerivativeMarket, KalshiMarket

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def mkt(ticker, ev, sub, bid="0.4300", ask="0.4500", result="", status="active"):
    return {"ticker": ticker, "event_ticker": ev, "yes_sub_title": sub,
            "title": f"Will {sub} ...", "status": status, "result": result,
            "yes_bid_dollars": bid, "yes_ask_dollars": ask,
            "last_price_dollars": "0.4400", "close_time": "2026-08-11T18:00:00Z"}


class FakeDB:
    def __init__(self, kalshi=(), derivative=()):
        self.kalshi, self.derivative = list(kalshi), list(derivative)
        self.added, self.commits = [], 0

    def execute(self, stmt):
        s = str(stmt)
        rows = self.derivative if "derivative_markets" in s else self.kalshi
        return _Res(rows)

    def add(self, obj):
        self.added.append(obj)
        self.derivative.append(obj)

    def commit(self):
        self.commits += 1


class _Res:
    def __init__(self, v):
        self.v = v

    def scalars(self):
        return self

    def all(self):
        return self.v

    def __iter__(self):
        return iter(self.v)


class FakeClient:
    def __init__(self, by_series, singles=None):
        self.by_series, self.singles = by_series, (singles or {})
        self.fetched_singles = []

    def markets(self, series):
        return self.by_series.get(series, [])

    def market(self, ticker):
        self.fetched_singles.append(ticker)
        return self.singles[ticker]


def tracked_match(ev="KXATPMATCH-26AUG11WOLSAM", a="Jeffrey John Wolf",
                  b="Toby Samuel"):
    return [KalshiMarket(ticker=f"{ev}-WOL", event_ticker=ev,
                         raw={"yes_sub_title": a}),
            KalshiMarket(ticker=f"{ev}-SAM", event_ticker=ev,
                         raw={"yes_sub_title": b})]


# --- ticker parsing -------------------------------------------------------

def test_match_key_is_the_segment_shared_across_every_series_for_one_match():
    assert match_key("KXATPEXACTMATCH-26AUG11WOLSAM") == "26AUG11WOLSAM"
    assert match_key("KXATPSETWINNER-26AUG11WOLSAM-2") == "26AUG11WOLSAM"
    assert match_key("KXATPMATCH-26AUG11WOLSAM") == "26AUG11WOLSAM"
    assert match_key("NODASH") is None and match_key("") is None


def test_set_number_only_applies_to_set_winner_events():
    assert set_number("KXATPSETWINNER-26AUG11WOLSAM-2", "set_winner") == 2
    assert set_number("KXATPEXACTMATCH-26AUG11WOLSAM", "exact_score") is None
    # a set-winner event without a numeric third segment must not crash
    assert set_number("KXATPSETWINNER-26AUG11WOLSAM", "set_winner") is None
    assert set_number("KXATPSETWINNER-26AUG11WOLSAM-X", "set_winner") is None


def test_kind_labels_name_the_set_and_stay_readable_for_unknown_kinds():
    assert kind_label("set_winner", 2) == "Set 2 winner"
    assert kind_label("set_winner", None) == "Set winner"
    assert kind_label("exact_score") == "Exact score"
    assert kind_label("total_games") == "Total games"
    assert kind_label("something_new") == "Something new"


def test_every_configured_series_is_a_tennis_series_with_a_known_tour():
    for series, (kind, tour) in DERIVATIVE_SERIES.items():
        assert series.startswith(("KXATP", "KXWTA")), series
        assert tour in ("atp", "wta")
        assert kind_label(kind)          # every kind has a label


# --- refresh --------------------------------------------------------------

def test_refresh_stores_derivatives_linked_back_to_the_tracked_match():
    db = FakeDB(kalshi=tracked_match())
    client = FakeClient({"KXATPSETWINNER": [
        mkt("KXATPSETWINNER-26AUG11WOLSAM-2-WOL",
            "KXATPSETWINNER-26AUG11WOLSAM-2", "Jeffrey John Wolf"),
        mkt("KXATPSETWINNER-26AUG11WOLSAM-2-SAM",
            "KXATPSETWINNER-26AUG11WOLSAM-2", "Toby Samuel")]})
    stats = refresh_derivatives(db, client)

    assert stats["kept"] == 2 and stats["new"] == 2
    r = db.added[0]
    assert r.kind == "set_winner" and r.set_no == 2
    assert r.match_event_ticker == "KXATPMATCH-26AUG11WOLSAM"
    assert r.label == "Jeffrey John Wolf"
    assert r.match_label == "Jeffrey John Wolf vs Toby Samuel"
    assert (r.yes_bid_cents, r.yes_ask_cents) == (43, 45)
    assert r.result is None          # '' from Kalshi is not a settled outcome


def test_derivatives_for_untracked_matches_are_dropped_not_orphaned():
    db = FakeDB(kalshi=tracked_match())
    client = FakeClient({"KXATPEXACTMATCH": [
        mkt("KXATPEXACTMATCH-26AUG11ZZZZZZ-ZZZ21",
            "KXATPEXACTMATCH-26AUG11ZZZZZZ", "Someone wins 2-1")]})
    stats = refresh_derivatives(db, client)
    assert stats["seen"] == 1 and stats["kept"] == 0 and db.added == []


def test_a_wta_derivative_does_not_bind_to_an_atp_match_with_the_same_key():
    # match keys are date+letters and could collide across tours; the tour must
    # disambiguate or a WTA set-winner market would attach to an ATP match
    db = FakeDB(kalshi=tracked_match())          # ATP only
    client = FakeClient({"KXWTASETWINNER": [
        mkt("KXWTASETWINNER-26AUG11WOLSAM-2-WOL",
            "KXWTASETWINNER-26AUG11WOLSAM-2", "Someone Else")]})
    stats = refresh_derivatives(db, client)
    assert stats["kept"] == 0 and db.added == []


def test_refresh_updates_an_existing_row_rather_than_duplicating_it():
    existing = DerivativeMarket(
        ticker="KXATPSETWINNER-26AUG11WOLSAM-2-WOL",
        event_ticker="KXATPSETWINNER-26AUG11WOLSAM-2", series_ticker="KXATPSETWINNER",
        kind="set_winner", match_event_ticker="KXATPMATCH-26AUG11WOLSAM",
        set_no=2, label="Jeffrey John Wolf", yes_ask_cents=45)
    db = FakeDB(kalshi=tracked_match(), derivative=[existing])
    client = FakeClient({"KXATPSETWINNER": [
        mkt("KXATPSETWINNER-26AUG11WOLSAM-2-WOL",
            "KXATPSETWINNER-26AUG11WOLSAM-2", "Jeffrey John Wolf",
            bid="0.7000", ask="0.7200")]})
    stats = refresh_derivatives(db, client)
    assert stats["new"] == 0 and db.added == []
    assert existing.yes_ask_cents == 72 and existing.yes_bid_cents == 70


def test_settlement_is_chased_for_rows_that_dropped_out_of_the_open_crawl():
    held = DerivativeMarket(
        ticker="KXATPSETWINNER-26AUG11WOLSAM-1-WOL",
        event_ticker="KXATPSETWINNER-26AUG11WOLSAM-1", series_ticker="KXATPSETWINNER",
        kind="set_winner", match_event_ticker="KXATPMATCH-26AUG11WOLSAM",
        set_no=1, label="Jeffrey John Wolf", result=None)
    db = FakeDB(kalshi=tracked_match(), derivative=[held])
    client = FakeClient(
        {"KXATPSETWINNER": []},                       # set 1 is no longer open
        {held.ticker: mkt(held.ticker, held.event_ticker, "Jeffrey John Wolf",
                          result="yes", status="settled")})
    stats = refresh_derivatives(db, client)
    assert client.fetched_singles == [held.ticker]
    assert held.result == "yes" and held.settled_at is not None
    assert stats["settled"] == 1


def test_already_settled_rows_are_not_chased_again():
    done = DerivativeMarket(
        ticker="KXATPSETWINNER-26AUG11WOLSAM-1-WOL",
        event_ticker="KXATPSETWINNER-26AUG11WOLSAM-1", series_ticker="KXATPSETWINNER",
        kind="set_winner", match_event_ticker="KXATPMATCH-26AUG11WOLSAM",
        set_no=1, result="yes")
    db = FakeDB(kalshi=tracked_match(), derivative=[done])
    client = FakeClient({"KXATPSETWINNER": []})
    refresh_derivatives(db, client)
    assert client.fetched_singles == []


def test_nothing_is_fetched_when_no_matches_are_tracked():
    db = FakeDB(kalshi=[])
    client = FakeClient({"KXATPSETWINNER": [
        mkt("KXATPSETWINNER-X-2-A", "KXATPSETWINNER-26AUG11WOLSAM-2", "A")]})
    stats = refresh_derivatives(db, client)
    assert stats == {"seen": 0, "kept": 0, "new": 0, "settled": 0}


def test_a_failing_series_does_not_abort_the_rest_of_the_crawl():
    class Flaky(FakeClient):
        def markets(self, series):
            if series == "KXATPEXACTMATCH":
                raise RuntimeError("kalshi 500")
            return self.by_series.get(series, [])

    db = FakeDB(kalshi=tracked_match())
    client = Flaky({"KXATPSETWINNER": [
        mkt("KXATPSETWINNER-26AUG11WOLSAM-2-WOL",
            "KXATPSETWINNER-26AUG11WOLSAM-2", "Jeffrey John Wolf")]})
    stats = refresh_derivatives(db, client)
    assert stats["kept"] == 1 and len(db.added) == 1
