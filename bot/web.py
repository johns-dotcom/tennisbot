"""Web UI: python -m bot web — the advisory delivery surface (no Discord).

Visual language: "Deuce Terminal" (claude.ai/design project 57f12b3b — Modernist
system: Archivo 800 headings, uppercase kickers, square corners, 2px dividers,
red accent) re-tuned for a permanent dark scheme. The mockup's trading controls
(stakes/approvals/bankroll) are deliberately NOT implemented — CLAUDE.md rule 1:
advisory only, never any execution surface.

Storage is UTC; display is US/Pacific. Optional WEB_TOKEN gate.
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
@import url('https://fonts.googleapis.com/css2?family=Archivo:wght@400;600;800&display=swap');
:root {
  color-scheme: dark;
  --bg: #141312; --surface: #1d1b1a; --surface-2: #242120;
  --text: #f3f2f2; --muted: rgba(243,242,242,.55); --faint: rgba(243,242,242,.38);
  --divider: rgba(243,242,242,.22); --divider-strong: rgba(243,242,242,.34);
  --accent: #ff563c; --accent-fill: #ec3013;
  --good: #35c26e; --warning: #fab219; --critical: #ff563c;
  --font: "Archivo", system-ui, sans-serif;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 400 15px/1.55 var(--font); -webkit-font-smoothing: antialiased; }
h1,h2,h3,h4,h5,h6 { font-family: var(--font); font-weight: 800;
  line-height: 1.12; letter-spacing: -0.015em; margin: 0; }
h2 { font-size: 30px; }
.kicker { font-size: 11px; letter-spacing: .1em; text-transform: uppercase;
  color: var(--accent); font-weight: 800; margin: 0 0 6px; }
.mono { font-variant-numeric: tabular-nums; font-feature-settings: "tnum"; }
a { color: var(--accent); text-underline-offset: 3px; }
::selection { background: rgba(255,86,60,.35); }

header.nav { position: sticky; top: 0; z-index: 20; background: var(--bg);
  display: flex; align-items: center; flex-wrap: wrap; gap: 12px 24px;
  padding: 14px 20px; border-bottom: 2px solid var(--divider-strong); }
.brand { font-weight: 800; font-size: 18px; letter-spacing: .02em; }
.brand small { font-size: 10px; letter-spacing: .14em; font-weight: 400;
  text-transform: uppercase; color: var(--muted); margin-left: 10px; }
nav.links { display: flex; gap: 22px; margin-right: auto; flex-wrap: wrap; }
nav.links a { padding: 6px 0; border-bottom: 2px solid transparent;
  font-weight: 800; font-size: 12.5px; letter-spacing: .05em;
  text-transform: uppercase; color: var(--muted); text-decoration: none; }
nav.links a:hover { color: var(--text); }
nav.links a.active { color: var(--text); border-bottom-color: var(--accent); }
.conn { font-size: 11px; color: var(--muted); display: inline-flex;
  align-items: center; gap: 7px; letter-spacing: .06em; }
.dot { width: 8px; height: 8px; display: inline-block; border-radius: 50%; }

main { width: 100%; max-width: 1360px; margin: 0 auto; padding: 26px 20px 60px; }
.pagehead { display: flex; align-items: flex-end; justify-content: space-between;
  flex-wrap: wrap; gap: 12px; margin-bottom: 18px; }
.pagehead .sub { font-size: 12px; color: var(--muted); }

.statstrip { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 2px; background: var(--divider); border: 2px solid var(--divider);
  margin-bottom: 26px; }
.stat { background: var(--surface); padding: 16px; }
.stat .l { font-size: 10px; letter-spacing: .1em; text-transform: uppercase;
  color: var(--muted); margin-bottom: 10px; }
.stat .v { font-weight: 800; font-size: 27px; letter-spacing: -.02em; line-height: 1; }
.stat .s { font-size: 12px; margin-top: 6px; color: var(--faint); }

section.block { margin-bottom: 30px; }
.blockhead { display: flex; align-items: baseline; justify-content: space-between;
  margin-bottom: 8px; }
.blockhead h4 { font-size: 19px; }
.blockhead .aside { font-size: 12px; color: var(--muted); }
.rule { height: 2px; background: var(--divider); margin: 0 0 10px; }

.tw { overflow-x: auto; }
table.t { width: 100%; border-collapse: collapse; font-size: 13.5px; }
table.t th { text-align: left; font-size: 10.5px; letter-spacing: .08em;
  text-transform: uppercase; color: var(--muted); padding: 8px;
  border-bottom: 2px solid var(--divider); white-space: nowrap; }
table.t td { padding: 9px 8px; border-bottom: 1px solid var(--divider);
  vertical-align: top; }
table.t tbody tr:hover { background: rgba(243,242,242,.04); }
.pname { font-weight: 800; font-size: 13.5px; }
.sub2 { color: var(--muted); font-size: 11.5px; }

.tag { display: inline-flex; align-items: center; gap: 5px; font-size: 10.5px;
  letter-spacing: .04em; padding: 3px 9px; font-weight: 600;
  text-transform: uppercase; }
.tag-accent { background: rgba(255,86,60,.16); color: var(--accent); }
.tag-good { background: rgba(53,194,110,.14); color: var(--good); }
.tag-warn { background: rgba(250,178,25,.13); color: var(--warning); }
.tag-neutral { background: rgba(243,242,242,.09); color: var(--muted); }
.tag-outline { border: 1px solid var(--accent); color: var(--accent); }

.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px; }
.card { display: flex; flex-direction: column; gap: 12px; padding: 16px;
  background: var(--surface); }
.card .title { font-weight: 800; font-size: 17px; }
.metric-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 2px;
  background: var(--divider); border: 1px solid var(--divider); }
.metric { background: var(--surface-2); padding: 8px 10px; }
.metric .k { font-size: 9px; letter-spacing: .08em; text-transform: uppercase;
  color: var(--muted); }
.metric .v { font-weight: 800; font-size: 15px; margin-top: 2px; }
.prose { color: rgba(243,242,242,.82); font-size: 13.5px; line-height: 1.6;
  max-width: 78ch; }
.playerrow { display: grid; grid-template-columns: 1fr auto; gap: 12px;
  align-items: center; padding: 8px 0; border-bottom: 1px solid var(--divider); }
.playerrow .nm { font-weight: 800; font-size: 15px; }
.playerrow .px { font-weight: 800; font-size: 16px; min-width: 48px; text-align: right; }
.empty { color: var(--faint); padding: 20px 0; text-align: center; }
pre.report { font: 12.5px/1.6 ui-monospace, Menlo, monospace; color: var(--text);
  overflow-x: auto; margin: 0; }
footer { color: var(--faint); font-size: 11.5px; margin-top: 30px;
  letter-spacing: .02em; }
"""


