"""The testrun bots.

Two primary dimensions:
  - WHEN it bets:  pre-game (off the model's opening read) · live (in-play, when
    an advisory clears mid-match) · top-5 (the day's best scenarios only) ·
    live 35–65¢ (only toss-up-priced live entries)
  - HOW it tunes:  fixed (the hand-tuned v-series policy) vs self-improving
    (re-tunes its OWN thresholds from its OWN settled record)

  bot id   when      tuning          notes
  ------   --------  --------------  --------------------------
  pre      prematch  fixed
  live     advisory  fixed
  top5     top5      fixed           ≤5 best scenarios/day (kept as a cautionary
                                     marker — worst CLV of any model bot)
  mid      advisory  fixed           live, 35–65¢ band (only non-negative CLV)
  midSI    advisory  self-improving  live, 35–65¢ — the SOLE SI experiment
  dec      advisory  fixed           decider-only, 35–65¢ band
  chalk    chalk     fixed           CONTROL — back the market favorite
  freshadj freshadj  fixed           fatigue+form-adjusted probability
  tmain/tchal/titf, hiconf, follow/fade — single-variable experiment gates

Pruned (2026-07-25): preSI/liveSI/top5SI/t5midSI/decSI (SI never diverged
selection — identical records to their fixed twins) and t5mid/t5midSI
(double-filtered, too little volume to ever reach significance).

A self-improving bot nudges its probability floor toward the lowest band where
it actually clears the win target, tightens its edge cap if big-edge bets
underperform, and scales its stake with recent ROI — all bounded, and only once
it has enough settled bets (cold start inherits the fixed v-series policy). It
learns ONLY from its own basis, so the live bots learn from live results and the
pre-game bots from pre-game results. Advisory-only: imaginary bets, never orders.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from bot.log import get_logger
from bot.models import IngestState, KalshiMarket, PaperBet
from bot.paper import DEFAULT_POLICY, Policy
from bot.track import advisory_outcome

log = get_logger("bots")

# bot registry — id → (basis it bets on, whether it self-improves, label)
#   prematch : pre-game, off the model's opening read (watch loop)
#   advisory : live, in-play when an advisory clears (engine)
#   top5     : the day's 5 highest-salience scenarios only (daily selector)
BOTS: dict[str, dict] = {
    "pre":    {"basis": "prematch", "si": False, "label": "Pre-Game"},
    "live":   {"basis": "advisory", "si": False, "label": "Live"},
    # top5 kept as a CAUTIONARY data point: its salience selection had the worst
    # CLV of any model bot (the "best" scenarios are the market's most efficient)
    "top5":   {"basis": "top5",     "si": False, "label": "Top-5 Daily"},
    # 35–65¢ toss-up band — the only band with non-negative CLV so far. `midSI`
    # is the SOLE self-improving experiment (SI never diverged selection on the
    # others — identical records to their fixed twins — so they were pruned).
    "mid":    {"basis": "advisory", "si": False, "label": "Live · 35–65¢"},
    "midSI":  {"basis": "advisory", "si": True,  "label": "Live · 35–65¢ · Self-Improving"},
    # decider-only: same live basis, but only bets once the match reaches its
    # deciding-set trigger — isolates whether the edge lives at the signal
    "dec":     {"basis": "advisory", "si": False, "label": "Decider-Only",
                "decider_only": True},
    # CONTROL: back the market favorite, ignore the model — the baseline the
    # model bots must beat (own basis so no model path ever places for it)
    "chalk":   {"basis": "chalk", "si": False, "label": "Chalk · market favorite (control)"},
    # EXPERIMENT: bets the day's top plays using the freshness/form-adjusted
    # probability (opponent fatigue + year-form trend), to measure whether that
    # adjustment beats the base model. Own basis; base model paths untouched.
    "freshadj": {"basis": "freshadj", "si": False, "label": "Fresh/Form-Adjusted (experiment)"},
    # SINGLE-VARIABLE EXPERIMENTS — each is the live/toss-up bet PLUS exactly one
    # gate, so its record vs the ungated `mid` bot measures that indicator's
    # marginal value. Fixed (not self-improving) so the indicator's raw value
    # isn't confounded by tuning. Feed the eventual master bot by forward
    # selection on CLV. (basis 'advisory' → evaluated in the engine loop.)
    #   tier splits — WHERE is the market beatable? (laggy lower tiers vs sharp
    #   main tour — the adverse-selection lever)
    "tmain":  {"basis": "advisory", "si": False, "label": "Live · Main tour only",
               "tiers": ("A",)},
    "tchal":  {"basis": "advisory", "si": False, "label": "Live · Challenger only",
               "tiers": ("C",)},
    "titf":   {"basis": "advisory", "si": False, "label": "Live · ITF only",
               "tiers": ("15", "25")},
    #   gender — is men's or women's tennis more beatable?
    "men":    {"basis": "advisory", "si": False, "label": "Live · Men's (ATP) only",
               "tour": "atp"},
    "women":  {"basis": "advisory", "si": False, "label": "Live · Women's (WTA) only",
               "tour": "wta"},
    #   confidence — are thin-data bets the losers?
    "hiconf": {"basis": "advisory", "si": False, "label": "Live · High-confidence only",
               "min_conf": 0.80},
    #   line movement — is the market's move the signal? (follow the sharp money
    #   vs fade it; baseline `mid` bets regardless of the move)
    "follow": {"basis": "advisory", "si": False, "label": "Live · Follow the line move",
               "move": "follow"},
    "fade":   {"basis": "advisory", "si": False, "label": "Live · Fade the line move",
               "move": "fade"},
    #   dropped-set-1 favorite — buy the dip: back a pre-match MODEL favorite
    #   (≥60%, model-defined so no circularity) only after it loses the opening
    #   set, and only when the model still rates it the value side at the
    #   discounted price. Tests whether the market overreacts to a favorite
    #   dropping set 1 — the clean contrarian of the "never chase a dropped set"
    #   read. Bo3 only (dropping set 1 in Bo5 is far less meaningful).
    "dip":    {"basis": "advisory", "si": False,
               "label": "Live · Favorite after dropping set 1",
               "dropped_set1": True},
}
SI_BOTS = [b for b, m in BOTS.items() if m["si"]]
# advisory bots gated to one variable (single-indicator experiments)
GATED_ADVISORY = ("tmain", "tchal", "titf", "men", "women", "hiconf", "follow", "fade")
MID_BOTS = ("mid", "midSI")  # 35–65¢ price-band bots
# Decider-only bots ALSO use the toss-up band: the deciding set is a near
# coin-flip, so the model rates a side ≥82% there only ~0.1% of the time — the
# old favorite floor made them structurally unable to bet (0 bets ever). The
# tradeable decider entry lives at 35–65¢, so put them on the same band.
DEC_BOTS = ("dec",)
# dropped-set-1 favorite bot — also a toss-up-band entry (a repriced favorite
# sits ~35–65¢), so it shares the same band as the other in-play experiments
DIP_BOTS = ("dip",)
# the single-variable experiment bots also fish the 35–65¢ band, so each is a
# clean one-gate delta off `mid`
TOSSUP_BOTS = MID_BOTS + DEC_BOTS + GATED_ADVISORY + DIP_BOTS
TOP5_N = 5  # max bets per day for the Top-5 bots
MOVE_THRESH = 3  # cents a line must drift to count as a "move" for follow/fade
DIP_FAV_MIN = 0.60  # pre-match MODEL prob to count as a "favorite" for the dip bot


def advisory_gate_ok(meta: dict, *, at_decider: bool, confidence: float | None,
                     tier: str | None, move: int | None,
                     tour: str | None = None,
                     sets: tuple[int, int] | None = None,
                     best_of: int | None = None,
                     fav_side: str | None = None,
                     fav_prob: float | None = None,
                     recommended_side: str | None = None) -> bool:
    """A single-variable experiment bot's extra gate on a live advisory. Each
    bot declares at most one of: decider_only, tiers, tour, min_conf, move,
    dropped_set1 — so its record isolates that indicator. Bots with no gate
    always pass."""
    if meta.get("decider_only") and not at_decider:
        return False
    tiers = meta.get("tiers")
    if tiers and tier not in tiers:
        return False
    want_tour = meta.get("tour")
    if want_tour and tour != want_tour:
        return False
    mc = meta.get("min_conf")
    if mc is not None and (confidence or 0.0) < mc:
        return False
    mv = meta.get("move")
    if mv == "follow" and not (move is not None and move >= MOVE_THRESH):
        return False
    if mv == "fade" and not (move is not None and move <= -MOVE_THRESH):
        return False
    if meta.get("dropped_set1"):
        # Bo3 only; a clear pre-match favorite; that favorite is the side the
        # advisory now recommends (we back its value dip, never the opponent);
        # and it is down exactly one set to love.
        if best_of != 3:
            return False
        if fav_side is None or fav_prob is None or fav_prob < DIP_FAV_MIN:
            return False
        if recommended_side != fav_side:
            return False
        if sets is None:
            return False
        want = (0, 1) if fav_side == "yes" else (1, 0)  # favorite lost set 1
        if tuple(sets) != want:
            return False
    return True


# The toss-up bots take only 35–65¢ entries and drop the strong-favorite
# probability floor so the PRICE band is the operative gate (edge discipline of
# 3–15% is kept). Every other bot uses DEFAULT_POLICY.
_MID_BASE = replace(DEFAULT_POLICY, min_price=35, max_price=65,
                    min_prob=0.35, challenger_min_prob=0.35)
BASE_POLICIES: dict[str, Policy] = {b: _MID_BASE for b in TOSSUP_BOTS}


def base_policy(bot_id: str) -> Policy:
    """The fixed starting policy for a bot (DEFAULT unless it has a custom band)."""
    return BASE_POLICIES.get(bot_id, DEFAULT_POLICY)


MIN_BASIS = 30          # settled bets required before a bot adapts at all —
                        # raised from 15: learning off ~15 bets chases noise, not
                        # signal (a hot start pushed stakes to max on thin samples)
TARGET_WIN = 0.70
MIN_BUCKET = 8          # bets needed in a prob band to trust its win rate
FLOOR_MIN, FLOOR_MAX = 0.80, 0.92
MID_FLOOR_MIN, MID_FLOOR_MAX = 0.35, 0.55  # toss-up bots learn within this band
EDGE_MAX_TIGHT, EDGE_MAX_LOOSE = 0.10, 0.15
SIZE_MULT_MIN, SIZE_MULT_MAX = 0.7, 1.3


def _floor_bounds(bot_id: str) -> tuple[float, float]:
    """The probability-floor search range for an SI bot — the toss-up bots
    (mid + decider-only) tune within the toss-up band, everyone else within the
    favorite band."""
    return (MID_FLOOR_MIN, MID_FLOOR_MAX) if bot_id in TOSSUP_BOTS \
        else (FLOOR_MIN, FLOOR_MAX)


def _state_key(bot_id: str) -> str:
    return f"si:{bot_id}:policy"


def _default_state(bot_id: str) -> dict:
    base = base_policy(bot_id)
    return {"min_prob": base.min_prob, "max_edge": base.max_edge,
            "size_mult": 1.0, "version": f"{bot_id}.0", "n_basis": 0,
            "rationale": "cold start — inheriting the fixed policy until enough "
                         "settled bets to learn from", "history": []}


def _load_state(db: Session, bot_id: str) -> dict:
    row = db.execute(select(IngestState.value).where(
        IngestState.key == _state_key(bot_id))).scalar()
    if not row:
        return _default_state(bot_id)
    try:
        s = json.loads(row)
        for k, v in _default_state(bot_id).items():
            s.setdefault(k, v)
        return s
    except Exception:
        return _default_state(bot_id)


def _save_state(db: Session, bot_id: str, state: dict) -> None:
    now = datetime.now(timezone.utc)
    payload = json.dumps(state)
    db.execute(pg_insert(IngestState).values(
        key=_state_key(bot_id), value=payload, updated_at=now
    ).on_conflict_do_update(index_elements=["key"],
                            set_={"value": payload, "updated_at": now}))
    db.commit()


def bot_policy(db: Session, bot_id: str) -> Policy:
    """A bot's current Policy — its base policy for fixed bots, learned (on top
    of that base, so the price band survives) for SI bots."""
    base = base_policy(bot_id)
    if not BOTS.get(bot_id, {}).get("si"):
        return base
    s = _load_state(db, bot_id)
    return replace(base, min_prob=s["min_prob"], max_edge=s["max_edge"],
                   size_mult=s["size_mult"], version=s["version"])


def bot_state(db: Session, bot_id: str) -> dict | None:
    """Learned state + history for an SI bot's self-improvement panel (else None)."""
    return _load_state(db, bot_id) if BOTS.get(bot_id, {}).get("si") else None


