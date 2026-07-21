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
from datetime import date, datetime, timedelta, timezone
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
    """Relative time that self-updates client-side; absolute PT on hover."""
    if ts is None:
        return "—"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    abs_txt = ts.astimezone(PACIFIC).strftime("%b %d %I:%M %p PT")
    return (f'<span class="rel" data-ts="{int(ts.timestamp())}" '
            f'title="{abs_txt}">{abs_txt}</span>')


def tag(kind: str, icon: str, label: str) -> str:
    return f'<span class="tag tag-{kind}">{icon} {esc(label)}</span>'


def kalshi_url(ticker: str) -> str:
    """Series-board link (kalshi.com blocks fetchers, so the deep per-market
    URL format is unverified; the series page is one click away and stable)."""
    series = ticker.split("-")[0].lower()
    return f"https://kalshi.com/markets/{series}"


def kalshi_link(ticker: str) -> str:
    return (f'<a href="{kalshi_url(ticker)}" target="_blank" rel="noopener" '
            f'class="sub2">Kalshi ↗</a>')


def fact_panel(adv) -> str:
    """Expandable 'why' panel: the stored fact block + the gates it passed."""
    fb = adv.fact_block or {}
    facts = fb.get("facts") or []
    rows = "".join(
        f"<li>{esc(f.get('hint'))} <span class='sub2 mono'>"
        f"(salience {f.get('salience', 0):.2f})</span></li>" for f in facts)
    gates = (f"edge +{adv.edge * 100:.1f}% (≥6) · model conf "
             f"{adv.model_confidence:.0%} · volume {adv.market_volume or '—'} · "
             f"state conf {adv.state_confidence:.0%}"
             f"{' · score-confirmed' if adv.state_confirmed else ''}")
    return f"""<details style="margin-top:6px"><summary class="sub2"
 style="cursor:pointer">why — fact block &amp; gates</summary>
<ul class="prose" style="margin:6px 0 4px 18px;padding:0">{rows or '<li>—</li>'}</ul>
<div class="sub2 mono">gates passed: {gates}</div></details>"""


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


JS = """
function rel(){document.querySelectorAll('.rel').forEach(function(e){
 var t=+e.dataset.ts*1000, d=(t-Date.now())/1e3, a=Math.abs(d), s;
 if(a<60)s=Math.round(a)+'s'; else if(a<5400)s=Math.round(a/60)+'m';
 else if(a<86400)s=(a/3600).toFixed(1).replace('.0','')+'h';
 else s=Math.round(a/86400)+'d';
 e.textContent=d>0?('in '+s):(s+' ago');});}
function bindWatch(){
 document.querySelectorAll('details.coll').forEach(function(d){
  if(d._bound) return; d._bound=true;
  var key=d.dataset.key;
  if(localStorage.getItem(key)==='1') d.setAttribute('open',''); else d.removeAttribute('open');
  var c=d.querySelector('.coll-caret');
  function pc(){if(c)c.textContent=d.open?'▾ hide':'▸ show';} pc();
  d.addEventListener('toggle',function(){localStorage.setItem(key,d.open?'1':'0');pc();});
 });}
function typing(){var a=document.activeElement;
 return a && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName);}
async function refreshMain(){
 if(typing()) return;  // don't clobber a field mid-keystroke
 try{var r=await fetch(location.pathname+location.search,{headers:{'X-Fragment':'1'}});
  if(r.ok){var h=await r.text(); var m=document.querySelector('main');
   if(!typing() && h && h.length>50 && h!==m.innerHTML){var y=window.scrollY;
    m.innerHTML=h; window.scrollTo(0,y); bindWatch();}
  }}catch(e){} rel();}
var seen=null;
function notify(title, body){
 if(Notification.permission==='granted') new Notification(title,{body:body,silent:false});}
async function pollEvents(){
 try{var r=await fetch('/api/events'); var d=await r.json();
  var prev=seen; seen={adv:d.max_advisory_id||0, bet:d.max_bet_id||0};
  localStorage.setItem('deuce_seen',JSON.stringify(seen));
  if(!prev) return;
  (d.advisories||[]).forEach(function(a){if(a.id>prev.adv) notify('DEUCE · ADVISORY',a.text);});
  (d.bets||[]).forEach(function(b){if(b.id>prev.bet) notify('DEUCE · PAPER BET',b.text);});
 }catch(e){}}
document.addEventListener('DOMContentLoaded',function(){
 try{seen=JSON.parse(localStorage.getItem('deuce_seen'))||null;}catch(e){}
 rel(); setInterval(rel,5000);
 setInterval(refreshMain,7000);
 setInterval(pollEvents,10000); pollEvents();
 bindWatch();
 var bell=document.getElementById('bell');
 function paint(){bell.textContent=Notification.permission==='granted'?'🔔 alerts on':'🔕 enable alerts';}
 if(!('Notification' in window)){bell.style.display='none';return;} paint();
 bell.addEventListener('click',function(){Notification.requestPermission().then(paint);});});
"""


def page(title: str, active: str, body: str, fragment: bool = False) -> str:
    footer = """<footer>All times relative · updates in place every 7s ·
historical data © Jeff Sackmann / Tennis Abstract (CC BY-NC-SA 4.0), personal
research use · advisory only, nothing here is an order.</footer>"""
    if fragment:
        return body + footer
    navs = "".join(
        f'<a href="{href}" class="{"active" if key == active else ""}">{label}</a>'
        for href, key, label in (("/", "home", "Overview"),
                                 ("/live", "live", "Live"),
                                 ("/scenarios", "scenarios", "Scenarios"),
                                 ("/testrun", "testrun", "Bot Testrun"),
                                 ("/players", "players", "Database"),
                                 ("/flags", "flags", "Flags"),
                                 ("/system", "system", "System")))
    dot, conn = _feed_status()
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · DEUCE</title><style>{CSS}</style></head>
<body>
<header class="nav">
  <span class="brand">DEUCE<small>Kalshi Terminal · advisory only — never trades</small></span>
  <nav class="links">{navs}</nav>
  <button id="bell" class="conn mono" style="background:none;border:1px solid var(--divider);
   color:var(--muted);cursor:pointer;padding:4px 10px;font:inherit;font-size:11px"></button>
  <span class="conn mono"><span class="dot" style="background:{dot}"></span>{conn}</span>
</header>
<main>
{page(title, active, body, fragment=True)}
</main><script>{JS}</script></body></html>"""



def respond(request: web.Request, title: str, active: str, body: str) -> web.Response:
    frag = request.headers.get("X-Fragment") == "1"
    return web.Response(text=page(title, active, body, fragment=frag),
                        content_type="text/html")

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


def statstrip(items: list[tuple[str, str, str]], cols: int | None = None) -> str:
    cells = []
    for label, value, sub in items:
        sub_html = f'<div class="s">{esc(sub)}</div>' if sub else ""
        cells.append(f'<div class="stat"><div class="l">{esc(label)}</div>'
                     f'<div class="v mono">{value}</div>{sub_html}</div>')
    # a fixed column count fills every cell (no dangling gray grid cells when the
    # tile count doesn't divide evenly into the auto-fit row); default is auto-fit
    style = (f' style="grid-template-columns:repeat({cols},1fr)"'
             if cols else "")
    return f'<div class="statstrip"{style}>{"".join(cells)}</div>'


# ---------------------------------------------------------------------------
# views
# ---------------------------------------------------------------------------


async def home(request: web.Request) -> web.Response:
    from bot.models import Scenario

    now = datetime.now(timezone.utc)
    with db_session() as db:
        s = _summary(db)
        rows = db.execute(
            select(Advisory, Player.full_name)
            .join(Player, Player.id == Advisory.recommended_player_id, isouter=True)
            .order_by(Advisory.created_at.desc()).limit(15)
        ).all()
        live_mkts = db.execute(select(KalshiMarket).where(
            KalshiMarket.status.in_(["active", "open"]),
            KalshiMarket.player_a_id.is_not(None),
            KalshiMarket.raw["_live_status"].astext == "live")
            .limit(12)).scalars().all()
        next_plans = db.execute(
            select(Scenario, Player.full_name)
            .join(Player, Player.id == Scenario.player_id, isouter=True)
            .where(Scenario.scheduled_start > now)
            .order_by(Scenario.scheduled_start).limit(3)).all()

    live_events: dict[str, list] = {}
    for m in live_mkts:
        live_events.setdefault(m.event_ticker, []).append(m)
    live_cards = "".join(
        f'<a class="tag tag-accent" style="text-decoration:none" '
        f'href="/match/{esc(ev)}">● '
        f'{esc((sides[0].title or "").split(":")[0].replace("Will ", "").split(" win the ")[-1] if sides else ev)}</a>'
        for ev, sides in list(live_events.items())[:6])
    plan_cards = "".join(
        f'<a class="tag tag-neutral" style="text-decoration:none" '
        f'href="/match/{esc(sc.event_ticker)}">○ {esc((sc.facts or {}).get("match", sc.event_ticker))} '
        f'· {pt(sc.scheduled_start)}</a>'
        for sc, _p in next_plans)
    overview = f"""<section class="block"><div class="blockhead">
<h4>Right now</h4><span class="aside"><a href="/live">full board →</a></span></div>
<div class="rule"></div>
<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
{live_cards or '<span class="sub2">no matches live</span>'}</div>
<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px">
<span class="sub2">next plans:</span>
{plan_cards or '<span class="sub2">none scheduled</span>'}</div></section>"""
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
<span class="mono sub2">{esc(adv.market_ticker)}</span> · {kalshi_link(adv.market_ticker)}
{prose}{fact_panel(adv)}</td>
<td class="mono">{adv.executable_price_cents}¢</td>
<td class="mono" style="color:var(--accent);font-weight:800">+{adv.edge * 100:.1f}%</td>
<td>{esc(adv.inferred_state)}<br>{state_tags(adv.state_confirmed, adv.probation)}</td>
<td>{status}</td></tr>""")
    body = pagehead("Terminal", "Overview",
                    f"updated {datetime.now(PACIFIC):%H:%M:%S} PT") + strip + overview + f"""
<section class="block"><div class="blockhead"><h4>Feed</h4>
<span class="aside mono">every gate must pass before a row appears here</span></div>
<div class="rule"></div><div class="tw">
<table class="t"><tr><th>time</th><th>recommendation</th><th>price</th>
<th>edge</th><th>state</th><th>status</th></tr>
{''.join(items) or f'<tr><td colspan="6" class="empty">No advisories yet — the engine is scanning {s["watched"]} open markets ({s["live"]} live now) and has held fire: nothing cleared edge ≥6% with sufficient model + state confidence. Disciplined silence, not downtime.</td></tr>'}
</table></div></section>"""
    return respond(request, "Advisories", "home", body)


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
<a href="/match/{esc(sc.event_ticker)}" style="text-decoration:none">
<div class="title">{esc(match_label)} <span class="sub2">→</span></div></a>
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
    return respond(request, "Scenarios", "scenarios", body)


TP_LIMIT = 90  # take-profit limit price (cents) for the TP variant


def _tp_effective(b, result, touched90):
    """Take-profit-at-90 outcome for a paper bet, derived from the same pick.
    A limit sell at 90¢ fills whenever our side's bid reaches 90 — which any
    eventual winner does en route to 100, and a was-winning-then-lost match
    does mid-play. Returns (status, pnl_cents)."""
    from bot.track import advisory_outcome

    u = b.units or 1
    if b.price_cents >= TP_LIMIT:  # TP below entry is nonsensical → behave as hold
        return b.status, b.pnl_cents
    o = advisory_outcome(b.side, result)  # won | lost | void | None
    if o == "void":
        return "void", 0
    if touched90 or o == "won":
        return "took_profit", (TP_LIMIT - b.price_cents) * u
    if o == "lost":
        return "lost", -b.price_cents * u
    return "open", None


def _opponent_surname(title: str | None, pick_surname: str) -> str:
    """Pull the loser/opponent surname out of a Kalshi title
    ('Will X win the A vs B: ... match?')."""
    if not title or " vs " not in title:
        return "the opponent"
    mid = title.split(" the ", 1)[-1].split(":")[0].split(" match")[0]
    parts = [p.strip().split()[-1] for p in mid.split(" vs ") if p.strip()]
    for p in parts:
        if p.lower() != pick_surname.lower():
            return p
    return parts[-1] if parts else "the opponent"


