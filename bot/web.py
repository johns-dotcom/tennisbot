"""Web UI: python -m bot web — the advisory delivery surface (no Discord).

Server-rendered aiohttp app: advisories feed, live market states, estimator
accountability, review queue. Storage is UTC; display is US/Pacific
(CLAUDE.md). Optional access token via WEB_TOKEN env (cookie after first ?token=).
"""
from __future__ import annotations

import html
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiohttp import web
from sqlalchemy import func, select

from bot.db import session as db_session
from bot.log import get_logger
from bot.models import (
    Advisory,
    FeedGap,
    KalshiMarket,
    LiveMatchState,
    MatchReviewQueue,
    Player,
    StateInferenceLog,
)

log = get_logger("web")
PACIFIC = ZoneInfo("America/Los_Angeles")

CSS = """
:root { color-scheme: light;
  --surface: #fcfcfb; --card: #ffffff; --border: #e4e3df;
  --ink: #0b0b0b; --ink-2: #52514e; --ink-3: #8a8984;
  --good: #0ca30c; --warning: #fab219; --serious: #ec835a; --critical: #d03b3b;
  --accent: #2a78d6; }
@media (prefers-color-scheme: dark) { :root {
  color-scheme: dark;
  --surface: #131312; --card: #1a1a19; --border: #33322f;
  --ink: #ffffff; --ink-2: #c3c2b7; --ink-3: #8a8984;
  --accent: #3987e5; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface); color: var(--ink);
  font: 15px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 1080px; margin: 0 auto; padding: 24px 20px 60px; }
header { display: flex; align-items: baseline; gap: 16px; margin-bottom: 20px; }
h1 { font-size: 20px; margin: 0; }
h1 small { color: var(--ink-3); font-weight: 400; font-size: 13px; }
nav a { color: var(--ink-2); text-decoration: none; margin-right: 14px; font-size: 14px; }
nav a.active { color: var(--ink); font-weight: 600; border-bottom: 2px solid var(--accent); }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px; margin-bottom: 24px; }
.tile { background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 14px 16px; }
.tile .v { font-size: 26px; font-weight: 650; font-variant-numeric: tabular-nums; }
.tile .l { color: var(--ink-2); font-size: 12.5px; margin-top: 2px; }
.tile .s { color: var(--ink-3); font-size: 11.5px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 16px 18px; margin-bottom: 16px; }
.card h2 { font-size: 14px; margin: 0 0 10px; color: var(--ink-2);
  text-transform: uppercase; letter-spacing: .04em; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th { text-align: left; color: var(--ink-3); font-weight: 500; font-size: 12px;
  padding: 4px 10px 6px 0; border-bottom: 1px solid var(--border); }
td { padding: 7px 10px 7px 0; border-bottom: 1px solid var(--border);
  font-variant-numeric: tabular-nums; vertical-align: top; }
tr:last-child td { border-bottom: none; }
.chip { display: inline-flex; align-items: center; gap: 5px; border-radius: 999px;
  padding: 1px 9px; font-size: 12px; font-weight: 600; border: 1.5px solid; }
.chip.good { color: var(--good); border-color: var(--good); }
.chip.warning { color: var(--warning); border-color: var(--warning); }
.chip.serious { color: var(--serious); border-color: var(--serious); }
.chip.critical { color: var(--critical); border-color: var(--critical); }
.chip.muted { color: var(--ink-3); border-color: var(--border); }
.prose { color: var(--ink-2); font-size: 13.5px; margin-top: 6px; max-width: 70ch; }
.edge-pos { color: var(--good); font-weight: 650; }
.mono { font-family: ui-monospace, Menlo, monospace; font-size: 12.5px; }
.empty { color: var(--ink-3); padding: 18px 0; text-align: center; }
footer { color: var(--ink-3); font-size: 12px; margin-top: 28px; }
"""


def esc(v) -> str:
    return html.escape(str(v)) if v is not None else "—"


def pt(ts: datetime | None) -> str:
    if ts is None:
        return "—"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(PACIFIC).strftime("%b %d %I:%M:%S %p PT")


def chip(kind: str, icon: str, label: str) -> str:
    return f'<span class="chip {kind}">{icon} {esc(label)}</span>'


def state_chip(adv_or_state, confirmed: bool, probation: bool) -> str:
    out = []
    if confirmed:
        out.append(chip("good", "✓", "score-confirmed"))
    else:
        out.append(chip("warning", "≈", "inferred"))
    if probation:
        out.append(chip("serious", "⚠", "PROBATION"))
    return " ".join(out)


def page(title: str, active: str, body: str) -> str:
    navs = "".join(
        f'<a href="{href}" class="{"active" if key == active else ""}">{label}</a>'
        for href, key, label in (("/", "home", "Advisories"), ("/live", "live", "Live"),
                                 ("/report", "report", "Estimator"),
                                 ("/queue", "queue", "Review queue")))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>{esc(title)} · tennisbot</title><style>{CSS}</style></head>