def iter_bot_policies(db: Session, basis: str) -> list[tuple[str, Policy]]:
    """(bot_id, policy) for the two bots that bet on this basis (prematch/advisory)."""
    return [(bid, bot_policy(db, bid)) for bid, m in BOTS.items()
            if m["basis"] == basis]


def _settled(db: Session, bot_id: str) -> list[dict]:
    rows = db.execute(
        select(PaperBet, KalshiMarket.result)
        .join(KalshiMarket, KalshiMarket.ticker == PaperBet.market_ticker)
        .where(PaperBet.bot == bot_id)).all()
    out = []
    for b, res in rows:
        o = advisory_outcome(b.side, res)
        if o in ("won", "lost"):
            out.append({"won": o == "won", "prob": b.model_prob, "edge": b.edge,
                        "price": b.price_cents, "units": b.units or 1.0,
                        "pnl": b.pnl_cents or 0, "at": b.created_at})
    return out


def _wr(pool: list[dict]):
    return sum(x["won"] for x in pool) / len(pool) if pool else None


def self_improve(db: Session, bot_id: str) -> dict:
    """Recompute an SI bot's policy from its own settled record; persist if changed."""
    if not BOTS.get(bot_id, {}).get("si"):
        return {"changed": False, "reason": "not a self-improving bot"}
    s = _load_state(db, bot_id)
    S = _settled(db, bot_id)
    n = len(S)
    if n < MIN_BASIS:
        return {"changed": False, "n": n,
                "reason": f"only {n}/{MIN_BASIS} settled — holding defaults"}

    # 1) probability floor: the lowest band where the bot clears the target
    #    (searched within the bot's own strategy band — favorite vs toss-up)
    fmin, fmax = _floor_bounds(bot_id)
    floor = fmax
    f = fmin
    while f <= fmax + 1e-9:
        pool = [x for x in S if x["prob"] >= f]
        wr = _wr(pool)
        if len(pool) >= MIN_BUCKET and wr is not None and wr >= TARGET_WIN:
            floor = round(f, 2)
            break
        f += 0.02

    # 2) edge cap: tighten if the big-edge slice underperforms overall
    overall = _wr(S) or 0.0
    big = [x for x in S if x["edge"] > 0.10]
    bw = _wr(big)
    max_edge = (EDGE_MAX_TIGHT if len(big) >= 5 and bw is not None
                and bw < overall - 0.10 else EDGE_MAX_LOOSE)

    # 3) stake multiplier: scale with recent ROI (last 20 settled)
    recent = sorted(S, key=lambda x: x["at"])[-20:]
    stake = sum(x["price"] * x["units"] for x in recent)
    roi = (sum(x["pnl"] for x in recent) / stake) if stake else 0.0
    size_mult = s["size_mult"]
    if roi > 0.05:
        size_mult = min(SIZE_MULT_MAX, round(size_mult + 0.1, 2))
    elif roi < -0.05:
        size_mult = max(SIZE_MULT_MIN, round(size_mult - 0.1, 2))

    if (floor == s["min_prob"] and max_edge == s["max_edge"]
            and size_mult == s["size_mult"]):
        return {"changed": False, "n": n, "reason": "no adjustment warranted"}

    try:
        ver_n = int(s["version"].split(".")[-1]) + 1
    except ValueError:
        ver_n = 1
    rationale = (f"n={n}, overall {overall:.0%}: floor→{floor:.0%} (lowest band "
                 f"clearing {TARGET_WIN:.0%}), edge cap→{max_edge:.0%}, "
                 f"stake ×{size_mult:.1f} (recent ROI {roi:+.0%})")
    hist = (s.get("history", []) + [{
        "version": s["version"], "min_prob": s["min_prob"],
        "max_edge": s["max_edge"], "size_mult": s["size_mult"],
        "rationale": s.get("rationale", "")}])[-10:]
    new = {"min_prob": floor, "max_edge": max_edge, "size_mult": size_mult,
           "version": f"{bot_id}.{ver_n}", "n_basis": n, "rationale": rationale,
           "history": hist}
    _save_state(db, bot_id, new)
    log.info("bot self-improved", bot=bot_id, version=new["version"],
             min_prob=floor, max_edge=max_edge, size_mult=size_mult, n=n)
    return {"changed": True, "n": n, **new}


