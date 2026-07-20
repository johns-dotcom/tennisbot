# PLAN — Tennis Live-Betting Advisory Bot (Kalshi)

Advisory-only system: player database + stat profiles ("play scripts") from Sackmann
historical data and api-tennis.com recent results, live Kalshi market monitoring with
odds-implied match-state inference, own-data win probabilities, narrative advisories
to Discord. Never trades. All rules in CLAUDE.md bind every phase.

**Live results source ({{LIVE_RESULTS_SOURCE}} resolved): api-tennis.com**
(env: `API_TENNIS_KEY`).

## Module layout

```
tennisbot/
├── CLAUDE.md                  # binding project rules
├── PLAN.md                    # this file — updated with state at each phase boundary
├── DEPLOY.md                  # Phase 6: Railway runbook
├── pyproject.toml             # uv-managed; python >=3.11
├── docker-compose.yml         # local Postgres (Neon used while Docker unavailable)
├── alembic.ini
├── migrations/                # Alembic
├── .env.example
├── bot/
│   ├── __main__.py            # CLI: ingest | watch | profile | backtest | replay |
│   │                          #      inference-report | graduate
│   ├── config.py              # pydantic-settings; thresholds, sample-size minimums,
│   │                          #   detector X/Y, probation flag & graduation thresholds
│   ├── db.py                  # engine/session helpers
│   ├── models.py              # SQLAlchemy models (all tables below)
│   ├── log.py                 # structured JSON logging to stdout
│   ├── sources/
│   │   ├── base.py            # TennisDataSource ABC
│   │   ├── sackmann.py        # SackmannDataSource — GitHub CSV backfill + incremental
│   │   └── api_tennis.py      # ApiTennisSource — daily results gap-fill + 48h schedule
│   ├── ingest/
│   │   ├── score_parser.py    # "6-4 3-6 7-5" → set rows; RET / W.O. / DEF
│   │   └── pipeline.py        # orchestrates sources → upsert → dedup → stats refresh
│   ├── matching/
│   │   └── market_matcher.py  # normalize + rapidfuzz + manual overrides + review queue
│   ├── stats/
│   │   ├── profile.py         # PlayerProfile: form, deciding-set, matchup, trajectory
│   │   │                      #   — every function takes as_of
│   │   └── fallback.py        # min-sample → widen window → common-opponent → omit
│   ├── prob/
│   │   ├── model.py           # WinProbabilityModel interface (predict → (p_a, conf))
│   │   ├── elo.py             # surface-adjusted set-level Elo + logistic state adj.
│   │   └── backtest.py        # walk-forward; Brier + calibration by bucket
│   ├── market/
│   │   ├── kalshi.py          # READ-ONLY client: RSA auth, discovery, WS + REST fallback
│   │   ├── recorder.py        # every tick/trade/score → market_ticks (session_id)
│   │   ├── estimator.py       # rule-based set-state estimator; persists live_match_state
│   │   └── replay.py          # feed recorded session back through estimator
│   ├── advisory/
│   │   ├── facts.py           # deterministic salience-ranked fact block
│   │   ├── render.py          # Anthropic API prose rendering
│   │   ├── validate.py        # numeric validator (regex-extract, assert ∈ fact block)
│   │   ├── template.py        # plain-template fallback rendering
│   │   └── discord.py         # webhook embed push
│   ├── engine.py              # edge detection, gating, debounce, probation labeling
│   ├── watch.py               # live loop + aiohttp health endpoint + SIGTERM protocol
│   └── reports.py             # inference-report, graduate
└── tests/                     # incl. score parser, isolation test, restart test
```

## Tables

`players`, `tournaments`, `matches`, `match_sets` (one row per set — non-negotiable),
`player_stats_cache`, `kalshi_markets`, `advisories` (full audit incl. probation flag),
`state_inference_log`, `market_ticks`, `live_match_state`, `feed_gaps`,
`player_aliases` (manual override table), `match_review_queue` (unmatched names).
`source` column + cross-source dedup on `matches`.

## Phases & DONE checks

### Phase 1 — Data layer  [DONE — live-source (api-tennis) verification deferred to
### a new Phase 5.5 by user decision 2026-07-19; adapter is written, needs key + audit]
Repo scaffold; schema + Alembic migration; score parser; SackmannDataSource
(atp/wta main draw + qual/chall + futures + wta qual/ITF, 3+ yr backfill,
incremental upsert); ApiTennisSource (completed results newer than Sackmann,
48h schedule — no in-play); market_matcher.
Prereq honored: read `matches_data_dictionary.txt` from tennis_atp before ingest code.
**DONE when:** `python -m bot ingest` completes; spot-check SELECTs match known real
results; score parser tests pass incl. RET/W.O./DEF.

### Phase 2 — Stats engine  [DONE 2026-07-19]
PlayerProfile with as_of everywhere; form / deciding-set / matchup / trajectory
metrics; config-driven fallback hierarchy; player_stats_cache.
**DONE check passed:** `python -m bot profile` verified against independent raw-CSV
computation (scripts/verify_profiles.py) for Jannik Sinner (ATP), Aryna Sabalenka
(WTA), Katarina Kuzmova (ITF) — career/365d records, career decider records, skunk
shares all exact matches (12/12). Semantics: as_of strict (match_date < as_of); W.O.
excluded from all stats; RET/DEF count; round-order sequencing within tourney week.