<body><main>
<header><h1>tennisbot <small>advisory-only · never trades</small></h1><nav>{navs}</nav></header>
{body}
<footer>All times US/Pacific. Auto-refreshes every 30s. Data: Jeff Sackmann /
Tennis Abstract (CC BY-NC-SA 4.0) — personal research use.</footer>
</main></body></html>"""


# ---------------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------------


def _summary(db) -> dict:
    now = datetime.now(timezone.utc)
    day_start_pt = datetime.now(PACIFIC).replace(hour=0, minute=0, second=0,
                                                 microsecond=0).astimezone(timezone.utc)
    adv_today = db.execute(select(func.count(Advisory.id)).where(
        Advisory.created_at >= day_start_pt, Advisory.status == "sent")).scalar()
    watched = db.execute(select(func.count(KalshiMarket.id)).where(
        KalshiMarket.status.in_(["active", "open"]),
        KalshiMarket.player_a_id.is_not(None))).scalar()
    live = db.execute(select(func.count(LiveMatchState.market_ticker)).where(
        LiveMatchState.last_tick_at >= now - timedelta(minutes=10),
        LiveMatchState.state != "final")).scalar()
    logs = db.execute(select(StateInferenceLog).where(
        StateInferenceLog.confirmed_at >= now - timedelta(days=30),
        StateInferenceLog.session_had_gap.is_(False))).scalars().all()
    hits = sum(1 for r in logs if r.hit)
    leads = [r.lead_time_seconds for r in logs if r.hit and r.lead_time_seconds]
    pending = db.execute(select(func.count(Advisory.id)).where(
        Advisory.status == "pending")).scalar()
    return {
        "adv_today": adv_today, "watched": watched, "live": live,
        "hit_rate": (hits / len(logs)) if logs else None, "n_inf": len(logs),
        "avg_lead": (sum(leads) / len(leads)) if leads else None,
        "pending": pending,
    }


def tiles_html(s: dict) -> str:
    hr = f"{s['hit_rate']:.0%}" if s["hit_rate"] is not None else "—"
    lead = f"{s['avg_lead']:.0f}s" if s["avg_lead"] is not None else "—"
    return f"""<div class="tiles">
<div class="tile"><div class="v">{s['adv_today']}</div><div class="l">advisories today</div></div>
<div class="tile"><div class="v">{s['pending']}</div><div class="l">held pending score</div></div>
<div class="tile"><div class="v">{s['live']}</div><div class="l">matches live now</div></div>
<div class="tile"><div class="v">{s['watched']}</div><div class="l">markets watched</div></div>
<div class="tile"><div class="v">{hr}</div><div class="l">state hit rate</div>
  <div class="s">30d clean · n={s['n_inf']}</div></div>
<div class="tile"><div class="v">{lead}</div><div class="l">avg score lead</div></div>
</div>"""


# ---------------------------------------------------------------------------
# views
# ---------------------------------------------------------------------------


async def home(request: web.Request) -> web.Response:
    with db_session() as db:
        s = _summary(db)
        rows = db.execute(
            select(Advisory, Player.full_name)
            .join(Player, Player.id == Advisory.recommended_player_id, isouter=True)
            .order_by(Advisory.created_at.desc()).limit(40)
        ).all()
    items = []
    for adv, player in rows:
        status = {"sent": chip("good", "✓", "sent"),
                  "pending": chip("warning", "…", "pending"),
                  "killed": chip("critical", "✕", f"killed: {adv.kill_reason or ''}")}[adv.status]
        prose = f'<div class="prose">{esc(adv.prose)}</div>' if adv.prose else ""
        items.append(f"""<tr>
<td class="mono">{pt(adv.created_at)}</td>
<td><strong>{esc(player)}</strong><br><span class="mono">{esc(adv.market_ticker)}</span>{prose}</td>
<td>{adv.executable_price_cents}¢</td>
<td class="edge-pos">+{adv.edge * 100:.1f}%</td>
<td>{esc(adv.inferred_state)}<br>{state_chip(adv, adv.state_confirmed, adv.probation)}</td>
<td>{status}</td></tr>""")
    body = tiles_html(s) + f"""<div class="card"><h2>Advisories</h2>
