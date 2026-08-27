"""Guards on the things that cost money to run.

Railway bills memory. Each of these protects a measured regression, not a
hypothetical one."""
import bot.web as W
from bot.market.portfolio import aggregate_positions


def _main_tag(html):
    """Just the <main ...> tag — the JS constant also mentions data-nopoll, so a
    naive substring check passes on every page."""
    i = html.index("<main")
    return html[i:html.index(">", i) + 1]


def test_kalshi_page_opts_out_of_the_7s_poll():
    """/kalshi re-reads every fill and settlement and re-aggregates ~1.3k
    positions per render. Its data only changes on a manual sync, so polling it
    every 7s was ~500 needless full scans an hour."""
    html = W.page("Kalshi", "kalshi", "<p>b</p>",
                  user={"id": 1, "username": "o", "is_admin": True})
    assert 'data-nopoll="1"' in _main_tag(html)


def test_live_pages_still_poll():
    html = W.page("Live", "live", "<p>b</p>",
                  user={"id": 1, "username": "o", "is_admin": True})
    assert "data-nopoll" not in _main_tag(html)


def test_the_client_honours_the_marker():
    assert "main[data-nopoll]" in W.JS


def test_the_model_fit_is_not_rebuilt_every_half_hour():
    """_load_matches peaks ~330 MB at 875k matches. Ratings only change on the
    daily ingest, so a 30-minute TTL meant ~48 spikes a day in the web service
    for data that moves once."""
    assert W._MODEL_TTL_S >= 6 * 3600


def test_fills_carry_no_raw_payload():
    """Every Kalshi field is columnised; keeping the JSONB cost ~2.3 kB per fill
    (~24 MB across this account) every time fills were loaded."""
    from bot.models import KalshiFill
    assert not hasattr(KalshiFill, "raw")


def test_aggregation_needs_only_the_slim_columns():
    """The page loads ten columns, not whole ORM rows. If aggregate_positions
    ever starts reading another attribute this fails loudly rather than
    silently reintroducing the full-row load."""
    import types
    slim = types.SimpleNamespace(
        fill_id="f", ticker="T", event_ticker="E", action="buy",
        outcome_side="yes", count=1.0, yes_price_cents=40,
        no_price_cents=60, fee_cents=0.0, ts=1)
    (p,) = aggregate_positions([slim], {})
    assert p["ticker"] == "T" and p["gross_pnl"] == 0.0