def esc(v) -> str:
    return html.escape(str(v)) if v is not None else "—"


def pt(ts: datetime | None) -> str:
    if ts is None:
        return "—"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(PACIFIC).strftime("%b %d %I:%M %p PT")


def tag(kind: str, icon: str, label: str) -> str:
    return f'<span class="tag tag-{kind}">{icon} {esc(label)}</span>'


def state_tags(confirmed: bool, probation: bool) -> str:
    out = [tag("good", "✓", "score-confirmed") if confirmed
           else tag("warn", "≈", "inferred")]
    if probation:
        out.append(tag("accent", "⚠", "probation"))
    return " ".join(out)


def _feed_status() -> tuple[str, str]:
    """(dot color, label) from the recorder's most recent tick."""
    from sqlalchemy import text as sqltext

    try:
        with db_session() as db:
            last = db.execute(sqltext(
                "SELECT max(ts) FROM market_ticks")).scalar()
    except Exception:
        return "var(--critical)", "DB UNREACHABLE"
    if last is None:
        return "var(--faint)", "NO FEED YET"
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - last).total_seconds()
    if age < 300:
        return "var(--good)", "FEED LIVE"
    if age < 3600:
        return f"var(--warning)", f"FEED IDLE {int(age // 60)}M"
    return "var(--critical)", "FEED STALE"


def page(title: str, active: str, body: str) -> str:
    navs = "".join(
        f'<a href="{href}" class="{"active" if key == active else ""}">{label}</a>'
        for href, key, label in (("/", "home", "Advisories"),
                                 ("/scenarios", "scenarios", "Scenarios"),
                                 ("/track", "track", "Track record"),
                                 ("/live", "live", "Live"),
                                 ("/report", "report", "Estimator"),
                                 ("/queue", "queue", "Review queue")))
    dot, conn = _feed_status()
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>{esc(title)} · DEUCE</title><style>{CSS}</style></head>
<body>
<header class="nav">
  <span class="brand">DEUCE<small>Kalshi Terminal · advisory only — never trades</small></span>
  <nav class="links">{navs}</nav>
  <span class="conn mono"><span class="dot" style="background:{dot}"></span>{conn}</span>