### Phase 3 — Probability engine  [DONE 2026-07-19]
WinProbabilityModel interface; surface-adjusted set-level Elo baseline + logistic
in-play state adjustment; circularity isolation test (price structurally unreachable).
**DONE check passed:** backtest 2025-01-01→2026-06-02: 69,827 matches scored,
Brier 0.2035, log loss 0.5914, calibration gaps ≤ ±0.015 across all buckets
(after PLATT_A=1.65 pre-match sharpening fitted on 2023-24, n=120k, held out from
eval). Isolation enforced by 4 structural AST tests (tests/test_isolation.py).

### Phase 3.5 — Market recorder + state estimator  [DONE 2026-07-19]
Recorder FIRST (all ticks/trades/score updates → market_ticks with session_id);
`replay <session>`; rule-based estimator (sets-won state space only; volume-confirmed
discontinuity detector, asymmetric fav/dog signatures; set-duration transition priors;
snap-to-score reconciliation → state_inference_log; persist to live_match_state after
every transition — no memory-only state).
**DONE check passed:** synthetic Bo3 session recorded via MarketRecorder, replayed
via `python -m bot replay` → 3/3 boundaries inferred ahead of delayed score
(avg lead 116s, 0 false boundaries); `inference-report` renders hit rate / lead /
false-boundary rate with clean-vs-gap split. 14 estimator unit tests cover noise
rejection (quote-only, degraded, too-early), asymmetric thresholds, conflict
kill+snap, missed-boundary logging, quarantine, restore. `graduate` also
implemented (reports thresholds; never flips the flag). Known v1 limits: anchor
from first trade (pre-match trading weakens set-1 gate), minutes/sets duration
approximation.

### Phase 4 — Kalshi integration (READ ONLY)
Fetch docs.kalshi.com first; RSA env-var auth verified with one GET; scheduled market
discovery → market_matcher; WS subscriptions; REST fallback flagged `degraded`
(never sole trigger for boundary detection); top-of-book both sides + volume;
edge vs executable price.
**DONE when:** live markets discovered, matched, streaming into market_ticks.

### Phase 5 — Edge detection + advisories
Gates (ALL): edge ≥ 6% default, model confidence ≥ min, volume ≥ floor, state
confidence ≥ 85% or score-confirmed; hold/release/kill pending on score; debounce per
meaningful state change; kill on retirement/suspension. Two-stage generation
(deterministic fact block → Anthropic prose) + numeric validator (retry once → plain
template). Discord embed + advisories audit row. Probation mode default ON;
`graduate` reports thresholds; flip is manual only.
**DONE when:** end-to-end dry run on one live market → validated, correctly labeled
advisory in Discord.

### Phase 5.5 — Live-results source activation (moved from Phase 1 by user decision)
User signs up for api-tennis.com trial → API_TENNIS_KEY in .env → run live sync →
coverage audit: sync the May–June 2026 Sackmann-overlap window, measure ITF coverage
and name-match rate vs known-good rows. Fallback if ITF women's coverage is thin:
Goalserve adapter behind the same TennisDataSource interface.
**DONE when:** `python -m bot ingest` (no --skip-live) completes clean; audit report
acceptable; schedule rows landing for next 48h.

### Phase 6 — Railway deployment
GitHub → Railway; worker (`watch` + health endpoint on $PORT) + daily ingest cron;
Alembic on deploy; secrets via env; restart protocol (SIGTERM flush; boot reload of
live_match_state, <60s resume / ≥60s STALE quarantine); restart integration test;
feed_gaps auditing (≥30s gap = stale treatment); inference-report clean-vs-gap split;
DEPLOY.md.
**DONE when:** deployed; restart test passes; manual worker kill mid-match shows
quarantine in logs; one real [PROBATION] advisory in Discord from production.

## Environment notes (for session resume)
- Machine has no Docker/Homebrew; Python via uv (3.12.13), `~/.local/bin/uv`.
- Local dev DB: Neon Postgres (project TBD) in `.env` DATABASE_URL;
  docker-compose.yml kept for when Docker exists. Production: Railway Postgres.
- api-tennis.com key: env `API_TENNIS_KEY` (user to supply before Phase 1 live-sync
  testing).

## Current state (2026-07-19)
- Phase 1 built and committed (354b8a8). Sackmann backfill complete on Neon
  (project morning-snow-90827624): 284,360 matches (ATP 139,770 / WTA 144,590),
  ~672k set rows, 2022-01 → 2026-06 (ATP) / 2026-04 (WTA), zero ingest errors,
  idempotent re-run verified (27 files skipped via blob-SHA watermarks).
- Spot-checks passed: Sinner–Zverev AO25 F, Alcaraz–Djokovic Wimbledon24 F,
  Keys–Sabalenka AO25 F, Krejcikova–Paolini Wimbledon24 F, Sabalenka–Pegula USO24 F;
  set rows + RET/W.O./DEF semantics verified in DB. 19 tests green.
- NOTE: canonical JeffSackmann repos went private/deleted mid-2026. Using mirrors
  Kadantte/tennis_atp + VictorSquidWei/tennis_wta (config: sackmann_*_repo).
- BLOCKED on user: API_TENNIS_KEY needed to exercise ApiTennisSource (adapter
  written, untested against live API). Then Phase 1 DONE check fully passes.
- Phase 2 (stats engine) DONE 2026-07-19; cache refresh wired into ingest.
- Phase 3 (probability engine) DONE 2026-07-19.
- Phase 3.5 DONE. Phase 4 DONE 2026-07-19 (Kalshi creds in .env; WS streaming verified live). Phase 5 (edge detection + advisories) next; needs DISCORD_WEBHOOK_URL + ANTHROPIC_API_KEY for the dry run. GitHub remote configured (github.com-tennisbot alias); push pending user adding deploy key.