def self_improve_all(db: Session) -> dict:
    """Run self-improvement for every SI bot; return {bot_id: result}."""
    return {bid: self_improve(db, bid) for bid in SI_BOTS}


def place_top5_bets(db: Session) -> int:
    """The Top-5 bots: each day, walk the day's scenarios by salience (best
    first) and back the watch side of each that clears the bot's policy, up to
    TOP5_N bets. A concentrated 'best plays of the day' strategy — same gate as
    the other bots, but a candidate universe of only the strongest scenarios.
    Idempotent: one bet per event per bot, capped at TOP5_N/day."""
    from datetime import time
    from sqlalchemy import func, text

    from bot.models import Scenario
    from bot.paper import BetDecision, place_bet, policy_ok, size_units
    from bot.scenarios import SERIES_TIER

    day = db.execute(select(func.max(Scenario.created_for))).scalar()
    if not day:
        return 0
    scen = db.execute(select(Scenario).where(Scenario.created_for == day)
                      .order_by(Scenario.salience.desc())).scalars().all()
    start_today = datetime.combine(datetime.now(timezone.utc).date(),
                                   time.min, tzinfo=timezone.utc)
    placed = 0
    for bot_id in [b for b, m in BOTS.items() if m["basis"] == "top5"]:
        policy = bot_policy(db, bot_id)
        done = set(db.execute(select(PaperBet.event_ticker).where(
            PaperBet.bot == bot_id)).scalars())
        today_n = db.execute(select(func.count(PaperBet.id)).where(
            PaperBet.bot == bot_id, PaperBet.created_at >= start_today)).scalar() or 0
        for sc in scen:
            if today_n >= TOP5_N:
                break
            if sc.event_ticker in done:
                continue
            row = db.execute(text(
                "SELECT yes_ask FROM market_ticks WHERE market_ticker = :t "
                "AND kind='quote' AND yes_ask IS NOT NULL "
                "AND ts > now() - interval '45 minutes' ORDER BY ts DESC LIMIT 1"),
                {"t": sc.market_ticker}).first()
            if not row or row[0] is None:
                continue
            price = int(row[0])              # yes_ask = cost to back the watch side
            prob = sc.prematch_prob          # P(watch side wins), pre-play
            edge = prob - price / 100
            tier = next((t for s, t in SERIES_TIER.items()
                         if sc.market_ticker.startswith(s)), "15")
            conf = (sc.facts or {}).get("model_confidence", 0.7)
            if not policy_ok(prob, edge, price, tier, policy, confidence=conf):
                continue
            dec = BetDecision(True, side="yes", prob=round(prob, 3),
                              edge=round(edge, 3), price_cents=price,
                              units=size_units(prob, edge, conf, policy.size_mult),
                              reason=f"top-5 daily play (salience {sc.salience:.2f}) "
                                     f"— cleared {policy.version}")
            if place_bet(db, event_ticker=sc.event_ticker,
                         market_ticker=sc.market_ticker, player_id=sc.player_id,
                         decision=dec, confidence=conf, basis="prematch",
                         tier=tier, bot=bot_id, policy_version=policy.version,
                         reasoning={"match": (sc.facts or {}).get("match"),
                                    "prematch_prob": round(prob, 3),
                                    "salience": sc.salience, "top5": True}):
                done.add(sc.event_ticker)
                today_n += 1
                placed += 1
    return placed


