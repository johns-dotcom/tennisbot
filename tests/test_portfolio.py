"""Average-cost P&L over real Kalshi fills.

Both regressions guarded here were found by reconciling against the live
account's own `realized_pnl_dollars`, not by reasoning — the first version of
this code reported −$47,301 realized on an account holding $6,615."""
import types

from bot.market.portfolio import (
    account_pnl,
    aggregate_positions,
    fills_window,
    hold_side,
    summarize,
)


def fill(action, side, count, yes, fid="f", ts=1, fee=0.0, ticker="KXATPMATCH-X-A"):
    """yes = the YES price in cents; NO is always its complement."""
    return types.SimpleNamespace(
        fill_id=fid, ticker=ticker, event_ticker="KXATPMATCH-X", action=action,
        outcome_side=side, count=count, yes_price_cents=yes,
        no_price_cents=100 - yes, fee_cents=fee, ts=ts)


def sett(result, revenue=0.0, fee=0.0):
    return types.SimpleNamespace(market_result=result, revenue_cents=revenue,
                                 fee_cents=fee)


# --- which side do we actually hold -------------------------------------

def test_hold_side_comes_from_buys_and_is_exact_when_one_sided():
    assert hold_side([fill("buy", "no", 10, 49), fill("sell", "yes", 10, 20)]) == ("no", True)
    assert hold_side([fill("buy", "yes", 10, 40)]) == ("yes", True)


def test_mixed_side_buying_is_flagged_inexact():
    side, exact = hold_side([fill("buy", "yes", 10, 40), fill("buy", "no", 3, 40)])
    assert side == "yes" and exact is False      # majority side, but not certain


def test_no_buys_at_all_degrades_without_crashing():
    assert hold_side([fill("sell", "yes", 1, 50)]) == ("yes", False)


# --- the two bugs that produced impossible numbers ----------------------

def test_an_exit_is_priced_at_the_side_held_not_the_side_printed():
    """The real case: 290 NO bought at ~54¢, exiting as `sell/outcome_side=yes`
    at yes=20¢. Kalshi's realized P&L was +$76.36 — i.e. the exit priced at the
    NO side (80¢). Pricing it at the printed `yes` side gives −$100.56."""
    fills = [fill("buy", "no", 290, 46, fid="b", ts=1),      # NO costs 54
             fill("sell", "yes", 290, 20, fid="s", ts=2)]    # NO worth 80
    (p,) = aggregate_positions(fills, {})
    assert p["side"] == "no"
    assert round(p["gross_pnl"]) == 290 * (80 - 54)          # +7540¢ = $75.40
    assert p["gross_pnl"] > 0                                 # NOT a loss


def test_an_open_position_realizes_nothing():
    """Cost is exposure, not a loss. Booking it as a loss is what produced the
    −$47k figure — Kalshi reports exactly 0.00 realized for these."""
    (p,) = aggregate_positions([fill("buy", "yes", 240, 45)], {})
    assert p["gross_pnl"] == 0.0
    assert p["open_count"] == 240 and p["settled"] is False


def test_a_partial_sale_realizes_only_the_part_sold():
    fills = [fill("buy", "yes", 100, 40, fid="b", ts=1),
             fill("sell", "yes", 30, 60, fid="s", ts=2)]
    (p,) = aggregate_positions(fills, {})
    assert p["gross_pnl"] == 30 * (60 - 40)      # only the 30 sold
    assert p["open_count"] == 70


# --- settlement ---------------------------------------------------------

def test_a_held_winner_settles_at_100():
    (p,) = aggregate_positions([fill("buy", "yes", 10, 40)],
                               {"KXATPMATCH-X-A": sett("yes")})
    assert p["gross_pnl"] == 10 * (100 - 40) and p["settled"] and p["open_count"] == 0


def test_a_held_loser_settles_at_zero():
    (p,) = aggregate_positions([fill("buy", "yes", 10, 40)],
                               {"KXATPMATCH-X-A": sett("no")})
    assert p["gross_pnl"] == -10 * 40


def test_a_no_side_winner_settles_on_the_no_result():
    # bought NO at 55; the market resolves NO -> our side won
    (p,) = aggregate_positions([fill("buy", "no", 10, 45)],
                               {"KXATPMATCH-X-A": sett("no")})
    assert p["side"] == "no" and p["gross_pnl"] == 10 * (100 - 55)