</header>
<main>
{body}
<footer>All times US/Pacific · auto-refreshes every 30s · historical data ©
Jeff Sackmann / Tennis Abstract (CC BY-NC-SA 4.0), personal research use ·
advisory only, nothing here is an order.</footer>
</main></body></html>"""


def pagehead(kicker: str, title: str, sub: str = "") -> str:
    return f"""<div class="pagehead"><div>
<div class="kicker">{esc(kicker)}</div><h2>{esc(title)}</h2></div>
{f'<span class="sub mono">{sub}</span>' if sub else ''}</div>"""


# ---------------------------------------------------------------------------
# shared queries
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


def statstrip(items: list[tuple[str, str, str]]) -> str:
    cells = []
    for label, value, sub in items:
        sub_html = f'<div class="s">{esc(sub)}</div>' if sub else ""
        cells.append(f'<div class="stat"><div class="l">{esc(label)}</div>'
                     f'<div class="v mono">{value}</div>{sub_html}</div>')
    return f'<div class="statstrip">{"".join(cells)}</div>'


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
    hr = f"{s['hit_rate']:.0%}" if s["hit_rate"] is not None else "—"
    lead = f"{s['avg_lead']:.0f}s" if s["avg_lead"] is not None else "—"
    strip = statstrip([
        ("Advisories today", str(s["adv_today"]), ""),
        ("Held pending score", str(s["pending"]), ""),
        ("Matches live", str(s["live"]), ""),
        ("Markets watched", str(s["watched"]), ""),
        ("State hit rate", hr, f"30d clean · n={s['n_inf']}"),
        ("Avg score lead", lead, ""),
    ])
    items = []
    for adv, player in rows:
        status = {"sent": tag("good", "✓", "sent"),
                  "pending": tag("warn", "…", "pending"),
                  "killed": tag("accent", "✕", f"killed · {adv.kill_reason or ''}")}[adv.status]
        prose = f'<div class="prose">{esc(adv.prose)}</div>' if adv.prose else ""
        items.append(f"""<tr>
<td class="mono sub2">{pt(adv.created_at)}</td>
<td><span class="pname">{esc(player)}</span><br>
<span class="mono sub2">{esc(adv.market_ticker)}</span>{prose}</td>
<td class="mono">{adv.executable_price_cents}¢</td>
<td class="mono" style="color:var(--accent);font-weight:800">+{adv.edge * 100:.1f}%</td>
<td>{esc(adv.inferred_state)}<br>{state_tags(adv.state_confirmed, adv.probation)}</td>
<td>{status}</td></tr>""")
    body = pagehead("Signal", "Advisories",
                    f"updated {datetime.now(PACIFIC):%H:%M:%S} PT") + strip + f"""
<section class="block"><div class="blockhead"><h4>Feed</h4>
<span class="aside mono">every gate must pass before a row appears here</span></div>
<div class="rule"></div><div class="tw">
<table class="t"><tr><th>time</th><th>recommendation</th><th>price</th>
<th>edge</th><th>state</th><th>status</th></tr>
{''.join(items) or '<tr><td colspan="6" class="empty">No advisories yet — the engine only fires when edge, volume, model confidence and state confidence all clear.</td></tr>'}
</table></div></section>"""
    return web.Response(text=page("Advisories", "home", body), content_type="text/html")


async def scenarios(request: web.Request) -> web.Response:
    from bot.models import Scenario

    with db_session() as db:
        latest_day = db.execute(select(func.max(Scenario.created_for))).scalar()
        rows = db.execute(
            select(Scenario, Player.full_name)
            .join(Player, Player.id == Scenario.player_id, isouter=True)
            .where(Scenario.created_for == latest_day)
            .order_by(Scenario.salience.desc())
        ).all() if latest_day else []

    cards = []
    for sc, player in rows:
        f = sc.facts or {}
        match_label = f.get("match") or sc.event_ticker
        conf = f.get("model_confidence")
        metrics = f"""<div class="metric-grid">
