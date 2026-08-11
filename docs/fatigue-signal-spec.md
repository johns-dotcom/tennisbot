# Spec: Fatigue / Freshness Signal (v1)

Status: **proposed** · Owner: TBD · Depends on: #20 (dob backfill, for the age amplifier)
Related: #8 (model upgrade — this stays OUT of core Elo for now), #13 (live in-match stats)

## 1. Motivation

Two independent analyst reads (Kokkinakis/Walton → staleness; **Chavez/Sema** → same-day
fatigue on a 37-yo) landed on the same blind spot: the model reasons about *ability* but not
about *how tired a player is right now*. In the Sema case the analyst's entire edge was
"second match today, age 37, collapses in deciders" — a read the model could not act on.

Tennis fatigue is real and **most decisive exactly where our bots bet: the deciding set.**
An edge, if it exists, is likeliest to live here.

## 2. What already exists (do NOT rebuild)

`bot/scenarios.py` + `bot/stats/profile.py` already carry a partial fatigue read:

- `_yesterday_played(db, now)` → per-player `{played, played_today, went_distance}` from the
  Kalshi market `occurrence_datetime` (8h = today, 8–40h = yesterday).
- gameflow **section 4**: narrates opponent fatigue ("played earlier today and went the
  distance — fatigue is live in the later sets"), bumps **salience**, caveats if the watch
  side also played today.
- **4b / 4b-ii**: age-decline ("34 and fading") and youth-vs-age stamina reads.
- **4c**: `layoff.deciders_last_3d` / `deciders_last_30d` — acute decider load.

So narrative + ranking already respond to fatigue. The gaps below are why it's not enough.

## 3. Why it failed on Chavez/Sema (the gaps to close)

1. **Gated behind scenario generation.** No scenario was generated for Sema vs Chavez
   (thin data on the 20-yo → `pred.confidence < 0.3` → skipped), so *none* of the fatigue
   logic ran. Fatigue must be computable independent of whether a full scenario is built.
2. **Timing source is brittle.** `occurrence_datetime` is the *scheduled* start, which drifts
   by hours for ITF (same root cause as the live-board bug). "Played today" via a fixed 8h
   window on a drifting timestamp is unreliable and misses matches spaced >8h apart.
3. **It's cosmetic.** Fatigue changes *narrative* and *salience* only — never the
   probability, the bet decision, or the stake. It cannot currently make a bot fade or
   downsize a tired favorite.
4. **Load is coarse.** "Went the distance" is a boolean. A 7-6 7-6 (26 games) is far heavier
   than 6-1 6-1 (14 games); a 3-setter that was 7-5 6-7 7-6 is brutal. Games ≫ sets as a load
   proxy.
5. **Age amplifier is decoupled** from fatigue (a separate "≥34 and fading" rule), when the
   real interaction is *fatigue hurts older players more* (slower recovery, shorter gas tank).

## 4. Proposed feature: a first-class `FatigueBlock`

Formalize what's scattered today into one computed block per player, in `bot/stats/profile.py`.

```
@dataclass
class FatigueBlock:
    hours_since_last_end: float | None   # wall-clock since last match ENDED (best source)
    matches_today: int                   # completed matches in the local tournament day
    games_last_24h: int                  # cumulative games played, last 24h (load proxy)
    sets_last_24h: int
    went_distance_last: bool             # last match reached its decider
    deciders_last_72h: int               # (exists today as deciders_last_3d)
    short_turnaround: bool               # hours_since_last_end < SAME_DAY_HRS
    fatigue_score: float                 # 0..1, the single number others consume
    source: str                          # which timing source was used (for honesty)
```

### 4.1 Data sources for "last match ended", ranked (use best available)

| Rank | Source | Wall-clock accuracy | Notes |
|---|---|---|---|
| 1 | `match_score_log` latest `is_final=true` ts | **real** finish time | only for matches we tracked live |
| 2 | api-tennis `event_date`+`event_time` (`_event_dt`) | start only (~+90m for end) | good for existence + rough timing |
| 3 | Kalshi `occurrence_datetime` | scheduled start, **drifts** | current source; weakest |
| 4 | `matches.match_date` | day granularity | existence-only fallback |

Prefer **(1)** for `hours_since_last_end`; fall back down the list, and record `source` so the
narrative can hedge ("~" when timing is approximate). `matches_today` and `games_last_24h`
come from the `matches` table (day window in the tournament's local tz) joined with score
data — independent of scenario generation, closing gap #1.

### 4.2 `fatigue_score` (0..1)

A bounded blend, tuned later against outcomes:

```
turnaround = clamp01((SHORT_TURNAROUND_HRS - hours_since_last_end) / SHORT_TURNAROUND_HRS)
load       = clamp01(games_last_24h / HEAVY_GAMES_24H)            # e.g. HEAVY=30
decider    = 0.5*went_distance_last + 0.5*min(1, deciders_last_72h)
score      = clamp01(0.5*turnaround + 0.3*load + 0.2*decider)
```

Constants (`SAME_DAY_HRS=12`, `SHORT_TURNAROUND_HRS=24`, `HEAVY_GAMES_24H=30`) start as
hand-set and become the tuning surface.

### 4.3 Age amplifier (needs dob — gated on #20)

Older players carry fatigue worse. Multiply the *effect* (not the raw score) by an age factor,
neutral when dob is unknown so we never fabricate:

```
age_mult = 1.0                     if age is None or age < 30
         = 1 + 0.06*(age - 30)     capped at ~1.6 (a 40-yo ≈ 1.6×)
```

## 5. Where it plugs in (three layers, increasing commitment)

**Layer A — narrative (exists, upgrade).** Replace the boolean "went the distance" with the
richer read: "Sema is on her 2nd match today (43 games in the last 20h) and is 37 — fatigue is
acute in a decider." Falls out of the block for free.

**Layer B — salience (exists, re-point).** Salience already nudges on fatigue; re-point it at
`fatigue_score * age_mult` (opponent) minus watch-side fatigue, so the ranking reflects the
graded score, not a flag.

**Layer C — decision (NEW, the real change).** A **bounded, decider-context** adjustment to the
favored side's probability at the scenario/advisory layer — NOT in the core Elo:

```
delta   = (opp_fatigue - watch_fatigue) * age-adjusted            # in [-1, 1]
p_dec'  = clamp(p_dec - FATIGUE_K * delta, ...)                   # FATIGUE_K ≈ 0.04 (max ±4pts)
```

Applied only to the *decider* probability (fatigue barely matters at 0-0), bounded to ±4pts so
it can down-weight / down-size a tired favorite or thin a bet's edge, but **cannot flip a
pick** on its own (same discipline we set after Kokkinakis). Logged in the fact block so the
numeric validator (rule 3) still sees every number.

## 6. Rule-2 / architecture note

Fatigue is **discrete match context** (matches played, games, timing) — not market price — so
it is permissible under CLAUDE.md rule 2. Deliberately keep it **out of `bot/prob/elo.py`**:
the calibrated Elo core stays pure and testable, and fatigue is a documented conditioning layer
(like `condition_on_state`) at the advisory/scenario boundary. This also lets us A/B it cleanly.

## 7. Validation (before it touches live bets)

1. **Backtest slice.** On matches where one side had `short_turnaround`, does the
   fatigue-adjusted decider prob beat the unadjusted one on Brier / log-loss? (walk-forward,
   `bot/prob/backtest.py` harness, outcomes only — rule 2 safe).
2. **Bot A/B.** Add a `freshadj` live-bot pair (fatigue-on) beside `live` (fatigue-off) and
   compare record + CLV on their shared matches — the framework already supports this.
3. **Guardrails:** ±4pt cap; never flips a pick; age amplifier only with known dob; log
   `source` so approximate-timing reads are visibly hedged.

## 8. Phasing & effort

- **Phase 1 (½–1 day):** `FatigueBlock` in profile with the reliable `match_score_log`/matches
  end-time source + games-based load; wire Layers A & B (mostly re-pointing existing code).
  Decouples fatigue from scenario generation (fixes the Sema gap #1).
- **Phase 2 (1 day):** Layer C bounded prob adjustment + the backtest slice + `freshadj` A/B bot.
- **Phase 3 (gated on #20):** age amplifier once dob coverage is broad enough to matter.

## 9. Open questions

- Local tournament tz for "matches_today" — derive from tournament, or approximate from
  match timestamps? (affects the day boundary.)
- Doubles / walkover prior matches — exclude from load (a walkover isn't fatigue).
- Is `games_last_24h` reliably reconstructable for source-2/3 matches (no game-level data)?
  If not, fall back to sets for those.
