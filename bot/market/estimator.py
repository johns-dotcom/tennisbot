"""Rule-based set-state estimator v1 (deliberately NOT a Bayesian/particle filter).

State space: sets won only — 0-0, 1-0, 0-1, 1-1 (Bo3; Bo5 analogous), plus
'final'. No game-level inference, no momentum flag.

Detector: volume-confirmed price discontinuity — a jump of more than X cents
within Y seconds, backed by trade prints in the same window. Quote-only moves in
thin ITF books are noise and mean-revert; degraded (REST-fallback) ticks never
trigger detection. Favorite-won-set and underdog-won-set have asymmetric
signatures: the favorite holding serve to a set is priced in (smaller jump
suffices), an underdog set win is a surprise (bigger jump required).

Reconciliation: when Kalshi's delayed score arrives, snap to it and log
(inferred, confirmed, lead_time) — the labeled data for the v2 upgrade.

Persistence: after EVERY transition and EVERY reconciliation the full estimator
state goes through the persist callback (→ live_match_state). The estimator
holds no state that lives only in memory.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from bot.config import settings
from bot.log import get_logger
from bot.market.priors import DEFAULT_PRIORS, SetDurationPriors

log = get_logger("market.estimator")

FAV_JUMP_MULT = 0.7  # favorite-won-set jump threshold multiplier (asymmetry)
BASE_CONFIDENCE = 0.72
CONFIDENCE_CAP = 0.95
# price-plausibility guards on the 'final' state: a decided match trades near a
# terminal price (winner ~99¢). An inferred 'final' while the market is a
# coin-flip is noise (the detector over-counted a boundary). Require the winner's
# mid ≥ FINAL_MID_MIN to ENTER final; if an existing 'final' is contradicted by a
# live quote below UNSTICK_MID, revert it (hysteresis avoids flapping).
FINAL_MID_MIN = 80
UNSTICK_MID = 70


@dataclass
class Inference:
    state_key: str
    inferred_at: datetime
    detail: dict


@dataclass
class EstimatorSnapshot:
    market_ticker: str
    state: str
    confidence: float
    last_confirmed_state: str | None
    last_tick_at: datetime | None
    stale: bool = False


@dataclass
class ReconcileResult:
    changed: bool
    conflict: bool  # confirmed state contradicts current/pending inference
    confirmed_state: str


class SetBoundaryEstimator:
    """One instance per watched market. YES side is player A by convention."""

    def __init__(self, market_ticker: str, best_of: int = 3,
                 priors: SetDurationPriors = DEFAULT_PRIORS,
                 persist: Callable[[EstimatorSnapshot], None] = lambda s: None,
                 log_inference: Callable[[dict], None] = lambda d: None,
                 on_conflict: Callable[[str], None] = lambda ticker: None):
        cfg = settings()
        self.ticker = market_ticker
        self.best_of = best_of
        self.need = best_of // 2 + 1
        self.priors = priors
        self.persist = persist
        self.log_inference = log_inference
        self.on_conflict = on_conflict
        self.jump_cents = cfg.boundary_jump_cents
        self.window_s = cfg.boundary_window_seconds
        self.min_trades = cfg.boundary_min_trade_contracts

        self.sets_a = 0
        self.sets_b = 0
        self.confidence = 1.0  # 0-0 pre-match is certain
        self.last_confirmed: str | None = "0-0"
        self.final = False
        self.last_tick_at: datetime | None = None
        self.anchor_at: datetime | None = None  # last boundary (or first trade)
        self.pending: list[Inference] = []
        self._quotes: deque[tuple[datetime, float]] = deque()
        self._trades: deque[tuple[datetime, int]] = deque()

    # ---------- state helpers ----------

    @property
    def state_key(self) -> str:
        return "final" if self.final else f"{self.sets_a}-{self.sets_b}"

    def snapshot(self) -> EstimatorSnapshot:
        return EstimatorSnapshot(
            market_ticker=self.ticker, state=self.state_key,
            confidence=self.confidence, last_confirmed_state=self.last_confirmed,
            last_tick_at=self.last_tick_at)

    def restore(self, state: str, confidence: float, last_confirmed: str | None,
                last_tick_at: datetime | None) -> None:
        """Rehydrate from live_match_state on boot (restart protocol)."""
        if state == "final":
            self.final = True
        else:
            a, b = state.split("-")
            self.sets_a, self.sets_b = int(a), int(b)
        self.confidence = confidence
        self.last_confirmed = last_confirmed
        self.last_tick_at = last_tick_at

    def quarantine(self, now: datetime, reason: str) -> None:
        """Feed gap / stale restart: confidence to zero until score re-confirms."""
        self.confidence = 0.0
        self.pending.clear()
        self._quotes.clear()
        self._trades.clear()
        self.last_tick_at = now
        log.warning("estimator quarantined", ticker=self.ticker, reason=reason,
                    state=self.state_key)
        self.persist(self.snapshot())
        self.on_conflict(self.ticker)  # kill pending advisories

    # ---------- tick ingestion ----------

    def on_quote(self, ts: datetime, yes_bid: int | None, yes_ask: int | None,
                 degraded: bool = False) -> None:
        self.last_tick_at = ts
        if yes_bid is None or yes_ask is None:
            return
        mid = (yes_bid + yes_ask) / 2
        # self-heal: an inferred 'final' contradicted by a live coin-flip price was
        # a mis-count — revert it so the read keeps tracking the live match instead
        # of freezing (e.g. "plan done" at 54/46). Only a real, non-degraded quote.
        if self.final and not degraded:
            winner_mid = mid if self.sets_a >= self.need else 100 - mid
            if winner_mid < UNSTICK_MID:
                self.final = False
                self.sets_a = min(self.sets_a, self.need - 1)
                self.sets_b = min(self.sets_b, self.need - 1)
                self.confidence = min(self.confidence, BASE_CONFIDENCE)
                log.info("estimator un-final — live price contradicts final",
                         ticker=self.ticker, mid=round(mid, 1),
                         sets=f"{self.sets_a}-{self.sets_b}")
                self.persist(self.snapshot())
        if self.final or degraded:
            return  # final (corroborated) or REST-fallback: no boundary detection
        self._quotes.append((ts, mid))
        self._prune(ts)
        self._detect(ts)

    def on_trade(self, ts: datetime, price: int, count: int,
                 degraded: bool = False) -> None:
        self.last_tick_at = ts
        if self.final or degraded:
            return
        if self.anchor_at is None:
            self.anchor_at = ts  # first trade ≈ match underway
        self._trades.append((ts, count))
        self._prune(ts)

    def _prune(self, now: datetime) -> None:
        horizon = self.window_s
        while self._quotes and (now - self._quotes[0][0]).total_seconds() > horizon:
            self._quotes.popleft()
        while self._trades and (now - self._trades[0][0]).total_seconds() > horizon:
            self._trades.popleft()

    # ---------- boundary detection ----------

    def _detect(self, now: datetime) -> None:
        if len(self._quotes) < 2 or self.confidence == 0.0:
            # zero confidence = quarantined: only a score update revives advising,
            # but we still must not stack inferences on an unknown base state
            return
        oldest_ts, oldest_mid = self._quotes[0]
        _, latest_mid = self._quotes[-1]
        jump = latest_mid - oldest_mid
        # asymmetric thresholds: favorite = side whose price was already >50
        fav_is_yes = oldest_mid > 50
        toward_fav = (jump > 0) == fav_is_yes
        threshold = self.jump_cents * (FAV_JUMP_MULT if toward_fav else 1.0)
        if abs(jump) < threshold:
            return
        traded = sum(c for _, c in self._trades)
        if traded < self.min_trades:
            return  # quote-only move in a thin book: noise, will mean-revert
        anchor = self.anchor_at or oldest_ts
        elapsed_min = (now - anchor).total_seconds() / 60
        plaus = self.priors.plausibility(elapsed_min)
        if plaus == 0.0:
            log.info("boundary candidate rejected by set-duration prior",
                     ticker=self.ticker, jump=round(jump, 1), elapsed_min=round(elapsed_min, 1))
            return
        self._transition(yes_won_set=jump > 0, now=now, jump=jump, traded=traded,
                         plausibility=plaus, threshold=threshold)

    def _transition(self, yes_won_set: bool, now: datetime, jump: float,
                    traded: int, plausibility: float, threshold: float) -> None:
        # would this boundary end the match? if so, require the price to
        # corroborate a decided match — otherwise it's a mis-count on noise and we
        # reject it (a finished match isn't priced as a coin-flip).
        prosp_a = self.sets_a + (1 if yes_won_set else 0)
        prosp_b = self.sets_b + (0 if yes_won_set else 1)
        if prosp_a >= self.need or prosp_b >= self.need:
            _, latest_mid = self._quotes[-1]
            winner_mid = latest_mid if yes_won_set else 100 - latest_mid
            if winner_mid < FINAL_MID_MIN:
                log.info("final boundary rejected — price not terminal",
                         ticker=self.ticker, winner_mid=round(winner_mid, 1),
                         jump=round(jump, 1))
                return
        if yes_won_set:
            self.sets_a += 1
        else:
            self.sets_b += 1
        if self.sets_a >= self.need or self.sets_b >= self.need:
            self.final = True
        overshoot = min(1.0, (abs(jump) - threshold) / threshold)
        vol_factor = min(1.0, traded / (self.min_trades * 4))
        self.confidence = min(
            CONFIDENCE_CAP,
            (BASE_CONFIDENCE + 0.12 * overshoot + 0.08 * vol_factor) * plausibility,
        )
        self.anchor_at = now
        self._quotes.clear()
        self._trades.clear()
        inf = Inference(state_key=self.state_key, inferred_at=now, detail={
            "jump_cents": round(jump, 1), "traded": traded,
            "plausibility": plausibility, "threshold": threshold})
        self.pending.append(inf)
        log.info("set boundary inferred", ticker=self.ticker, state=self.state_key,
                 confidence=round(self.confidence, 2), jump=round(jump, 1),
                 traded=traded)
        self.persist(self.snapshot())

    # ---------- reconciliation with delayed score ----------

    def on_score(self, ts: datetime, sets_a: int, sets_b: int) -> ReconcileResult:
        self.last_tick_at = ts
        confirmed_key = f"{sets_a}-{sets_b}"
        if sets_a >= self.need or sets_b >= self.need:
            confirmed_key = "final"
        prev_confirmed = self.last_confirmed
        changed = confirmed_key != prev_confirmed
        conflict = False

        if not changed and confirmed_key == self.state_key:
            self.last_confirmed = confirmed_key
            self.persist(self.snapshot())
            return ReconcileResult(False, False, confirmed_key)

        # resolve pending inferences oldest-first
        matched: Inference | None = None
        for inf in self.pending:
            if inf.state_key == confirmed_key:
                matched = inf
                break
        if matched is not None:
            for inf in self.pending:
                if inf.inferred_at <= matched.inferred_at and inf is not matched:
                    self._log(inf, confirmed_key, ts, hit=False)
                    conflict = True
            self._log(matched, confirmed_key, ts, hit=True)
            self.pending = [i for i in self.pending if i.inferred_at > matched.inferred_at]
        else:
            for inf in self.pending:
                self._log(inf, confirmed_key, ts, hit=False)
            if self.pending:
                conflict = True
            self.pending.clear()
            if changed and self.state_key == prev_confirmed:
                # score moved and we never noticed: a silent miss, log it
                self.log_inference({
                    "market_ticker": self.ticker, "session_id": None,
                    "inferred_state": self.state_key, "inferred_at": ts,
                    "confirmed_state": confirmed_key, "confirmed_at": ts,
                    "lead_time_seconds": 0.0, "hit": False,
                    "detail": {"missed_boundary": True},
                })

        # snap
        if confirmed_key != self.state_key:
            conflict = conflict or bool(matched is None and self.state_key != prev_confirmed)
        if confirmed_key == "final":
            self.final = True
        else:
            self.sets_a, self.sets_b = sets_a, sets_b
            self.final = False
        self.confidence = 1.0
        self.last_confirmed = confirmed_key
        self.anchor_at = ts if changed else self.anchor_at
        self.persist(self.snapshot())
        if conflict:
            log.warning("score conflict — killing pending advisories",
                        ticker=self.ticker, confirmed=confirmed_key)
            self.on_conflict(self.ticker)
        return ReconcileResult(changed, conflict, confirmed_key)

    def _log(self, inf: Inference, confirmed_key: str, ts: datetime, hit: bool) -> None:
        self.log_inference({
            "market_ticker": self.ticker, "session_id": None,
            "inferred_state": inf.state_key, "inferred_at": inf.inferred_at,
            "confirmed_state": confirmed_key, "confirmed_at": ts,
            "lead_time_seconds": (ts - inf.inferred_at).total_seconds(),
            "hit": hit, "detail": inf.detail,
        })