def postgame_analysis(pick: str, opp: str, side: str, result: str | None,
                      scoreline: str | None, sets_a, sets_b, touched90: bool,
                      price_cents: int, hold_pnl, tp_status: str, tp_pnl,
                      clv, thesis: str) -> str:
    """Deterministic post-game read on a settled bet: what actually happened vs
    the pre-game thesis, the exit outcome, and CLV. No LLM — the fact block is
    the prose (same discipline as the advisories)."""
    from bot.mapping_fix import flip_scoreline
    from bot.track import advisory_outcome

    o = advisory_outcome(side, result)
    if o not in ("won", "lost") or sets_a is None or sets_b is None:
        return ""
    # match_score_log is YES-oriented; flip to the side we actually backed
    if side == "yes":
        ps, os_, line = sets_a, sets_b, (scoreline or "")
    else:
        ps, os_, line = sets_b, sets_a, flip_scoreline(scoreline or "")
    pick, opp, line = esc(pick), esc(opp), esc(line)
    won = o == "won"
    wsets = max(ps, os_)
    straight = won and os_ == 0
    lost_straight = (not won) and ps == 0
    decider = wsets >= 2 and (ps + os_) == (2 * wsets - 1)  # went the full distance

    S: list[str] = []
    if won and straight:
        S.append(f"<strong>Won as planned.</strong> {pick} closed it out {line} "
                 f"in straight sets — {opp} never drew level on sets.")
    elif won and decider:
        S.append(f"<strong>Won the hard way.</strong> {pick} dropped a set but "
                 f"took the decider, {line} — the distance held up in our favor.")
    elif won:
        S.append(f"<strong>Won.</strong> {pick} came through {line}.")
    elif lost_straight:
        S.append(f"<strong>Missed.</strong> {pick} lost in straight sets {line}; "
                 f"the edge never materialized and {opp} led throughout.")
    elif decider:
        S.append(f"<strong>Missed in a decider.</strong> {pick} forced the "
                 f"distance but lost {line} — right that it would be close, wrong "
                 f"on the finish.")
    else:
        S.append(f"<strong>Missed.</strong> {pick} lost {line}.")

    if not won and touched90 and tp_pnl is not None:
        S.append(f"But {pick} led first — our side traded up to {TP_LIMIT}¢ before "
                 f"the reversal, so the {TP_LIMIT}¢ take-profit salvaged "
                 f"+{tp_pnl}¢ here where holding took the full −{price_cents}¢.")
    elif won and tp_status == "took_profit" and hold_pnl is not None \
            and tp_pnl is not None and tp_pnl < hold_pnl:
        S.append(f"Hold banked +{hold_pnl}¢; the {TP_LIMIT}¢ exit capped at "
                 f"+{tp_pnl}¢ — ~{hold_pnl - tp_pnl}¢ given up for the early lock-in.")

    if thesis:
        first = thesis.split(". ")[0]
        S.append(f"Pre-game read was: “{esc(first)}.”")

    if clv is not None:
        verb = "beat" if clv > 0 else "lagged" if clv < 0 else "matched"
        S.append(f"Closing-line value: {verb} the close by {abs(clv)}¢"
                 + (" (bought too late)." if clv < 0 else "."))
    return '<div class="prose" style="margin-top:6px">' + " ".join(S) + "</div>"


async def testrun(request: web.Request) -> web.Response:
    return await _testrun_view(request, "hold")


async def testrun_tp(request: web.Request) -> web.Response:
    # both exits now live on one page (hold headline + 90¢ comparison); keep the
    # old URL working
    raise web.HTTPFound("/testrun")