<div class="metric"><div class="k">Prematch</div><div class="v mono">{sc.prematch_prob:.0%}</div></div>
<div class="metric"><div class="k">In decider</div><div class="v mono">{sc.model_prob_at_state:.0%}</div></div>
<div class="metric"><div class="k">Model conf</div><div class="v mono">{f"{conf:.0%}" if conf is not None else "—"}</div></div>
<div class="metric"><div class="k">Salience</div><div class="v mono">{sc.salience:.2f}</div></div>
</div>"""
        cards.append(f"""<div class="card">
<div style="display:flex;align-items:center;justify-content:space-between;gap:12px">
<span class="kicker" style="margin:0">{esc(f.get('event_label') or 'gameflow plan')}</span>
{tag('outline', '◆', 'gameflow')}</div>
<div class="title">{esc(match_label)}</div>
<div class="sub2 mono">watch <strong style="color:var(--text)">{esc(player)}</strong>
· {pt(sc.scheduled_start)} · {esc(sc.market_ticker)}</div>
{metrics}
<div class="prose">{esc(sc.narrative)}</div>
</div>""")
    body = pagehead("Strategy", "Gameflow Scenarios",
                    f"generated {latest_day} · next 48h" if latest_day else "") + f"""
<p class="prose" style="margin:0 0 18px">Pre-computed before play: if the match
reaches the named situation, the model already knows which side is live. The
engine still applies every gate before any advisory fires.</p>
<div class="cards">{''.join(cards) or
    '<div class="card"><div class="empty">No scenarios yet — they generate with the daily ingest run.</div></div>'}
</div>"""
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
        oc = {"won": tag("good", "✓", "won"), "lost": tag("accent", "✕", "lost"),
              "void": tag("neutral", "·", "void"), None: tag("warn", "…", "open")}[outcome]
        pnl_txt = f"{pnl:+d}¢" if pnl is not None else "—"
        pnl_color = "var(--good)" if (pnl or 0) > 0 else \
            ("var(--accent)" if (pnl or 0) < 0 else "var(--muted)")
        items.append(f"""<tr>
<td class="mono sub2">{pt(adv.created_at)}</td>
<td><span class="pname">{esc(player)}</span><br>
<span class="mono sub2">{esc(adv.market_ticker)}</span></td>
<td class="mono">{adv.executable_price_cents}¢</td>
<td class="mono">{adv.model_prob:.0%}</td>
<td class="mono" style="color:var(--accent);font-weight:800">+{adv.edge * 100:.1f}%</td>
<td>{esc(adv.inferred_state)} {state_tags(adv.state_confirmed, adv.probation)}</td>
<td>{oc}</td>
<td class="mono" style="text-align:right;font-weight:800;color:{pnl_color}">{pnl_txt}</td></tr>""")

    n = len(settled)
    wins = sum(1 for o in settled if o == "won")
    win_rate = f"{wins / n:.0%}" if n else "—"
    roi = f"{pnl_total / stake_total:+.1%}" if stake_total else "—"
    rec = lambda b: f"{b[0]}-{b[1]}" if (b[0] or b[1]) else "0-0"
    pnl_v = f"{pnl_total:+d}¢" if n else "—"
    strip = statstrip([
        ("Advisories sent", str(len(rows)), ""),
        ("Settled record", rec(buckets["all"]), ""),
        ("Win rate", win_rate, ""),
        ("Flat-stake P&L", pnl_v, "1 contract per advisory"),
        ("ROI on stakes", roi, ""),
        ("Probation record", rec(buckets["probation"]),
         f"confirmed {rec(buckets['confirmed'])}"),
    ])
    body = pagehead("History", "Track Record",
                    f"{n} settled of {len(rows)} sent") + strip + f"""
