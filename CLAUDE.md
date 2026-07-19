# Tennis Advisory Bot — Project Rules

## Non-negotiable constraints (never violate, never "temporarily" relax)
1. ADVISORY ONLY. Never call any Kalshi trading/order endpoint. Never scaffold order
   execution code, even disabled. Read-only market access.
2. NO CIRCULARITY. The probability engine must not receive market price as input —
   structurally: price must not appear in its function signature or reachable state.
   Market data may only inform discrete match state. A test must assert this isolation.
3. NEVER FABRICATE A STATISTIC. Every number in an advisory must exist in the
   structured fact block that generated it. The numeric validator enforces this;
   do not weaken or bypass it.
4. Stats below minimum sample size are handled by the fallback hierarchy
   (widen window → common opponents → omit), never computed on thin samples.
5. PROBATION GATING. Advisories from unconfirmed inferred state are labeled
   [PROBATION] until the graduation thresholds in config are met and probation is
   manually disabled. Never remove the label logic or auto-graduate.

## Definitions (tennis vocabulary — do not guess)
- "match" = a full match. In advisory prose, "games" colloquially means matches
  (e.g. "won 7 of her last 9 games" = 7 of last 9 MATCHES).
- "game" in the stats engine = an actual tennis game within a set. Never conflate.
- "deciding set" / "set 3" = set 3 in best-of-3, set 5 in best-of-5.
- "skunk" = a straight-sets win (2-0 or 3-0).
- "executable price" = the ask on the side you would buy, never midpoint.

## Stack
- Python 3.11+, PostgreSQL (Railway), httpx, websockets, rapidfuzz, pydantic,
  SQLAlchemy + Alembic migrations
- Deployed on Railway: `worker` service (watch loop) + Railway cron (daily ingest)
- Local dev: docker-compose Postgres, `.env`; production config via Railway env vars
- All timestamps UTC in storage, US/Pacific in display
- Logs to stdout (structured JSON) — Railway captures them. Advisories also push to
  Discord webhook (env: DISCORD_WEBHOOK_URL).

## Workflow
- Work one phase at a time per PLAN.md. Do not start phase N+1 until phase N's DONE
  check passes. Commit at each phase boundary with a descriptive message.
- If context runs long, update PLAN.md with current state so a fresh session resumes.
- Before writing any Kalshi client code: fetch and read current docs at
  docs.kalshi.com (auth scheme, market/orderbook/websocket endpoints, tennis ticker
  conventions). Verify auth with one authenticated GET before building further.
- Before writing the Sackmann ingest: read matches_data_dictionary.txt from the
  tennis_atp repo. Do not assume column layouts.
