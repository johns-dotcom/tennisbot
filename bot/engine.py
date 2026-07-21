"""Edge detection + advisory generation engine.

On material updates: state → model prob conditioned on state → edge vs the
EXECUTABLE price (the ask on the side you would buy, never midpoint).

An advisory fires only if ALL gates pass:
  edge ≥ threshold, model confidence ≥ min, volume ≥ floor,
  state confidence ≥ min OR state confirmed by the delayed score.
Below the state threshold the advisory is held pending: released when the score
confirms the state, killed when it contradicts it (or on retirement/suspension/
feed trouble). Debounce: one advisory per market per meaningful state change
(new set, or the edge crossing into a new band) — never per tick.

Probation (CLAUDE.md rule 5): while cfg.probation is true, unconfirmed-state
advisories still generate/validate/push but carry the [PROBATION] prefix and
probation=true in the audit row. Score-confirmed advisories are never prefixed.
The engine NEVER flips probation itself.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from bot.advisory.deliver import deliver_advisory
from bot.advisory.facts import build_fact_block, build_facts
from bot.advisory.render import render_prose
from bot.config import settings
from bot.log import get_logger
from bot.models import Advisory, KalshiMarket, StateInferenceLog
from bot.prob.elo import SetElo
from bot.prob.model import MatchState

log = get_logger("engine")

EDGE_BANDS = (0.06, 0.10, 0.15)  # band edges; crossing into a new band re-arms debounce


def edge_band(edge: float) -> int:
    band = -1
    for i, lo in enumerate(EDGE_BANDS):
        if edge >= lo:
            band = i
    return band


class AdvisoryEngine:
    def __init__(self, db_session_factory, model: SetElo):
        self.cfg = settings()
        self.db_session = db_session_factory
        self.model = model
        self.ctx: dict[str, dict] = {}  # ticker -> market context
        self.last_quote: dict[str, tuple[int | None, int | None, int | None]] = {}
        self.session_volume: dict[str, int] = {}  # ticker -> contracts traded (live liquidity)
        self.last_advised: dict[str, tuple[str, int]] = {}  # ticker -> (state, band)
        self.pending: dict[str, dict] = {}  # ticker -> {advisory_id, state, block}
        self._profiles: dict[int, tuple] = {}  # player_id -> (profile, history)
        self._hitrate_warned_at: datetime | None = None

    # ---------- context ----------

    def _context(self, ticker: str) -> dict | None:
        ctx = self.ctx.get(ticker)
        if ctx is not None:
            return ctx or None
        with self.db_session() as db:
            row = db.execute(select(KalshiMarket).where(
                KalshiMarket.ticker == ticker)).scalar()
            if row is None or row.player_a_id is None:
                self.ctx[ticker] = {}
                return None
            sib = db.execute(select(KalshiMarket).where(
                KalshiMarket.event_ticker == row.event_ticker,
                KalshiMarket.ticker != ticker)).scalar()
            if sib is None or sib.player_a_id is None:
                self.ctx[ticker] = {}
                return None
            from bot.models import Player

            pa, pb = db.get(Player, row.player_a_id), db.get(Player, sib.player_a_id)
            raw = row.raw or {}
            series = raw.get("_series", "")
            ctx = {
                "player_a_id": row.player_a_id, "player_b_id": sib.player_a_id,
                "name_a": pa.full_name, "name_b": pb.full_name,
                "best_of": int(raw.get("_best_of", 3)),
                "event_ticker": row.event_ticker,
                "tier": {"KXATPMATCH": "A", "KXWTAMATCH": "A", "KXWTAGAME": "A",
                         "KXATPCHALLENGERMATCH": "C"}.get(series, "15"),
            }
        self.ctx[ticker] = ctx
        return ctx

    def _profile(self, db, player_id: int):
        hit = self._profiles.get(player_id)
        if hit is None:
            from bot.stats.profile import build_profile, load_history

            as_of = date.today() + timedelta(days=1)
            history = load_history(db, player_id)
            profile = build_profile(db, player_id, as_of)
            hit = (profile, history)
            self._profiles[player_id] = hit
        return hit

    # ---------- watch hooks ----------

    def on_quote(self, ticker: str, est, yes_bid: int | None, yes_ask: int | None,
                 volume: int | None) -> None:
        self.last_quote[ticker] = (yes_bid, yes_ask, volume)
        self._evaluate(ticker, est)

    def note_trade(self, ticker: str, count: int) -> None:
        """Accumulate live traded volume — the WS ticker feed carries no volume,
        so the trade stream is the liquidity signal the volume gate relies on."""
        self.session_volume[ticker] = self.session_volume.get(ticker, 0) + max(0, count)

    def on_confirmed_state(self, ticker: str, est) -> None:
        """Score arrived. Release or kill any pending advisory, then re-evaluate."""
        pend = self.pending.pop(ticker, None)
        if pend is not None:
            if pend["state"] == est.state_key:
                self._fire(ticker, est, release_of=pend["advisory_id"])
            else:
                self._kill(pend["advisory_id"], "score contradicted pending state")
        self._evaluate(ticker, est)

    def kill_pending(self, ticker: str, reason: str) -> None:
        pend = self.pending.pop(ticker, None)
        if pend is not None:
            self._kill(pend["advisory_id"], reason)

    # ---------- core ----------

    def _evaluate(self, ticker: str, est) -> None:
        if est.final or est.state_key == "final":
            return
        ctx = self._context(ticker)
        if ctx is None:
            return
        yes_bid, yes_ask, quote_vol = self.last_quote.get(ticker, (None, None, None))
        if yes_bid is None or yes_ask is None:
            return
        # WS ticker carries no volume; the trade-stream accumulator is the real
        # liquidity measure. Take the larger of the two.
        volume = max(quote_vol or 0, self.session_volume.get(ticker, 0))
        sa, sb = (int(x) for x in est.state_key.split("-"))
        try:
            state = MatchState(sa, sb, ctx["best_of"])
        except ValueError:
            return
        pred = self.model.predict(ctx["player_a_id"], ctx["player_b_id"], None,
                                  ctx["tier"], state)
        # executable price: ask on the side you would buy
        sides = [
            ("yes", pred.p_a, yes_ask),
            ("no", 1 - pred.p_a, 100 - yes_bid),
        ]
        side, model_prob, price = max(sides, key=lambda s: s[1] - s[2] / 100)
        edge = model_prob - price / 100
        if edge < self.cfg.edge_threshold or price <= 0 or price >= 100:
            return
        if pred.confidence < self.cfg.min_model_confidence:
            return
        if (volume or 0) < self.cfg.min_market_volume:
            return
        band = edge_band(edge)
        if self.last_advised.get(ticker) == (est.state_key, band):
            return  # debounced: same state, same band
        confirmed = est.last_confirmed == est.state_key
        if not confirmed and est.confidence < self.cfg.min_state_confidence:
            self._hold(ticker, est, side, model_prob, pred.confidence, price,
                       volume, state, confirmed)
            return
        self._fire(ticker, est, side=side, model_prob=model_prob,
                   model_conf=pred.confidence, price=price, volume=volume,
                   state=state, confirmed=confirmed, band=band)

    def _build_block(self, ticker: str, side: str, model_prob: float,
                     model_conf: float, price: int, volume, state: MatchState,
                     est, confirmed: bool):
        ctx = self._context(ticker)
        with self.db_session() as db:
            prof_a, hist_a = self._profile(db, ctx["player_a_id"])
            prof_b, hist_b = self._profile(db, ctx["player_b_id"])
        if side == "no":
            prof_a, prof_b = prof_b, prof_a
            hist_a, hist_b = hist_b, hist_a
            state = MatchState(state.sets_b, state.sets_a, state.best_of)
        as_of = date.today() + timedelta(days=1)
        facts = build_facts(prof_a, prof_b, hist_a, hist_b, state, as_of)
        return build_fact_block(
            market_ticker=ticker, name_a=prof_a.player_name,
            name_b=prof_b.player_name, side=side, facts=facts,
            model_prob=model_prob, model_confidence=model_conf,
            price_cents=price, volume=volume, state=state,
            state_confidence=est.confidence, state_confirmed=confirmed,
            probation=self.cfg.probation)

    def _persist(self, block, ctx, prose, validated, used_template,
                 status: str) -> int:
        with self.db_session() as db:
            adv = Advisory(
                created_at=datetime.now(timezone.utc), market_ticker=block.market_ticker,
                recommended_player_id=ctx["player_a_id"] if block.recommended_side == "yes"
                else ctx["player_b_id"],
                model_prob=block.model_prob, model_confidence=block.model_confidence,
                executable_price_cents=block.executable_price_cents,
                implied_prob=block.implied_prob, edge=block.edge,
                market_volume=block.volume, inferred_state=block.state_key,
                state_confidence=block.state_confidence,
                state_confirmed=block.state_confirmed,
                probation=block.probation and not block.state_confirmed,
                fact_block=block.to_json(), prose=prose, validator_passed=validated,
                used_template_fallback=used_template, status=status)
            db.add(adv)
            db.flush()
            return adv.id

    def _hold(self, ticker, est, side, model_prob, model_conf, price, volume,
              state, confirmed) -> None:
        if ticker in self.pending:
            return
        block = self._build_block(ticker, side, model_prob, model_conf, price,
                                  volume, state, est, confirmed)
        adv_id = self._persist(block, self._context(ticker), None, None, False,
                               "pending")
        self.pending[ticker] = {"advisory_id": adv_id, "state": est.state_key,
                                "block": block}
        log.info("advisory held pending score confirmation", ticker=ticker,
                 state=est.state_key, state_confidence=round(est.confidence, 2),
                 edge=round(block.edge, 3))

    def _fire(self, ticker: str, est, side=None, model_prob=None, model_conf=None,
              price=None, volume=None, state=None, confirmed=None, band=None,
              release_of: int | None = None) -> None:
        if release_of is not None:
            # re-evaluate with fresh quote at now-confirmed state
            yes_bid, yes_ask, volume = self.last_quote.get(ticker, (None, None, None))
            with self.db_session() as db:
                db.execute(Advisory.__table__.update()
                           .where(Advisory.id == release_of)
                           .values(status="killed", kill_reason="superseded by release"))
            self._evaluate(ticker, est)
            return
        block = self._build_block(ticker, side, model_prob, model_conf, price,
                                  volume, state, est, confirmed)
        self._warn_if_hitrate_low(confirmed)
        prose, validated, used_template = render_prose(block)
        ctx = self._context(ticker)
        adv_id = self._persist(block, ctx, prose, validated, used_template, "sent")
        pushed = deliver_advisory(block, prose)
        with self.db_session() as db:
            db.execute(Advisory.__table__.update().where(Advisory.id == adv_id)
                       .values(delivered_at=datetime.now(timezone.utc)
                               if pushed else None))
        self.last_advised[ticker] = (est.state_key, band if band is not None
                                     else edge_band(block.edge))
        self._paper_from_advisory(ticker, ctx, block)
        log.info("ADVISORY SENT", ticker=ticker, player=block.recommended_name,
                 price=block.executable_price_cents, edge=round(block.edge, 3),
                 state=block.state_key, confirmed=confirmed,
                 probation=block.probation and not confirmed, pushed=pushed)

    def _paper_from_advisory(self, ticker: str, ctx: dict, block) -> None:
        """Bot testrun: an advisory that also clears the paper policy becomes
        an imaginary bet (basis 'advisory'). Never an order — CLAUDE.md rule 1."""
        from bot.paper import BetDecision, place_bet, policy_ok, size_units

        prob, price = block.model_prob, block.executable_price_cents
        edge = block.edge
        # same v4 gate as the prematch path (shared policy_ok — no drift)
        if not (ctx.get("event_ticker")
                and policy_ok(prob, edge, price, ctx.get("tier"))):
            return
        decision = BetDecision(True, side=block.recommended_side, prob=prob,
                               edge=edge, price_cents=price,
                               units=size_units(prob, edge, block.model_confidence),
                               reason="advisory cleared paper policy (v4)")
        with self.db_session() as db:
            place_bet(db, event_ticker=ctx["event_ticker"], market_ticker=ticker,
                      player_id=ctx["player_a_id"] if block.recommended_side == "yes"
                      else ctx["player_b_id"],
                      decision=decision, confidence=block.model_confidence,
                      basis="advisory", tier=ctx["tier"], state=block.state_key,
                      reasoning={"match": f"{ctx['name_a']} vs {ctx['name_b']}",
                                 "state_confirmed": block.state_confirmed})

    def _kill(self, advisory_id: int, reason: str) -> None:
        with self.db_session() as db:
            db.execute(Advisory.__table__.update()
                       .where(Advisory.id == advisory_id)
                       .values(status="killed", kill_reason=reason[:64]))
        log.info("pending advisory killed", advisory_id=advisory_id, reason=reason)

    def _warn_if_hitrate_low(self, confirmed: bool) -> None:
        """Post-graduation guardrail: loud warning on every unconfirmed advisory
        while the trailing-30d estimator hit rate is below threshold."""
        if self.cfg.probation or confirmed:
            return
        now = datetime.now(timezone.utc)
        with self.db_session() as db:
            rows = db.execute(select(StateInferenceLog.hit).where(
                StateInferenceLog.confirmed_at >= now - timedelta(days=30),
                StateInferenceLog.session_had_gap.is_(False))).scalars().all()
        if rows:
            rate = sum(1 for h in rows if h) / len(rows)
            if rate < self.cfg.graduate_min_hit_rate:
                self._hitrate_warned_at = now
                log.warning("TRAILING 30-DAY STATE HIT RATE BELOW THRESHOLD — "
                            "treat unconfirmed advisories with suspicion",
                            hit_rate=round(rate, 3),
                            threshold=self.cfg.graduate_min_hit_rate, n=len(rows))
