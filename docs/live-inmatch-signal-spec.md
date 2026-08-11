# Spec: Live In-Match Serve/Break Signals (#13, v1)

Status: **proposed** · Depends on: nothing hard (data already flows) · Related: fatigue
spec (same conditioning-layer pattern), #22 (charting shot data, for the historical half)

## 1. Motivation (why this is now the top lever)

The segmented calibration backtest (2026, n=26,419 walk-forward) showed the model is
**already well-calibrated** — every probability bucket within ±1.4pts of observed. Combined
with negative CLV, that means the losses are **adverse selection, not model error**: the
market is at least as calibrated and *sharper on the matches where it disagrees*, because it
prices information the model can't see.

You cannot out-*math* a calibrated market pre-match. The one realistic edge is an **in-play
information/speed edge**: during a match, a slow-moving (especially ITF/Challenger) market
reprices *behind* the actual play, while the model already knows the set state. Live serve/
break dynamics are the richest in-play signal the analysts keep citing (aces, double faults,
serve holds, break-point pressure, set-2 momentum) and the one input we currently **collect
but never use for a decision**.

## 2. What already exists (do NOT rebuild)

- The worker polls Kalshi's `live_data(milestone_id)` and stores per-poll detail into
  `match_score_log.detail` (JSONB) — a *time series* of in-match stats per match.
- `bot/web.py` `match_detail` already renders a **"Live match stats"** table from that detail:
  `competitor{1,2}_statistics` → **aces, double_faults, breakpoints_won,
  first_serve_points_won, games_won, points_won**. It maps competitor1/2 → our YES/sibling
  via the recorded set score (`yes_is_c1`), and **skips when that mapping is ambiguous**.
- The estimator (`scoreboard.py`/`estimator.py`) already infers discrete set state (fed to the
  model as `MatchState`).

So the data pipeline and the display exist. The gap is entirely: **nothing consumes these
stats for the read, the probability, or a bet.**

## 3. The gaps to close

1. **Display-only.** Serve stats appear on the match page but never touch the narrative,
   salience, or any decision.
2. **No in-play conditioning.** The model conditions on *set score* only. A player winning 70%
   of serve points / up 2 breaks / with a clean hold streak is materially ahead of what the
   set score alone implies — the model ignores it.
3. **No in-match conditionals in the read.** The analysts' "wins 78% when winning set 2",
   "84% with 2+ aces", "still holds with 4 DFs" — none of these are computed or surfaced.

## 4. Proposed feature: a `LiveServeBlock` + a bounded in-play adjustment

### 4.1 `LiveServeBlock` (derived from the latest `match_score_log.detail`)

Per side (watch / opponent), from the current in-match detail:

```
@dataclass
class LiveServeBlock:
    aces: int
    double_faults: int
    first_serve_won_pct: float | None     # first_serve_points_won / first_serve_points
    break_pts_won: int
    games_won: int
    serve_dominance: float                # normalized (see 4.2), −1..+1 (watch vs opp)
    mapped: bool                          # False if competitor↔YES mapping was ambiguous
    source_ts: datetime                   # freshness of the snapshot
```

Built by a `live_serve_block(detail, yes_is_c1)` helper reusing the exact mapping logic already
in `match_detail` (competitor1/2 → YES/sibling via set score; return `mapped=False` on a tie/
ambiguous score so a mirror-flip never poisons the signal — same discipline as the mapping-fix
work).

### 4.2 `serve_dominance` (the single number others consume, −1..+1)

A bounded blend of the serve/return differentials, watch minus opponent:

```
d_serve  = clamp((watch.first_serve_won_pct − opp.first_serve_won_pct) / 0.20, −1, 1)
d_breaks = clamp((watch.break_pts_won − opp.break_pts_won) / 3, −1, 1)
d_wobble = clamp(((opp.double_faults − watch.double_faults)) / 4, −1, 1)   # opp shakier ⇒ +
serve_dominance = clamp(0.5*d_serve + 0.35*d_breaks + 0.15*d_wobble, −1, 1)
```