<section class="block"><div class="blockhead"><h4>Every advisory, scored</h4>
<span class="aside">outcomes settle from Kalshi results, checked every 30 min</span></div>
<div class="rule"></div><div class="tw">
<table class="t"><tr><th>time</th><th>recommendation</th><th>price</th><th>model</th>
<th>edge</th><th>state at fire</th><th>outcome</th><th style="text-align:right">P&amp;L</th></tr>
{''.join(items) or '<tr><td colspan="8" class="empty">No sent advisories yet.</td></tr>'}
</table></div>
<p class="prose" style="margin-top:10px">P&amp;L convention: one contract at the
quoted executable price per advisory — an accounting yardstick, not betting advice.</p>
</section>"""
    return web.Response(text=page("Track record", "track", body),
                        content_type="text/html")


LIVE_WINDOW_BEFORE = timedelta(minutes=10)
LIVE_WINDOW_AFTER = timedelta(hours=6)
UPCOMING_HORIZON = timedelta(hours=12)


def _latest_quotes(db, tickers: list[str]) -> dict[str, tuple]:
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

        # Kalshi's occurrence_datetime is the SCHEDULED time and never updates;
        # tennis runs early/late constantly. A match is treated as over when any
        # side has a settlement result, or when discovery stopped seeing the
        # market as open (it left the open set → closed/settled on Kalshi).
        seen_cutoff = now - timedelta(minutes=45)
        # only trust "disappeared from discovery" while discovery is provably
        # alive — otherwise a worker outage would empty the whole board
        global_last_seen = max((m.last_seen_at for m in markets
                                if m.last_seen_at), default=None)
        discovery_alive = global_last_seen is not None and global_last_seen >= seen_cutoff
        live_evs, soon_evs, done_evs = [], [], []
        for ev_ticker, ev in events.items():
            settled = any(m.result for m in ev["sides"])
            last_seen = max((m.last_seen_at for m in ev["sides"]
                             if m.last_seen_at), default=None)
            gone = discovery_alive and last_seen is not None and last_seen < seen_cutoff
            if settled or gone:
                if now - ev["occ"] <= timedelta(hours=18):
                    done_evs.append((ev_ticker, ev))
                continue
            if ev["occ"] - LIVE_WINDOW_BEFORE <= now <= ev["occ"] + LIVE_WINDOW_AFTER:
                live_evs.append((ev_ticker, ev))
            elif now < ev["occ"] <= now + UPCOMING_HORIZON:
                soon_evs.append((ev_ticker, ev))
        live_evs.sort(key=lambda e: e[1]["occ"])
        soon_evs.sort(key=lambda e: e[1]["occ"])
        done_evs.sort(key=lambda e: e[1]["occ"], reverse=True)

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
                    "KXATPCHALLENGERMATCH": "CHALLENGER", "KXITFMATCH": "ITF M",
                    "KXITFWMATCH": "ITF W"}

    def match_card(ev_ticker: str, ev: dict, is_live: bool) -> str:
        sides = sorted(ev["sides"], key=lambda m: m.ticker)
        est = next((states.get(m.ticker) for m in sides if states.get(m.ticker)), None)
        rows_html = []
        for m in sides[:2]:
            name = (m.raw or {}).get("yes_sub_title") or m.ticker.rsplit("-", 1)[-1]
            q = quotes.get(m.ticker)
            px = f"{(q[0] + q[1]) / 2:.0f}¢" if q and q[0] is not None and q[1] is not None else "—"
            rows_html.append(f'<div class="playerrow"><span class="nm">{esc(name)}</span>'
                             f'<span class="px mono">{px}</span></div>')
        if est is not None:
            if est.stale:
                st = tag("accent", "⛔", f"{est.state} stale")
            elif est.last_confirmed_state == est.state:
                st = tag("good", "✓", f"{est.state} score")
            else:
                st = tag("warn", "≈", f"{est.state} · {est.confidence:.0%}")
        else:
            st = tag("accent" if is_live else "neutral", "●" if is_live else "○",
                     "LIVE" if is_live else "PRE")
        play = tag("outline", "▲", "play") if any(m.ticker in advised for m in sides) else ""
        return f"""<div class="card">
<div style="display:flex;align-items:center;justify-content:space-between">
<span class="kicker" style="margin:0">{series_label.get(ev['series'], '?')}</span>
<span>{st} {play}</span></div>
<div>{''.join(rows_html)}</div>
<div class="sub2 mono">{'started' if is_live else 'starts'} {pt(ev['occ'])}
· {esc(ev_ticker)}</div>
</div>"""

    def done_card(ev_ticker: str, ev: dict) -> str:
        sides = sorted(ev["sides"], key=lambda m: m.ticker)
        rows_html = []
        for m in sides[:2]:
            name = (m.raw or {}).get("yes_sub_title") or m.ticker.rsplit("-", 1)[-1]
            won = m.result == "yes"
            mark = tag("good", "✓", "won") if won else ""
            style = "" if won or not any(x.result for x in sides) \
                else "color:var(--muted)"
            rows_html.append(f'<div class="playerrow"><span class="nm" '
                             f'style="{style}">{esc(name)}</span><span>{mark}</span></div>')
        return f"""<div class="card" style="opacity:.75">