async def _testrun_view(request: web.Request, mode: str) -> web.Response:
    from bot.models import KalshiMarket, PaperBet, Scenario
    from bot.paper import PAPER_MIN_EDGE, PAPER_MIN_PROB

    is_tp = mode == "tp"
    with db_session() as db:
        bets = db.execute(
            select(PaperBet, Player.full_name)
            .join(Player, Player.id == PaperBet.player_id, isouter=True)
            .order_by(PaperBet.created_at.desc()).limit(300)
        ).all()
        evs = [b.event_ticker for b, _ in bets]
        plans = {}
        if evs:
            for sc in db.execute(select(Scenario).where(
                    Scenario.event_ticker.in_(evs))
                    .order_by(Scenario.created_for)).scalars():
                plans[sc.event_ticker] = sc  # latest generation wins
        # match result + whether our side's bid touched 90 — needed for the TP
        # variant AND the hold-vs-TP comparison shown on both pages
        results, touched, closes = {}, {}, {}
        if bets:
            tks = [b.market_ticker for b, _ in bets]
            for tk, res, cl in db.execute(select(
                    KalshiMarket.ticker, KalshiMarket.result,
                    KalshiMarket.close_yes_cents).where(
                    KalshiMarket.ticker.in_(tks))).all():
                results[tk] = res
                closes[tk] = cl
            since = min(b.created_at for b, _ in bets)
            from sqlalchemy import text as sqltext
            for r in db.execute(sqltext("""
                SELECT market_ticker, max(yes_bid) yb, max(no_bid) nb
                FROM market_ticks WHERE market_ticker = ANY(:t)
                  AND kind='quote' AND ts >= :since GROUP BY market_ticker"""),
                    {"t": tks, "since": since}).all():
                touched[r[0]] = (r[1], r[2])

    def hold_eff(b):
        return b.status, b.pnl_cents

    def tp_eff(b):
        yb, nb = touched.get(b.market_ticker, (None, None))
        hit = (b.side == "yes" and (yb or 0) >= TP_LIMIT) or \
              (b.side == "no" and (nb or 0) >= TP_LIMIT)
        return _tp_effective(b, results.get(b.market_ticker), hit)

    hold_effs = {b.id: hold_eff(b) for b, _ in bets}
    tp_effs = {b.id: tp_eff(b) for b, _ in bets}
    effs = tp_effs if is_tp else hold_effs
    WON = ("won", "took_profit")
    settled = [(b, p) for b, p in bets if effs[b.id][0] in ("won", "lost", "took_profit")]
    open_bets = [(b, p) for b, p in bets if effs[b.id][0] == "open"]

    # Headline record/profit for BOTH exit rules on ONE basis: matches that have
    # FINISHED (result known), each derived from that same result. Identical
    # denominators — no timing-artifact mismatch between the hold and TP records.
    from bot.track import advisory_outcome

    def cmp_out(b, tp):
        o = advisory_outcome(b.side, results.get(b.market_ticker))
        u = b.units or 1
        if o is None:
            return None                       # match not finished → excluded
        if o == "void":
            return 0
        if not tp or b.price_cents >= TP_LIMIT:
            return (100 - b.price_cents) * u if o == "won" else -b.price_cents * u
        yb, nb = touched.get(b.market_ticker, (None, None))
        hit = (b.side == "yes" and (yb or 0) >= TP_LIMIT) or \
              (b.side == "no" and (nb or 0) >= TP_LIMIT)
        return (TP_LIMIT - b.price_cents) * u if (hit or o == "won") else -b.price_cents * u

    finished = [(b, p) for b, p in bets if cmp_out(b, False) is not None]

    def mode_stats(tp):
        w = sum(1 for b, _ in finished if cmp_out(b, tp) > 0)
        l = sum(1 for b, _ in finished if cmp_out(b, tp) < 0)
        pc = sum(cmp_out(b, tp) for b, _ in finished)
        stk = sum(b.price_cents * (b.units or 1) for b, _ in finished)
        un = sum(cmp_out(b, tp) / b.price_cents for b, _ in finished if b.price_cents)
        return w, l, pc, un, (pc / stk if stk else None)

    wins, losses, pnl, profit_units, roi_v = mode_stats(is_tp)
    n = wins + losses
    win_rate = wins / n if n else None
    staked = sum(b.price_cents * (b.units or 1) for b, _ in finished)
    first = min((b.created_at for b, _ in bets), default=None)
    days = (datetime.now(timezone.utc) - first).days if first else 0

    def bucket(pred) -> str:
        s = [b for b, _ in settled if pred(b)]
        w = sum(1 for b in s if effs[b.id][0] in WON)
        return f"{w}-{len(s) - w}" if s else "0-0"

    target_txt = f"{win_rate:.0%}" if win_rate is not None else "—"
    target_color = "var(--good)" if (win_rate or 0) >= 0.70 else \
        ("var(--warning)" if (win_rate or 0) >= 0.60 else "var(--text)")
    pcolor = "var(--good)" if pnl > 0 else ("var(--accent)" if pnl < 0 else "var(--text)")
    # closing-line value: did our entry beat the match-start line?
    from bot.track import clv_cents
    clvs = [(b, clv_cents(b.side, b.price_cents, closes.get(b.market_ticker)))
            for b, _ in bets]
    clv_by_id = {b.id: c for b, c in clvs}
    clv_vals = [c for _, c in clvs if c is not None]
    beat = sum(1 for c in clv_vals if c > 0)
    avg_clv = sum(clv_vals) / len(clv_vals) if clv_vals else None
    clv_color = ("var(--good)" if avg_clv and avg_clv > 0 else
                 "var(--accent)" if avg_clv and avg_clv < 0 else "var(--text)")
    strip = statstrip([
        ("Record", f"{wins}-{n - wins}" if n else "0-0", f"{len(open_bets)} open"),
        ("Win rate", f'<span style="color:{target_color}">{target_txt}</span>',
         "target 70% by month 1"),
        ("Profit ($)", f'<span style="color:{pcolor}">{pnl / 100:+.2f}</span>' if n else "—",
         "1 contract per unit"),
        ("Profit (units)", f'<span style="color:{pcolor}">{profit_units:+.2f}u</span>' if n else "—",
         "profit ÷ stake, unit-weighted"),
        ("ROI", f"{pnl / staked:+.1%}" if staked else "—", f"{days}d running"),
        ("CLV", f'<span style="color:{clv_color}">{avg_clv:+.1f}¢</span>'
         if avg_clv is not None else "—",
         f"beat close {beat}/{len(clv_vals)}" if clv_vals else "vs match-start line"),
    ] + (lambda ow, ol, opc, oun, _r: [
        (("90¢" if not is_tp else "Hold") + " · record",
         f"{ow}-{ol}", "same picks, other exit"),
        (("90¢" if not is_tp else "Hold") + " · profit",
         f'<span style="color:{"var(--good)" if opc > 0 else "var(--accent)" if opc < 0 else "var(--text)"}">{opc / 100:+.2f}</span>'
         if (ow + ol) else "—", "on the same finished matches"),
    ])(*mode_stats(not is_tp)), cols=4)
    breakdown = f"""<div class="metric-grid" style="grid-template-columns:repeat(6,1fr)">
<div class="metric"><div class="k">prematch basis</div><div class="v mono">{bucket(lambda b: b.basis == 'prematch')}</div></div>
<div class="metric"><div class="k">advisory basis</div><div class="v mono">{bucket(lambda b: b.basis == 'advisory')}</div></div>
<div class="metric"><div class="k">tour (A)</div><div class="v mono">{bucket(lambda b: b.tier == 'A')}</div></div>
<div class="metric"><div class="k">challenger</div><div class="v mono">{bucket(lambda b: b.tier == 'C')}</div></div>
<div class="metric"><div class="k">ITF</div><div class="v mono">{bucket(lambda b: b.tier == '15')}</div></div>
<div class="metric"><div class="k">prob ≥ 80%</div><div class="v mono">{bucket(lambda b: b.model_prob >= 0.8)}</div></div>
<div class="metric"><div class="k">2u+ bets</div><div class="v mono">{bucket(lambda b: (b.units or 1) >= 2)}</div></div>
</div>"""

    def why(b, player) -> str:
        """Every bet explains itself: sizing logic + the match's gameflow read."""
        r = b.reasoning or {}
        basis_txt = ("placed pre-match, evaluated against the opening quote"
                     if b.basis == "prematch"
                     else f"triggered by an in-play advisory at {esc(b.state_at_placement)} sets")
        parts = [
            f"Model made {esc((player or 'the pick').split()[-1])} "
            f"{b.model_prob:.0%} against a {b.price_cents}¢ ask — a "
            f"{b.edge * 100:.1f}% edge at {b.model_confidence:.0%} model "
            f"confidence; {basis_txt}.",
            f"Sized {b.units or 1}u: " + (
                "baseline unit — cleared the entry gates without 2u conviction."
                if (b.units or 1) == 1 else
                "probability, edge and confidence all cleared the 2u conviction bar."
                if b.units == 2 else
                "extreme reading on all three axes — the rare 3u."),
        ]
        pr = r.get("policy_reason")
        if pr:
            parts.append(f"Policy check: {esc(pr)}.")
        sc = plans.get(b.event_ticker)
        if sc:
            first_two = ". ".join(sc.narrative.split(". ")[:2])
            parts.append(f"Gameflow read: {esc(first_two)}… "
                         f"<a href='/match/{esc(b.event_ticker)}'>full match data →</a>")
        return f'<div class="prose" style="margin-top:6px">{" ".join(parts)}</div>'

    def rows_html(pairs) -> str:
        out = []
        for b, player in pairs:
            st, pc = effs[b.id]
            oc = {"won": tag("good", "✓", "won"),
                  "took_profit": tag("good", "✓", "TP @90¢"),
                  "lost": tag("accent", "✕", "lost"),
                  "void": tag("neutral", "·", "void"),
                  "open": tag("warn", "…", "open")}[st]
            pnl_txt = f"{pc:+d}¢" if pc is not None else "—"
            match = (b.reasoning or {}).get("match", b.event_ticker)
            out.append(f"""<tr>
<td class="mono sub2">{pt(b.created_at)}</td>
<td><span class="pname">{esc(player)}</span><br><span class="sub2">{esc(match)}</span>
· {kalshi_link(b.market_ticker)}
{why(b, player)}</td>
<td class="mono">{b.price_cents}¢</td>
<td class="mono" style="font-weight:800">{b.units or 1}u</td>
<td class="mono">{b.model_prob:.0%}</td>
<td class="mono">+{b.edge * 100:.1f}%</td>
<td>{tag('neutral', '·', b.basis)} {tag('neutral', '·', b.tier or '?')}</td>
<td>{oc}</td>
<td class="mono" style="text-align:right">{f'{clv_by_id[b.id]:+d}¢' if clv_by_id.get(b.id) is not None else '—'}</td>
<td class="mono" style="text-align:right;font-weight:800">{pnl_txt}</td></tr>""")
        return "".join(out)

    # pace vs the 70% month-1 target
    if n == 0:
        pace = "No settled bets yet — pace unknown."
    elif win_rate >= 0.70:
        pace = f"On target: {wins}-{n - wins} ({win_rate:.1%}) — hold above 70%."
    else:
        import math
        need = math.ceil((0.70 * n - wins) / 0.30)
        pace = (f"Below target at {win_rate:.1%}: needs {need} straight winners "
                f"to reach 70% — the tuning breakdown below says where to look.")

    # cumulative timeline — both exit rules overlaid, policy-version markers
    def cum_of(tp):
        chron = sorted([b for b, _ in finished if b.settled_at],
                       key=lambda b: b.settled_at)
        cum, series, vm, last = 0, [], [], None
        for b in chron:
            cum += cmp_out(b, tp)
            series.append((b.settled_at, cum / 100))
            ver = (b.reasoning or {}).get("policy_version", "v1")
            if ver != last:
                if last is not None:
                    vm.append((b.settled_at, ver))
                last = ver
        return series, vm

    pts, vmarks = cum_of(False)
    tp_pts, _ = cum_of(True)
    timeline = timeline_svg(pts, "$", vmarks, points2=tp_pts,
                            label="hold to settlement", label2="90¢ take-profit")
    timeline_html = f"""<section class="block"><div class="blockhead">
<h4>Cumulative P&amp;L</h4><span class="aside">{esc(pace)}</span></div>
<div class="rule"></div>{timeline or
    '<p class="prose">Chart appears after the first two settlements.</p>'}
<p class="sub2">Both exit rules on the same picks · dashed markers = policy
version changes; records before and after a tune are never blended silently.</p></section>"""

    # --- Watching: matches the policy is currently evaluating ---
    from bot.models import Scenario
    from bot.paper import decide_bet

    now = datetime.now(timezone.utc)
    with db_session() as db:
        bet_events = set(db.execute(select(PaperBet.event_ticker)).scalars().all())
        cand = db.execute(
            select(Scenario, Player.full_name)
            .join(Player, Player.id == Scenario.player_id, isouter=True)
            .where(Scenario.scheduled_start > now - timedelta(hours=6),
                   Scenario.scheduled_start < now + timedelta(hours=24))
            .order_by(Scenario.scheduled_start)).all()
        cand = [(sc, nm) for sc, nm in cand if sc.event_ticker not in bet_events]
        tickers = [sc.market_ticker for sc, _ in cand]
        quotes = _latest_quotes(db, tickers) if tickers else {}
        sl = {}
        if tickers:
            from sqlalchemy import text as sqltext
            for r in db.execute(sqltext("""
                SELECT DISTINCT ON (market_ticker) market_ticker, scoreline
                FROM match_score_log WHERE market_ticker = ANY(:t)
                ORDER BY market_ticker, ts DESC"""), {"t": tickers}).all():
                sl[r[0]] = r[1]

    watch_rows, seen_ev = [], set()
    for sc, nm in cand:
        if sc.event_ticker in seen_ev:
            continue
        seen_ev.add(sc.event_ticker)
        q = quotes.get(sc.market_ticker)
        yb, ya = (q[0], q[1]) if q else (None, None)
        conf = (sc.facts or {}).get("model_confidence", 0.7)
        dec = decide_bet(sc.prematch_prob, conf, ya, yb)
        price = ya if ya is not None else "—"
        if dec.place:
            verdict = tag("good", "✓", f"clears — {dec.units}u candidate")
        elif ya is None:
            verdict = tag("neutral", "○", "awaiting live price")
        else:
            verdict = tag("warn", "◉", "watching · " + dec.reason.split(",")[0])
        live_sl = sl.get(sc.market_ticker)
        watch_rows.append((sc.scheduled_start, f"""<tr>
<td class="mono sub2">{pt(sc.scheduled_start)}</td>
<td><a href="/match/{esc(sc.event_ticker)}" style="text-decoration:none">
<span class="pname">{esc(nm)}</span></a>
{f'<br><span class="mono sub2">{esc(live_sl)}</span>' if live_sl else ''}</td>
<td class="mono">{sc.prematch_prob:.0%}</td>
<td class="mono">{price}{'¢' if price != '—' else ''}</td>
<td>{verdict}</td></tr>"""))
    watch_rows.sort(key=lambda r: (r[0] or now))
    clears = sum(1 for _, h in watch_rows if "clears —" in h)
    watching_html = f"""<section class="block"><details class="coll" data-key="watch_open">
<summary style="cursor:pointer;list-style:none;display:flex;align-items:baseline;
justify-content:space-between;gap:12px">
<span><span style="font-family:var(--font);font-weight:800;font-size:19px">Watching</span>
<span class="sub2" style="margin-left:8px">{len(watch_rows)} evaluating ·
{clears} clearing gates now</span></span>
<span class="sub2 coll-caret">▸ show</span></summary>
<div class="rule" style="margin-top:8px"></div>
<div class="tw"><table class="t"><tr><th>starts</th><th>watch side</th>
<th>model</th><th>price</th><th>policy verdict</th></tr>
{''.join(r[1] for r in watch_rows) or
 '<tr><td colspan="5" class="empty">Nothing in the evaluation window right now.</td></tr>'}
</table></div>
<p class="sub2" style="margin-top:6px">A bet fires only when the price meets the
model — most of these will pass without one. ✓ = clears every gate now;
◉ = tracked, gate not yet met.</p></details></section>"""

    pregame_watch = sum(1 for _, h in watch_rows)
    empty_note = (f"No settled bets yet — the policy evaluated {pregame_watch} "
                  f"upcoming matches in the last 24h and {clears} currently clear "
                  f"every gate. Disciplined silence: it stays flat until price "
                  f"meets model, not because nothing is happening.")

    def bets_section(label, aside, basis, key) -> str:
        opens = [x for x in open_bets if x[0].basis == basis]
        setts = [x for x in settled if x[0].basis == basis]
        w = sum(1 for b, _ in setts if effs[b.id][0] in WON)
        rec = f"{w}-{len(setts) - w}" if setts else "0-0"
        rowsel = rows_html(opens) + rows_html(setts)
        empty = (f'<tr><td colspan="10" class="empty">No {esc(label.lower())} '
                 f'settled yet. {empty_note if basis == "prematch" else "In-play bets fire only when a live advisory clears the policy mid-match."}</td></tr>')
        return f"""<section class="block"><details class="coll" data-key="{key}">
<summary style="cursor:pointer;list-style:none;display:flex;align-items:baseline;
justify-content:space-between;gap:12px">
<span><span style="font-family:var(--font);font-weight:800;font-size:19px">{label}</span>
<span class="sub2" style="margin-left:8px">{esc(aside)} · {rec} settled · {len(opens)} open</span></span>
<span class="sub2 coll-caret">▸ show</span></summary>
<div class="rule" style="margin-top:8px"></div><div class="tw">
<table class="t"><tr><th>placed</th><th>pick</th><th>price</th><th>units</th><th>model</th>
<th>edge</th><th>tier</th><th>{status_th}</th><th style="text-align:right">CLV</th>
<th style="text-align:right">P&amp;L</th></tr>
{rowsel or empty}
</table></div></details></section>"""

    # --- Hold vs Take-Profit comparison ---
    # Like-for-like: only matches that have FINISHED (result known), with both
    # exit rules derived from that same result. Identical denominators — the
    # only differences are exit outcomes (reversal salvages), not timing.
    from bot.track import advisory_outcome

    def cstats(tp: bool):  # reuses the page-level cmp_out / finished / mode_stats
        return mode_stats(tp)

    nfin = len(finished)
    tp_live = sum(1 for b, _ in bets if results.get(b.market_ticker) is None
                  and (lambda t: (b.side == "yes" and (t[0] or 0) >= TP_LIMIT)
                       or (b.side == "no" and (t[1] or 0) >= TP_LIMIT))(
                      touched.get(b.market_ticker, (None, None))))
    comparison_html = ""
    if nfin:
        hw, hl, hpc, hun, hroi = cstats(False)
        tw, tl, tpc, tun, troi = cstats(True)
        roi = lambda v: f"{v:+.1%}" if v is not None else "—"
        wr = lambda w, t: f"{w / t:.0%}" if t else "—"

        # per-match decomposition: TP salvages reversals (+90¢ each) and caps
        # clean winners (−10¢ each) — this is exactly why the two differ
        salv_n = salv = cap_n = cap = 0
        for b, _ in finished:
            d = cmp_out(b, True) - cmp_out(b, False)
            if d > 0:
                salv_n += 1; salv += d
            elif d < 0:
                cap_n += 1; cap += d
        net = salv + cap

        def drow(label, hv, tv, dv, dcolor=None):
            dc = dcolor or ("var(--good)" if dv.startswith("+") and dv != "+0"
                            else "var(--accent)" if dv.startswith("-") else "var(--muted)")
            return (f"<tr><td class='sub2'>{label}</td>"
                    f"<td class='mono' style='text-align:right'>{hv}</td>"
                    f"<td class='mono' style='text-align:right'>{tv}</td>"
                    f"<td class='mono' style='text-align:right;color:{dc}'>{dv}</td></tr>")

        d_pnl = tpc - hpc
        d_un = tun - hun
        table = f"""<table class="t" style="max-width:560px">
<tr><th>metric</th><th style="text-align:right">Hold</th>
<th style="text-align:right">Take-Profit</th><th style="text-align:right">Δ</th></tr>
{drow("record", f"{hw}-{hl}", f"{tw}-{tl}", f"{tw - hw:+d}W", "var(--muted)")}
{drow("win rate", wr(hw, hw + hl), wr(tw, tw + tl), f"{(tw/(tw+tl) if (tw+tl) else 0) - (hw/(hw+hl) if (hw+hl) else 0):+.0%}")}
{drow("profit ($)", f"{hpc / 100:+.2f}", f"{tpc / 100:+.2f}", f"{d_pnl / 100:+.2f}")}
{drow("profit (units)", f"{hun:+.2f}u", f"{tun:+.2f}u", f"{d_un:+.2f}u")}
{drow("ROI", roi(hroi), roi(troi), f"{(troi - hroi):+.1%}" if hroi is not None and troi is not None else "—")}
</table>"""
        verdict = ("Take-profit is ahead" if net > 0 else "Hold is ahead"
                   if net < 0 else "Dead even")
        comparison_html = f"""<section class="block"><div class="blockhead">
<h4>Hold vs Take-Profit</h4><span class="aside">same {nfin} finished matches · identical picks</span></div>
<div class="rule"></div>
<div class="tw">{table}</div>
<p class="prose" style="margin-top:10px"><strong>{verdict}</strong> by
{abs(net) / 100:+.2f} on these {nfin} matches. Why they differ: take-profit
<strong>salvaged {salv_n}</strong> lead(s) that reversed (+{salv / 100:.2f}, the
limit banked ~90¢ before the collapse) and <strong>capped {cap_n}</strong> clean
winner(s) at 90¢ ({cap / 100:.2f}, giving up ~10¢ each vs holding to 100¢).
TP wins this trade-off only when it salvages at least one reversal per nine
winners it caps — here {salv_n} vs {cap_n}. Both curves are plotted together in
the Cumulative P&amp;L chart above.</p>
{f'<p class="sub2">(TP has also realized {tp_live} live position(s) on matches still in play — held out here for a like-for-like record.)</p>' if tp_live else ''}
</section>"""

    title = "Bot Testrun"
    active = "testrun"
    exit_note = f"""<p class="prose" style="margin:0 0 18px">The bot places
<strong>imaginary</strong> one-contract bets for itself — selectively; most
matches get no bet. The headline record holds every bet to settlement
(100¢/0¢); the <strong>{TP_LIMIT}¢ take-profit</strong> variant below runs the
<em>same</em> picks with a limit sell at {TP_LIMIT}¢ — banking {TP_LIMIT}¢ on
winners but salvaging leads that later collapse. Both exits are tracked and
compared on this one page. Settled results are the tuning data: the policy
iterates until the record holds above 70%. Nothing here is, or ever becomes, a
real order.</p>"""
    status_th = "status"
    body = pagehead("Strategy Lab", title,
                    f'{n} settled · <a href="/testrun/history">post-game log →</a> · '
                    f'<a href="/track">advisory track record →</a>') \
        + strip + exit_note + watching_html + timeline_html + f"""
{comparison_html}
<section class="block"><div class="blockhead"><h4>Tuning breakdown</h4>
<span class="aside">where the record comes from — the improvement signal</span></div>
<div class="rule"></div>{breakdown}</section>
{bets_section("Pre-game bets", "placed before the match, off the model's "
              "opening read", "prematch", "pre_open")}
{bets_section("Live-game bets", "fired in-play when an advisory cleared the "
              "policy mid-match", "advisory", "live_open")}"""
    return respond(request, title, active, body)