(Constants are hand-set starting points and become the tuning surface. `first_serve_won_pct`
is the strongest single term — it *is* whether they're holding.)

## 5. Where it plugs in (three layers, mirrors the fatigue design)

**Layer A — narrative (new).** "Ghirardato is winning 71% of first-serve points and has
broken twice; the set score understates her control." Falls out of the block.

**Layer B — salience.** A live scenario where serve dominance agrees with the model pick is
more actionable → bump; where it contradicts → damp / flag.

**Layer C — decision (the real change), bounded, IN-PLAY only.** A ±N-pt conditioning of the
live (state-conditioned) probability at the advisory/engine boundary — **not** in the
calibrated Elo core (CLAUDE.md rule 2 is preserved: serve stats are discrete match state, not
price, and they enter as a documented conditioning layer, like `condition_on_state` and the
fatigue adjust):

```
p_live' = clamp(p_live + LIVE_K * serve_dominance, ...)   # LIVE_K ≈ 0.05, and only when mapped
```

Capped so it can nudge/size but not flip a pick on its own, and **only when `mapped` is True
and the snapshot is fresh** (stale/ambiguous detail contributes nothing).

## 6. The two halves — live state vs historical conditionals

- **6a. Live in-match state (this spec's core).** Derivable *now* from `live_data` — no new
  data needed. Serve dominance right now.
- **6b. Per-player in-match conditionals** ("this player wins 78% after taking set 2", "84%
  with 2+ aces"). These need *historical* per-match in-match features. Set-outcome conditionals
  (won-set-2 → match) are computable from our set history (`MatchSet`) and are cheap. Ace/DF
  conditionals need per-match serve counts — only the **Match Charting** subset has them
  (#22), so ship those as "[thin sample]"-flagged where coverage exists, omit otherwise
  (invariant #4). Split 6b into: set-outcome conditionals (do now) vs shot-level (charting-gated).

## 7. Trading thesis + honest risk

The edge requires the market to reprice **slower** than the model reacts to live play. That's
plausible on **ITF/Challenger** (thin, retail, laggy books) and unlikely on ATP/WTA (sharp,
fast in-play markets). So the realistic target is the lower tiers — and the backtest already
showed those are where the volume is. **Risk:** if the market reprices instantly, there's no
edge and this becomes a nicer display, not a profit source. That's exactly why it ships behind
a measured bot, not as a live model change.

## 8. Validation (before it touches decisions)

1. **`liveadj` bot** — a live bot that bets the serve-dominance-adjusted probability, A/B'd
   against `live`/`chalk` on CLV, identical to how `freshadj` is measured. If it doesn't beat
   the base live bot *and* clear chalk, the in-play signal isn't tradeable.
2. **Replay check** — `market/replay.py` can replay a recorded session; verify the adjustment
   would have moved toward the eventual winner on stored `detail` snapshots (sanity, not proof).
3. **Guardrails:** ±5pt cap; never flips a pick; contributes only when `mapped` and fresh;
   log the mapping-skip rate so silent coverage gaps are visible.

## 9. Phasing & effort

- **Phase 1 (~1 day):** `LiveServeBlock` + `serve_dominance` from `live_data.detail` (reuse the
  match-page mapping); Layers A & B (narrative + salience). Pure display/ranking — zero risk.
- **Phase 2 (~1 day):** Layer C bounded in-play adjustment + the `liveadj` A/B bot + replay
  check. This is where it becomes a measured edge test.
- **Phase 3:** the historical conditionals (6b) — set-outcome first (cheap), shot-level gated on
  charting coverage (#22).

## 10. Open questions

- Live-detail **coverage** by tier — does `live_data` return `competitor_statistics` for ITF/
  Challenger, or mostly ATP/WTA? (If serve stats only exist where the market is sharp, the
  tradeable edge shrinks — measure coverage first.)
- Polling cadence vs market repricing speed — how stale is our latest snapshot when a line moves?
- Mapping ambiguity rate on the live detail (how often `mapped=False`).