<div style="display:flex;align-items:center;justify-content:space-between">
<span class="kicker" style="margin:0">{series_label.get(ev['series'], '?')}</span>
{tag('neutral', '·', 'finished')}</div>
<div>{''.join(rows_html)}</div>
<div class="sub2 mono">was scheduled {pt(ev['occ'])} · {esc(ev_ticker)}</div>
</div>"""

    live_cards = "".join(match_card(t, e, True) for t, e in live_evs)
    soon_cards = "".join(match_card(t, e, False) for t, e in soon_evs)
    done_cards = "".join(done_card(t, e) for t, e in done_evs[:12])
    body = pagehead("Match Board", "Live Now",
                    f"{len(live_evs)} live · {len(soon_evs)} next 12h") + f"""
<section class="block">
<div class="cards">{live_cards or
    '<div class="card"><div class="empty">No tennis in the playing window right now.</div></div>'}
</div></section>
<section class="block"><div class="blockhead"><h4>Starting soon</h4>
<span class="aside">next 12 hours · scheduled times — tennis runs early and late</span></div>
<div class="rule"></div>
<div class="cards">{soon_cards or
    '<div class="card"><div class="empty">Nothing scheduled.</div></div>'}
</div></section>
{f'<section class="block"><div class="blockhead"><h4>Recently finished</h4></div><div class="rule"></div><div class="cards">{done_cards}</div></section>' if done_cards else ''}
<p class="prose">Every match the bot watches appears here whether or not a play
fired. Prices are the latest streamed mids; set states come from the estimator
(≈ inferred from odds movement, ✓ confirmed by the delayed score). Matches leave
the board as soon as their market settles or closes on Kalshi.</p>"""
    return web.Response(text=page("Live", "live", body), content_type="text/html")


async def report(request: web.Request) -> web.Response:
    from bot.reports import graduate_report, inference_report

    with db_session() as db:
        text = inference_report(db)
        grad, _ = graduate_report(db)
        gaps = db.execute(select(FeedGap).order_by(FeedGap.gap_start.desc()).limit(15)
                          ).scalars().all()
    gap_rows = "".join(
        f"<tr><td class='mono sub2'>{esc(g.market_ticker)}</td><td class='mono'>{pt(g.gap_start)}</td>"
        f"<td class='mono'>{g.duration_seconds or 0:.0f}s</td></tr>" for g in gaps)
    body = pagehead("Accountability", "Estimator") + f"""
<section class="block"><div class="blockhead"><h4>Inference report</h4></div>
<div class="rule"></div><pre class="report">{esc(text)}</pre></section>
<section class="block"><div class="blockhead"><h4>Graduation check</h4>
<span class="aside">probation lifts only by manual config change</span></div>
<div class="rule"></div><pre class="report">{esc(grad)}</pre></section>
<section class="block"><div class="blockhead"><h4>Recent feed gaps</h4></div>
<div class="rule"></div><div class="tw"><table class="t">
<tr><th>market</th><th>start</th><th>duration</th></tr>
{gap_rows or '<tr><td colspan="3" class="empty">None recorded.</td></tr>'}
</table></div></section>"""
    return web.Response(text=page("Estimator", "report", body), content_type="text/html")


async def queue(request: web.Request) -> web.Response:
    with db_session() as db:
        rows = db.execute(select(MatchReviewQueue).where(
            MatchReviewQueue.resolved.is_(False))
            .order_by(MatchReviewQueue.created_at.desc()).limit(100)).scalars().all()
    items = "".join(
        f"<tr><td class='pname'>{esc(r.raw_name)}</td><td>{esc(r.source)}</td>"
        f"<td class='sub2'>{esc((r.context or {}).get('reason'))}</td>"
        f"<td class='mono sub2'>{esc((r.context or {}).get('ticker', ''))}</td>"
        f"<td class='mono sub2'>{pt(r.created_at)}</td></tr>" for r in rows)
    body = pagehead("Data Hygiene", "Review Queue", f"{len(rows)} unmatched names") + f"""
<section class="block"><div class="rule"></div><div class="tw">
<table class="t"><tr><th>name</th><th>source</th><th>reason</th><th>market</th><th>queued</th></tr>
{items or '<tr><td colspan="5" class="empty">Queue is empty.</td></tr>'}
</table></div>
<p class="prose" style="margin-top:10px">Resolve by inserting a row into
<span class="mono">player_aliases</span> (alias_normalized → player_id) and
marking the queue row resolved. Unmatched names are never silently dropped.</p>
</section>"""
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