async def testrun_history(request: web.Request) -> web.Response:
    """Every settled testrun bet with a post-game read: did the thesis hold,
    what the match actually did, how each exit fared, and CLV."""
    from sqlalchemy import text as sqltext

    from bot.models import KalshiMarket, PaperBet, Scenario
    from bot.track import advisory_outcome, clv_cents

    with db_session() as db:
        bets = db.execute(
            select(PaperBet, Player.full_name)
            .join(Player, Player.id == PaperBet.player_id, isouter=True)
            .where(PaperBet.status.in_(("won", "lost", "void")))
            .order_by(PaperBet.settled_at.desc().nullslast(),
                      PaperBet.created_at.desc()).limit(150)
        ).all()
        results, closes, titles, touched, scorel, plans = {}, {}, {}, {}, {}, {}
        if bets:
            tks = [b.market_ticker for b, _ in bets]
            evs = [b.event_ticker for b, _ in bets]
            for tk, res, cl, tt in db.execute(select(
                    KalshiMarket.ticker, KalshiMarket.result,
                    KalshiMarket.close_yes_cents, KalshiMarket.title)
                    .where(KalshiMarket.ticker.in_(tks))).all():
                results[tk], closes[tk], titles[tk] = res, cl, tt
            since = min(b.created_at for b, _ in bets)
            for r in db.execute(sqltext(
                "SELECT market_ticker, max(yes_bid) yb, max(no_bid) nb "
                "FROM market_ticks WHERE market_ticker = ANY(:t) AND kind='quote' "
                "AND ts >= :since GROUP BY market_ticker"),
                    {"t": tks, "since": since}).all():
                touched[r[0]] = (r[1], r[2])
            for r in db.execute(sqltext(
                "SELECT DISTINCT ON (market_ticker) market_ticker, scoreline, "
                "sets_a, sets_b FROM match_score_log WHERE market_ticker = ANY(:t) "
                "ORDER BY market_ticker, ts DESC"), {"t": tks}).all():
                scorel[r[0]] = (r[1], r[2], r[3])
            for sc in db.execute(select(Scenario).where(
                    Scenario.event_ticker.in_(evs))
                    .order_by(Scenario.created_for)).scalars():
                plans[sc.event_ticker] = sc

    entries, settled, held, salvaged = [], 0, 0, 0
    for b, player in bets:
        res = results.get(b.market_ticker)
        o = advisory_outcome(b.side, res)
        if o not in ("won", "lost"):
            continue
        settled += 1
        held += (o == "won")
        line, sa, sb = scorel.get(b.market_ticker, (None, None, None))
        yb, nb = touched.get(b.market_ticker, (None, None))
        t90 = (b.side == "yes" and (yb or 0) >= TP_LIMIT) or \
              (b.side == "no" and (nb or 0) >= TP_LIMIT)
        tp_status, tp_pnl = _tp_effective(b, res, t90)
        clv = clv_cents(b.side, b.price_cents, closes.get(b.market_ticker))
        pick = (player or "").split()[-1] or "the pick"
        opp = _opponent_surname(titles.get(b.market_ticker), pick)
        thesis = plans[b.event_ticker].narrative if b.event_ticker in plans else ""
        if o == "lost" and t90:
            salvaged += 1
        analysis = postgame_analysis(pick, opp, b.side, res, line, sa, sb, t90,
                                     b.price_cents, b.pnl_cents, tp_status, tp_pnl,
                                     clv, thesis)
        badge = tag("good", "✓", "won") if o == "won" else tag("accent", "✕", "lost")
        pc = lambda v: ("var(--good)" if (v or 0) > 0 else
                        "var(--accent)" if (v or 0) < 0 else "var(--muted)")
        tp_txt = (f"+{tp_pnl}¢" if tp_status == "took_profit" else
                  f"{tp_pnl:+d}¢" if tp_pnl is not None else "—")
        entries.append(f"""<div style="padding:14px 0;border-top:1px solid var(--divider)">
<div class="blockhead"><h4 style="font-size:16px">{esc(player or pick)} {badge}
<span class="mono sub2" style="font-weight:400">{esc(line or '—')}</span></h4>
<span class="aside">{pt(b.settled_at) if b.settled_at else ''}</span></div>
{analysis}
<div class="metric-grid" style="grid-template-columns:repeat(4,1fr);margin-top:8px">
<div class="metric"><div class="k">bet</div><div class="v mono">{esc(b.side)} @ {b.price_cents}¢ · {b.units or 1}u</div></div>
<div class="metric"><div class="k">hold P&amp;L</div><div class="v mono" style="color:{pc(b.pnl_cents)}">{f'{b.pnl_cents:+d}¢' if b.pnl_cents is not None else '—'}</div></div>
<div class="metric"><div class="k">90¢ take-profit</div><div class="v mono" style="color:{pc(tp_pnl)}">{tp_txt}</div></div>
<div class="metric"><div class="k">CLV</div><div class="v mono" style="color:{pc(clv)}">{f'{clv:+d}¢' if clv is not None else '—'}</div></div>
</div>
<a class="sub2" href="/match/{esc(b.event_ticker)}">full match data →</a></div>""")

    hold_rate = f"{held / settled:.0%}" if settled else "—"
    strip = statstrip([
        ("Settled bets", str(settled), "post-game analysed"),
        ("Thesis held", f"{held}/{settled}", f"{hold_rate} of picks won"),
        ("Reversals salvaged", str(salvaged),
         "lost on hold, +90¢ on take-profit"),
    ])
    intro = ("""<p class="prose" style="margin:0 0 18px">Every settled bot bet,
newest first, with a post-game read: whether the pre-game thesis held, what the
match actually did, how each exit (hold vs 90¢) fared, and closing-line value.
Generated from the recorded scoreline and settlement — advisory only.</p>""")
    log_html = (f'<section class="block"><div class="blockhead"><h4>Post-game log'
                f'</h4><span class="aside">{settled} settled bets</span></div>'
                f'{"".join(entries)}</section>' if entries else
                '<section class="block"><p class="prose">No settled bets yet — '
                'analysis appears here once the first bet resolves.</p></section>')
    body = pagehead("Strategy Lab", "Testrun History",
                    '<a href="/testrun">← back to testrun</a>') \
        + strip + intro + log_html
    return respond(request, "Testrun History", "testrun", body)


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
    return respond(request, "Track record", "track", body)


def price_chart_svg(points: list[tuple[datetime, float]], marks: list[dict],
                    label: str) -> str:
    """Price line (side-A mid, 0-100¢) with the bot's actions annotated.

    Marker shapes carry identity (not color alone): ◆ boundary inferred,
    ✓ score confirmed, ▲ advisory, ● paper bet. Single series → no legend box
    needed for the line itself; the marker legend renders below in HTML.
    """
    if len(points) < 5:
        return ""
    W, H, PL, PR, PT_, PB = 860, 220, 46, 10, 12, 26
    t0, t1 = points[0][0].timestamp(), points[-1][0].timestamp()
    if t1 - t0 < 60:
        return ""

    def x(ts: float) -> float:
        return PL + (ts - t0) / (t1 - t0) * (W - PL - PR)

    def y(price: float) -> float:
        return PT_ + (100 - price) / 100 * (H - PT_ - PB)

    path = "M" + " L".join(f"{x(p[0].timestamp()):.1f},{y(p[1]):.1f}" for p in points)
    grid = "".join(
        f'<line x1="{PL}" y1="{y(g):.0f}" x2="{W - PR}" y2="{y(g):.0f}" '
        f'stroke="rgba(243,242,242,.10)" stroke-width="1"/>'
        f'<text x="{PL - 8}" y="{y(g) + 4:.0f}" text-anchor="end" '
        f'font-size="10" fill="rgba(243,242,242,.45)">{g}¢</text>'
        for g in (25, 50, 75))
    marks_svg = []
    for m in marks:
        ts = m["ts"].timestamp()
        if not (t0 <= ts <= t1):
            continue
        mx, my = x(ts), y(m.get("price", 50))
        title = esc(m["title"])
        if m["kind"] == "boundary":
            col = "var(--good)" if m.get("hit") else (
                "var(--critical)" if m.get("hit") is False else "var(--warning)")
            marks_svg.append(
                f'<g><title>{title}</title><rect x="{mx - 5:.0f}" y="{my - 5:.0f}" '
                f'width="10" height="10" transform="rotate(45 {mx:.0f} {my:.0f})" '
                f'fill="{col}"/></g>')
        elif m["kind"] == "score":
            marks_svg.append(
                f'<g><title>{title}</title><text x="{mx:.0f}" y="{my - 8:.0f}" '
                f'text-anchor="middle" font-size="13" fill="var(--good)">✓</text></g>')
        elif m["kind"] == "advisory":
            marks_svg.append(
                f'<g><title>{title}</title><path d="M{mx:.0f},{my - 7:.0f} '
                f'l6,11 l-12,0 z" fill="var(--accent)"/></g>')
        elif m["kind"] == "bet":
            marks_svg.append(
                f'<g><title>{title}</title><circle cx="{mx:.0f}" cy="{my:.0f}" r="5" '
                f'fill="none" stroke="var(--accent)" stroke-width="2"/></g>')
    start_lbl = points[0][0].astimezone(PACIFIC).strftime("%H:%M")
    end_lbl = points[-1][0].astimezone(PACIFIC).strftime("%H:%M PT")
    legend = ('<div class="sub2" style="display:flex;gap:16px;flex-wrap:wrap;margin-top:6px">'
              '<span><span style="color:var(--warning)">◆</span> boundary inferred '
              '(green=confirmed, red=miss)</span>'
              '<span><span style="color:var(--good)">✓</span> score update</span>'
              '<span><span style="color:var(--accent)">▲</span> advisory</span>'
              '<span><span style="color:var(--accent)">●</span> paper bet</span></div>')
    return f"""<section class="block"><div class="blockhead">
<h4>Price · {esc(label)}</h4><span class="aside">{start_lbl} → {end_lbl}</span></div>
<div class="rule"></div>
<div class="tw"><svg viewBox="0 0 {W} {H}" role="img"
 aria-label="market price over time with bot actions annotated"
 style="width:100%;min-width:640px;display:block">
{grid}
<path d="{path}" fill="none" stroke="var(--accent)" stroke-width="2"
 stroke-linejoin="round"/>
{''.join(marks_svg)}
</svg></div>{legend}</section>"""


