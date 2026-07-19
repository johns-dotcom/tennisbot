# tennisbot

Advisory-only tennis live-betting analysis system for Kalshi markets. Maintains
ATP/WTA/ITF player statistical profiles, monitors live Kalshi tennis markets,
infers in-play match state from odds movement, and pushes narrative betting
advisories to Discord. **It advises only — it never trades.**

See `CLAUDE.md` for binding project rules, `PLAN.md` for architecture and phase
status, and `DEPLOY.md` (Phase 6) for the Railway runbook.

## Data attribution

Historical match data © Jeff Sackmann / Tennis Abstract
(originally published at github.com/JeffSackmann/tennis_atp and tennis_wta),
licensed under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).
Used here for personal, non-commercial research only. The data is never
redistributed with or from this project (raw CSVs are excluded via .gitignore,
and the database is private).

Recent results and schedules via [api-tennis.com](https://api-tennis.com).

## Quick start

```bash
uv sync
cp .env.example .env   # fill in DATABASE_URL and keys
alembic upgrade head
python -m bot ingest   # Sackmann backfill + api-tennis sync
```

CLI: `ingest | watch | profile "Name" | backtest --from --to | replay <session> | inference-report | graduate`
