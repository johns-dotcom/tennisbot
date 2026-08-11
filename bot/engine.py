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
from bot.market.live_status import is_live_status
from bot.models import Advisory, KalshiMarket, StateInferenceLog
from bot.notify import notify_signal
from bot.prob.elo import SetElo
from bot.prob.model import MatchState

log = get_logger("engine")

EDGE_BANDS = (0.06, 0.10, 0.15)  # band edges; crossing into a new band re-arms debounce
# "armed" phone heads-up: a live match reaching its deciding set with the model
# favouring a side by at least this much (a bet setup is now live). Pushed
# advisories have their own, stricter gates (edge/volume/confidence).
ARMED_MIN_PROB = 0.55
# Discord alerts fire ONLY for tracked scenarios, and ONLY when the alerted
# side's price is in the toss-up band (user decision 2026-07-23).
ALERT_PRICE_MIN, ALERT_PRICE_MAX = 35, 65


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
        self._signalled: set[str] = set()  # event_tickers already signal-alerted

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
            from bot.stats.surface import live_match_surface
            ctx = {
                "player_a_id": row.player_a_id, "player_b_id": sib.player_a_id,
                "name_a": pa.full_name, "name_b": pb.full_name,
                "best_of": int(raw.get("_best_of", 3)),
                "event_ticker": row.event_ticker,
                "tier": {"KXATPMATCH": "A", "KXWTAMATCH": "A", "KXWTAGAME": "A",
                         "KXATPCHALLENGERMATCH": "C"}.get(series, "15"),
                # men/women: Player.tour is 'atp' (men, incl. ITF men) or 'wta'
                # (women, incl. ITF women) — the authoritative gender split
                "tour": pa.tour,
                # this match's court → the model applies its surface-specific
                # rating (else it falls back to overall). Static per match.
                "surface": live_match_surface(db, row.player_a_id, sib.player_a_id,
                                               date.today()),
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
        pred = self.model.predict(ctx["player_a_id"], ctx["player_b_id"],
                                  ctx.get("surface"), ctx["tier"], state,
                                  as_of=date.today())
        self._maybe_arm(ticker, ctx, est, sa, sb, pred)
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

    def _scenario_plan(self, event_ticker: str | None) -> dict | None:
        """The latest scenario for this event as {market_ticker (watch side),
        narrative, triggers}, or None if the event isn't a tracked scenario.
        The alert gate: None ⇒ no Discord alert. triggers is the watch-side
        set-state list (takes set 1 / drops set 1 / decider), each with the model
        prob there; falls back to the decider for pre-triggers scenarios."""
        if not event_ticker:
            return None
        from bot.models import Scenario
        with self.db_session() as db:
            row = db.execute(select(
                Scenario.market_ticker, Scenario.narrative, Scenario.facts,
                Scenario.model_prob_at_state)
                .where(Scenario.event_ticker == event_ticker)
                .order_by(Scenario.created_for.desc()).limit(1)).first()
        if row is None:
            return None
        mkt, narr, facts, dec_prob = row
        triggers = (facts or {}).get("triggers") or [
            {"kind": "decider", "label": "deciding set", "state": "1-1",
             "prob": dec_prob}]
        return {"market_ticker": mkt, "narrative": narr, "triggers": triggers}


    def _match_live(self, ticker: str) -> bool:
        """Is the match started AND not over? The gate for a Discord alert. An
        authoritative END signal — a posted Kalshi result or a final scoreline —
        OUTRANKS a live milestone status, so a finished match with a stale 'P'/
        'live' flag can never keep alerting. (Not-yet-started matches have no
        started signal; finished ones have an end signal.)"""
        from bot.models import KalshiMarket, MatchScoreLog
        with self.db_session() as db:
            res, raw = db.execute(select(KalshiMarket.result, KalshiMarket.raw)
                                  .where(KalshiMarket.ticker == ticker)).first() or (None, None)
            if res is not None:
                return False  # settled → over
            row = db.execute(
                select(MatchScoreLog.total_games, MatchScoreLog.is_final)
                .where(MatchScoreLog.market_ticker == ticker)
                .order_by(MatchScoreLog.ts.desc()).limit(1)).first()
            if row and row[1]:
                return False  # latest scoreline is final → over
            started = (is_live_status((raw or {}).get("_live_status"))
                       or bool(row and (row[0] or 0) > 0))
            return started

    def _maybe_arm(self, ticker: str, ctx: dict, est, sa: int, sb: int, pred) -> None:
        """Fire the phone SIGNAL alert when a SCENARIO's trigger fires — ANY of its
        watch-side set-state triggers (takes set 1 / drops set 1 / decider), not
        just the decider — with the model still favouring the pick and the price in
        the toss-up band. Once per EVENT — alerts happen when signals happen."""
        ev = ctx.get("event_ticker")
        if ev and ev in self._signalled:
            return
        plan = self._scenario_plan(ev)
        if plan is None:
            return  # not a tracked scenario
        # Evaluate ONLY on the watch-side market: its state is in the watch
        # player's perspective, so an asymmetric trigger (1-0 vs 0-1) reads right.
        # The sibling ticker's state is mirrored and would misfire.
        if ticker != plan["market_ticker"]:
            return
        state_ok = (est.last_confirmed == est.state_key
                    or est.confidence >= self.cfg.min_state_confidence)
        if not (state_ok and pred.confidence >= self.cfg.min_model_confidence):
            return
        # the fired trigger: watch state matches a trigger AND the model still
        # favours the pick there (≥ ARMED_MIN_PROB)
        trig = next((t for t in plan["triggers"]
                     if t.get("state") == est.state_key
                     and (t.get("prob") or 0) >= ARMED_MIN_PROB), None)
        if trig is None:
            return
        # price gate: watch is the YES side of this (watch) ticker — toss-up band
        yb, ya, _ = self.last_quote.get(ticker, (None, None, None))
        if ya is None:
            return
        price = ya
        if not (ALERT_PRICE_MIN <= price <= ALERT_PRICE_MAX):
            return
        if not self._match_live(ticker):
            return  # hasn't started / already over
        self._signalled.add(ev)
        fav = ctx["name_a"]  # YES of the watch ticker = the watch pick
        match = f"{ctx['name_a']} vs {ctx['name_b']}"
        analysis = (f"Trigger fired: {trig['label']} ({est.state_key}), "
                    f"priced {price}¢.\n\n{plan['narrative']}")
        # message 1 pings @everyone with just the pick + confidence; message 2
        # (no ping) carries this analysis and the structured fields.
        notify_signal(match=match, pick=fav, confidence=f"{trig['prob']:.0%}",
                      analysis=analysis, kind="armed",
                      fields=[("Trigger", trig["label"]), ("State", est.state_key),
                              ("Favours", fav), ("Model", f"{trig['prob']:.0%}"),
                              ("Price", f"{price}¢")])
        log.info("SIGNAL alert", ticker=ticker, state=est.state_key,
                 trigger=trig["kind"], favours=fav, price=price)

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
        # NO Discord alert here: an advisory can fire at any in-play edge, which
        # is not the same as a SCENARIO SIGNAL. Alerts fire only when a scenario's
        # trigger fires (see _maybe_arm) — "when signals happen is when alerts
        # happen". The advisory still logs, persists, and drives paper bets.
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
        from bot.market.line_move import market_move_cents
        from bot.paper import BetDecision, place_bet, policy_ok, size_units
        from bot.t2 import BOTS, advisory_gate_ok, iter_bot_policies

        prob, price = block.model_prob, block.executable_price_cents
        edge = block.edge
        if not ctx.get("event_ticker"):
            return
        best_of = ctx.get("best_of", 3)
        need = best_of // 2
        at_decider = block.state_key == f"{need}-{need}"
        try:
            sa, sb = (int(x) for x in block.state_key.split("-"))
        except (ValueError, AttributeError):
            sa = sb = None
        # pre-match favorite for the 'dip' bot — recompute the model's OPENING
        # read at 0-0 so "favorite" is model-defined, never price-defined (NO
        # CIRCULARITY). fav_side is the YES/NO side the model favours pre-play.
        fav_side = fav_prob = None
        try:
            pre = self.model.predict(ctx["player_a_id"], ctx["player_b_id"],
                                     ctx.get("surface"), ctx.get("tier"),
                                     MatchState(0, 0, best_of), as_of=date.today())
            fav_side, fav_prob = (("yes", pre.p_a) if pre.p_a >= 0.5
                                  else ("no", 1 - pre.p_a))
        except Exception:
            pass
        with self.db_session() as db:
            # line drift on our side since the open — the follow/fade gate reads it
            move = market_move_cents(db, ticker, block.recommended_side, price)
            # the LIVE bots evaluate the advisory under their own policy — shared
            # policy_ok so they can't drift. Single-variable experiment bots add
            # exactly one gate (decider / tier / confidence / line-move) so their
            # record isolates that indicator.
            for bot, policy in iter_bot_policies(db, "advisory"):
                if not advisory_gate_ok(
                        BOTS[bot], at_decider=at_decider,
                        confidence=block.model_confidence,
                        tier=ctx.get("tier"), move=move, tour=ctx.get("tour"),
                        sets=(sa, sb) if sa is not None else None,
                        best_of=best_of, fav_side=fav_side, fav_prob=fav_prob,
                        recommended_side=block.recommended_side):
                    continue
                if not policy_ok(prob, edge, price, ctx.get("tier"), policy,
                                 confidence=block.model_confidence):
                    continue
                decision = BetDecision(
                    True, side=block.recommended_side, prob=prob, edge=edge,
                    price_cents=price,
                    units=size_units(prob, edge, block.model_confidence, policy.size_mult),
                    reason=f"advisory cleared {policy.version} paper policy")
                place_bet(db, event_ticker=ctx["event_ticker"], market_ticker=ticker,
                          player_id=ctx["player_a_id"] if block.recommended_side == "yes"
                          else ctx["player_b_id"],
                          decision=decision, confidence=block.model_confidence,
                          basis="advisory", tier=ctx["tier"], state=block.state_key,
                          bot=bot, policy_version=policy.version,
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