def timeline_svg(points: list[tuple[datetime, float]], unit: str,
                 vmarks: list[tuple[datetime, str]] = (),
                 points2: list[tuple[datetime, float]] | None = None,
                 label: str = "", label2: str = "") -> str:
    """Cumulative-value line with vertical annotation markers (policy versions).
    Auto y-domain; zero line emphasized when the range crosses it. An optional
    second series (points2) overlays in a muted stroke for A/B comparison."""
    if len(points) < 2:
        return ""
    W, H, PL, PR, PT_, PB = 860, 200, 54, 10, 12, 26
    allpts = list(points) + list(points2 or [])
    t0 = min(p[0].timestamp() for p in allpts)
    t1 = max(p[0].timestamp() for p in allpts)
    vals = [v for _, v in allpts]
    lo, hi = min(min(vals), 0), max(max(vals), 0)
    if hi == lo:
        hi = lo + 1

    def x(ts): return PL + (ts - t0) / max(t1 - t0, 1) * (W - PL - PR)
    def y(v): return PT_ + (hi - v) / (hi - lo) * (H - PT_ - PB)

    def draw(pts, color, width):
        return (f'<path d="M' + " L".join(
            f"{x(p[0].timestamp()):.1f},{y(p[1]):.1f}" for p in pts) +
            f'" fill="none" stroke="{color}" stroke-width="{width}" '
            f'stroke-linejoin="round"/>')

    zero = (f'<line x1="{PL}" y1="{y(0):.0f}" x2="{W - PR}" y2="{y(0):.0f}" '
            f'stroke="rgba(243,242,242,.25)" stroke-width="1.5"/>'
            f'<text x="{PL - 8}" y="{y(0) + 4:.0f}" text-anchor="end" font-size="10" '
            f'fill="rgba(243,242,242,.45)">0{unit}</text>') if lo < 0 < hi or lo == 0 else ""
    ymax_lbl = (f'<text x="{PL - 8}" y="{y(hi) + 4:.0f}" text-anchor="end" font-size="10" '
                f'fill="rgba(243,242,242,.45)">{hi:+.0f}{unit}</text>')
    ymin_lbl = (f'<text x="{PL - 8}" y="{y(lo) + 4:.0f}" text-anchor="end" font-size="10" '
                f'fill="rgba(243,242,242,.45)">{lo:+.0f}{unit}</text>') if lo < 0 else ""
    # x-axis: first and last dates, so the run's span is legible
    d0 = min(p[0] for p in allpts)
    d1 = max(p[0] for p in allpts)
    fmt = lambda d: d.strftime("%b %d").replace(" 0", " ")
    xaxis = (f'<text x="{PL}" y="{H - 4}" font-size="10" '
             f'fill="rgba(243,242,242,.45)">{fmt(d0)}</text>'
             f'<text x="{W - PR}" y="{H - 4}" text-anchor="end" font-size="10" '
             f'fill="rgba(243,242,242,.45)">{fmt(d1)}</text>')
    vlines = "".join(
        f'<g><title>{esc(lbl)}</title>'
        f'<line x1="{x(ts.timestamp()):.0f}" y1="{PT_}" x2="{x(ts.timestamp()):.0f}" '
        f'y2="{H - PB}" stroke="var(--warning)" stroke-width="1.5" stroke-dasharray="4 3"/>'
        f'<text x="{x(ts.timestamp()) + 4:.0f}" y="{PT_ + 10}" font-size="10" '
        f'fill="var(--warning)">{esc(lbl)}</text></g>'
        for ts, lbl in vmarks)
    second = draw(points2, "var(--muted)", 2) if points2 and len(points2) >= 2 else ""
    legend = ""
    if points2 and label and label2:
        legend = (f'<div class="sub2" style="display:flex;gap:16px;margin-top:6px">'
                  f'<span><span style="color:var(--accent)">▬</span> {esc(label)}</span>'
                  f'<span><span style="color:var(--muted)">▬</span> {esc(label2)}</span></div>')
    return f"""<div class="tw"><svg viewBox="0 0 {W} {H}" role="img"
 aria-label="cumulative {esc(unit)} over time"
 style="width:100%;min-width:640px;display:block">
{zero}{ymax_lbl}{ymin_lbl}{xaxis}{vlines}{second}
{draw(points, "var(--accent)", 2)}
</svg></div>{legend}"""


def histogram_svg(buckets: list[tuple[str, int]], accent_note: str = "") -> str:
    """Small labeled bar histogram (lead-time distribution)."""
    if not buckets or all(n == 0 for _, n in buckets):
        return ""
    W, H, PB = 560, 130, 34
    bw = W / len(buckets)
    peak = max(n for _, n in buckets)
    bars = []
    for i, (label, n) in enumerate(buckets):
        bh = (n / peak) * (H - PB - 14) if peak else 0
        bx = i * bw + 8
        bars.append(
            f'<g><title>{esc(label)}: {n}</title>'
            f'<rect x="{bx:.0f}" y="{H - PB - bh:.0f}" width="{bw - 16:.0f}" '
            f'height="{bh:.0f}" fill="var(--accent)"/>'
            f'<text x="{bx + (bw - 16) / 2:.0f}" y="{H - PB - bh - 4:.0f}" '
            f'text-anchor="middle" font-size="11" fill="var(--text)">{n}</text>'
            f'<text x="{bx + (bw - 16) / 2:.0f}" y="{H - PB + 14:.0f}" '
            f'text-anchor="middle" font-size="10" '
            f'fill="rgba(243,242,242,.5)">{esc(label)}</text></g>')
    return (f'<div class="tw"><svg viewBox="0 0 {W} {H}" role="img" '
            f'aria-label="distribution{esc(accent_note)}" '
            f'style="width:100%;max-width:620px;display:block">{"".join(bars)}</svg></div>')


def trigger_html(sc, est_state: str | None, is_live: bool,
                 watch_mid: float | None) -> str:
    """Plan-vs-reality for a gameflow scenario: armed → hit → done, with the
    model-vs-market read once the trigger state arrives. v1 targets the Bo3
    decider (1-1)."""
    target = "1-1"
    if est_state == target:
        badge = tag("accent", "◎", f"trigger HIT · {target}")
        if watch_mid is not None:
            gap = sc.model_prob_at_state * 100 - watch_mid
            verdict = ("value" if gap >= 3 else "no value")
            badge += " " + tag("good" if gap >= 3 else "neutral", "±",
                               f"model {sc.model_prob_at_state:.0%} vs {watch_mid:.0f}¢ "
                               f"→ {verdict}")
        return badge
    if est_state == "final":
        return tag("neutral", "·", "plan done")
    if is_live:
        return tag("warn", "◉", f"trigger armed · watching for {target}")
    return tag("neutral", "○", f"plan set · trigger {target}")


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
        ENDED = {"finished", "complete", "ended", "closed", "cancelled", "P"}
        live_evs, soon_evs, done_evs = [], [], []
        for ev_ticker, ev in events.items():
            settled = any(m.result for m in ev["sides"])
            last_seen = max((m.last_seen_at for m in ev["sides"]
                             if m.last_seen_at), default=None)
            gone = discovery_alive and last_seen is not None and last_seen < seen_cutoff
            # authoritative: the milestone sweep's actual match status
            status = next(((m.raw or {}).get("_live_status") for m in ev["sides"]
                           if (m.raw or {}).get("_live_status")), None)
            if settled or gone or status in ENDED:
                if now - ev["occ"] <= timedelta(hours=18):
                    done_evs.append((ev_ticker, ev))
                continue
            if status == "live":
                live_evs.append((ev_ticker, ev))
            elif ev["occ"] - LIVE_WINDOW_BEFORE <= now <= ev["occ"] + LIVE_WINDOW_AFTER:
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
        from sqlalchemy import text as sqltext

        scorelines = {}
        if all_tickers:
            for r in db.execute(sqltext("""
                SELECT DISTINCT ON (market_ticker) market_ticker, scoreline, sets_a, sets_b
                FROM match_score_log WHERE market_ticker = ANY(:t)
                ORDER BY market_ticker, ts DESC"""), {"t": all_tickers}).all():
                scorelines[r[0]] = (r[1], r[2], r[3])
        from bot.models import Scenario

        plans = {}
        live_ev_tickers = [t for t, _ in live_evs]
        if live_ev_tickers:
            for sc in db.execute(select(Scenario).where(
                    Scenario.event_ticker.in_(live_ev_tickers))
                    .order_by(Scenario.created_for)).scalars():
                plans[sc.event_ticker] = sc

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
        score_row = ""
        sl = next((scorelines.get(m.ticker) for m in sides if scorelines.get(m.ticker)),
                  None)
        if sl and sl[0]:
            score_row = (f'<div class="mono" style="font-size:15px;font-weight:800">'
                         f'{esc(sl[0])} <span class="sub2">· {sl[1]}-{sl[2]} sets</span></div>')
        plan_row = ""
        sc = plans.get(ev_ticker)
        if sc is not None:
            wq = quotes.get(sc.market_ticker)
            wmid = (wq[0] + wq[1]) / 2 if wq and wq[0] is not None else None
            plan_row = (f'<div class="sub2">plan: '
                        f'{trigger_html(sc, est.state if est else None, is_live, wmid)}</div>')
        return f"""<div class="card">
<div style="display:flex;align-items:center;justify-content:space-between">
<span class="kicker" style="margin:0">{series_label.get(ev['series'], '?')}</span>
<span>{st} {play}</span></div>
<a href="/match/{esc(ev_ticker)}" style="text-decoration:none;color:inherit">
<div>{''.join(rows_html)}</div></a>
{score_row}
{plan_row}
<div class="sub2 mono">{'started' if is_live else 'starts'} {pt(ev['occ'])}
· <a href="/match/{esc(ev_ticker)}" class="sub2">match data →</a>
· {kalshi_link(sides[0].ticker)}</div>
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
    return respond(request, "Live", "live", body)


async def system(request: web.Request) -> web.Response:
    from bot.reports import graduate_report, inference_report

    from bot.config import settings as cfg_fn

    cfg = cfg_fn()
    with db_session() as db:
        text = inference_report(db)
        grad, grad_ok = graduate_report(db)
        gaps = db.execute(select(FeedGap).order_by(FeedGap.gap_start.desc()).limit(15)
                          ).scalars().all()
        clean = db.execute(select(StateInferenceLog).where(
            StateInferenceLog.session_had_gap.is_(False))).scalars().all()

    n_i = len(clean)
    hits_i = sum(1 for r in clean if r.hit)
    hit_rate = hits_i / n_i if n_i else None
    false_rate = (n_i - hits_i) / n_i if n_i else None
    leads = [r.lead_time_seconds for r in clean
             if r.hit and r.lead_time_seconds is not None]
    avg_lead = sum(leads) / len(leads) if leads else None
    est_strip = statstrip([
        ("Confirmed transitions", str(n_i),
         f"graduation needs ≥ {cfg.graduate_min_confirmed_transitions}"),
        ("Hit rate (clean)", f"{hit_rate:.0%}" if hit_rate is not None else "—",
         f"needs ≥ {cfg.graduate_min_hit_rate:.0%}"),
        ("False boundaries", f"{false_rate:.0%}" if false_rate is not None else "—",
         f"needs ≤ {cfg.graduate_max_false_boundary_rate:.0%}"),
        ("Avg score lead", f"{avg_lead:.0f}s" if avg_lead is not None else "—",
         "how far the bot beats the scoreboard"),
        ("Probation", "ON" if cfg.probation else "OFF",
         "lifts only by manual config change"),
    ])
    prog = min(1.0, n_i / cfg.graduate_min_confirmed_transitions)
    checks = [
        (n_i >= cfg.graduate_min_confirmed_transitions,
         f"{n_i}/{cfg.graduate_min_confirmed_transitions} transitions"),
        (hit_rate is not None and hit_rate >= cfg.graduate_min_hit_rate,
         f"hit rate {hit_rate:.0%}" if hit_rate is not None else "hit rate —"),
        (false_rate is not None and false_rate <= cfg.graduate_max_false_boundary_rate,
         f"false rate {false_rate:.0%}" if false_rate is not None else "false rate —"),
    ]
    check_tags = " ".join(tag("good" if ok else "neutral", "✓" if ok else "○", lbl)
                          for ok, lbl in checks)
    grad_html = f"""<section class="block"><div class="blockhead">
<h4>Graduation progress</h4>
<span class="aside">{'ALL THRESHOLDS MET — manual flip is now allowed' if grad_ok
 else 'probation continues until every check passes'}</span></div>
<div class="rule"></div>
<div style="height:10px;background:var(--surface);border:1px solid var(--divider)">
<div style="height:100%;width:{prog:.0%};background:var(--accent)"></div></div>
<p style="margin:8px 0 0">{check_tags}</p></section>"""
    buckets = [("<30s", 0), ("30-60s", 0), ("1-2m", 0), ("2-4m", 0), ("4m+", 0)]
    bl = [b[1] for b in buckets]
    for v in leads:
        i = 0 if v < 30 else 1 if v < 60 else 2 if v < 120 else 3 if v < 240 else 4
        bl[i] += 1
    buckets = [(buckets[i][0], bl[i]) for i in range(5)]
    lead_html = f"""<section class="block"><div class="blockhead">
<h4>Lead-time distribution</h4><span class="aside">seconds the estimator beat
the delayed score, confirmed inferences only</span></div>
<div class="rule"></div>{histogram_svg(buckets, " of score lead times") or
    '<p class="prose">Appears once confirmed inferences accumulate.</p>'}</section>"""
    gap_rows = "".join(
        f"<tr><td class='mono sub2'>{esc(g.market_ticker)}</td><td class='mono'>{pt(g.gap_start)}</td>"
        f"<td class='mono'>{g.duration_seconds or 0:.0f}s</td></tr>" for g in gaps)
    with db_session() as db:
        qrows = db.execute(select(MatchReviewQueue).where(
            MatchReviewQueue.resolved.is_(False))
            .order_by(MatchReviewQueue.created_at.desc()).limit(100)).scalars().all()
    qitems = "".join(
        f"<tr><td class='pname'>{esc(r.raw_name)}</td><td>{esc(r.source)}</td>"
        f"<td class='sub2'>{esc((r.context or {}).get('reason'))}</td>"
        f"<td class='mono sub2'>{esc((r.context or {}).get('ticker', ''))}</td>"
        f"<td class='mono sub2'>{pt(r.created_at)}</td></tr>" for r in qrows)
    queue_html = f"""<section class="block"><div class="blockhead">
<h4>Review queue — unmatched names ({len(qrows)})</h4>
<span class="aside">never silently dropped</span></div>
<div class="rule"></div><div class="tw">
<table class="t"><tr><th>name</th><th>source</th><th>reason</th><th>market</th><th>queued</th></tr>
{qitems or '<tr><td colspan="5" class="empty">Queue is empty.</td></tr>'}
</table></div>
<p class="prose" style="margin-top:10px">Resolve by inserting a row into
<span class="mono">player_aliases</span> (alias_normalized → player_id) and
marking the queue row resolved.</p></section>"""
    body = pagehead("System", "Estimator & Data Health") + est_strip + grad_html + lead_html + f"""
