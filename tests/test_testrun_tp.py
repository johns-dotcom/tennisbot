from types import SimpleNamespace

from bot.web import TP_LIMIT, _tp_effective


def bet(side="yes", price=60, units=1, status="open", pnl=None):
    return SimpleNamespace(side=side, price_cents=price, units=units,
                           status=status, pnl_cents=pnl)


def test_winner_takes_profit_at_90():
    st, pnl = _tp_effective(bet(price=60), result="yes", touched90=False)
    assert st == "took_profit"
    assert pnl == (TP_LIMIT - 60)  # 30¢ per contract, 1 unit


def test_winner_scales_with_units():
    st, pnl = _tp_effective(bet(price=60, units=3), result="yes", touched90=False)
    assert st == "took_profit" and pnl == (TP_LIMIT - 60) * 3


def test_was_winning_then_lost_still_banks_via_touch():
    # match lost, but our side's bid touched 90 mid-play → limit filled
    st, pnl = _tp_effective(bet(side="yes", price=55), result="no", touched90=True)
    assert st == "took_profit" and pnl == (TP_LIMIT - 55)


def test_untouched_loss_is_full_loss():
    st, pnl = _tp_effective(bet(side="yes", price=55), result="no", touched90=False)
    assert st == "lost" and pnl == -55


def test_no_side_winner():
    # bought NO; NO wins when the market resolves 'no'
    st, pnl = _tp_effective(bet(side="no", price=40), result="no", touched90=False)
    assert st == "took_profit" and pnl == (TP_LIMIT - 40)


def test_void_is_zero():
    st, pnl = _tp_effective(bet(price=60), result="void", touched90=True)
    assert st == "void" and pnl == 0


def test_open_when_unsettled_and_untouched():
    st, pnl = _tp_effective(bet(price=60), result=None, touched90=False)
    assert st == "open" and pnl is None


def test_entry_at_or_above_limit_falls_back_to_hold():
    b = bet(price=91, status="won", pnl=9)
    st, pnl = _tp_effective(b, result="yes", touched90=True)
    assert st == "won" and pnl == 9  # TP below entry is nonsensical → hold outcome