<table><tr><th>time</th><th>recommendation</th><th>price</th><th>edge</th>
<th>state</th><th>status</th></tr>
{''.join(items) or '<tr><td colspan="6" class="empty">No advisories yet — the engine fires only when every gate passes.</td></tr>'}
</table></div>"""
    return web.Response(text=page("Advisories", "home", body), content_type="text/html")


async def live(request: web.Request) -> web.Response:
    with db_session() as db:
        rows = db.execute(
            select(LiveMatchState, KalshiMarket.title)
            .join(KalshiMarket, KalshiMarket.ticker == LiveMatchState.market_ticker,
                  isouter=True)
            .order_by(LiveMatchState.updated_at.desc()).limit(60)
        ).all()
    now = datetime.now(timezone.utc)
    items = []
    for st, title in rows:
        fresh = st.last_tick_at and (now - st.last_tick_at) < timedelta(minutes=10)
        conf = chip("good", "✓", "confirmed") if st.last_confirmed_state == st.state \
            else chip("warning", "≈", f"{st.confidence:.0%} est.")
        flags = chip("critical", "⛔", "STALE") if st.stale else (
            chip("muted", "·", "idle") if not fresh else "")
        items.append(f"""<tr><td>{esc(title or st.market_ticker)}<br>
<span class="mono">{esc(st.market_ticker)}</span></td>
<td><strong>{esc(st.state)}</strong></td><td>{conf} {flags}</td>
<td class="mono">{pt(st.last_tick_at)}</td></tr>""")
    body = f"""<div class="card"><h2>Live match states (estimator)</h2>
<table><tr><th>market</th><th>sets</th><th>state confidence</th><th>last tick</th></tr>
{''.join(items) or '<tr><td colspan="4" class="empty">No estimator state yet.</td></tr>'}
</table></div>"""
    return web.Response(text=page("Live", "live", body), content_type="text/html")


async def report(request: web.Request) -> web.Response:
    from bot.reports import graduate_report, inference_report

    with db_session() as db:
        text = inference_report(db)
        grad, _ = graduate_report(db)
        gaps = db.execute(select(FeedGap).order_by(FeedGap.gap_start.desc()).limit(15)
                          ).scalars().all()
    gap_rows = "".join(
        f"<tr><td class='mono'>{esc(g.market_ticker)}</td><td>{pt(g.gap_start)}</td>"
        f"<td>{g.duration_seconds or 0:.0f}s</td></tr>" for g in gaps)
    body = f"""<div class="card"><h2>Inference report</h2><pre class="mono">{esc(text)}</pre></div>
<div class="card"><h2>Graduation check</h2><pre class="mono">{esc(grad)}</pre></div>
<div class="card"><h2>Recent feed gaps</h2><table>
<tr><th>market</th><th>start</th><th>duration</th></tr>
{gap_rows or '<tr><td colspan="3" class="empty">None recorded.</td></tr>'}</table></div>"""
    return web.Response(text=page("Estimator", "report", body), content_type="text/html")


async def queue(request: web.Request) -> web.Response:
    with db_session() as db:
        rows = db.execute(select(MatchReviewQueue).where(
            MatchReviewQueue.resolved.is_(False))
            .order_by(MatchReviewQueue.created_at.desc()).limit(100)).scalars().all()
    items = "".join(
        f"<tr><td>{esc(r.raw_name)}</td><td>{esc(r.source)}</td>"
        f"<td>{esc((r.context or {}).get('reason'))}</td>"
        f"<td class='mono'>{esc((r.context or {}).get('ticker', ''))}</td>"
        f"<td class='mono'>{pt(r.created_at)}</td></tr>" for r in rows)
    body = f"""<div class="card"><h2>Unmatched names — manual review
({len(rows)})</h2>
<table><tr><th>name</th><th>source</th><th>reason</th><th>market</th><th>queued</th></tr>
{items or '<tr><td colspan="5" class="empty">Queue is empty.</td></tr>'}</table>
<p class="prose">Resolve by inserting a row into <span class="mono">player_aliases</span>
(alias_normalized → player_id) and marking the queue row resolved.</p></div>"""
    return web.Response(text=page("Review queue", "queue", body), content_type="text/html")


async def healthz(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


# ---------------------------------------------------------------------------
# app
# ---------------------------------------------------------------------------


@web.middleware
async def token_guard(request: web.Request, handler):
    token = os.environ.get("WEB_TOKEN")
    if token and request.path != "/healthz":
        supplied = request.query.get("token") or request.cookies.get("wt")
        if supplied != token:
            return web.Response(status=401, text="unauthorized (append ?token=…)")
        resp = await handler(request)
        resp.set_cookie("wt", token, max_age=30 * 86400, httponly=True)
        return resp
    return await handler(request)


def make_app() -> web.Application:
    app = web.Application(middlewares=[token_guard])
    app.router.add_get("/", home)
    app.router.add_get("/live", live)
    app.router.add_get("/report", report)
    app.router.add_get("/queue", queue)
    app.router.add_get("/healthz", healthz)
    return app


def main() -> int:
    port = int(os.environ.get("PORT", 8080))
    log.info("web ui starting", port=port)
    web.run_app(make_app(), host="0.0.0.0", port=port, print=None)
    return 0