<details class="block"><summary class="sub2" style="cursor:pointer">raw reports</summary>
<pre class="report" style="margin-top:8px">{esc(text)}

{esc(grad)}</pre></details>
<section class="block"><div class="blockhead"><h4>Recent feed gaps</h4></div>
<div class="rule"></div><div class="tw"><table class="t">
<tr><th>market</th><th>start</th><th>duration</th></tr>
{gap_rows or '<tr><td colspan="3" class="empty">None recorded.</td></tr>'}
</table></div></section>""" + queue_html
    return respond(request, "System", "system", body)


async def legacy_redirect(request: web.Request) -> web.Response:
    raise web.HTTPFound("/system")


def _rate_cell(stat) -> str:
    if stat is None or stat.value is None:
        return "—"
    win = "past year" if "last365" in stat.window else \
        ("career" if "career" in stat.window else "recent")
    return (f"{stat.value:.0%} <span class='sub2'>({stat.wins}-{stat.losses}, "
            f"{win})</span>")


async def players(request: web.Request) -> web.Response:
    from bot.matching.market_matcher import normalize_name
    from bot.models import Match

    q = (request.query.get("q") or "").strip()
    tour = (request.query.get("tour") or "").strip().lower()  # '', 'atp', 'wta'
    surface = (request.query.get("surface") or "").strip()    # '', Hard/Clay/Grass
    hand = (request.query.get("hand") or "").strip().upper()  # '', 'R', 'L'
    sort = (request.query.get("sort") or "matches").strip()   # matches | recent | name
    tour = tour if tour in ("atp", "wta") else ""
    surface = surface if surface in ("Hard", "Clay", "Grass", "Carpet") else ""
    hand = hand if hand in ("R", "L") else ""

    def base_filters(query):
        if tour:
            query = query.where(Player.tour == tour)
        if hand:
            query = query.where(Player.hand == hand)
        return query

    with db_session() as db:
        # candidate players by search or activity, then annotate record
        if q:
            found = db.execute(base_filters(select(Player).where(
                Player.normalized_name.ilike(f"%{normalize_name(q)}%")))
                .limit(200)).scalars().all()
            heading = f'results for "{q}"'
        else:
            latest = db.execute(select(func.max(Match.match_date)).where(
                Match.is_duplicate.is_(False))).scalar()
            cutoff = (latest - timedelta(days=90)) if latest else None
            mq = select(Player).join(
                Match, ((Match.winner_id == Player.id) | (Match.loser_id == Player.id)))
            if cutoff is not None:
                mq = mq.where(Match.match_date >= cutoff)
            if surface:
                mq = mq.where(Match.surface == surface)
            mq = base_filters(mq).where(Match.is_duplicate.is_(False))\
                .group_by(Player.id).order_by(func.count(Match.id).desc()).limit(200)
            found = db.execute(mq).scalars().all()
            heading = f"active players through {latest}" if latest else "database empty"

        ids = [p.id for p in found]
        wins = dict(db.execute(select(Match.winner_id, func.count()).where(
            Match.winner_id.in_(ids), Match.is_duplicate.is_(False))
            .group_by(Match.winner_id)).all()) if ids else {}
        losses = dict(db.execute(select(Match.loser_id, func.count()).where(
            Match.loser_id.in_(ids), Match.is_duplicate.is_(False))
            .group_by(Match.loser_id)).all()) if ids else {}
        last_seen = dict(db.execute(
            select(Player.id, func.max(Match.match_date))
            .join(Match, ((Match.winner_id == Player.id) | (Match.loser_id == Player.id)))
            .where(Player.id.in_(ids), Match.is_duplicate.is_(False))
            .group_by(Player.id)).all()) if ids else {}

    plist = [(p, wins.get(p.id, 0), losses.get(p.id, 0), last_seen.get(p.id))
             for p in found]
    if sort == "name":
        plist.sort(key=lambda t: t[0].full_name.lower())
    elif sort == "recent":
        plist.sort(key=lambda t: (t[3] or date(1900, 1, 1)), reverse=True)
    else:  # matches
        plist.sort(key=lambda t: t[1] + t[2], reverse=True)
    plist = plist[:80]

    rows = "".join(f"""<tr>
<td><a href="/player/{p.id}" style="text-decoration:none">
<span class="pname">{esc(p.full_name)}</span></a></td>
<td>{tag('neutral', '·', p.tour.upper())}</td>
<td class="mono sub2">{esc(p.ioc or '—')}</td>
<td class="mono">{w}-{l}</td>
<td class="mono sub2">{esc(ls) if ls else '—'}</td>
<td class="mono sub2">{esc(p.hand or '—')}</td></tr>"""
        for p, w, l, ls in plist)

    def opts(name, current, choices):
        o = "".join(f'<option value="{esc(v)}"{" selected" if v == current else ""}>'
                    f'{esc(lbl)}</option>' for v, lbl in choices)
        return (f'<select name="{name}" onchange="this.form.submit()" '
                f'style="background:var(--surface);border:1px solid var(--divider);'
                f'color:var(--text);font:inherit;padding:9px 12px">{o}</select>')

    filters = (
        opts("tour", tour, [("", "All tours"), ("atp", "ATP"), ("wta", "WTA")]) +
        opts("surface", surface, [("", "All surfaces"), ("Hard", "Hard"),
              ("Clay", "Clay"), ("Grass", "Grass"), ("Carpet", "Carpet")]) +
        opts("hand", hand, [("", "Either hand"), ("R", "Right"), ("L", "Left")]) +
        opts("sort", sort, [("matches", "Sort: most matches"),
              ("recent", "Sort: most recent"), ("name", "Sort: name")]))
    body = pagehead("Database", "Players", f"{heading} · {len(plist)} shown") + f"""
<form method="get" action="/players" style="display:flex;gap:10px;flex-wrap:wrap;
 align-items:center;margin:0 0 18px">
<input name="q" value="{esc(q)}" placeholder="Search any player…"
 style="flex:1;min-width:220px;background:var(--surface);border:1px solid var(--divider);
 color:var(--text);font:inherit;padding:10px 14px">
{filters}
<button class="tag tag-outline" type="submit" style="cursor:pointer;padding:9px 16px">Apply</button>
</form>
<div class="tw"><table class="t">
<tr><th>player</th><th>tour</th><th>country</th><th>record</th><th>last match</th><th>hand</th></tr>
{rows or '<tr><td colspan="6" class="empty">No players match these filters.</td></tr>'}
</table></div>
<p class="prose" style="margin-top:12px">137,000+ players indexed from 2022 on.
Surface filter applies to the activity listing; search matches names across all
tours. Click a player for the full play script.</p>"""
    return respond(request, "Players", "players", body)


async def player_detail(request: web.Request) -> web.Response:
    from bot.models import Match
    from bot.stats.profile import build_profile, compute_set_rates, load_history

    from bot.models import ChartingStat
    from bot.stats.profile import compute_charting

    pid = int(request.match_info["pid"])
    as_of = datetime.now(timezone.utc).date() + timedelta(days=1)
    with db_session() as db:
        p = db.get(Player, pid)
        if p is None:
            return web.Response(status=404, text="no such player")
        history = load_history(db, pid)
        prof = build_profile(db, pid, as_of)
        set_rates = compute_set_rates(history, as_of)
        ch_cols = ("winners", "winners_fh", "winners_bh", "unforced",
                   "unforced_fh", "unforced_bh", "serve_pts", "aces", "first_in",
                   "first_won", "second_in", "second_won", "return_pts",
                   "return_pts_won")
        ch_rows = [{c: getattr(r, c) for c in ch_cols} for r in db.execute(
            select(ChartingStat).where(ChartingStat.player_id == pid)).scalars()]
        charting = compute_charting(ch_rows)
        opp = Player.__table__.alias()
        recent = db.execute(
            select(Match, Player.full_name, Player.id)
            .join(Player, Player.id == func.coalesce(
                func.nullif(Match.winner_id, pid), Match.loser_id))
            .where(((Match.winner_id == pid) | (Match.loser_id == pid)),
                   Match.is_duplicate.is_(False),
                   Match.outcome.in_(("completed", "ret", "def")))
            .order_by(Match.match_date.desc()).limit(20)).all()

    f, d, t = prof.form, prof.deciding, prof.trajectory
    age = ""
    if p.dob:
        age = f" · {int((datetime.now(timezone.utc).date() - p.dob).days / 365.25)}y"
    strip = statstrip([
        ("Career", f"{f.win_rate_career.wins}-{f.win_rate_career.losses}"
         if f.win_rate_career.wins is not None else "—",
         f"{f.win_rate_career.value:.0%} win rate" if f.win_rate_career.value else ""),
        ("Past year", f"{f.win_rate_365.wins}-{f.win_rate_365.losses}"
         if f.win_rate_365.wins is not None else "—",
         f"{f.win_rate_365.value:.0%}" if f.win_rate_365.value else ""),
        ("Streak", (f"W{f.streak}" if f.streak > 0 else f"L{abs(f.streak)}")
         if f.streak else "—", ""),
        ("Deciders", f"{d.best.wins}-{d.best.losses}" if not d.best.is_omitted else "—",
         f"{d.best.value:.0%} · {'past year' if 'last365' in d.best.window else 'career'}"
         if d.best.value is not None else "insufficient sample"),
        ("Skunk share", f"{d.skunk_share_of_wins_365.value:.0%}"
         if not d.skunk_share_of_wins_365.is_omitted
         and d.skunk_share_of_wins_365.value is not None else "—",
         "of last-year wins were straight-sets"),
        ("Form 60d vs 180d", f"{t.delta:+.0%}" if t.delta is not None else "—",
         f"{t.last60.value:.0%} vs {t.last180.value:.0%}"
         if t.last60.value is not None and t.last180.value is not None else ""),
    ])
    sr_cells = "".join(
        f'<div class="metric"><div class="k">set {n} win rate</div>'
        f'<div class="v mono">{_rate_cell(s)}</div></div>'
        for n, s in sorted(set_rates.items()) if n <= 3)
    surf_rows = "".join(
        f"<tr><td>{esc(s.surface)}</td><td class='mono'>{_rate_cell(s.last365)}</td>"
        f"<td class='mono'>{_rate_cell(s.career)}</td></tr>" for s in prof.surfaces)
    dec_seq = " ".join(("<span style='color:var(--good)'>W</span>"
                        if r["won"] else "<span style='color:var(--accent)'>L</span>")
                       for r in d.last_n_results) or "—"

    # deeper analytics: serve/return, clutch, level splits
    sr = prof.serve_return
    def pctf(v, digits=0):
        return f"{v:.{digits}%}" if v is not None else "—"
    serve_html = ""
    if sr and sr.n_matches:
        serve_cells = [
            ("hold %", pctf(sr.hold_pct)), ("1st in", pctf(sr.first_in_pct)),
            ("1st win", pctf(sr.first_win_pct)), ("2nd win", pctf(sr.second_win_pct)),
            ("ace %", pctf(sr.ace_pct, 1)), ("df %", pctf(sr.df_pct, 1)),
            ("bp saved", pctf(sr.bp_saved_pct)), ("break %", pctf(sr.break_pct)),
            ("return win", pctf(sr.return_pts_win_pct)),
        ]
        cells = "".join(
            f'<div class="metric"><div class="k">{k}</div>'
            f'<div class="v mono">{v}</div></div>' for k, v in serve_cells)
        serve_html = f"""<section class="block"><div class="blockhead">
<h4>Serve &amp; return</h4><span class="aside">{sr.n_matches} matches with
point data · past year, widened to career if thin</span></div>
<div class="rule"></div>
<div class="metric-grid" style="grid-template-columns:repeat(auto-fit,minmax(90px,1fr))">
{cells}</div></section>"""
    c = prof.clutch
    clutch_html = ""
    if c:
        level_names = {"G": "Grand Slam", "M": "Masters", "A": "Tour", "C": "Challenger",
                       "15": "ITF 15k", "25": "ITF 25k"}
        lv_rows = "".join(
            f"<tr><td>{esc(level_names.get(lv, lv))}</td><td class='mono'>{_rate_cell(st)}</td></tr>"
            for lv, st in c.by_level.items() if st.n >= 3)
        clutch_html = f"""<section class="block"><div class="blockhead">
