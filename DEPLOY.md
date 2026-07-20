# DEPLOY — Railway runbook

The repo deploys from GitHub (`johns-dotcom/tennisbot`, branch `main`) to Railway
as three pieces sharing one Postgres.

**Production actuals (2026-07-19):** project `tennisbot` (400a8a62), dashboard at
https://tennisbot-production-1d98.up.railway.app (WEB_TOKEN gate; token in local
`.env.railway.notes`). The web service is named `tennisbot` (created via GitHub
connect) with SERVICE_ROLE=web; `worker` and `ingest` deploy via `railway up`
until their GitHub sources are attached in the dashboard. Ingest cron:
`30 9 * * *`. Advisory delivery = dashboard + DB + logs (Discord removed;
ANTHROPIC_API_KEY unset → template prose by user decision).

| Service | Start command | Purpose |
|---|---|---|
| `web` | `alembic upgrade head && python -m bot web` | dashboard UI — the advisory delivery surface (health: `/healthz`) |
| `worker` | `python -m bot watch` | live loop: discovery, websocket, estimator, advisory engine (health: `/health` on $PORT) |
| cron on `worker` image | `python -m bot ingest` | daily: Sackmann incremental + live-results sync + stats cache refresh |

Migrations run in the `web` start command (release-style step) — `worker` assumes
the schema is current.

## One-time setup

1. **Postgres**: Railway → New → Database → PostgreSQL. Note: every service below
   needs `DATABASE_URL` — use a reference variable
   `${{Postgres.DATABASE_URL}}` and *replace the scheme* via
   `DATABASE_URL=postgresql+psycopg://...` (SQLAlchemy needs the `+psycopg`
   driver suffix; Railway's default is plain `postgresql://`). Easiest: set
   `DATABASE_URL` manually per service from the Postgres connection string with
   the scheme edited.
2. **web service**: New → GitHub Repo → `johns-dotcom/tennisbot`.
   - Settings → Deploy → Custom start command: `alembic upgrade head && python -m bot web`
   - Settings → Networking → Generate domain (this is the dashboard URL).
   - Builds with Railpack: `pyproject.toml` + `uv.lock` + `.python-version` are
     detected automatically; no Dockerfile needed.
3. **worker service**: New → GitHub Repo → same repo, second service.
   - Custom start command: `python -m bot watch`
   - No public domain needed; Railway health check path `/health`.
4. **cron**: New → GitHub Repo → same repo, third service.
   - Custom start command: `python -m bot ingest`
   - Settings → Cron Schedule: `30 9 * * *` (09:30 UTC daily, after overnight
     matches settle).
5. **Environment variables** (per service; web needs only DATABASE_URL + WEB_TOKEN):

   | Var | Who | Notes |
   |---|---|---|
   | `DATABASE_URL` | all | `postgresql+psycopg://…` (see step 1) |
   | `KALSHI_API_KEY_ID` | worker | Kalshi API key id |
   | `KALSHI_PRIVATE_KEY_B64` | worker | RSA private key PEM, base64 one line: `base64 -i key.pem \| tr -d '\n'` |
   | `API_TENNIS_KEY` | cron/worker | api-tennis.com key (Phase 5.5; ingest skips live sync if unset) |
   | `ANTHROPIC_API_KEY` | worker | optional — narrative prose; template fallback if unset |
   | `WEB_TOKEN` | web | optional — if set, dashboard requires `?token=…` once (cookie after) |

   Never commit any of these. `.env` is gitignored.

## Restart protocol (worker restarts on every deploy — routine)

- SIGTERM: advisory generation stops, recorder flushes, websocket closes, exit
  (15s watchdog force-cancels stragglers).
- Boot: `live_match_state` reloads per market. Gap < 60s → resume with existing
  confidence. Gap ≥ 60s → state STALE: confidence 0, pending advisories killed,
  no advising until the delayed Kalshi score re-confirms. Matches that ended
  during downtime close out on their next score/lifecycle event.
- Websocket gaps ≥ 30s during a live match are treated identically (feed_gaps
  rows are written on every reconnect).

## Reading inference-report

`python -m bot inference-report` (or the dashboard's Estimator tab):

- **hit rate** — share of inferred set-boundaries the delayed score confirmed.
- **false-boundary rate** — inferences contradicted or never confirmed; the
  detector's noise level.
- **avg lead** — seconds the estimator beat the delayed score; the product's
  entire edge window. 60–180s is the expected band.
- **clean vs gap sessions** — accuracy is reported separately for sessions with
  feed gaps; only clean sessions count toward graduation.
- **gap time per day** — infrastructure health; rising gap time means the
  websocket is flapping.

## Probation graduation procedure

1. Run in probation (default) until `state_inference_log` accumulates real
   sessions.
2. `python -m bot graduate` — requires ALL of (config-adjustable):
   ≥ 200 confirmed transitions, ≥ 90% hit rate on clean sessions,
   ≤ 5% false-boundary rate.
3. Only after a passing run: set `probation: false` via env
   (`PROBATION=false`) or config — a deliberate manual change. The bot never
   graduates itself.
4. Post-graduation, if the trailing-30d hit rate drops below threshold the
   worker logs a loud warning on every unconfirmed advisory until it recovers.

## Local dev

```bash
uv sync
cp .env.example .env       # fill in
docker compose up -d       # local Postgres (or point DATABASE_URL at Neon)
alembic upgrade head
python -m bot ingest --skip-live
python -m bot web          # http://localhost:8080
python -m bot watch
```