def place_chalk_bets(db: Session) -> int:
    """CONTROL bot: on each of the day's scenarios, back the MARKET FAVORITE at
    its executable price, ignoring the model entirely. One flat unit per event,
    once ever. The baseline the model bots must beat — if they don't clear
    'chalk' on ROI/CLV, the model isn't adding anything. Never an order."""
    from datetime import time
    from sqlalchemy import func, text

    from bot.models import Scenario
    from bot.paper import BetDecision, place_bet
    from bot.scenarios import SERIES_TIER

    day = db.execute(select(func.max(Scenario.created_for))).scalar()
    if not day:
        return 0
    scen = db.execute(select(Scenario).where(Scenario.created_for == day)).scalars().all()
    done = set(db.execute(select(PaperBet.event_ticker).where(
        PaperBet.bot == "chalk")).scalars())
    placed = 0
    for sc in scen:
        if sc.event_ticker in done:
            continue
        row = db.execute(text(
            "SELECT yes_ask, yes_bid FROM market_ticks WHERE market_ticker = :t "
            "AND kind='quote' AND yes_ask IS NOT NULL AND yes_bid IS NOT NULL "
            "AND ts > now() - interval '45 minutes' ORDER BY ts DESC LIMIT 1"),
            {"t": sc.market_ticker}).first()
        if not row:
            continue
        ya, yb = int(row[0]), int(row[1])
        # favorite = the side the market prices as more likely. YES mid ≥ 50 ⇒
        # the watch side is the favorite (back YES @ its ask); else back the
        # opponent (NO @ 100 − yes_bid).
        if (ya + yb) / 2 >= 50:
            side, price, player_id = "yes", ya, sc.player_id
        else:
            side, price, player_id = "no", 100 - yb, sc.opponent_id
        if not (1 <= price <= 99):
            continue
        tier = next((t for s, t in SERIES_TIER.items()
                     if sc.market_ticker.startswith(s)), "15")
        dec = BetDecision(True, side=side, prob=round(price / 100, 3), edge=0.0,
                          price_cents=price, units=1.0,
                          reason="chalk control — back the market favorite (no model)")
        if place_bet(db, event_ticker=sc.event_ticker,
                     market_ticker=sc.market_ticker, player_id=player_id,
                     decision=dec, confidence=1.0, basis="chalk", tier=tier,
                     bot="chalk", policy_version="chalk",
                     reasoning={"match": (sc.facts or {}).get("match"), "control": True}):
            placed += 1
    return placed