<h4>Clutch &amp; quality of competition</h4></div><div class="rule"></div>
<div class="metric-grid" style="grid-template-columns:repeat(4,1fr)">
<div class="metric"><div class="k">tiebreaks</div><div class="v mono">{_rate_cell(c.tiebreak)}</div></div>
<div class="metric"><div class="k">deciding sets</div><div class="v mono">{_rate_cell(c.deciding_set)}</div></div>
<div class="metric"><div class="k">vs top 50</div><div class="v mono">{_rate_cell(c.vs_top50)}</div></div>
<div class="metric"><div class="k">vs top 20</div><div class="v mono">{_rate_cell(c.vs_top20)}</div></div>
</div>
{f'<div class="tw" style="margin-top:12px"><table class="t"><tr><th>level</th><th>record</th></tr>{lv_rows}</table></div>' if lv_rows else ''}
{f'<p class="sub2" style="margin-top:10px">Strength of schedule: {prof.schedule.field} field · avg opponent rank ~{int(prof.schedule.avg_opp_rank)} · vs top-100 {_rate_cell(prof.schedule.vs_top100)}</p>' if prof.schedule and prof.schedule.avg_opp_rank else ''}
</section>"""

    cond = prof.conditional
    cond_html = ""
    if cond:
        cells = []
        for lbl, st in (("win % · won set 1", cond.win_given_set1_won),
                        ("win % · lost set 1", cond.win_given_set1_lost),
                        ("forces set 3 · lost set 1", cond.decider_given_set1_lost),
                        ("wins set 3 · lost set 2", cond.set3_given_lost_set2),
                        ("tiebreaks", prof.clutch.tiebreak if prof.clutch else None)):
            if st is not None and not st.is_omitted:
                cells.append(f'<div class="metric"><div class="k">{lbl}</div>'
                             f'<div class="v mono">{_rate_cell(st)}</div></div>')
        if cells:
            cond_html = f"""<section class="block"><div class="blockhead">
<h4>Gameflow conditionals</h4><span class="aside">how set 1 shapes the match</span>
</div><div class="rule"></div>
<div class="metric-grid" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr))">
{''.join(cells)}</div></section>"""

    ch = charting
    charting_html = ""
    if ch and ch.n_matches:
        ch_cells = [
            ("winners/match", f"{ch.winners_per_match:.1f}" if ch.winners_per_match else "—"),
            ("UFE/match", f"{ch.unforced_per_match:.1f}" if ch.unforced_per_match else "—"),
            ("W:UFE ratio", f"{ch.winner_ufe_ratio:.2f}" if ch.winner_ufe_ratio else "—"),
            ("FH winners", pctf(ch.fh_winner_share)),
            ("BH winners", pctf(ch.bh_winner_share)),
            ("FH of UFE", pctf(ch.fh_ufe_share)),
            ("ace rate", pctf(ch.ace_rate, 1)),
            ("1st-serve win", pctf(ch.first_serve_win)),
            ("2nd-serve win", pctf(ch.second_serve_win)),
            ("return win", pctf(ch.return_win)),
        ]
        cells = "".join(
            f'<div class="metric"><div class="k">{k}</div>'
            f'<div class="v mono">{v}</div></div>' for k, v in ch_cells)
        from bot.stats.profile import style_profile
        sp = style_profile(ch)
        style_tags = " ".join(tag("neutral", "◆", t) for t in sp["tags"]) if sp else ""
        charting_html = f"""<section class="block"><div class="blockhead">
<h4>Shot-level (Match Charting Project)</h4>
<span class="aside">{ch.n_matches} hand-charted matches · winners, errors and
wing splits no feed sells</span></div><div class="rule"></div>
{f'<p style="margin:0 0 10px">style: {style_tags}</p>' if style_tags else ''}
<div class="metric-grid" style="grid-template-columns:repeat(auto-fit,minmax(96px,1fr))">
{cells}</div>
<p class="sub2" style="margin-top:8px">Data © Tennis Abstract Match Charting
Project (CC BY-NC-SA 4.0).</p></section>"""
    m_rows = []
    for m, opp_name, opp_id in recent:
        won = m.winner_id == pid
        m_rows.append(f"""<tr>
<td class="mono sub2">{m.match_date}</td>
<td>{tag('good', '✓', 'W') if won else tag('accent', '✕', 'L')}</td>
<td><a href="/player/{opp_id}" style="text-decoration:none">
<span class="pname">{esc(opp_name)}</span></a></td>
<td class="mono">{esc(m.score_raw or m.outcome)}</td>
<td class="sub2">{esc(m.round or '')} · {esc(m.surface or '?')} · {esc(m.tourney_level or '')}</td></tr>""")
    body = pagehead(p.tour.upper() + (f" · {p.ioc}" if p.ioc else ""),
                    p.full_name, f"{prof.matches_in_db} matches in DB{age}") + strip + f"""
<section class="block"><div class="blockhead"><h4>Set-by-set profile</h4>
<span class="aside">the gameflow backbone</span></div><div class="rule"></div>
<div class="metric-grid" style="grid-template-columns:repeat(3,1fr)">{sr_cells or
    '<div class="metric"><div class="k">no set data</div><div class="v">—</div></div>'}</div>
</section>
<section class="block"><div class="blockhead"><h4>Deciding sets</h4>
<span class="aside">last {len(d.last_n_results)}: newest first</span></div>
<div class="rule"></div>
<p class="prose">Career {d.career.wins}-{d.career.losses}
({f"{d.career.value:.0%}" if d.career.value is not None else "—"}) ·
past year {d.last365.wins}-{d.last365.losses}
({f"{d.last365.value:.0%}" if d.last365.value is not None else "—"}) ·
sequence {dec_seq} ·
days since last decider win:
{d.days_since_decider_win if d.days_since_decider_win is not None else "—"} ·
last decider played:
{f"{d.days_since_decider_played}d ago" if d.days_since_decider_played is not None else "—"}</p>
</section>
{cond_html}
{serve_html}
{clutch_html}
{charting_html}
<section class="block"><div class="blockhead"><h4>Surfaces</h4></div>
<div class="rule"></div><div class="tw"><table class="t">
<tr><th>surface</th><th>past year</th><th>career</th></tr>
{surf_rows or '<tr><td colspan="3" class="empty">No surface data.</td></tr>'}
</table></div></section>
<section class="block"><div class="blockhead"><h4>Recent matches</h4></div>
<div class="rule"></div><div class="tw"><table class="t">
<tr><th>date</th><th></th><th>opponent</th><th>score</th><th>context</th></tr>
{''.join(m_rows) or '<tr><td colspan="5" class="empty">No matches.</td></tr>'}
</table></div></section>"""
    return respond(request, p.full_name, "players", body)


async def match_detail(request: web.Request) -> web.Response:
    from bot.models import Scenario
    from bot.stats.profile import (
        build_profile,
        compute_matchup,
        compute_set_rates,
        load_history,
    )

    event_ticker = request.match_info["event"]
    as_of = datetime.now(timezone.utc).date() + timedelta(days=1)
    with db_session() as db:
        sides = db.execute(select(KalshiMarket).where(
            KalshiMarket.event_ticker == event_ticker)
            .order_by(KalshiMarket.ticker)).scalars().all()
        if len(sides) < 2 or any(m.player_a_id is None for m in sides[:2]):
            return web.Response(status=404, text="match not found or unmatched")
        a, b = sides[0], sides[1]
        pa, pb = db.get(Player, a.player_a_id), db.get(Player, b.player_a_id)
        hist_a, hist_b = load_history(db, pa.id), load_history(db, pb.id)
        prof_a, prof_b = build_profile(db, pa.id, as_of), build_profile(db, pb.id, as_of)
        sr_a, sr_b = compute_set_rates(hist_a, as_of), compute_set_rates(hist_b, as_of)
        mu = compute_matchup(hist_a, hist_b, pb.id, pa.id, as_of, None)
        sc = db.execute(select(Scenario).where(Scenario.event_ticker == event_ticker)
                        .order_by(Scenario.created_for.desc())).scalars().first()
        from bot.models import ChartingStat
        from bot.stats.profile import compute_charting, style_matchup

        _cc = ("winners", "winners_fh", "winners_bh", "unforced", "unforced_fh",
               "unforced_bh", "serve_pts", "aces", "first_in", "first_won",
               "second_in", "second_won", "return_pts", "return_pts_won")
        def charting_for(pid):
            rows = [{c: getattr(r, c) for c in _cc} for r in db.execute(
                select(ChartingStat).where(ChartingStat.player_id == pid)).scalars()]
            return compute_charting(rows)
        ch_a, ch_b = charting_for(pa.id), charting_for(pb.id)
        style_notes = style_matchup(ch_a, ch_b, pa.full_name.split()[-1],
                                    pb.full_name.split()[-1])
        quotes = _latest_quotes(db, [a.ticker, b.ticker])
        est = db.execute(select(LiveMatchState).where(
            LiveMatchState.market_ticker.in_([a.ticker, b.ticker]))).scalars().first()

        # price series + bot-action annotations for the chart (side A mid)
        from sqlalchemy import text as sqltext

        from bot.models import PaperBet

        tick_rows = db.execute(sqltext("""
            SELECT date_trunc('minute', ts) AS m, avg((yes_bid + yes_ask) / 2.0)
            FROM market_ticks
            WHERE market_ticker = :t AND kind = 'quote'
              AND yes_bid IS NOT NULL AND yes_ask IS NOT NULL
              AND ts > now() - interval '12 hours'
            GROUP BY 1 ORDER BY 1"""), {"t": a.ticker}).all()
        chart_points = [(r[0].replace(tzinfo=timezone.utc) if r[0].tzinfo is None
                         else r[0], float(r[1])) for r in tick_rows]
        marks: list[dict] = []
        for r in db.execute(select(StateInferenceLog).where(
                StateInferenceLog.market_ticker.in_([a.ticker, b.ticker]))
                .order_by(StateInferenceLog.inferred_at.desc()).limit(30)).scalars():
            marks.append({"kind": "boundary", "ts": r.inferred_at, "price": 50,
                          "hit": r.hit,
                          "title": f"boundary inferred → {r.inferred_state} "
                                   f"({'confirmed' if r.hit else 'miss' if r.hit is False else 'pending'})"})
            if r.confirmed_at:
                marks.append({"kind": "score", "ts": r.confirmed_at, "price": 58,
                              "title": f"score confirmed {r.confirmed_state}"})
        for adv in db.execute(select(Advisory).where(
                Advisory.market_ticker.in_([a.ticker, b.ticker]),
                Advisory.status == "sent")).scalars():
            marks.append({"kind": "advisory", "ts": adv.created_at,
                          "price": adv.executable_price_cents,
                          "title": f"advisory @ {adv.executable_price_cents}¢ "
                                   f"edge +{adv.edge * 100:.1f}%"})
        for bet in db.execute(select(PaperBet).where(
                PaperBet.event_ticker == event_ticker)).scalars():
            marks.append({"kind": "bet", "ts": bet.created_at,
                          "price": bet.price_cents,
                          "title": f"paper bet {bet.units}u @ {bet.price_cents}¢ "
                                   f"({bet.basis})"})
        from bot.models import MatchScoreLog

        score_rows = db.execute(select(MatchScoreLog).where(
            MatchScoreLog.market_ticker.in_([a.ticker, b.ticker]))
            .order_by(MatchScoreLog.ts.desc()).limit(40)).scalars().all()
        live_row = next((r for r in score_rows if r.detail), None)

    def px(m):
        q = quotes.get(m.ticker)
        return f"{(q[0] + q[1]) / 2:.0f}¢" if q and q[0] is not None else "—"

    def profile_col(p, prof, sr):
        f, d = prof.form, prof.deciding
        cells = "".join(
            f'<div class="metric"><div class="k">set {n}</div>'
            f'<div class="v mono">{_rate_cell(s)}</div></div>'
            for n, s in sorted(sr.items()) if n <= 3)
        return f"""<div class="card">
<a href="/player/{p.id}" style="text-decoration:none">
<div class="title">{esc(p.full_name)}</div></a>
<div class="sub2">{p.tour.upper()} · {esc(p.ioc or '')} ·
{prof.matches_in_db} matches in DB</div>
<div class="metric-grid" style="grid-template-columns:repeat(3,1fr)">{cells}</div>
<div class="prose">Past year {f.win_rate_365.wins}-{f.win_rate_365.losses} ·
last 10: {f.last10.wins}-{f.last10.losses} ·
deciders {d.best.wins if not d.best.is_omitted else '—'}-{d.best.losses if not d.best.is_omitted else ''}
{f"({d.best.value:.0%})" if d.best.value is not None else ""} ·
streak {("W" + str(f.streak)) if f.streak > 0 else ("L" + str(abs(f.streak))) if f.streak else "—"}</div>
</div>"""

    h2h_txt = f"{mu.h2h.wins}-{mu.h2h.losses}" if mu.h2h.n else "no meetings"
    common = (f"vs {mu.common_opponent_count} common opponents: "
              f"{pa.full_name.split()[-1]} {mu.common_opponents.wins}-{mu.common_opponents.losses}, "
              f"{pb.full_name.split()[-1]} {mu.common_opponents_b.wins}-{mu.common_opponents_b.losses}"
              if not mu.common_opponents.is_omitted else "")
    state_txt = ""
    if est:
        state_txt = f" · estimator {est.state} ({est.confidence:.0%})"
    scenario_html = ""
    if sc:
        wq = quotes.get(sc.market_ticker)
        wmid = (wq[0] + wq[1]) / 2 if wq and wq[0] is not None else None
        trig = trigger_html(sc, est.state if est else None, est is not None, wmid)
        scenario_html = f"""<section class="block"><div class="blockhead">
