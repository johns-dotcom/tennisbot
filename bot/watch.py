"""The live loop: python -m bot watch

- scheduled market discovery → market_matcher
- websocket subscriptions (ticker/trade/market_lifecycle_v2) for watched markets
- REST polling fallback with exponential backoff + jitter; fallback ticks are
  flagged degraded and never trigger boundary detection on their own
- delayed-score polling via milestones → estimator reconciliation
- estimator state persisted to live_match_state after every transition
- aiohttp health endpoint on $PORT
- SIGTERM: stop advising, flush recorder, close websocket, exit (restart
  protocol; boot-time stale handling in reload_estimators)
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import signal
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from bot.config import settings
from bot.db import session as db_session
from bot.log import get_logger
from bot.market.discovery import discover_markets
from bot.market.estimator import SetBoundaryEstimator
from bot.market.kalshi import KalshiClient, dollars_to_cents
from bot.market.priors import DEFAULT_PRIORS, load_priors, tier_bucket
from bot.market.recorder import MarketRecorder, new_session_id
from bot.market.replay import persist_snapshot
from bot.models import FeedGap, KalshiMarket, LiveMatchState, StateInferenceLog

log = get_logger("watch")

SERIES_TIER = {"KXATPMATCH": "A", "KXWTAMATCH": "A", "KXWTAGAME": "A",
               "KXATPCHALLENGERMATCH": "C", "KXITFMATCH": "15", "KXITFWMATCH": "15"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WatchService:
    def __init__(self) -> None:
        self.cfg = settings()
        self.client = KalshiClient()
        self.stop = asyncio.Event()
        self.estimators: dict[str, SetBoundaryEstimator] = {}
        self.watched: dict[str, dict] = {}  # ticker -> {event_ticker, milestone_id, side}
        self.recorder: MarketRecorder | None = None
        self.ws_connected = False
        self.disconnect_at: datetime | None = None
        self.priors_by_bucket: dict[str, object] = {}
        self.advisory_hook = None  # Phase 5 plugs in here

    # ---------- estimator management ----------

    def _priors(self, series: str):
        bucket = tier_bucket(SERIES_TIER.get(series))
        return self.priors_by_bucket.get(bucket, DEFAULT_PRIORS)

    def _estimator(self, ticker: str, series: str) -> SetBoundaryEstimator:
        est = self.estimators.get(ticker)
        if est is None:
            def persist(snap):
                with db_session() as db:
                    persist_snapshot(db, snap)

            def log_inference(row):
                with db_session() as db:
                    detail = dict(row.get("detail") or {})
                    db.add(StateInferenceLog(
                        market_ticker=row["market_ticker"],
                        session_id=self.recorder.session_id if self.recorder else None,
                        inferred_state=row["inferred_state"], inferred_at=row["inferred_at"],
                        confirmed_state=row["confirmed_state"], confirmed_at=row["confirmed_at"],
                        lead_time_seconds=row["lead_time_seconds"], hit=row["hit"],
                        detail=detail))

            def on_conflict(t):
                if self.advisory_hook:
                    self.advisory_hook.kill_pending(t, reason="state conflict")

            est = SetBoundaryEstimator(ticker, priors=self._priors(series),
                                       persist=persist, log_inference=log_inference,
                                       on_conflict=on_conflict)
            self.reload_estimator_state(est)
            self.estimators[ticker] = est
        return est

    def reload_estimator_state(self, est: SetBoundaryEstimator) -> None:
        """Boot reload per restart protocol: <60s gap resumes, else quarantine."""
        with db_session() as db:
            row = db.get(LiveMatchState, est.ticker)
        if row is None:
            return
        est.restore(row.state, row.confidence, row.last_confirmed_state, row.last_tick_at)
        if row.last_tick_at is None:
            return
        gap = (utcnow() - row.last_tick_at).total_seconds()
        if gap >= self.cfg.stale_gap_seconds and row.state not in ("0-0", "final"):
            est.quarantine(utcnow(), f"stale on boot (gap {gap:.0f}s)")

    # ---------- discovery ----------

    async def discovery_loop(self) -> None:
        while not self.stop.is_set():
            try:
                with db_session() as db:
                    await asyncio.to_thread(discover_markets, db, self.client)
                    rows = db.execute(
                        select(KalshiMarket).where(
                            KalshiMarket.status.in_(["active", "open"]),
                            KalshiMarket.player_a_id.is_not(None))
                    ).scalars().all()
                    self.watched = {
                        r.ticker: {
                            "event_ticker": r.event_ticker,
                            "milestone_id": (r.raw or {}).get("_milestone_id"),
                            "series": (r.raw or {}).get("_series", "KXATPMATCH"),
                            "title": r.title,
                            "occurrence": (r.raw or {}).get("occurrence_datetime"),
                        } for r in rows
                    }
                log.info("watch set updated", markets=len(self.watched))
            except Exception as e:
                log.error("discovery loop error", error=str(e))
            try:
                await asyncio.wait_for(self.stop.wait(),
                                       timeout=self.cfg.kalshi_discovery_interval_s)
            except asyncio.TimeoutError:
                pass

    # ---------- websocket ----------

    async def ws_loop(self) -> None:
        import websockets

        backoff = 1.0
        while not self.stop.is_set():
            if not self.watched:
                await asyncio.sleep(5)
                continue
            try:
                headers = self.client.ws_headers()
            except RuntimeError as e:
                log.warning("websocket unavailable, REST fallback only", reason=str(e))
                return  # rest_fallback_loop keeps recording (degraded)
            try:
                async with websockets.connect(self.cfg.kalshi_ws_url,
                                              additional_headers=headers,
                                              ping_interval=10, ping_timeout=10) as ws:
                    self._on_connect()
                    backoff = 1.0
                    await ws.send(json.dumps({
                        "id": 1, "cmd": "subscribe",
                        "params": {"channels": ["ticker", "trade", "market_lifecycle_v2"],
                                   "market_tickers": sorted(self.watched)}}))
                    async for raw in ws:
                        self._on_ws_message(json.loads(raw))
                        if self.stop.is_set():
                            break
            except Exception as e:
                log.error("websocket dropped", error=str(e))
            self._on_disconnect()
            jitter = random.uniform(0, backoff / 2)
            await asyncio.sleep(min(60.0, backoff + jitter))
            backoff = min(60.0, backoff * 2)

    def _on_connect(self) -> None:
        now = utcnow()
        self.ws_connected = True
        with db_session() as db:
            self.recorder = MarketRecorder(db, session_id=new_session_id())
            if self.disconnect_at is not None:
                gap_s = (now - self.disconnect_at).total_seconds()
                for ticker in self.watched:
                    db.add(FeedGap(market_ticker=ticker, session_id=self.recorder.session_id,
                                   gap_start=self.disconnect_at, gap_end=now,
                                   duration_seconds=gap_s))
                    if gap_s >= self.cfg.feed_gap_quarantine_seconds:
                        est = self.estimators.get(ticker)
                        if est:
                            est.quarantine(now, f"feed gap {gap_s:.0f}s")
                db.commit()
        self.disconnect_at = None
        log.info("websocket connected", session=self.recorder.session_id)

    def _on_disconnect(self) -> None:
        if self.ws_connected:
            self.ws_connected = False
            self.disconnect_at = utcnow()
            if self.recorder:
                self.recorder.flush()

    def _on_ws_message(self, msg: dict) -> None:
        mtype, body = msg.get("type"), msg.get("msg") or {}
        ticker = body.get("market_ticker")
        if not ticker or ticker not in self.watched or self.recorder is None:
            return
        now = utcnow()
        series = self.watched[ticker]["series"]
        est = self._estimator(ticker, series)
        if mtype == "ticker":
            yb = body.get("yes_bid") if isinstance(body.get("yes_bid"), int) \
                else dollars_to_cents(body.get("yes_bid_dollars"))
            ya = body.get("yes_ask") if isinstance(body.get("yes_ask"), int) \
                else dollars_to_cents(body.get("yes_ask_dollars"))
            vol = body.get("volume")
            try:
                vol = int(float(vol)) if vol is not None else None
            except (TypeError, ValueError):
                vol = None
            self.recorder.quote(ticker, now, yb, ya,
                                100 - ya if ya is not None else None,
                                100 - yb if yb is not None else None, volume=vol)
            est.on_quote(now, yb, ya)
            if self.advisory_hook:
                self.advisory_hook.on_quote(ticker, est, yb, ya, vol)
        elif mtype == "trade":
            price = body.get("yes_price") if isinstance(body.get("yes_price"), int) \
                else dollars_to_cents(body.get("yes_price_dollars"))
            count = int(float(body.get("count", 1) or 1))
            self.recorder.trade(ticker, now, price or 0, count)
            est.on_trade(now, price or 0, count)
        elif mtype == "market_lifecycle_v2":
            event = body.get("event_type", "unknown")
            self.recorder.lifecycle(ticker, now, event, raw=body)
            if self.advisory_hook and event in ("paused", "halted", "suspended",
                                                "closed", "determined", "settled"):
                self.advisory_hook.kill_pending(ticker, f"market {event}")

    def _active_tickers(self) -> list[str]:
        """Markets whose match is near/inside its playing window — the only ones
        worth polling (rate limits; 386 open markets but ~a dozen live)."""
        now = utcnow()
        out = []
        for ticker, info in self.watched.items():
            occ = (info.get("occurrence") or "").replace("Z", "+00:00")
            try:
                start = datetime.fromisoformat(occ)
            except ValueError:
                continue
            if start - timedelta(minutes=10) <= now <= start + timedelta(hours=6):
                out.append(ticker)
        return out

    # ---------- REST fallback (degraded) ----------

    async def rest_fallback_loop(self) -> None:
        while not self.stop.is_set():
            if self.ws_connected or not self.watched:
                await asyncio.sleep(3)
                continue
            if self.recorder is None:
                with db_session() as db:
                    self.recorder = MarketRecorder(db, session_id=new_session_id())
                log.warning("recording via REST fallback (degraded)",
                            session=self.recorder.session_id)
            for ticker in self._active_tickers():
                info = self.watched.get(ticker)
                if info is None or self.stop.is_set() or self.ws_connected:
                    break
                try:
                    m = await asyncio.to_thread(self.client.market, ticker)
                    now = utcnow()
                    yb = dollars_to_cents(m.get("yes_bid_dollars"))
                    ya = dollars_to_cents(m.get("yes_ask_dollars"))
                    self.recorder.quote(ticker, now, yb, ya,
                                        100 - ya if ya is not None else None,
                                        100 - yb if yb is not None else None,
                                        degraded=True)
                    est = self._estimator(ticker, info["series"])
                    est.on_quote(now, yb, ya, degraded=True)  # never triggers detection
                    if self.advisory_hook:
                        try:
                            vol = int(float(m.get("volume_fp") or 0))
                        except (TypeError, ValueError):
                            vol = None
                        self.advisory_hook.on_quote(ticker, est, yb, ya, vol)
                except Exception as e:
                    log.warning("rest fallback poll failed", ticker=ticker, error=str(e))
            await asyncio.sleep(8 + random.uniform(0, 4))

    # ---------- delayed score ----------

    async def score_loop(self) -> None:
        while not self.stop.is_set():
            active = self._active_tickers()
            polled = scored = 0
            for ticker in active:
                info = self.watched.get(ticker)
                if info is None or self.stop.is_set():
                    break
                mid = info.get("milestone_id")
                if not mid:
                    continue
                try:
                    payload = await asyncio.to_thread(self.client.live_data, mid)
                    polled += 1
                except Exception as e:
                    log.warning("live_data poll failed", ticker=ticker, error=str(e))
                    continue
                sets = self.client.sets_from_live_data(payload)
                if sets is None:
                    continue
                scored += 1
                c1, c2 = sets
                sa, sb = (c1, c2) if self._yes_is_competitor1(ticker, info, payload) \
                    else (c2, c1)
                now = utcnow()
                if self.recorder:
                    self.recorder.score(ticker, now, sa, sb)
                est = self._estimator(ticker, info["series"])
                res = est.on_score(now, sa, sb)
                if res.changed and self.advisory_hook:
                    self.advisory_hook.on_confirmed_state(ticker, est)
            if active:
                log.info("score poll cycle", active=len(active), polled=polled,
                         scored=scored)
            try:
                await asyncio.wait_for(self.stop.wait(),
                                       timeout=self.cfg.kalshi_score_poll_interval_s)
            except asyncio.TimeoutError:
                pass

    @staticmethod
    def _yes_is_competitor1(ticker: str, info: dict, payload: dict) -> bool:
        """Heuristic: competitor1 is the first-named player in the event title
        ('Rocha vs Martinez'); market ticker suffix is built from that surname."""
        title = (info.get("title") or "")
        first_surname = title.replace("Will ", "").split(" vs ")[0].strip().split()[-1] \
            if " vs " in title else ""
        suffix = ticker.rsplit("-", 1)[-1].upper()
        return bool(first_surname) and first_surname.upper().startswith(suffix[:3])

    # ---------- settlement resolution (track record) ----------

    async def settlement_loop(self) -> None:
        """Resolve results for recently-finished markets: scores the track
        record (advisory outcomes) and records final set counts (fatigue
        signal for scenario generation). Runs every 30 min."""
        from sqlalchemy import select

        from bot.models import KalshiMarket

        while not self.stop.is_set():
            try:
                now = utcnow()
                with db_session() as db:
                    rows = db.execute(select(KalshiMarket).where(
                        KalshiMarket.result.is_(None),
                        KalshiMarket.player_a_id.is_not(None))).scalars().all()
                    pending = []
                    for r in rows:
                        occ_raw = (r.raw or {}).get("occurrence_datetime")
                        if not occ_raw:
                            continue
                        try:
                            occ = datetime.fromisoformat(occ_raw.replace("Z", "+00:00"))
                        except ValueError:
                            continue
                        # match should be over but not ancient
                        if timedelta(hours=2) <= now - occ <= timedelta(hours=72):
                            pending.append((r.ticker, (r.raw or {}).get("_milestone_id")))
                for ticker, milestone_id in pending:
                    if self.stop.is_set():
                        break
                    try:
                        m = await asyncio.to_thread(self.client.market, ticker)
                    except Exception:
                        continue
                    result = (m.get("result") or "").strip().lower()
                    if result not in ("yes", "no", "void"):
                        continue
                    final_sets = None
                    if milestone_id:
                        try:
                            payload = await asyncio.to_thread(
                                self.client.live_data, milestone_id)
                            sets = self.client.sets_from_live_data(payload)
                            if sets:
                                final_sets = sets[0] + sets[1]
                        except Exception:
                            pass
                    with db_session() as db:
                        row = db.execute(select(KalshiMarket).where(
                            KalshiMarket.ticker == ticker)).scalar()
                        if row is not None:
                            row.result = result
                            row.settled_at = utcnow()
                            row.status = m.get("status") or row.status
                            if final_sets is not None:
                                raw = dict(row.raw or {})
                                raw["_final_sets"] = final_sets
                                row.raw = raw
                    log.info("market settled", ticker=ticker, result=result,
                             final_sets=final_sets)
            except Exception as e:
                log.error("settlement loop error", error=str(e))
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=1800)
            except asyncio.TimeoutError:
                pass

    # ---------- health + lifecycle ----------

    async def health_server(self) -> None:
        from aiohttp import web

        async def health(_req):
            return web.json_response({
                "ok": True, "ws_connected": self.ws_connected,
                "watched_markets": len(self.watched),
                "session": self.recorder.session_id if self.recorder else None})

        app = web.Application()
        app.router.add_get("/health", health)
        app.router.add_get("/", health)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080)))
        await site.start()
        log.info("health endpoint up", port=os.environ.get("PORT", 8080))
        await self.stop.wait()
        await runner.cleanup()

    def _fit_model(self):
        from bot.prob.elo import SetElo

        model = SetElo()
        with db_session() as db:
            model.fit_from_db(db)
        return model

    def _handle_sigterm(self) -> None:
        log.info("SIGTERM: flushing state and shutting down")
        self.stop.set()

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._handle_sigterm)
        with db_session() as db:
            self.priors_by_bucket = {
                b: load_priors(db, lvl, None)
                for b, lvl in (("tour", "A"), ("challenger", "C"), ("itf", "15"),
                               ("slam", "G"))}
        log.info("fitting probability model from history (one-time, ~1-3 min)")
        model = await asyncio.to_thread(self._fit_model)
        from bot.engine import AdvisoryEngine

        self.advisory_hook = AdvisoryEngine(db_session, model)
        log.info("advisory engine armed", probation=self.cfg.probation)
        async def supervised(fn, name: str):
            while not self.stop.is_set():
                try:
                    await fn()
                    return  # normal exit (stop set or ws deliberately disabled)
                except Exception as e:
                    log.error("loop crashed — restarting in 5s", loop=name, error=str(e))
                    await asyncio.sleep(5)

        tasks = [asyncio.create_task(supervised(fn, name)) for fn, name in (
            (self.discovery_loop, "discovery"), (self.ws_loop, "websocket"),
            (self.rest_fallback_loop, "rest_fallback"), (self.score_loop, "score"),
            (self.settlement_loop, "settlement"), (self.health_server, "health"))]

        async def shutdown_watchdog():
            await self.stop.wait()
            await asyncio.sleep(15)  # grace period to flush/close
            for t in tasks:
                t.cancel()

        watchdog = asyncio.create_task(shutdown_watchdog())
        await asyncio.gather(*tasks, return_exceptions=True)
        watchdog.cancel()
        if self.recorder:
            self.recorder.flush()
        log.info("watch stopped cleanly")


def main() -> int:
    asyncio.run(WatchService().run())
    return 0
