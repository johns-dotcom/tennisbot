"""The owner's real Kalshi account history — sync and position aggregation.

READ-ONLY. Uses only the portfolio GETs permitted by CLAUDE.md rule 1's narrow
exception (fills, settlements, positions, balance, deposits, withdrawals). No
order endpoint is reachable from here and none may ever be added.

Two P&L figures, deliberately, because only one of them can be trusted for the
lifetime total:
  - account_pnl()  — lifetime, from (equity - net funding). Exact.
  - summarize()    — from fills, and therefore scoped to the fills window only.
                     The page MUST state that window; /portfolio/fills reaches
                     back ~2 months while the account is older, so 679 of 1,926
                     settled markets have no fills and are absent from it.

Why aggregate fills ourselves rather than trust /portfolio/positions: that
endpoint returns only currently-tracked positions — 15 rows against 1,256
distinct tickers of real history (checked against the live account 2026-08-13).
It is a useful cross-check on those 15, never a source of truth for the history.

Two scale traps in the payloads, both normalized here:
  - `revenue` is an INT IN CENTS, while `fee_cost` is a DOLLAR STRING.
  - `count_fp` is FRACTIONAL ("5.57"), so contracts are floats, not ints.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bot.log import get_logger
from bot.market.kalshi import KalshiClient, dollars_to_cents
from bot.models import KalshiFill, KalshiSettlement

log = get_logger("market.portfolio")


def _f(v) -> float | None:
    """Kalshi sends numbers as strings ('5.57', '0.017400'). None-safe."""
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fee_cents(v) -> float | None:
    """fee_cost is a dollar string and is routinely SUB-CENT ('0.017400' =
    1.74¢). Kept as float cents — rounding each of ~10k fills would visibly
    skew the total."""
    d = _f(v)
    return None if d is None else d * 100.0


def _ts(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _event_of(ticker: str) -> str | None:
    """A market ticker is '<EVENT>-<SIDE>'; the event is everything before the
    last dash. Falls back to None rather than guessing on a malformed ticker."""
    if not ticker or "-" not in ticker:
        return None
    return ticker.rsplit("-", 1)[0]


def sync_portfolio(db: Session, client: KalshiClient, full: bool = False) -> dict:
    """Pull new fills and settlements into the local mirror.

    Incremental by default: only fills newer than the newest stored `ts` are
    fetched, so the ~10.5k-fill backfill happens once. Same three-phase shape as
    refresh_derivatives — short DB read, all HTTP with NO transaction open
    (serverless Postgres drops connections that idle through a crawl), short DB
    write."""
    stats = {"fills_seen": 0, "fills_new": 0, "setts_seen": 0, "setts_new": 0}

    # ---- pass 1 (DB, short) ----
    since = None if full else db.execute(select(func.max(KalshiFill.ts))).scalar()
    known_fills = {r for r in db.execute(select(KalshiFill.fill_id)).scalars()}
    known_setts = {r for r in db.execute(select(KalshiSettlement.ticker)).scalars()}
    db.commit()   # release the transaction for the duration of the crawl

    # ---- pass 2 (HTTP only) ----
    # `since` is inclusive on Kalshi's side, so the newest already-stored fills
    # come back again; fill_id dedupe below makes that harmless.
    try:
        fills = client.fills(min_ts=since)
    except Exception as e:
        log.error("fills fetch failed", error=str(e))
        fills = []
    try:
        setts = client.settlements(min_ts=since)
    except Exception as e:
        log.error("settlements fetch failed", error=str(e))
        setts = []
    stats["fills_seen"], stats["setts_seen"] = len(fills), len(setts)

    # ---- pass 3 (DB, short) ----
    for f in fills:
        fid = f.get("fill_id")
        if not fid or fid in known_fills:
            continue
        known_fills.add(fid)
        tk = f.get("ticker") or f.get("market_ticker") or ""
        db.add(KalshiFill(
            fill_id=fid, trade_id=f.get("trade_id"), order_id=f.get("order_id"),
            ticker=tk, event_ticker=_event_of(tk),
            action=(f.get("action") or "").lower() or "buy",
            outcome_side=(f.get("outcome_side") or f.get("side") or "yes").lower(),
            book_side=f.get("book_side"), count=_f(f.get("count_fp")) or 0.0,
            yes_price_cents=dollars_to_cents(f.get("yes_price_dollars")),
            no_price_cents=dollars_to_cents(f.get("no_price_dollars")),
            fee_cents=_fee_cents(f.get("fee_cost")), is_taker=f.get("is_taker"),
            ts=f.get("ts"), created_time=_ts(f.get("created_time")), raw=f))
        stats["fills_new"] += 1

    for s in setts:
        tk = s.get("ticker")
        if not tk or tk in known_setts:
            continue
        known_setts.add(tk)
        db.add(KalshiSettlement(
            ticker=tk, event_ticker=s.get("event_ticker"),
            market_result=(s.get("market_result") or "").lower() or None,
            yes_count=_f(s.get("yes_count_fp")), no_count=_f(s.get("no_count_fp")),
            # revenue is already an int in CENTS — do not scale it like the
            # dollar-string fields around it
            revenue_cents=float(s.get("revenue") or 0),
            yes_cost_cents=(_f(s.get("yes_total_cost_dollars")) or 0) * 100.0,
            no_cost_cents=(_f(s.get("no_total_cost_dollars")) or 0) * 100.0,
            fee_cents=_fee_cents(s.get("fee_cost")),
            settled_time=_ts(s.get("settled_time")), raw=s))
        stats["setts_new"] += 1

    db.commit()
    log.info("portfolio sync", **stats)
    return stats


def hold_side(fills: list) -> tuple[str, bool]:
    """Which side of the market this position is actually on, and whether that
    is unambiguous.

    This matters more than it looks. A fill's `outcome_side` is the side of the
    ORDER BOOK the trade printed on, NOT the side you hold. Verified against the
    live account: a position built by buying 290 NO at ~54¢ exits as
    `sell / outcome_side=yes / yes=20¢`, and Kalshi's own realized P&L for it
    ($76.36) equals 290 × 80.5¢ − $157.10 — i.e. the exit priced at the NO side.
    Pricing that exit at the stated `yes` side instead gives −$100.56.

    Buys are unambiguous, so the side carrying the most bought volume is the
    side held. Returns exact=False when both sides were bought, because then
    this heuristic cannot decide and the resulting P&L must be shown as
    approximate rather than stated as fact."""
    vol: dict[str, float] = {}
    for f in fills:
        if (f.action or "buy") == "buy":
            s = f.outcome_side or "yes"
            vol[s] = vol.get(s, 0.0) + (f.count or 0.0)
    if not vol:
        return "yes", False
    side = max(vol, key=vol.get)
    return side, len(vol) == 1


def _price_at(f, side: str) -> int:
    return (f.no_price_cents or 0) if side == "no" else (f.yes_price_cents or 0)


def aggregate_positions(fills: list, settlements: dict) -> list[dict]:
    """Collapse fills into one row per market ticker, with average-cost P&L.

    Realized P&L accrues ONLY when a position is reduced or settles. An open
    position contributes zero — its cost is exposure, not a loss. Booking cost
    as loss was the original error here and it produced a −$47k realized figure
    on an account holding $6.6k.

    Reconciled against Kalshi's own `realized_pnl_dollars`: 10 of the 15
    positions it reports match to the cent. The remainder are mixed-side
    positions, which carry `pnl_exact=False` — see hold_side()."""
    by_ticker: dict[str, list] = {}
    for f in fills:
        by_ticker.setdefault(f.ticker, []).append(f)

    out = []
    for tk, fs in by_ticker.items():
        side, exact = hold_side(fs)
        st = settlements.get(tk)
        pos = avg = realized = cost = fees = 0.0
        bought = sold = 0.0
        first_ts = last_ts = None
        for f in sorted(fs, key=lambda x: (x.ts or 0, x.fill_id or "")):
            n, px = (f.count or 0.0), _price_at(f, side)
            fees += f.fee_cents or 0.0
            if (f.action or "buy") == "buy":
                avg = ((pos * avg) + n * px) / (pos + n) if (pos + n) else 0.0
                pos += n
                bought += n
                cost += n * px
            else:
                q = min(n, pos)          # never realize more than is held
                realized += q * (px - avg)
                pos -= q
                sold += n
            first_ts = f.ts if first_ts is None else min(first_ts, f.ts or first_ts)
            last_ts = f.ts if last_ts is None else max(last_ts, f.ts or last_ts)
        if st:
            # whatever is left settles at 100¢ if our side won, else 0
            won = (st.market_result or "") == side
            realized += pos * ((100 if won else 0) - avg)
            pos = 0.0
            # NB: st.fee_cents is deliberately NOT added. Kalshi's settlement
            # `fee_cost` RESTATES the trading fees already charged on that
            # market's fills — verified identical to the penny on
            # KXITFWMATCH-26AUG13LEMKHO-LEM ($3.3378 vs $3.3378 across 14
            # fills). Adding it double-counted fees on every settled market and
            # inflated the account total from ~$10.4k to ~$21.3k. Summed fill
            # fees alone match Kalshi's own fees_paid_dollars on 15/17 tickers.
        out.append({
            "ticker": tk, "event_ticker": (fs[0].event_ticker if fs else None),
            "side": side, "pnl_exact": exact,
            "pnl": realized - fees, "gross_pnl": realized, "fees": fees,
            "cost": cost, "bought": bought, "sold": sold,
            "open_count": round(pos, 4), "avg_price": avg or None,
            "settled": st is not None,
            "result": st.market_result if st else None,
            "n_fills": len(fs), "first_ts": first_ts, "last_ts": last_ts,
        })
    out.sort(key=lambda x: x["last_ts"] or 0, reverse=True)
    return out


def account_pnl(balance: dict, deposits: list, withdrawals: list) -> dict:
    """Lifetime net P&L by the accounting identity:

        net P&L = (cash balance + portfolio value) - (deposits - withdrawals)

    This is the ONLY trustworthy lifetime figure. Deriving it from fills is
    impossible: /portfolio/fills reaches back only ~2 months, and 679 of the
    account's 1,926 settled markets have no fills in that window — which is
    exactly why the fills-derived total came out ~$2k too negative.

    Only `applied` funding rows count; `failed` deposits never moved money."""
    cash = float(balance.get("balance") or 0) / 100.0
    positions_value = float(balance.get("portfolio_value") or 0) / 100.0
    dep = sum(float(r.get("amount_cents") or 0) for r in deposits
              if r.get("status") == "applied") / 100.0
    wit = sum(float(r.get("amount_cents") or 0) for r in withdrawals
              if r.get("status") == "applied") / 100.0
    equity = cash + positions_value
    funded = dep - wit
    return {
        "cash": cash, "positions_value": positions_value, "equity": equity,
        "deposits": dep, "withdrawals": wit, "funded": funded,
        "net_pnl": equity - funded,
        # equity moves with live prices, so this is a point-in-time read
        "as_of": datetime.now(timezone.utc),
    }


def fills_window(fills: list) -> tuple[int | None, int | None]:
    """(earliest, latest) fill timestamp. The page must state this — anything
    computed from fills covers only this window, not the account's lifetime."""
    ts = [f.ts for f in fills if f.ts]
    return (min(ts), max(ts)) if ts else (None, None)


def summarize(positions: list[dict]) -> dict:
    """Headline numbers for the page. Win rate counts settled positions only —
    an open position has no outcome yet and must not be scored as a loss."""
    settled = [p for p in positions if p["settled"]]
    wins = sum(1 for p in settled if p["pnl"] > 0)
    losses = sum(1 for p in settled if p["pnl"] < 0)
    return {
        "n": len(positions),
        "n_settled": len(settled),
        "n_open": sum(1 for p in positions if not p["settled"]),
        # every position contributes realized P&L, not just settled ones — a
        # position sold out before settlement realized its result already
        "pnl": sum(p["pnl"] for p in positions),
        "fees": sum(p["fees"] for p in positions),
        "staked": sum(p["cost"] for p in positions),
        "wins": wins, "losses": losses,
        "win_rate": (wins / (wins + losses)) if (wins + losses) else None,
        # positions whose held side is ambiguous — their P&L is approximate and
        # must be labelled as such rather than presented as fact
        "n_approx": sum(1 for p in positions if not p["pnl_exact"]),
    }