<h4>Gameflow plan</h4><span class="aside">generated {sc.created_for}</span></div>
<div class="rule"></div>
<p style="margin:0 0 10px">{trig}</p>
<div class="prose">{esc(sc.narrative)}</div></section>"""
    yes_name = (a.raw or {}).get("yes_sub_title", "side A")
    chart_html = price_chart_svg(chart_points, marks, f"{yes_name} to win")

    style_html = ""
    if style_notes:
        style_html = f"""<section class="block"><div class="blockhead">
<h4>Style matchup</h4><span class="aside">from Match Charting shot data</span></div>
<div class="rule"></div>""" + "".join(
            f'<div class="prose">{esc(nt)}</div>' for nt in style_notes) + "</section>"

    # live in-match serve stats from the milestone feed (aces, DFs, break pts) —
    # the '13 aces this match' / '4 double faults' signals. Map competitor1/2
    # to our YES(pa)/sibling(pb) via the recorded scoreline; skip if ambiguous
    # (avoid mirror-flipped labels).
    livestats_html = ""
    if live_row and live_row.detail:
        det = live_row.detail
        s1 = det.get("competitor1_statistics") or {}
        s2 = det.get("competitor2_statistics") or {}
        c1s, c2s = det.get("competitor1_overall_score"), det.get("competitor2_overall_score")
        yes_is_c1 = None
        if c1s is not None and c2s is not None and c1s != c2s:
            yes_is_c1 = (live_row.sets_a == c1s)
        if (s1 or s2) and yes_is_c1 is not None:
            sa, sb = (s1, s2) if yes_is_c1 else (s2, s1)
            keys = [("aces", "aces"), ("double_faults", "double faults"),
                    ("breakpoints_won", "break pts won"),
                    ("first_serve_points_won", "1st-serve pts won"),
                    ("games_won", "games won"), ("points_won", "points won")]
            rows = "".join(
                f"<tr><td class='sub2'>{lbl}</td>"
                f"<td class='mono' style='text-align:right'>{esc(sa.get(k, '—'))}</td>"
                f"<td class='mono' style='text-align:right'>{esc(sb.get(k, '—'))}</td></tr>"
                for k, lbl in keys if k in sa or k in sb)
            livestats_html = f"""<section class="block"><div class="blockhead">
<h4>Live match stats</h4><span class="aside">this match, from the score feed</span>
</div><div class="rule"></div><div class="tw"><table class="t">
<tr><th>stat</th><th style="text-align:right">{esc(pa.full_name.split()[-1])}</th>
<th style="text-align:right">{esc(pb.full_name.split()[-1])}</th></tr>{rows}</table></div>
<p class="sub2" style="margin-top:6px">Serve dominance (aces) points to tiebreak
strength; double faults are a live wobble signal.</p></section>"""

    # the bot's own game-by-game record
    scorelog_html = ""
    if score_rows:
        a_only = [r for r in score_rows if r.market_ticker == a.ticker] or score_rows
        latest = a_only[0]
        lead = ("current" if not latest.is_final else "final")
        srows = "".join(
            f"<tr><td class='mono sub2'>{pt(r.ts)}</td>"
            f"<td class='mono'>{r.sets_a}-{r.sets_b} sets</td>"
            f"<td class='mono pname'>{esc(r.scoreline)}</td>"
            f"<td class='sub2'>set {r.set_number}</td></tr>"
            for r in a_only[:25])
        scorelog_html = f"""<section class="block"><div class="blockhead">
<h4>Game-by-game score</h4><span class="aside">the bot's own record ·
{lead} {esc(latest.scoreline)}</span></div><div class="rule"></div>
<div class="tw"><table class="t"><tr><th>recorded</th><th>sets</th>
<th>games ({esc(yes_name)} first)</th><th></th></tr>{srows}</table></div>
<p class="sub2" style="margin-top:6px">Every game the bot observed via Kalshi's
score feed, newest first — its own scoring database, independent of the
odds-based estimator.</p></section>"""
    body = pagehead("Match", (a.title or "").split(":")[0].replace("Will ", "")
                    or event_ticker,
                    f"{px(a)} / {px(b)}{state_txt}") + f"""
<div class="cards" style="grid-template-columns:repeat(auto-fit,minmax(340px,1fr));margin-bottom:26px">
{profile_col(pa, prof_a, sr_a)}
{profile_col(pb, prof_b, sr_b)}
</div>
<section class="block"><div class="blockhead"><h4>Head to head</h4></div>
<div class="rule"></div>
<p class="prose">{esc(pa.full_name)} vs {esc(pb.full_name)}: {h2h_txt}
{f"(sets {mu.h2h_sets[0]}-{mu.h2h_sets[1]})" if mu.h2h.n else ""}.
{esc(common)}</p></section>
{style_html}
{livestats_html}
{scorelog_html}
{chart_html}
{scenario_html}
<p class="sub2 mono">{esc(a.ticker)} · {esc(b.ticker)}</p>"""
    return respond(request, "Match", "scenarios", body)


async def flags(request: web.Request) -> web.Response:
    """Data-completeness audit: everything the database is still missing,
    honestly flagged with severity and what would fix it."""
    from bot.config import settings as cfg_fn
    from bot.models import Match

    cfg = cfg_fn()
    today = datetime.now(timezone.utc).date()
    with db_session() as db:
        frontiers = db.execute(
            select(Match.tour, Match.source, func.max(Match.match_date),
                   func.count(Match.id))
            .where(Match.outcome != "scheduled", Match.is_duplicate.is_(False))
            .group_by(Match.tour, Match.source)).all()
        k_no_sets = db.execute(select(func.count(Match.id)).where(
            Match.source == "kalshi", Match.sets_won_winner.is_(None),
            Match.is_duplicate.is_(False))).scalar()
        k_total = db.execute(select(func.count(Match.id)).where(
            Match.source == "kalshi", Match.is_duplicate.is_(False))).scalar()
        no_surface = db.execute(select(func.count(Match.id)).where(
            Match.surface.is_(None), Match.is_duplicate.is_(False),
            Match.outcome != "scheduled")).scalar()
        provisional = db.execute(select(func.count(Player.id)).where(
            Player.sackmann_id.is_(None))).scalar()
        unmatched = db.execute(select(func.count(MatchReviewQueue.id)).where(
            MatchReviewQueue.resolved.is_(False))).scalar()
        from bot.reports import mapping_audit
        audit = mapping_audit(db)

    items = []

    def flag(sev: str, title: str, detail: str, fix: str):
        icon = {"critical": "⛔", "warn": "⚠", "info": "·"}[sev]
        kind = {"critical": "accent", "warn": "warn", "info": "neutral"}[sev]
        items.append(f"""<div class="card">
<div>{tag(kind, icon, sev)}</div>
<div class="title" style="font-size:15px">{esc(title)}</div>
<div class="prose">{detail}</div>
<div class="sub2">fix: {esc(fix)}</div></div>""")

    # data-integrity check first — a flipped YES↔competitor mapping silently
    # corrupts scorelines, estimator state, and bet settlement
    nmis = len(audit["mismatches"])
    if nmis:
        detail = ("The side our recorded scoreline says won does NOT match how "
                  "the market settled, for: " + ", ".join(
                      f"{esc(m['ticker'])} (we saw {m['sets']}, settled {m['settled']})"
                      for m in audit["mismatches"][:6]) +
                  ". These bets, scorelines and estimator states are mirror-flipped.")
        flag("critical", f"{nmis} match(es) with a flipped player mapping", detail,
             "correct _yes_is_competitor1 for these tickers and re-derive; "
             "quarantine affected bets/advisories")
    else:
        flag("info", "Player-mapping integrity: clean",
             f"All {audit['ok']} verifiable settled matches agree — the side our "
             f"scoreline says won matches the side that settled YES. "
             f"({audit['unverifiable']} unverifiable: RET/tie/no final score.)",
             "none needed")

    by = {}
    for tour, source, latest, n in frontiers:
        by.setdefault(tour, {})[source] = (latest, n)
    for tour in ("atp", "wta"):
        srcs = by.get(tour, {})
        newest = max((v[0] for v in srcs.values()), default=None)
        if newest is None:
            flag("critical", f"{tour.upper()}: no results at all",
                 "The database has no completed matches for this tour.", "run ingest")
            continue
        age = (today - newest).days
        parts = " · ".join(f"{s}: through {v[0]} ({v[1]:,} matches)"
                           for s, v in sorted(srcs.items()))
        if age > 3:
            flag("critical" if age > 14 else "warn",
                 f"{tour.upper()} results are {age} days stale",
                 f"Newest completed match: {newest}. {parts}",
                 "activate api-tennis (Phase 5.5) or verify the kalshi-results "
                 "sync in the daily ingest")
        else:
            flag("info", f"{tour.upper()} results current through {newest}", parts,
                 "none needed")
    if k_total:
        pct = k_no_sets / k_total
        if k_no_sets:
            flag("warn" if pct > 0.15 else "info",
                 f"{k_no_sets:,} of {k_total:,} Kalshi-mined results lack set detail",
                 "Winner is known from settlement, but per-set scores were "
                 "unavailable or ambiguous — these matches count for W/L form "
                 "but not for deciding-set or set-rate stats.",
                 "api-tennis activation will supply full scorelines")
    flag("warn" if not cfg.api_tennis_key else "info",
         "Live results feed (api-tennis) " +
         ("INACTIVE" if not cfg.api_tennis_key else "active"),
         "The permanent recency source is deferred (Phase 5.5). Until it runs, "
         "recent results come only from Kalshi-listed matches: tour-level is "
         "near-complete, ITF partial, and surface is unknown for all of them.",
         "sign up for the api-tennis trial and set API_TENNIS_KEY")
    flag("info", f"{no_surface:,} matches have no surface recorded",
         "All Kalshi-mined rows lack surface (Kalshi doesn't expose it), so "
         "they're excluded from surface-split stats.",
         "api-tennis backfill will fill surfaces going forward")
    flag("info", f"{provisional:,} provisional players",
         "Players created from live sources without a historical profile — "
         "typically new ITF entrants after the mirror freeze; stats thin until "
         "they accumulate matches.", "expected; resolves as data accrues")
    if unmatched:
        flag("warn", f"{unmatched} unmatched names in the review queue",
             "Names that couldn't be confidently matched to a player — their "
             "matches are NOT ingested until resolved.",
             "resolve via player_aliases (System → review queue)")
    body = pagehead("Data Audit", "Flags",
                    f"{sum(1 for _ in items)} checks · refreshed live") + \
        f'<div class="cards">{"".join(items)}</div>'
    return respond(request, "Flags", "flags", body)


async def api_events(request: web.Request) -> web.Response:
    """Lightweight poll target for local browser notifications."""
    from bot.models import PaperBet

    with db_session() as db:
        advs = db.execute(
            select(Advisory, Player.full_name)
            .join(Player, Player.id == Advisory.recommended_player_id, isouter=True)
            .where(Advisory.status == "sent")
            .order_by(Advisory.id.desc()).limit(5)).all()
        bets = db.execute(
            select(PaperBet, Player.full_name)
            .join(Player, Player.id == PaperBet.player_id, isouter=True)
            .order_by(PaperBet.id.desc()).limit(5)).all()
    return web.json_response({
        "max_advisory_id": max((a.id for a, _ in advs), default=0),
        "max_bet_id": max((b.id for b, _ in bets), default=0),
        "advisories": [
            {"id": a.id,
             "text": f"{p or '?'} @ {a.executable_price_cents}¢ · edge "
                     f"+{a.edge * 100:.1f}% · state {a.inferred_state}"
                     f"{' · PROBATION' if a.probation else ''}"}
            for a, p in advs],
        "bets": [
            {"id": b.id,
             "text": f"{p or b.event_ticker} · {b.units}u @ {b.price_cents}¢ · "
                     f"model {b.model_prob:.0%} ({b.basis})"}
            for b, p in bets],
    })


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
    app.router.add_get("/testrun", testrun)
    app.router.add_get("/testrun/history", testrun_history)
    app.router.add_get("/testrun-tp", testrun_tp)
    app.router.add_get("/players", players)
    app.router.add_get("/player/{pid:\\d+}", player_detail)
    app.router.add_get("/match/{event}", match_detail)
    app.router.add_get("/api/events", api_events)
    app.router.add_get("/flags", flags)
    app.router.add_get("/track", track)
    app.router.add_get("/live", live)
    app.router.add_get("/system", system)
    app.router.add_get("/report", legacy_redirect)
    app.router.add_get("/queue", legacy_redirect)
    app.router.add_get("/healthz", healthz)
    return app


def main() -> int:
    port = int(os.environ.get("PORT", 8080))
    log.info("web ui starting", port=port)
    web.run_app(make_app(), host="0.0.0.0", port=port, print=None)
    return 0