def test_average_cost_blends_across_buys():
    fills = [fill("buy", "yes", 100, 40, fid="a", ts=1),
             fill("buy", "yes", 100, 60, fid="b", ts=2),
             fill("sell", "yes", 200, 50, fid="c", ts=3)]
    (p,) = aggregate_positions(fills, {})
    assert p["avg_price"] == 50 and p["gross_pnl"] == 0


def test_selling_more_than_held_never_over_realizes():
    fills = [fill("buy", "yes", 10, 40, fid="a", ts=1),
             fill("sell", "yes", 50, 90, fid="b", ts=2)]
    (p,) = aggregate_positions(fills, {})
    assert p["gross_pnl"] == 10 * (90 - 40)     # only the 10 actually held
    assert p["open_count"] == 0


# --- fees and summary ---------------------------------------------------

def test_fees_reduce_pnl_and_are_reported_separately():
    (p,) = aggregate_positions([fill("buy", "yes", 10, 40, fee=174.0)],
                               {"KXATPMATCH-X-A": sett("yes")})
    assert p["fees"] == 174.0
    assert p["gross_pnl"] == 600.0 and p["pnl"] == 426.0


def test_settlement_fee_is_not_added_because_it_restates_the_fill_fees():
    """Kalshi's settlement `fee_cost` repeats the trading fees already charged on
    that market's fills — verified identical to the penny on a real market.
    Adding it doubled the account's fee total from ~$10.4k to ~$21.3k."""
    (p,) = aggregate_positions(
        [fill("buy", "yes", 10, 40, fee=100.0, fid="a", ts=1),
         fill("buy", "yes", 10, 40, fee=100.0, fid="b", ts=2)],
        {"KXATPMATCH-X-A": sett("yes", fee=200.0)})   # == the two fill fees
    assert p["fees"] == 200.0        # not 400


def test_summary_counts_open_positions_but_scores_only_settled():
    pos = aggregate_positions(
        [fill("buy", "yes", 10, 40, ticker="A", fid="a"),
         fill("buy", "yes", 10, 40, ticker="B", fid="b")],
        {"A": sett("yes")})
    s = summarize(pos)
    assert s["n"] == 2 and s["n_settled"] == 1 and s["n_open"] == 1
    assert s["wins"] == 1 and s["losses"] == 0    # the open one is not a loss
    assert s["n_approx"] == 0


# --- lifetime P&L by the accounting identity ----------------------------
# The fills-derived total CANNOT be the lifetime figure: /portfolio/fills only
# reaches ~2 months back, and 679 of the account's 1,926 settled markets have no
# fills in that window. That omission made the fills total ~$2k too negative.

def _bal(cash_c, pos_c):
    return {"balance": cash_c, "portfolio_value": pos_c}


def dep(cents, status="applied"):
    return {"amount_cents": cents, "status": status}


def test_identity_is_equity_minus_net_funding():
    a = account_pnl(_bal(676_900, 124_854), [dep(1_800_220)], [dep(700_000)])
    assert a["equity"] == 8017.54
    assert a["funded"] == 11002.20
    assert round(a["net_pnl"], 2) == -2984.66


def test_failed_deposits_never_count_as_funding():
    """3 failed deposits totalling $3,600 exist on the real account; counting
    them would overstate funding and invent a $3,600 loss."""
    a = account_pnl(_bal(100_000, 0), [dep(100_000), dep(360_000, "failed")], [])
    assert a["deposits"] == 1000.0 and a["net_pnl"] == 0.0


def test_withdrawals_reduce_net_funding_not_pnl():
    # take $500 out of a break-even account: still break-even
    a = account_pnl(_bal(50_000, 0), [dep(100_000)], [dep(50_000)])
    assert a["funded"] == 500.0 and a["net_pnl"] == 0.0


def test_an_unfunded_account_reports_its_equity_as_profit():
    a = account_pnl(_bal(10_000, 0), [], [])
    assert a["net_pnl"] == 100.0


def test_fills_window_is_reported_so_the_page_can_state_its_scope():
    f = [types.SimpleNamespace(ts=100), types.SimpleNamespace(ts=500),
         types.SimpleNamespace(ts=None)]
    assert fills_window(f) == (100, 500)
    assert fills_window([]) == (None, None)
