"""The live board flags matches you already have a position on.

Distinct from the board's existing `data-bet`, which is the MODEL's bet verdict —
this is the user's own logged bet."""
from bot.models import UserBet
from bot.web import _mybet_badge


def bet(name="Iga Swiatek", px=62, shares=20, exit_px=None):
    return UserBet(user_id=1, event_ticker="EV", market_ticker="MT", side="yes",
                   player_name=name, opponent_name="Opp", entry_price_cents=px,
                   shares=shares, exit_price_cents=exit_px)


def test_no_bet_renders_nothing():
    assert _mybet_badge([]) == ""


def test_a_held_position_reads_as_active():
    out = _mybet_badge([bet()])
    assert "tag-good" in out          # green = you are still in
    assert "Swiatek 62¢" in out       # surname + entry, compact enough for a card
    assert "Iga Swiatek 62¢ × 20" in out   # full detail in the tooltip


def test_a_fully_closed_position_is_still_shown_but_quiet():
    # a match you have already traded must never look identical to an untouched one
    out = _mybet_badge([bet(exit_px=88)])
    assert "tag-outline" in out and "tag-good" not in out
    assert "closed" in out
    assert "out 88¢" in out


def test_a_held_bet_outranks_a_closed_one_on_the_same_match():
    out = _mybet_badge([bet("Carlos Alcaraz", 40, 30, exit_px=88), bet()])
    assert "tag-good" in out and "Swiatek" in out
    assert "closed" not in out


def test_several_held_bets_collapse_to_a_count():
    out = _mybet_badge([bet(), bet(px=55, shares=10)])
    assert "+1" in out
    assert "62¢ × 20" in out and "55¢ × 10" in out   # both in the tooltip


def test_a_single_word_name_does_not_break_the_surname_split():
    assert "Sinner 71¢" in _mybet_badge([bet("Sinner", 71, 5)])


def test_the_badge_is_attribute_safe():
    # the detail string is interpolated into title="…" — an unescaped quote
    # would break out of the attribute
    out = _mybet_badge([bet('Ka"te <script>')])
    assert "<script>" not in out
    title = out.split('title="')[1].split('">')[0]
    assert '"' not in title