def place_freshadj_bets(db: Session) -> int:
    """EXPERIMENT bot: like the top-5 daily play, but gates/sizes on the
    freshness/form-ADJUSTED probability (facts.fresh_adj.prematch) instead of the
    raw model prob. Measured against pre/top5 to see if the adjustment helps.
    Advisory only."""
    from datetime import time
    from sqlalchemy import func, text

    from bot.models import Scenario
    from bot.paper import BetDecision, place_bet, policy_ok, size_units
    from bot.scenarios import SERIES_TIER

    day = db.execute(select(func.max(Scenario.created_for))).scalar()
    if not day:
        return 0
    scen = db.execute(select(Scenario).where(Scenario.created_for == day)
                      .order_by(Scenario.salience.desc())).scalars().all()
    start_today = datetime.combine(datetime.now(timezone.utc).date(),
                                   time.min, tzinfo=timezone.utc)
    policy = bot_policy(db, "freshadj")
    done = set(db.execute(select(PaperBet.event_ticker).where(
        PaperBet.bot == "freshadj")).scalars())
    today_n = db.execute(select(func.count(PaperBet.id)).where(
        PaperBet.bot == "freshadj", PaperBet.created_at >= start_today)).scalar() or 0
    placed = 0
    for sc in scen:
        if today_n >= TOP5_N:
            break
        if sc.event_ticker in done:
            continue
        prob = ((sc.facts or {}).get("fresh_adj") or {}).get("prematch")
        if prob is None:
            continue
        row = db.execute(text(
            "SELECT yes_ask FROM market_ticks WHERE market_ticker = :t "
            "AND kind='quote' AND yes_ask IS NOT NULL "
            "AND ts > now() - interval '45 minutes' ORDER BY ts DESC LIMIT 1"),
            {"t": sc.market_ticker}).first()
        if not row or row[0] is None:
            continue
        price = int(row[0])
        edge = prob - price / 100
        tier = next((t for s, t in SERIES_TIER.items()
                     if sc.market_ticker.startswith(s)), "15")
        conf = (sc.facts or {}).get("model_confidence", 0.7)
        if not policy_ok(prob, edge, price, tier, policy, confidence=conf):
            continue
        dec = BetDecision(True, side="yes", prob=round(prob, 3),
                          edge=round(edge, 3), price_cents=price,
                          units=size_units(prob, edge, conf, policy.size_mult),
                          reason=f"fresh/form-adjusted play — cleared {policy.version}")
        if place_bet(db, event_ticker=sc.event_ticker,
                     market_ticker=sc.market_ticker, player_id=sc.player_id,
                     decision=dec, confidence=conf, basis="freshadj", tier=tier,
                     bot="freshadj", policy_version=policy.version,
                     reasoning={"match": (sc.facts or {}).get("match"),
                                "fresh_adj_prob": round(prob, 3),
                                "salience": sc.salience}):
            done.add(sc.event_ticker)
            today_n += 1
            placed += 1
    return placed
