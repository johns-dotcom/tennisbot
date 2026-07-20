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
        for href, key, label in (("/", "home", "Advisories"),
                                 ("/scenarios", "scenarios", "Scenarios"),
                                 ("/track", "track", "Track record"),
                                 ("/live", "live", "Live"),
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


LIVE_WINDOW_BEFORE = timedelta(minutes=10)
LIVE_WINDOW_AFTER = timedelta(hours=6)
UPCOMING_HORIZON = timedelta(hours=12)


def _latest_quotes(db, tickers: list[str]) -> dict[str, tuple]:
    """ticker -> (yes_bid, yes_ask, ts) from the most recent quote tick."""
    if not tickers:
        return {}
    from sqlalchemy import text as sqltext

    rows = db.execute(sqltext("""
        SELECT DISTINCT ON (market_ticker) market_ticker, yes_bid, yes_ask, ts
        FROM market_ticks
        WHERE market_ticker = ANY(:tickers) AND kind = 'quote'
          AND ts > now() - interval '30 minutes'
        ORDER BY market_ticker, ts DESC"""), {"tickers": tickers}).all()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


async def live(request: web.Request) -> web.Response:
    now = datetime.now(timezone.utc)
    with db_session() as db:
        markets = db.execute(select(KalshiMarket).where(
            KalshiMarket.status.in_(["active", "open"]))).scalars().all()

        # group complementary per-player markets by event
        events: dict[str, dict] = {}
        for m in markets:
            occ_raw = (m.raw or {}).get("occurrence_datetime")
            if not occ_raw:
                continue
            try:
                occ = datetime.fromisoformat(occ_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            ev = events.setdefault(m.event_ticker, {
                "occ": occ, "sides": [], "series": (m.raw or {}).get("_series", "")})
            ev["sides"].append(m)

        live_evs, soon_evs = [], []
        for ev_ticker, ev in events.items():
            if ev["occ"] - LIVE_WINDOW_BEFORE <= now <= ev["occ"] + LIVE_WINDOW_AFTER:
                live_evs.append((ev_ticker, ev))
            elif now < ev["occ"] <= now + UPCOMING_HORIZON:
                soon_evs.append((ev_ticker, ev))
        live_evs.sort(key=lambda e: e[1]["occ"])
        soon_evs.sort(key=lambda e: e[1]["occ"])

        all_tickers = [m.ticker for _, ev in live_evs for m in ev["sides"]]
        quotes = _latest_quotes(db, all_tickers)
        states = {s.market_ticker: s for s in db.execute(
            select(LiveMatchState).where(
                LiveMatchState.market_ticker.in_(all_tickers))).scalars().all()} \
            if all_tickers else {}
        advised = set(db.execute(
            select(Advisory.market_ticker).where(
                Advisory.market_ticker.in_(all_tickers),
                Advisory.status.in_(["sent", "pending"]))).scalars().all()) \
            if all_tickers else set()

    series_label = {"KXATPMATCH": "ATP", "KXWTAMATCH": "WTA", "KXWTAGAME": "WTA",
                    "KXATPCHALLENGERMATCH": "Challenger", "KXITFMATCH": "ITF M",
                    "KXITFWMATCH": "ITF W"}

    def side_cell(m) -> str:
        name = (m.raw or {}).get("yes_sub_title") or m.ticker.rsplit("-", 1)[-1]
        q = quotes.get(m.ticker)
        if q and q[0] is not None and q[1] is not None:
            mid = (q[0] + q[1]) / 2
            return f"{esc(name)} <strong>{mid:.0f}¢</strong>"
        return f"{esc(name)} <span class='mono'>—</span>"

    def event_rows(evs, show_state: bool) -> str:
        out = []
        for ev_ticker, ev in evs:
            sides = sorted(ev["sides"], key=lambda m: m.ticker)
            est = next((states.get(m.ticker) for m in sides if states.get(m.ticker)), None)
            fresh_tick = max((q[2] for m in sides if (q := quotes.get(m.ticker))),
                             default=None)
            if show_state and est is not None:
                if est.stale:
                    st_cell = f"<strong>{esc(est.state)}</strong> " + \
                        chip("critical", "⛔", "STALE")
                elif est.last_confirmed_state == est.state:
                    st_cell = f"<strong>{esc(est.state)}</strong> " + \
                        chip("good", "✓", "score")
                else:
                    st_cell = f"<strong>{esc(est.state)}</strong> " + \
                        chip("warning", "≈", f"{est.confidence:.0%}")
            else:
                st_cell = "<span class='mono'>0-0</span>" if show_state else "—"
            play = chip("good", "▲", "play") if any(m.ticker in advised for m in sides) \
                else chip("muted", "·", "no play")
            cells = " · ".join(side_cell(m) for m in sides[:2])
            out.append(f"""<tr>
<td>{chip('muted', '', series_label.get(ev['series'], ev['series'] or '?'))}</td>
<td>{cells}<br><span class="mono">{esc(ev_ticker)}</span></td>
<td class="mono">{pt(ev['occ'])}</td>
{f'<td>{st_cell}</td>' if show_state else ''}
{f'<td>{play}</td>' if show_state else ''}
{f'<td class="mono">{pt(fresh_tick)}</td>' if show_state else ''}</tr>""")
        return "".join(out)

    live_html = event_rows(live_evs, True)
    soon_html = event_rows(soon_evs, False)
    body = f"""<div class="card"><h2>Live now ({len(live_evs)})</h2>
<table><tr><th>tour</th><th>match · prices</th><th>started</th><th>sets</th>
<th>advisory</th><th>last tick</th></tr>
{live_html or '<tr><td colspan="6" class="empty">No tennis matches in the playing window right now.</td></tr>'}
</table>
<p class="prose">Every live match the bot is watching appears here whether or not
a play fired. Prices are the latest streamed mid; sets come from the estimator
(≈ inferred from odds movement, ✓ confirmed by the delayed score).</p></div>
<div class="card"><h2>Starting soon ({len(soon_evs)})</h2>
<table><tr><th>tour</th><th>match</th><th>starts</th></tr>
{soon_html or '<tr><td colspan="3" class="empty">Nothing scheduled in the next 12 hours.</td></tr>'}
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


async def scenarios(request: web.Request) -> web.Response:
    from bot.models import Scenario

    with db_session() as db:
        latest_day = db.execute(
            select(func.max(Scenario.created_for))).scalar()
        rows = db.execute(
            select(Scenario, Player.full_name)
            .join(Player, Player.id == Scenario.player_id, isouter=True)
            .where(Scenario.created_for == latest_day)
            .order_by(Scenario.salience.desc())
        ).all() if latest_day else []

    kind_chip = {
        "decider_edge": chip("warning", "⚖", "decider edge"),
        "resilient_favorite": chip("good", "▲", "resilient favorite"),
    }
    cards = []
    for sc, player in rows:
        match_label = (sc.facts or {}).get("match") or sc.event_ticker
        cards.append(f"""<div class="card">
<h2>{esc(match_label)} <span style="font-weight:400">· watch
<strong>{esc(player)}</strong> at {esc(sc.scenario_state)} sets</span></h2>
<p style="margin:4px 0 8px">{kind_chip.get(sc.kind, '')}
{chip('muted', '⏱', pt(sc.scheduled_start))}
{chip('muted', '№', f'salience {sc.salience:.2f}')}</p>
<div class="prose">{esc(sc.narrative)}</div>
<p class="mono" style="color:var(--ink-3);font-size:12px;margin-bottom:0">
prematch {sc.prematch_prob:.0%} → at {esc(sc.scenario_state)}:
{sc.model_prob_at_state:.0%} · {esc(sc.market_ticker)}</p>
</div>""")
    header = (f'<p class="prose" style="margin-bottom:14px">Generated daily for '
              f'matches in the next 48h (latest: {latest_day}). These are '
              f'pre-computed gameflow situations — if the match reaches the named '
              f'state, the model already knows which side is live. The engine '
              f'still applies every gate before any advisory fires.</p>') \
        if latest_day else ""
    body = header + ("".join(cards) or
                     '<div class="card"><div class="empty">No scenarios yet — they '
                     'generate with the daily ingest run, or on demand via '
                     '<span class="mono">python -m bot scenarios</span>.</div></div>')
    return web.Response(text=page("Scenarios", "scenarios", body),
                        content_type="text/html")


async def track(request: web.Request) -> web.Response:
    from bot.track import advisory_outcome, advisory_pnl_cents

    with db_session() as db:
        rows = db.execute(
            select(Advisory, Player.full_name, KalshiMarket.result)
            .join(Player, Player.id == Advisory.recommended_player_id, isouter=True)
            .join(KalshiMarket, KalshiMarket.ticker == Advisory.market_ticker,
                  isouter=True)
            .where(Advisory.status == "sent")
            .order_by(Advisory.created_at.desc()).limit(200)
        ).all()

    settled, pnl_total, stake_total = [], 0, 0
    buckets = {"all": [0, 0], "probation": [0, 0], "confirmed": [0, 0]}
    items = []
    for adv, player, result in rows:
        side = adv.fact_block.get("side", "yes") if adv.fact_block else "yes"
        outcome = advisory_outcome(side, result)
        pnl = advisory_pnl_cents(side, adv.executable_price_cents, result)
        if outcome in ("won", "lost"):
            settled.append(outcome)
            pnl_total += pnl
            stake_total += adv.executable_price_cents
            key = "probation" if adv.probation else "confirmed"
            for k in ("all", key):
                buckets[k][0] += (outcome == "won")
                buckets[k][1] += (outcome == "lost")
        oc = {"won": chip("good", "✓", "WON"), "lost": chip("critical", "✕", "LOST"),
              "void": chip("muted", "·", "void"), None: chip("warning", "…", "open")}[outcome]
        pnl_txt = f"{pnl:+d}¢" if pnl is not None else "—"
        items.append(f"""<tr>
<td class="mono">{pt(adv.created_at)}</td>
<td><strong>{esc(player)}</strong><br><span class="mono">{esc(adv.market_ticker)}</span></td>
<td>{adv.executable_price_cents}¢</td>
<td>{adv.model_prob:.0%}</td>
<td class="edge-pos">+{adv.edge * 100:.1f}%</td>
<td>{esc(adv.inferred_state)} {state_chip(adv, adv.state_confirmed, adv.probation)}</td>
<td>{oc}</td><td>{pnl_txt}</td></tr>""")

    n = len(settled)
    wins = sum(1 for o in settled if o == "won")
    win_rate = f"{wins / n:.0%}" if n else "—"
    roi = f"{pnl_total / stake_total:+.1%}" if stake_total else "—"
    rec = lambda b: f"{b[0]}-{b[1]}" if (b[0] or b[1]) else "0-0"
    tiles = f"""<div class="tiles">
<div class="tile"><div class="v">{len(rows)}</div><div class="l">advisories sent</div></div>
<div class="tile"><div class="v">{rec(buckets['all'])}</div><div class="l">settled record</div></div>
<div class="tile"><div class="v">{win_rate}</div><div class="l">win rate</div></div>
<div class="tile"><div class="v">{pnl_total:+d}¢</div><div class="l">flat-stake P&amp;L</div>
  <div class="s">1 contract per advisory</div></div>
<div class="tile"><div class="v">{roi}</div><div class="l">ROI on stakes</div></div>
<div class="tile"><div class="v">{rec(buckets['probation'])}</div><div class="l">probation record</div>
  <div class="s">confirmed: {rec(buckets['confirmed'])}</div></div>
</div>"""
    body = tiles + f"""<div class="card"><h2>Advisory track record</h2>
<table><tr><th>time</th><th>recommendation</th><th>price</th><th>model</th>
<th>edge</th><th>state at fire</th><th>outcome</th><th>P&amp;L</th></tr>
{''.join(items) or '<tr><td colspan="8" class="empty">No sent advisories yet.</td></tr>'}
</table>
<p class="prose">Outcomes settle from Kalshi market results (checked every 30
minutes). P&amp;L convention: one contract bought at the quoted executable price
per advisory — an accounting yardstick, not betting advice.</p></div>"""
    return web.Response(text=page("Track record", "track", body),
                        content_type="text/html")


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
    app.router.add_get("/scenarios", scenarios)
    app.router.add_get("/track", track)
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
