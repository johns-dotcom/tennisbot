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
  --bg: #131211; --surface: #1c1a19; --surface-2: #232120;
  --text: #f3f2f2; --muted: rgba(243,242,242,.56); --faint: rgba(243,242,242,.4);
  --divider: rgba(243,242,242,.13); --divider-strong: rgba(243,242,242,.2);
  --accent: #ff563c; --accent-fill: #ec3013;
  --good: #35c26e; --warning: #fab219; --critical: #ff563c;
  --font: "Archivo", system-ui, sans-serif;
  --radius: 10px; --radius-sm: 6px;
  --shadow: 0 1px 2px rgba(0,0,0,.35), 0 8px 24px rgba(0,0,0,.16);
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

header.nav { position: sticky; top: 0; z-index: 20;
  background: rgba(19,18,17,.82); backdrop-filter: saturate(140%) blur(10px);
  display: flex; align-items: center; flex-wrap: wrap; gap: 10px 20px;
  padding: 13px 22px; border-bottom: 1px solid var(--divider-strong); }
.brand { font-weight: 800; font-size: 18px; letter-spacing: .02em;
  display: inline-flex; align-items: center; }
.brand .tag { font-size: 9px; letter-spacing: .13em; font-weight: 700;
  text-transform: uppercase; color: var(--muted); margin-left: 10px;
  border: 1px solid var(--divider); border-radius: 999px; padding: 2px 8px; }
nav.links { display: flex; align-items: center; margin-right: auto;
  flex-wrap: wrap; }
.navgroup { display: flex; gap: 18px; padding: 0 16px; }
.navgroup:first-child { padding-left: 0; }
.navgroup + .navgroup { border-left: 1px solid var(--divider); }
nav.links a { padding: 5px 0; border-bottom: 2px solid transparent;
  font-weight: 800; font-size: 12px; letter-spacing: .05em;
  text-transform: uppercase; color: var(--muted); text-decoration: none;
  transition: color .12s ease, border-color .12s ease; white-space: nowrap; }
nav.links a:hover { color: var(--text); }
nav.links a.active { color: var(--text); border-bottom-color: var(--accent); }
.conn { font-size: 11px; color: var(--muted); display: inline-flex;
  align-items: center; gap: 7px; letter-spacing: .06em; }
@media (max-width: 760px) {
  header.nav { padding: 10px 14px; gap: 8px 14px; }
  nav.links { order: 3; width: 100%; flex-wrap: nowrap; overflow-x: auto;
    -webkit-overflow-scrolling: touch; scrollbar-width: none; }
  nav.links::-webkit-scrollbar { display: none; }
  .navgroup { padding: 0 12px; }
}
.dot { width: 8px; height: 8px; display: inline-block; border-radius: 50%; }

main { width: 100%; max-width: 1360px; margin: 0 auto; padding: 26px 20px 60px; }
.pagehead { display: flex; align-items: flex-end; justify-content: space-between;
  flex-wrap: wrap; gap: 12px; margin-bottom: 18px; }
.pagehead .sub { font-size: 12px; color: var(--muted); }

.statstrip { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 10px; margin-bottom: 26px; }
.stat { background: var(--surface); padding: 16px 18px; border: 1px solid var(--divider);
  border-radius: var(--radius); box-shadow: var(--shadow); }
.filterbar { display: flex; flex-wrap: wrap; gap: 7px; align-items: center;
  margin-bottom: 14px; }
.fchip { background: var(--surface); border: 1px solid var(--divider);
  color: var(--muted); font: inherit; font-size: 12px; padding: 5px 12px;
  border-radius: 999px; cursor: pointer; letter-spacing: .02em;
  transition: color .12s ease, border-color .12s ease, background .12s ease; }
.fchip:hover { color: var(--text); border-color: var(--divider-strong); }
.fchip.on { border-color: var(--accent); color: var(--text);
  background: rgba(255,86,60,.1); }
.planrow { margin-top: 6px; font-size: 12.5px; display: flex; flex-wrap: wrap;
  align-items: center; gap: 7px; }
.trig-live { border-color: var(--warning); box-shadow: 0 0 0 1px var(--warning); }
.trig-banner { background: var(--warning); color: #0a0a0a; font-weight: 800;
  font-size: 11px; letter-spacing: .04em; padding: 6px 10px; border-radius: 5px;
  margin: -2px 0 8px; }
.scen-flag { background: var(--accent); color: #0a0a0a; font-weight: 800;
  font-size: 10px; letter-spacing: .08em; padding: 2px 7px; border-radius: 4px; }
.cmeter { display: inline-flex; align-items: center; gap: 8px; }
@media (max-width: 760px) {
  main { padding: 18px 14px 48px; }
  h2 { font-size: 24px; }
  .pagehead { align-items: flex-start; }
  /* wide bet/feed tables scroll inside their own container, not the page */
  .tw { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  table.t { min-width: 620px; }
  /* multi-tile grids collapse to two-up on phones */
  .statstrip { grid-template-columns: repeat(2, 1fr) !important; }
  .vsgrid { grid-template-columns: 1fr !important; }
  .metric-grid { grid-template-columns: repeat(2, 1fr) !important; }
  .cards { grid-template-columns: 1fr !important; }
  .stat .v { font-size: 23px; }
}
.scard { transition: border-color .12s ease; }
.scard:hover { border-color: var(--accent); }
.scard-best { border-color: var(--warning); box-shadow: inset 0 0 0 1px var(--warning); }
.best-flag { background: var(--warning); color: #0a0a0a; font-weight: 800;
  font-size: 9px; letter-spacing: .1em; padding: 2px 6px; border-radius: 4px;
  margin-right: 8px; vertical-align: middle; }
.vsgrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 10px; margin-bottom: 26px; }
.vscol { background: var(--surface); padding: 16px 18px; border: 1px solid var(--divider);
  border-radius: var(--radius); box-shadow: var(--shadow); }
.vshead { display: flex; align-items: baseline; justify-content: space-between;
  font-size: 13px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase;
  margin-bottom: 8px; }
.vshead span { font-weight: 400; letter-spacing: 0; text-transform: none;
  color: var(--muted); font-size: 12px; }
.vsrow { display: flex; justify-content: space-between; align-items: baseline;
  padding: 7px 0; border-top: 1px solid var(--divider); }
.vsrow .k { color: var(--muted); font-size: 13px; }
.vsrow .v { font-size: 18px; font-weight: 800; }
.stat .l { font-size: 10px; letter-spacing: .1em; text-transform: uppercase;
  color: var(--muted); margin-bottom: 10px; }
.stat .v { font-weight: 800; font-size: 27px; letter-spacing: -.02em; line-height: 1; }
.stat .s { font-size: 12px; margin-top: 6px; color: var(--faint); }

section.block { margin-bottom: 30px; }
.blockhead { display: flex; align-items: baseline; justify-content: space-between;
  margin-bottom: 8px; }
.blockhead h4 { font-size: 19px; }
.blockhead .aside { font-size: 12px; color: var(--muted); }
.rule { height: 1px; background: var(--divider); margin: 0 0 12px; }

.tw { overflow-x: auto; }
table.t { width: 100%; border-collapse: collapse; font-size: 13.5px; }
table.t th { text-align: left; font-size: 10.5px; letter-spacing: .08em;
  text-transform: uppercase; color: var(--muted); padding: 8px;
  border-bottom: 1px solid var(--divider-strong); white-space: nowrap; }
table.t td { padding: 9px 8px; border-bottom: 1px solid var(--divider);
  vertical-align: top; }
table.t tbody tr:hover { background: rgba(243,242,242,.04); }
.pname { font-weight: 800; font-size: 13.5px; }
.sub2 { color: var(--muted); font-size: 11.5px; }

.tag { display: inline-flex; align-items: center; gap: 5px; font-size: 10.5px;
  letter-spacing: .04em; padding: 3px 9px; font-weight: 600;
  text-transform: uppercase; border-radius: 999px; }
.tag-accent { background: rgba(255,86,60,.16); color: var(--accent); }
.tag-good { background: rgba(53,194,110,.14); color: var(--good); }
.tag-warn { background: rgba(250,178,25,.13); color: var(--warning); }
.tag-neutral { background: rgba(243,242,242,.09); color: var(--muted); }
.tag-outline { border: 1px solid var(--accent); color: var(--accent); }

.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 14px; }
.card { display: flex; flex-direction: column; gap: 12px; padding: 18px;
  background: var(--surface); border: 1px solid var(--divider);
  border-radius: var(--radius); box-shadow: var(--shadow); }
.card .title { font-weight: 800; font-size: 17px; }
.metric-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; }
.metric { background: var(--surface-2); padding: 9px 11px; border: 1px solid var(--divider);
  border-radius: var(--radius-sm); }
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


def conf_label(conf: float | None) -> str:
    from bot.prob.confidence import confidence_label
    return confidence_label(conf)


# IOC (3-letter) → flag emoji for the common tennis nations; unknown → no flag
_FLAG = {
    "USA": "🇺🇸", "FRA": "🇫🇷", "ESP": "🇪🇸", "ITA": "🇮🇹", "GER": "🇩🇪", "GBR": "🇬🇧",
    "AUS": "🇦🇺", "ARG": "🇦🇷", "RUS": "🇷🇺", "SRB": "🇷🇸", "SUI": "🇨🇭", "CAN": "🇨🇦",
    "JPN": "🇯🇵", "CZE": "🇨🇿", "CRO": "🇭🇷", "AUT": "🇦🇹", "BEL": "🇧🇪", "NED": "🇳🇱",
    "POL": "🇵🇱", "BRA": "🇧🇷", "CHN": "🇨🇳", "KOR": "🇰🇷", "GRE": "🇬🇷", "NOR": "🇳🇴",
    "DEN": "🇩🇰", "SWE": "🇸🇪", "POR": "🇵🇹", "CHI": "🇨🇱", "COL": "🇨🇴", "KAZ": "🇰🇿",
    "BUL": "🇧🇬", "SVK": "🇸🇰", "HUN": "🇭🇺", "RSA": "🇿🇦", "IND": "🇮🇳", "TPE": "🇹🇼",
    "UKR": "🇺🇦", "ROU": "🇷🇴", "FIN": "🇫🇮", "SLO": "🇸🇮", "BLR": "🇧🇾", "LAT": "🇱🇻",
    "LTU": "🇱🇹", "EST": "🇪🇪", "MDA": "🇲🇩", "BIH": "🇧🇦", "GEO": "🇬🇪", "TUN": "🇹🇳",
    "EGY": "🇪🇬", "MAR": "🇲🇦", "ISR": "🇮🇱", "TUR": "🇹🇷", "THA": "🇹🇭", "MEX": "🇲🇽",
    "NZL": "🇳🇿", "IRL": "🇮🇪", "ECU": "🇪🇨", "PER": "🇵🇪", "URU": "🇺🇾", "BOL": "🇧🇴",
    "DOM": "🇩🇴", "PAR": "🇵🇾", "VEN": "🇻🇪", "HKG": "🇭🇰", "INA": "🇮🇩", "PHI": "🇵🇭",
}


def _flag(ioc: str | None) -> str:
    return _FLAG.get((ioc or "").upper(), "")


def score_grid(sl, a_name: str, b_name: str, a_ioc: str | None,
               b_ioc: str | None) -> str:
    """Kalshi-style live scoreline: two rows (flag · name · per-set columns ·
    boxed current set). sl = (scoreline a-perspective, sets_a, sets_b). The set
    leader is bold, the trailer dimmed; the last (in-progress) column is boxed."""
    scoreline = (sl[0] or "").strip()
    if not scoreline:
        return ""
    cols = []  # (a_val, b_val) per set/segment
    for seg in scoreline.split():
        head = seg.split("(")[0]
        if "-" not in head:
            continue
        av, bv = head.split("-", 1)
        cols.append((av, bv, "(" in seg))
    if not cols:
        return ""
    last = len(cols) - 1

    def cell(val, other, boxed):
        try:
            lead = int(val) > int(other)
        except ValueError:
            lead = False
        # the boxed (current-game) column carries POINTS: 0/15/30/40 and 50=AD.
        # Set columns carry games/tiebreak points and render as-is.
        disp = "AD" if (boxed and val == "50") else val
        style = ("font-weight:800;color:var(--text)" if lead
                 else "color:var(--muted)")
        box = ("border:1px solid var(--divider);border-radius:4px;"
               "min-width:26px;text-align:center;" if boxed else "min-width:20px;text-align:center;")
        return f'<span class="mono" style="{style};{box}padding:1px 4px">{esc(disp)}</span>'

    def row(name, ioc, idx):
        cells = "".join(cell(c[idx], c[1 - idx], i == last) for i, c in enumerate(cols))
        fl = _flag(ioc)
        return (f'<div style="display:flex;align-items:center;gap:8px">'
                f'<span style="width:1.4em">{fl}</span>'
                f'<span class="nm" style="flex:1">{esc(name)}</span>'
                f'<span style="display:flex;gap:5px">{cells}</span></div>')

    return (f'<div style="margin:2px 0 4px">'
            f'<div class="sub2" style="color:var(--accent);font-weight:800;'
            f'letter-spacing:.08em;margin-bottom:4px">● LIVE</div>'
            f'{row(a_name, a_ioc, 0)}{row(b_name, b_ioc, 1)}</div>')


def conf_meter(conf: float | None) -> str:
    """A 5-segment confidence meter — the named band, the value, and filled
    segments up to that band, coloured by tier."""
    from bot.prob.confidence import CONFIDENCE_BANDS, confidence_band
    b = confidence_band(conf)
    # bands are ordered high→low; segment i (1..5) is filled if conf reaches it
    order = list(reversed(CONFIDENCE_BANDS))  # Minimal→Strong
    fill = sum(1 for band in order if (conf or 0.0) >= band.lo)
    col = {"good": "var(--good)", "neutral": "var(--muted)",
           "warn": "var(--warning)", "critical": "var(--accent)"}[b.tier]
    segs = "".join(
        f'<span style="flex:1;height:6px;border-radius:2px;background:'
        f'{col if i < fill else "var(--divider)"}"></span>'
        for i in range(5))
    return (f'<span class="cmeter" title="{esc(b.note)}">'
            f'<span style="display:flex;gap:3px;width:64px">{segs}</span>'
            f'<span class="sub2" style="color:{col};font-weight:800">{esc(b.label)}</span>'
            f'<span class="sub2">{(conf or 0.0):.0%}</span></span>')


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
function lfState(){try{return JSON.parse(localStorage.getItem('deuce_lf'))||{}}catch(e){return {}}}
function applyLiveFilters(){
 var s=lfState(), tours=s.tours||[], shown=0, total=0;
 document.querySelectorAll('.livecard').forEach(function(c){
  total++;
  var okT=tours.length===0||tours.indexOf(c.dataset.tour)>=0;
  var okP=!s.play||c.dataset.play==='1';
  var okS=!s.scenario||c.dataset.scenario==='1';
  var okF=!s.trigfired||c.dataset.trigfired==='1';
  var okG=!s.trig||c.dataset.trig==='hit'||c.dataset.trig==='near';
  var ok=okT&&okP&&okS&&okF&&okG; c.style.display=ok?'':'none'; if(ok)shown++;});
 // never let a stale filter hide every live match — fall back to showing all
 if(shown===0&&total>0){document.querySelectorAll('.livecard').forEach(function(c){c.style.display='';});shown=total;}
 document.querySelectorAll('.fchip').forEach(function(ch){var f=ch.dataset.f,on;
  if(f==='play')on=!!s.play; else if(f==='trig')on=!!s.trig;
  else if(f==='trigfired')on=!!s.trigfired;
  else if(f==='scenario')on=!!s.scenario; else on=tours.indexOf(f)>=0;
  ch.classList.toggle('on',on);});
 var vc=document.getElementById('livecount'); if(vc)vc.textContent=shown;}
function bindFilters(){
 document.querySelectorAll('.fchip').forEach(function(ch){if(ch._b)return;ch._b=true;
  ch.addEventListener('click',function(){var s=lfState(),f=ch.dataset.f;
   if(f==='play')s.play=!s.play; else if(f==='trig')s.trig=!s.trig;
   else if(f==='trigfired')s.trigfired=!s.trigfired;
   else if(f==='scenario')s.scenario=!s.scenario;
   else{s.tours=s.tours||[];var i=s.tours.indexOf(f);if(i>=0)s.tours.splice(i,1);else s.tours.push(f);}
   localStorage.setItem('deuce_lf',JSON.stringify(s));applyLiveFilters();});});
 applyLiveFilters();}
function typing(){var a=document.activeElement;
 return a && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName);}
async function refreshMain(){
 if(typing()) return;  // don't clobber a field mid-keystroke
 try{var r=await fetch(location.pathname+location.search,{headers:{'X-Fragment':'1'}});
  if(r.ok){var h=await r.text(); var m=document.querySelector('main');
   if(!typing() && h && h.length>50 && h!==m.innerHTML){var y=window.scrollY;
    m.innerHTML=h; window.scrollTo(0,y); bindWatch(); bindFilters();}
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
 bindWatch(); bindFilters();
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
    nav_groups = (
        (("/", "home", "Overview"), ("/live", "live", "Live"),
         ("/scenarios", "scenarios", "Scenarios")),
        (("/testrun/pre", "testrun", "Testrun"),),
        (("/history", "history", "History"), ("/players", "players", "Database")),
        (("/flags", "flags", "Flags"), ("/system", "system", "System")),
    )
    navs = "".join(
        '<span class="navgroup">' + "".join(
            f'<a href="{href}" class="{"active" if key == active else ""}">{label}</a>'
            for href, key, label in group) + "</span>"
        for group in nav_groups)
    dot, conn = _feed_status()
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · DEUCE</title><style>{CSS}</style></head>
<body>
<header class="nav">
  <span class="brand">DEUCE<span class="tag">advisory only</span></span>
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
        # command-center extras: today's best plays + bot activity
        from bot.models import PaperBet
        from sqlalchemy import func as _func
        latest_day = db.execute(select(_func.max(Scenario.created_for))).scalar()
        best_plays = db.execute(
            select(Scenario, Player.full_name)
            .join(Player, Player.id == Scenario.player_id, isouter=True)
            .where(Scenario.created_for == latest_day)
            .order_by(Scenario.salience.desc()).limit(4)).all() if latest_day else []
        bet_open = db.execute(select(_func.count(PaperBet.id)).where(
            PaperBet.status == "open")).scalar()
        bet_settled = db.execute(select(_func.count(PaperBet.id)).where(
            PaperBet.status.in_(("won", "lost", "void")))).scalar()

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
    best_cards = "".join(
        f'<a class="tag tag-warn" style="text-decoration:none" href="/scenario/{sc.id}">'
        f'★ {esc(pl.split()[-1] if pl else "pick")} · {sc.prematch_prob:.0%} '
        f'<span class="sub2">({sc.salience:.2f})</span></a>'
        for sc, pl in best_plays)
    overview = f"""<section class="block"><div class="blockhead">
<h4>Right now</h4><span class="aside"><a href="/live">full board →</a></span></div>
<div class="rule"></div>
<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
<span class="sub2" style="min-width:74px">live:</span>
{live_cards or '<span class="sub2">no matches live</span>'}</div>
<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px">
<span class="sub2" style="min-width:74px">best plays:</span>
{best_cards or '<span class="sub2">none today</span>'}
<a class="sub2" href="/scenarios">all scenarios →</a></div>
<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px">
<span class="sub2" style="min-width:74px">next up:</span>
{plan_cards or '<span class="sub2">none scheduled</span>'}</div>
<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px">
<span class="sub2" style="min-width:74px">bots:</span>
<a class="tag tag-neutral" style="text-decoration:none" href="/testrun/leaderboard">
6 bots · {bet_settled} settled · {bet_open} open · leaderboard →</a></div></section>"""
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

    now = datetime.now(timezone.utc)
    with db_session() as db:
        latest_day = db.execute(select(func.max(Scenario.created_for))).scalar()
        rows = db.execute(
            select(Scenario, Player.full_name)
            .join(Player, Player.id == Scenario.player_id, isouter=True)
            .where(Scenario.created_for == latest_day)
            .order_by(Scenario.salience.desc())
        ).all() if latest_day else []
        tickers = [sc.market_ticker for sc, _ in rows]
        states = {s.market_ticker: s for s in db.execute(
            select(LiveMatchState).where(
                LiveMatchState.market_ticker.in_(tickers))).scalars()} \
            if tickers else {}

    # "best" = a genuine standout. Salience is a stable sum of edge components
    # (set-1 gap, decider edge, fatigue, percentile…); ≥ 1.0 is a strong setup.
    # Always flag at least the single top-ranked so there's a pick of the day.
    BEST_SALIENCE = 1.0
    top_id = rows[0][0].id if rows else None
    best_ids = {sc.id for sc, _ in rows
                if sc.salience >= BEST_SALIENCE or sc.id == top_id}

    def card(sc, player) -> str:
        f = sc.facts or {}
        match_label = f.get("match") or sc.event_ticker
        snippet = ". ".join(sc.narrative.split(". ")[:2]).strip()
        if snippet and not snippet.endswith("."):
            snippet += "."
        st = states.get(sc.market_ticker)
        live_tag = (f'<span class="scen-flag">● LIVE</span> '
                    f'<span class="sub2">{esc(st.state)}</span>'
                    if st is not None and not st.stale else "")
        best = sc.id in best_ids
        best_badge = '<span class="best-flag">★ BEST</span>' if best else ""
        return f"""<a class="card scard{' scard-best' if best else ''}" href="/scenario/{sc.id}"
style="text-decoration:none;color:inherit;display:block">
<div style="display:flex;align-items:center;justify-content:space-between;gap:12px">
<span class="kicker" style="margin:0">{best_badge}{esc(f.get('event_label') or 'gameflow plan')}</span>
{tag('outline', '◆', f'{sc.salience:.2f}')}</div>
<div class="title">{esc(match_label)} <span class="sub2">→</span></div>
<div class="sub2 mono">watch <strong style="color:var(--text)">{esc(player)}</strong>
· {sc.prematch_prob:.0%} prematch · {pt(sc.scheduled_start)}</div>
<div style="margin:4px 0">{live_tag}</div>
<div class="prose" style="margin-top:2px">{esc(snippet)}
<span class="sub2">full scenario →</span></div>
</a>"""

    # (is_best, sort-key, html) so best float to the top of each section
    live_cards, soon_cards = [], []
    for sc, player in rows:
        st = states.get(sc.market_ticker)
        start = sc.scheduled_start
        best = sc.id in best_ids
        is_live = (st is not None and not st.stale) or (
            start is not None and now - timedelta(hours=3) <= start <= now + timedelta(minutes=15))
        entry = (0 if best else 1, start or now, card(sc, player))
        if is_live:
            live_cards.append(entry)
        elif start is not None and now + timedelta(minutes=15) < start <= now + timedelta(hours=24):
            soon_cards.append(entry)
        # else: stale (finished) or beyond 24h — dropped
    live_cards.sort(key=lambda x: (x[0], x[1]))
    soon_cards.sort(key=lambda x: (x[0], x[1]))

    def section(title, aside, cards):
        inner = "".join(c for *_, c in cards) or \
            f'<div class="card"><div class="empty">{esc(aside)}</div></div>'
        return (f'<section class="block"><div class="blockhead"><h4>{title}</h4>'
                f'<span class="aside">{len(cards)}</span></div>'
                f'<div class="rule"></div><div class="cards">{inner}</div></section>')

    body = pagehead("Strategy", "Gameflow Scenarios",
                    f"generated {latest_day}" if latest_day else "") + f"""
<p class="prose" style="margin:0 0 18px">Pre-computed before play: if a match
reaches the named situation, the model already knows which side is live. Each is
its own page — click through for the full read. <span class="best-flag">★ BEST</span>
marks the standout setups (highest salience — the composite of set-1 gap, decider
edge, fatigue and field-percentile signals). Regenerated continuously by the
worker and the daily ingest; the engine still applies every gate before any
advisory fires.</p>
{section("Live now", "No scenario matches are live right now.", live_cards)}
{section("Next 24 hours", "Nothing scenario-flagged in the next 24h.", soon_cards)}"""
    return respond(request, "Scenarios", "scenarios", body)


async def scenario_detail(request: web.Request) -> web.Response:
    from bot.models import Scenario

    try:
        sid = int(request.match_info["sid"])
    except (KeyError, ValueError):
        return web.Response(status=404, text="no such scenario")
    with db_session() as db:
        row = db.execute(
            select(Scenario, Player.full_name)
            .join(Player, Player.id == Scenario.player_id, isouter=True)
            .where(Scenario.id == sid)).first()
        if row is None:
            return web.Response(status=404, text="no such scenario")
        sc, player = row
        opp = db.get(Player, sc.opponent_id) if sc.opponent_id else None
        opp_name = opp.full_name if opp else "opponent"
        # live context, if the match is under way
        st = db.execute(select(LiveMatchState).where(
            LiveMatchState.market_ticker == sc.market_ticker)).scalar()
        from sqlalchemy import text as sqltext
        sl = db.execute(sqltext(
            "SELECT scoreline, sets_a, sets_b FROM match_score_log "
            "WHERE market_ticker = :t ORDER BY ts DESC LIMIT 1"),
            {"t": sc.market_ticker}).first()
        # both sides' live Kalshi odds + a short game log for full match context
        mkts = db.execute(select(KalshiMarket).where(
            KalshiMarket.event_ticker == sc.event_ticker)
            .order_by(KalshiMarket.ticker)).scalars().all()
        quotes = _latest_quotes(db, [m.ticker for m in mkts]) if mkts else {}
        gamelog = db.execute(sqltext(
            "SELECT scoreline, ts FROM match_score_log WHERE market_ticker = :t "
            "ORDER BY ts DESC LIMIT 6"), {"t": sc.market_ticker}).all()

    f = sc.facts or {}
    conf = f.get("model_confidence")
    from bot.prob.confidence import confidence_band
    cb = confidence_band(conf)
    strip = statstrip([
        ("Prematch", f"{sc.prematch_prob:.0%}", "model, pre-play"),
        ("In a decider", f"{sc.model_prob_at_state:.0%}", "if it goes the distance"),
        ("Confidence", f'<span class="tag tag-{cb.tier}" style="font-size:13px">{cb.label}</span>',
         f"{(conf or 0):.0%} data depth — {cb.note}"),
        ("Salience", f"{sc.salience:.2f}", "why it ranked"),
    ], cols=4)

    # set-rate comparison from the fact block
    srw = f.get("set_rates_watch") or {}
    sro = f.get("set_rates_opp") or {}
    setrows = ""
    for n in ("1", "2", "3"):
        w = srw.get(n) if n in srw else srw.get(int(n))
        o = sro.get(n) if n in sro else sro.get(int(n))
        if w is None and o is None:
            continue
        setrows += (f'<div class="vsrow"><span class="k">set {n}</span>'
                    f'<span class="v mono">{w if w is not None else "—"}%'
                    f' <span class="sub2">vs {o if o is not None else "—"}%</span></span></div>')
    dw, do = f.get("decider_watch"), f.get("decider_opp")
    dec = ""
    if dw or do:
        dec = (f'<div class="vsrow"><span class="k">deciding sets</span>'
               f'<span class="v mono">{dw[0]}-{dw[1] if dw else ""}'
               f' <span class="sub2">vs {do[0]}-{do[1]}</span></span></div>'
               if dw and do else "")
    rates_html = (f'<section class="block"><div class="blockhead"><h4>'
                  f'{esc(player)} vs {esc(opp_name)} — set rates</h4></div>'
                  f'<div class="rule"></div>{setrows}{dec}</section>'
                  if setrows or dec else "")

    # live odds for both sides + game log
    odds_html = ""
    for m in mkts[:2]:
        nm = (m.raw or {}).get("yes_sub_title") or m.ticker.rsplit("-", 1)[-1]
        cents, live_q = _odds_cents(m, quotes)
        dot = '<span style="color:var(--good)">●</span> ' if live_q else ""
        odds_html += (f'<div class="vsrow"><span class="k">{esc(nm.split()[-1])}</span>'
                      f'<span class="v mono">{dot}{cents}¢</span></div>'
                      if cents is not None else "")
    log_html = ""
    if gamelog:
        rows = "".join(f'<div class="vsrow"><span class="k mono">{esc(r[0])}</span>'
                       f'<span class="sub2">{pt(r[1])}</span></div>' for r in gamelog)
        log_html = (f'<div class="vshead" style="margin-top:12px">Game log'
                    f'<span>most recent first</span></div>{rows}')
    live_html = ""
    if (sl and sl[0]) or odds_html:
        state = st.state if st else None
        score = (f'<div class="mono" style="font-size:17px;font-weight:800;margin-bottom:8px">'
                 f'{esc(sl[0])} <span class="sub2">· {sl[1]}-{sl[2]} sets</span></div>'
                 if sl and sl[0] else "")
        live_html = (f'<section class="block"><div class="blockhead"><h4>Live match</h4>'
                     f'<span class="aside">{esc(state or "current odds")}</span></div>'
                     f'<div class="rule"></div>{score}'
                     f'<div class="vshead">Kalshi odds<span>implied win %</span></div>'
                     f'{odds_html or "<div class=sub2>no live price</div>"}{log_html}</section>')

    match_label = f.get("match") or sc.event_ticker
    body = pagehead("Scenario", esc(match_label),
                    f'<a href="/scenarios">← all scenarios</a>') + f"""
<p class="prose" style="margin:0 0 14px">Watch <strong>{esc(player)}</strong>
· {esc(f.get('event_label') or '')} · scheduled {pt(sc.scheduled_start)} ·
{kalshi_link(sc.market_ticker)}</p>
<a href="/match/{esc(sc.event_ticker)}" class="fchip" style="display:inline-block;
margin:0 0 18px;border-color:var(--accent);color:var(--text)">📊 full match data, charts &amp; game-by-game →</a>
{strip}{live_html}
<section class="block"><div class="blockhead"><h4>The read</h4>
<span class="aside">deterministic — every number traces to the fact block</span></div>
<div class="rule"></div><div class="prose">{esc(sc.narrative)}</div></section>
{rates_html}"""
    return respond(request, f"Scenario · {match_label}", "scenarios", body)


TP_LIMIT = 90  # take-profit limit price (cents) for the TP variant
USD_PER_UNIT = 10  # a testrun "unit" of stake = $10 (i.e. 10 Kalshi contracts)


def dol(pnl_cents) -> float:
    """Convert a paper-bet P&L (cents at 1 contract per unit) to dollars under
    the $10-per-unit convention."""
    return pnl_cents * USD_PER_UNIT / 100.0


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

    if not won and touched90 and tp_pnl is not None and hold_pnl is not None:
        S.append(f"But {pick} led first — our side traded up to {TP_LIMIT}¢ before "
                 f"the reversal, so the {TP_LIMIT}¢ take-profit salvaged "
                 f"+${dol(tp_pnl):.2f} here where holding took a "
                 f"${dol(abs(hold_pnl)):.2f} loss.")
    elif won and tp_status == "took_profit" and hold_pnl is not None \
            and tp_pnl is not None and tp_pnl < hold_pnl:
        S.append(f"Hold banked +${dol(hold_pnl):.2f}; the {TP_LIMIT}¢ exit capped "
                 f"at +${dol(tp_pnl):.2f} — ~${dol(hold_pnl - tp_pnl):.2f} left on "
                 f"the table for the early lock-in.")

    if thesis:
        first = thesis.split(". ")[0]
        S.append(f"Pre-game read was: “{esc(first)}.”")

    if clv is not None:
        verb = "beat" if clv > 0 else "lagged" if clv < 0 else "matched"
        S.append(f"Closing-line value: {verb} the close by {abs(clv)}¢"
                 + (" (bought too late)." if clv < 0 else "."))
    return '<div class="prose" style="margin-top:6px">' + " ".join(S) + "</div>"


async def testrun(request: web.Request) -> web.Response:
    bot = request.match_info.get("bot", "pre")
    bot = {"t1": "pre", "t2": "preSI"}.get(bot, bot)  # legacy 2-bot ids
    if bot == "leaderboard":
        return await bots_leaderboard(request)
    return await _testrun_view(request, bot=bot)


async def bots_leaderboard(request: web.Request) -> web.Response:
    """All six bots side by side — record, win rate, ROI, CLV, units — so the
    winning strategy is visible without clicking through each page."""
    from bot.models import KalshiMarket, PaperBet
    from bot.track import advisory_outcome, clv_cents
    from bot.t2 import BOTS

    with db_session() as db:
        rows = db.execute(
            select(PaperBet, KalshiMarket.result, KalshiMarket.close_yes_cents)
            .join(KalshiMarket, KalshiMarket.ticker == PaperBet.market_ticker)).all()
    per = {b: {"w": 0, "l": 0, "open": 0, "pnl": 0, "stake": 0, "clv": [],
               "un": 0.0} for b in BOTS}
    for b, res, close in rows:
        if b.bot not in per:
            continue
        d = per[b.bot]
        o = advisory_outcome(b.side, res)
        u = b.units or 1.0
        if o == "won":
            d["w"] += 1; d["pnl"] += (100 - b.price_cents) * u; d["un"] += (100 - b.price_cents) * u / b.price_cents
        elif o == "lost":
            d["l"] += 1; d["pnl"] -= b.price_cents * u; d["un"] -= u
        else:
            d["open"] += 1; continue
        d["stake"] += b.price_cents * u
        c = clv_cents(b.side, b.price_cents, close)
        if c is not None:
            d["clv"].append(c)

    def rowhtml(bid):
        m = BOTS[bid]
        d = per[bid]
        n = d["w"] + d["l"]
        wr = d["w"] / n if n else None
        roi = d["pnl"] / d["stake"] if d["stake"] else None
        clv = sum(d["clv"]) / len(d["clv"]) if d["clv"] else None
        wrc = ("var(--good)" if wr and wr >= 0.7 else
               "var(--warning)" if wr and wr >= 0.6 else "var(--text)")
        pcol = ("var(--good)" if d["pnl"] > 0 else
                "var(--accent)" if d["pnl"] < 0 else "var(--muted)")
        clvc = ("var(--good)" if clv and clv > 0 else
                "var(--accent)" if clv and clv < 0 else "var(--muted)")
        wr_s = f"{wr:.0%}" if wr is not None else "—"
        profit_s = f"${dol(d['pnl']):+.2f}" if n else "—"
        units_s = f"{d['un']:+.1f}u" if n else "—"
        roi_s = f"{roi:+.1%}" if roi is not None else "—"
        clv_s = f"{clv:+.1f}¢" if clv is not None else "—"
        learns = " · learns" if m["si"] else ""
        return (f'<tr><td><a href="/testrun/{bid}"><strong>{esc(m["label"])}</strong></a>'
                f'<span class="sub2">{learns}</span></td>'
                f'<td class="mono">{d["w"]}-{d["l"]}</td>'
                f'<td class="mono" style="color:{wrc}">{wr_s}</td>'
                f'<td class="mono" style="color:{pcol};font-weight:800">{profit_s}</td>'
                f'<td class="mono" style="color:{pcol}">{units_s}</td>'
                f'<td class="mono">{roi_s}</td>'
                f'<td class="mono" style="color:{clvc}">{clv_s}</td>'
                f'<td class="mono sub2">{d["open"]}</td></tr>')

    # order: most settled bets first
    order = sorted(BOTS, key=lambda b: -(per[b]["w"] + per[b]["l"]))
    body = pagehead("Strategy Lab", "Bots — Leaderboard",
                    "all six side by side · hold-to-settlement basis") + f"""
<p class="prose" style="margin:0 0 16px">Six bots on the same model, differing by
<strong>when</strong> they bet (pre-game · live · top-5 daily) and <strong>how</strong>
they tune (fixed vs self-improving). Records are hold-to-settlement; CLV is entry
vs the closing line. Click a name for its full page.</p>
<div class="tw"><table class="t">
<tr><th>bot</th><th>record</th><th>win rate</th><th>profit</th><th>units</th>
<th>ROI</th><th>CLV</th><th>open</th></tr>
{''.join(rowhtml(b) for b in order)}
</table></div>"""
    return respond(request, "Bots Leaderboard", "testrun", body)


async def testrun_tp(request: web.Request) -> web.Response:
    raise web.HTTPFound("/testrun/pre")


async def _testrun_view(request: web.Request, bot: str = "pre") -> web.Response:
    from bot.models import KalshiMarket, PaperBet, Scenario
    from bot.paper import DEFAULT_POLICY, MAX_UNITS, PAPER_MIN_EDGE, PAPER_MIN_PROB
    from bot.t2 import BOTS, TOP5_N, bot_policy, bot_state
    TOP5_DISPLAY = 12

    if bot not in BOTS:
        return web.Response(status=404, text="no such bot")
    meta = BOTS[bot]
    is_si = meta["si"]
    is_live_bot = meta["basis"] == "advisory"
    is_tp = False  # both exits shown side-by-side; kept for existing branches
    with db_session() as db:
        policy = bot_policy(db, bot)
        learned = bot_state(db, bot)
        bets = db.execute(
            select(PaperBet, Player.full_name)
            .join(Player, Player.id == PaperBet.player_id, isouter=True)
            .where(PaperBet.bot == bot)
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
    # side-by-side scoreboard: every metric for BOTH exit rules, same picks
    nfin = len(finished)

    def _mode_col(label: str, sub: str, w, l, pc, un, roi) -> str:
        tot = w + l
        pcol = "var(--good)" if pc > 0 else "var(--accent)" if pc < 0 else "var(--text)"
        wr = f"{w / tot:.0%}" if tot else "—"
        wrc = ("var(--good)" if tot and w / tot >= 0.70 else
               "var(--warning)" if tot and w / tot >= 0.60 else "var(--text)")

        def r(k, v):
            return (f'<div class="vsrow"><span class="k">{k}</span>'
                    f'<span class="v mono">{v}</span></div>')
        return (f'<div class="vscol"><div class="vshead">{label}'
                f'<span>{esc(sub)}</span></div>'
                + r("Record", f"{w}-{l}")
                + r("Win rate", f'<span style="color:{wrc}">{wr}</span>')
                + r("Profit", f'<span style="color:{pcol}">${dol(pc):+.2f}</span>')
                + r("Units", f'<span style="color:{pcol}">{un:+.2f}u</span>')
                + r("ROI", f"{roi:+.1%}" if roi is not None else "—")
                + '</div>')
    scoreboard = (f'<div class="vsgrid">'
                  f'{_mode_col("Hold", "ride to settlement (100¢/0¢)", *mode_stats(False))}'
                  f'{_mode_col("90¢ Take-Profit", "limit exit at 90¢", *mode_stats(True))}'
                  f'</div>')
    shared = statstrip([
        ("Settled", str(nfin), "same picks · both exits"),
        ("Open", str(len(open_bets)), "awaiting result"),
        ("CLV", f'<span style="color:{clv_color}">{avg_clv:+.1f}¢</span>'
         if avg_clv is not None else "—",
         f"entry vs close · beat {beat}/{len(clv_vals)}" if clv_vals
         else "vs match-start line"),
        ("Running", f"{days}d", "since first bet · target 70%"),
    ], cols=4)
    strip = scoreboard + shared
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
            (lambda u: f"Sized {u:.1f}u: " + (
                "baseline stake — cleared the gates without extra conviction."
                if u < 1.5 else
                "elevated stake — a strong calibrated favorite on solid data."
                if u < 2.5 else
                "rare max stake — an exceptional favorite on deep data."))(b.units or 1.0),
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
            pnl_txt = f"${dol(pc):+.2f}" if pc is not None else "—"
            match = (b.reasoning or {}).get("match", b.event_ticker)
            out.append(f"""<tr>
<td class="mono sub2">{pt(b.created_at)}</td>
<td><a href="/match/{esc(b.event_ticker)}" style="color:inherit;text-decoration:none">
<span class="pname" style="border-bottom:1px dotted var(--muted)">{esc(player)}</span></a>
<br><span class="sub2">{esc(match)}</span> · {kalshi_link(b.market_ticker)}
· <a class="sub2" href="/match/{esc(b.event_ticker)}">full analysis →</a>
{why(b, player)}</td>
<td class="mono">{b.price_cents}¢</td>
<td class="mono" style="font-weight:800">{b.units or 1:.1f}u</td>
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
            series.append((b.settled_at, dol(cum)))
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
    from bot.scenarios import SERIES_TIER

    def _tier_of(ticker: str) -> str:
        return next((t for s, t in SERIES_TIER.items() if ticker.startswith(s)), "15")

    now = datetime.now(timezone.utc)
    with db_session() as db:
        bet_events = set(db.execute(select(PaperBet.event_ticker)
                                    .where(PaperBet.bot == bot)).scalars().all())
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
        dec = decide_bet(sc.prematch_prob, conf, ya, yb,
                         tier=_tier_of(sc.market_ticker), policy=policy)
        price = ya if ya is not None else "—"
        if dec.place:
            verdict = tag("good", "✓", f"clears — {dec.units:.1f}u candidate")
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
    tp_live = sum(1 for b, _ in bets if results.get(b.market_ticker) is None
                  and (lambda t: (b.side == "yes" and (t[0] or 0) >= TP_LIMIT)
                       or (b.side == "no" and (t[1] or 0) >= TP_LIMIT))(
                      touched.get(b.market_ticker, (None, None))))
    comparison_html = ""
    if nfin:
        # per-match decomposition: TP salvages reversals (+90¢ each) and caps
        # clean winners (−10¢ each) — exactly why the two exits differ. Full
        # metrics for both live in the scoreboard up top; this explains the gap.
        salv_n = salv = cap_n = cap = 0
        for b, _ in finished:
            d = cmp_out(b, True) - cmp_out(b, False)
            if d > 0:
                salv_n += 1; salv += d
            elif d < 0:
                cap_n += 1; cap += d
        net = salv + cap
        verdict = ("Take-profit is ahead" if net > 0 else "Hold is ahead"
                   if net < 0 else "Dead even")
        comparison_html = f"""<section class="block"><div class="blockhead">
<h4>Why the two exits differ</h4><span class="aside">same {nfin} finished matches · identical picks</span></div>
<div class="rule"></div>
<p class="prose" style="margin-top:6px"><strong>{verdict}</strong> by
${dol(abs(net)):.2f} on these {nfin} matches. Take-profit
<strong>salvaged {salv_n}</strong> lead(s) that reversed (+${dol(salv):.2f} — the
limit banked ~90¢ before the collapse) and <strong>capped {cap_n}</strong> clean
winner(s) at 90¢ (${dol(cap):.2f}, giving up ~10¢ each vs holding to 100¢).
TP wins this trade-off only when it salvages at least one reversal per nine
winners it caps — here {salv_n} vs {cap_n}. Both curves are on the Cumulative
P&amp;L chart above.</p>
{f'<p class="sub2">(TP has also realized {tp_live} live position(s) on matches still in play — held out here for a like-for-like record.)</p>' if tp_live else ''}
</section>"""

    is_top5 = meta["basis"] == "top5"
    # live bots don't have a pre-game watchlist — they fire from in-play advisories
    if is_live_bot:
        watching_html = (
            '<section class="block"><div class="blockhead"><h4>How live bets fire</h4>'
            '<span class="aside">in-play</span></div><div class="rule"></div>'
            '<p class="prose">This bot places no pre-game bets. It fires only '
            'when a match is under way and a live advisory clears its policy — so '
            'its bets appear below as they happen. Watch the '
            '<a href="/live">live board</a> for in-play triggers.</p></section>')
    elif is_top5:
        # the top-5 bot's "watchlist" IS the day's highest-salience scenarios
        with db_session() as db:
            topscen = db.execute(
                select(Scenario, Player.full_name)
                .join(Player, Player.id == Scenario.player_id, isouter=True)
                .where(Scenario.created_for == db.execute(
                    select(func.max(Scenario.created_for))).scalar())
                .order_by(Scenario.salience.desc()).limit(TOP5_DISPLAY)).all()
        trows = "".join(
            f'<tr><td class="mono sub2">{i + 1}</td>'
            f'<td><a href="/scenario/{sc.id}" style="text-decoration:none">'
            f'<span class="pname">{esc(nm)}</span></a> '
            f'<span class="sub2">{esc((sc.facts or {}).get("match", ""))}</span></td>'
            f'<td class="mono">{sc.prematch_prob:.0%}</td>'
            f'<td class="mono">{sc.salience:.2f}</td>'
            f'<td class="mono sub2">{pt(sc.scheduled_start)}</td></tr>'
            for i, (sc, nm) in enumerate(topscen))
        watching_html = (
            f'<section class="block"><div class="blockhead"><h4>Today\'s top scenarios</h4>'
            f'<span class="aside">salience-ranked · bets the top {TOP5_N} that clear policy</span>'
            f'</div><div class="rule"></div><div class="tw"><table class="t">'
            f'<tr><th>#</th><th>watch side</th><th>model</th><th>salience</th><th>starts</th></tr>'
            f'{trows or "<tr><td colspan=5 class=empty>No scenarios yet.</td></tr>"}'
            f'</table></div></section>')

    from bot.t2 import BOTS as _BOTS
    switcher = ('<div class="filterbar" style="margin-bottom:18px">'
                + '<a class="fchip" href="/testrun/leaderboard">★ Leaderboard</a>'
                + "".join(
                    f'<a class="fchip{" on" if bid == bot else ""}" href="/testrun/{bid}">'
                    f'{esc(m["label"])}</a>' for bid, m in _BOTS.items()) + '</div>')

    title = f"Testrun · {meta['label']}"
    active = "testrun"
    hist_link = f"/testrun/history?bot={bot}"
    when_txt = ("live, in-play — it fires only when an advisory clears mid-match"
                if is_live_bot else
                f"a curated slate — only the day's {TOP5_N} highest-salience "
                f"scenarios (best first), backing each that clears its policy"
                if is_top5 else
                "pre-game — off the model's opening read before the match")
    tune_txt = ("It <strong>tunes its own policy</strong> from its own settled "
                "record: it nudges its probability floor toward the band where it "
                "actually wins, tightens its edge cap if big-edge bets underperform, "
                "and scales its stake with recent ROI (all bounded). Until it has "
                "enough settled bets it inherits the fixed policy."
                if is_si else
                "It bets a <strong>fixed, hand-tuned policy</strong> (the same rules "
                "every time). Its self-improving twin re-tunes those rules from "
                "results.")
    exit_note = f"""<p class="prose" style="margin:0 0 18px">This bot bets
<strong>{when_txt}</strong>. {tune_txt} <strong>Imaginary</strong> bets at
$10/unit, held to settlement (the 90¢ take-profit variant is tracked alongside
for comparison). Nothing here is, or ever becomes, a real order.</p>"""
    status_th = "status"

    learned_html = ""
    if is_si and learned:
        rows = "".join(
            f'<div class="vsrow"><span class="k">{k}</span>'
            f'<span class="v mono">{v}</span></div>'
            for k, v in (("prob floor", f"{policy.min_prob:.0%}"),
                         ("edge cap", f"{policy.max_edge:.0%}"),
                         ("stake ×", f"{policy.size_mult:.1f}"),
                         ("challenger floor", f"{policy.challenger_min_prob:.0%}")))
        hist = learned.get("history", [])
        hist_html = "".join(
            f'<div class="vsrow"><span class="k mono">{esc(h.get("version",""))}</span>'
            f'<span class="sub2">floor {h.get("min_prob",0):.0%} · edge '
            f'{h.get("max_edge",0):.0%} · ×{h.get("size_mult",1):.1f}</span></div>'
            for h in reversed(hist)) or '<div class="sub2">no prior versions yet</div>'
        learned_html = f"""<section class="block"><div class="blockhead">
<h4>Self-improvement · {esc(learned.get("version",""))}</h4>
<span class="aside">re-tuned from this bot's own settled record</span></div>
<div class="rule"></div>
<div class="vsgrid" style="margin-bottom:12px"><div class="vscol">
<div class="vshead">Current learned policy<span>{esc(learned.get("rationale",""))}</span></div>
{rows}</div><div class="vscol">
<div class="vshead">Version history<span>most recent first</span></div>
{hist_html}</div></div>
<p class="sub2">Adapts only after {esc(str(learned.get("n_basis", 0)))} settled bets
inform it; every parameter is bounded and each change is versioned so records
never blend.</p></section>"""

    t2_extra = ("<p><strong>This bot learns.</strong> It re-tunes rules 3 and 4 "
                "from its own settled record (see the Self-improvement panel above) "
                "— everything else is identical to its fixed twin.</p>"
                if is_si else "")
    method_html = f"""<section class="block"><details class="coll" data-key="howbets-{bot}">
<summary style="cursor:pointer;list-style:none;display:flex;align-items:baseline;
justify-content:space-between;gap:12px">
<span><span style="font-family:var(--font);font-weight:800;font-size:19px">How this bot places bets</span>
<span class="sub2" style="margin-left:8px">its model · the rules · sizing · exit</span></span>
<span class="sub2 coll-caret">▸ show</span></summary>
<div class="rule" style="margin-top:8px"></div>
<div class="prose">
<p><strong>1 · Its own probability.</strong> A surface-adjusted, set-level Elo
model — recalibrated against real match outcomes — estimates each player's chance
to win. This is the bot's <em>own</em> number, built only from match results and
<strong>never from the Kalshi price</strong>, so the model can't just echo the
market.</p>
<p><strong>2 · Edge vs the market.</strong> It compares that probability to the
executable Kalshi price: <em>edge = model probability − price</em>. A positive edge
means the bot thinks the market underprices its pick — including spots where Kalshi
lists that player as an underdog under 50¢.</p>
<p><strong>3 · The rules</strong> (a bet fires only if <em>all</em> hold): model
probability ≥ <strong>{policy.min_prob:.0%}</strong> (its own number — favorites
only, since the target is a winning record); edge between
<strong>{policy.min_edge:.0%}</strong> and <strong>{policy.max_edge:.0%}</strong>
(a bigger gap than {policy.max_edge:.0%} is treated as a stale/thin quote and
skipped); executable price {policy.min_price}–{policy.max_price}¢; model confidence
≥ <strong>{policy.min_conf:.0%}</strong> (<strong>Fair</strong> or better on the
confidence scale — enough match history on <em>both</em> players); Challenger-tier
matches demand a stronger favorite
(≥ <strong>{policy.challenger_min_prob:.0%}</strong>). One bet per match, ever.</p>
<p><strong>Confidence scale.</strong> Confidence is <em>data depth</em> (how much
history the model has on the two players), not certainty:
<span class="tag tag-critical">Minimal</span>
<span class="tag tag-warn">Low</span>
<span class="tag tag-neutral">Fair</span>
<span class="tag tag-good">Good</span>
<span class="tag tag-good">Strong</span> — the bot bets only at Fair+.</p>
<p><strong>4 · Size by conviction.</strong> $10 per unit, {1.0:.1f}–{MAX_UNITS:.1f}u.
The stake grows only when the model is <em>both</em> very confident <em>and</em> the
read rests on deep data — so multi-unit bets are rare and most sit near 1u.</p>
<p><strong>5 · Exit.</strong> The headline record holds every bet to settlement
(100¢ or 0¢); the 90¢ take-profit variant is tracked alongside for comparison.</p>
{t2_extra}
<p class="sub2">Every bet below also carries its own written reasoning — the
gameflow read and why it was sized as it was. Advisory only: these are imaginary
bets, and no order is ever placed.</p>
</div></details></section>"""

    if is_live_bot:
        bets_html = bets_section("Live-game bets", "fired in-play when an advisory "
                                 "cleared the policy mid-match", "advisory", "live_open")
    elif is_top5:
        bets_html = bets_section("Top-5 daily bets", "the day's highest-salience "
                                 "plays that cleared policy", "prematch", "pre_open")
    else:
        bets_html = bets_section("Pre-game bets", "placed before the match, off the "
                                 "model's opening read", "prematch", "pre_open")

    body = pagehead("Strategy Lab", title,
                    f'{n} settled · <a href="{hist_link}">post-game log →</a> · '
                    f'<a href="/track">advisory track record →</a>') \
        + switcher + strip + exit_note + method_html + learned_html \
        + watching_html + timeline_html + f"""
{comparison_html}
<section class="block"><div class="blockhead"><h4>Tuning breakdown</h4>
<span class="aside">where the record comes from — the improvement signal</span></div>
<div class="rule"></div>{breakdown}</section>
{bets_html}"""
    return respond(request, title, active, body)


async def testrun_history(request: web.Request) -> web.Response:
    """Every settled testrun bet with a post-game read: did the thesis hold,
    what the match actually did, how each exit fared, and CLV."""
    from sqlalchemy import text as sqltext

    from bot.models import KalshiMarket, PaperBet, Scenario
    from bot.track import advisory_outcome, clv_cents

    from bot.t2 import BOTS as _BOTS
    bot = request.query.get("bot", "pre")
    bot = {"t1": "pre", "t2": "preSI"}.get(bot, bot)
    if bot not in _BOTS:
        bot = "pre"
    with db_session() as db:
        bets = db.execute(
            select(PaperBet, Player.full_name)
            .join(Player, Player.id == PaperBet.player_id, isouter=True)
            .where(PaperBet.bot == bot,
                   PaperBet.status.in_(("won", "lost", "void")))
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
        tp_txt = f"${dol(tp_pnl):+.2f}" if tp_pnl is not None else "—"
        entries.append(f"""<div style="padding:14px 0;border-top:1px solid var(--divider)">
<div class="blockhead"><h4 style="font-size:16px">{esc(player or pick)} {badge}
<span class="mono sub2" style="font-weight:400">{esc(line or '—')}</span></h4>
<span class="aside">{pt(b.settled_at) if b.settled_at else ''}</span></div>
{analysis}
<div class="metric-grid" style="grid-template-columns:repeat(4,1fr);margin-top:8px">
<div class="metric"><div class="k">bet</div><div class="v mono">{esc(b.side)} @ {b.price_cents}¢ · {b.units or 1:.1f}u</div></div>
<div class="metric"><div class="k">hold P&amp;L</div><div class="v mono" style="color:{pc(b.pnl_cents)}">{f'${dol(b.pnl_cents):+.2f}' if b.pnl_cents is not None else '—'}</div></div>
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
    body = pagehead("Strategy Lab", f"Testrun History · {esc(_BOTS[bot]['label'])}",
                    f'<a href="/testrun/{bot}">← back to testrun</a>') \
        + strip + intro + log_html
    return respond(request, "Testrun History", "testrun", body)


async def history(request: web.Request) -> web.Response:
    """Time-travel: browse any past day's settled slate — every match that
    resolved, who won, the scoreline, and which ones the bot played."""
    import datetime as _dt
    from sqlalchemy import text as sqltext

    from bot.models import Advisory, KalshiMarket, PaperBet

    qd = request.query.get("d")
    try:
        day = _dt.date.fromisoformat(qd) if qd else \
            datetime.now(timezone.utc).date()
    except ValueError:
        day = datetime.now(timezone.utc).date()
    lo = datetime.combine(day, _dt.time.min, tzinfo=timezone.utc)
    hi = lo + timedelta(days=1)
    label = {"KXATPMATCH": "ATP", "KXWTAMATCH": "WTA", "KXWTAGAME": "WTA",
             "KXATPCHALLENGERMATCH": "CHALLENGER", "KXITFMATCH": "ITF M",
             "KXITFWMATCH": "ITF W"}

    with db_session() as db:
        settled = db.execute(sqltext("""
            SELECT DISTINCT ON (event_ticker) event_ticker, ticker, title, result,
                   settled_at, raw
            FROM kalshi_markets
            WHERE settled_at >= :lo AND settled_at < :hi AND result IN ('yes','no')
            ORDER BY event_ticker, settled_at DESC"""),
            {"lo": lo, "hi": hi}).all()
        tks = [r[1] for r in settled]
        scl = {}
        if tks:
            for r in db.execute(sqltext("""
                SELECT DISTINCT ON (market_ticker) market_ticker, scoreline, sets_a, sets_b
                FROM match_score_log WHERE market_ticker = ANY(:t)
                ORDER BY market_ticker, ts DESC"""), {"t": tks}).all():
                scl[r[0]] = (r[1], r[2], r[3])
        our_bets = {b.market_ticker: b for b in db.execute(
            select(PaperBet).where(PaperBet.market_ticker.in_(tks),
                                   PaperBet.bot == "pre")).scalars()} \
            if tks else {}
        advised = set(db.execute(select(Advisory.market_ticker).where(
            Advisory.market_ticker.in_(tks), Advisory.status == "sent")).scalars()) \
            if tks else set()

    cards, our_n, our_w = [], 0, 0
    for ev, tk, title, result, settled_at, raw in settled:
        matchup = (title or "").split(" the ", 1)[-1].split(":")[0].split(" match")[0] \
            if title and " the " in title else (title or ev)
        yes_name = (raw or {}).get("yes_sub_title") or ""
        winner = yes_name if result == "yes" else \
            _opponent_surname(title, yes_name.split()[-1] if yes_name else "")
        line, sa, sb = scl.get(tk, (None, None, None))
        score = f' · <span class="mono">{esc(line)}</span>' if line else ""
        tour = label.get((raw or {}).get("_series", ""), "?")
        bet = our_bets.get(tk)
        tag_html = ""
        if bet is not None:
            our_n += 1
            won = bet.status == "won"
            our_w += won
            tag_html = (tag("good", "✓", f"bet {bet.side} {bet.units or 1:.1f}u — won")
                        if won else tag("accent", "✕", f"bet {bet.side} {bet.units or 1:.1f}u — lost")
                        if bet.status == "lost" else tag("neutral", "·", f"bet {bet.side}"))
        elif tk in advised:
            tag_html = tag("outline", "▲", "advised")
        cards.append(f"""<div class="card">
<div style="display:flex;justify-content:space-between;align-items:center">
<span class="kicker" style="margin:0">{tour}</span><span>{tag_html}</span></div>
<a href="/match/{esc(ev)}" style="color:inherit;text-decoration:none">
<div class="pname" style="font-size:15px">{esc(matchup)}</div></a>
<div class="sub2">won by <strong>{esc(winner) or '—'}</strong>{score}</div>
<div class="sub2 mono">settled {pt(settled_at)}</div></div>""")

    prev_d = (day - timedelta(days=1)).isoformat()
    next_d = (day + timedelta(days=1)).isoformat()
    is_today = day >= datetime.now(timezone.utc).date()
    nav = (f'<div class="filterbar" style="margin-bottom:16px">'
           f'<a class="fchip" href="/history?d={prev_d}">← {prev_d}</a>'
           f'<span class="fchip on">{day.isoformat()}</span>'
           f'{"" if is_today else f"<a class=fchip href=/history?d={next_d}>{next_d} →</a>"}'
           f'<a class="fchip" href="/history">today</a></div>')
    strip = statstrip([
        ("Matches settled", str(len(settled)), "resolved this day"),
        ("Bot played", str(our_n), "of the day's slate"),
        ("Bot record", f"{our_w}-{our_n - our_w}" if our_n else "—", "that day"),
    ], cols=3)
    grid = (f'<section class="block"><div class="cards">{"".join(cards)}</div></section>'
            if cards else
            '<section class="block"><p class="prose">No matches settled on this '
            'day (or before the bot started recording).</p></section>')
    body = pagehead("Archive", "History",
                    "browse any day's settled slate and the bot's calls") \
        + nav + strip + grid
    return respond(request, "History", "history", body)


async def track(request: web.Request) -> web.Response:
    from bot.track import advisory_outcome, advisory_pnl_cents, clv_cents

    with db_session() as db:
        rows = db.execute(
            select(Advisory, Player.full_name, KalshiMarket.result)
            .join(Player, Player.id == Advisory.recommended_player_id, isouter=True)
            .join(KalshiMarket, KalshiMarket.ticker == Advisory.market_ticker,
                  isouter=True)
            .where(Advisory.status == "sent")
            .order_by(Advisory.created_at.desc()).limit(200)
        ).all()
        # full settled set (chronological) for the calibration + CLV charts
        chart_rows = db.execute(
            select(Advisory.model_prob, Advisory.fact_block,
                   Advisory.executable_price_cents, Advisory.created_at,
                   KalshiMarket.result, KalshiMarket.close_yes_cents)
            .join(KalshiMarket, KalshiMarket.ticker == Advisory.market_ticker)
            .where(Advisory.status == "sent", KalshiMarket.result.in_(("yes", "no")))
            .order_by(Advisory.created_at)).all()

    # calibration bands + cumulative-average CLV
    cal = {}  # band index → [sum_pred, wins, n]
    clv_series, clv_run, clv_k = [], [], 0
    clv_sum = 0
    for mp, fb, price, created, result, close in chart_rows:
        side = (fb or {}).get("side", "yes")
        o = advisory_outcome(side, result)
        if o not in ("won", "lost"):
            continue
        b = min(int(mp * 10), 9)
        d = cal.setdefault(b, [0.0, 0, 0])
        d[0] += mp; d[1] += (o == "won"); d[2] += 1
        c = clv_cents(side, price, close)
        if c is not None:
            clv_k += 1; clv_sum += c
            clv_run.append((created, clv_sum / clv_k))
    bands = [(d[0] / d[2], d[1] / d[2], d[2], 0) for b, d in sorted(cal.items())
             if d[2] >= 3]
    # downsample the CLV run to ~150 points for a light path
    if clv_run:
        step = max(1, len(clv_run) // 150)
        clv_series = clv_run[::step] + [clv_run[-1]]
    cal_chart = calibration_svg(bands)
    clv_chart = clv_line_svg(clv_series)
    model_perf = (f'<section class="block"><div class="blockhead">'
                  f'<h4>Model performance</h4><span class="aside">the edge question '
                  f'— {len(clv_run)} settled advisories</span></div><div class="rule"></div>'
                  f'<div class="vsgrid"><div class="vscol">'
                  f'<div class="vshead">Calibration<span>predicted vs actual</span></div>'
                  f'{cal_chart or "<p class=sub2>not enough settled advisories yet</p>"}</div>'
                  f'<div class="vscol"><div class="vshead">Closing-line value'
                  f'<span>cumulative avg</span></div>'
                  f'{clv_chart or "<p class=sub2>not enough closing lines yet</p>"}</div>'
                  f'</div></section>') if chart_rows else ""

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
                    f"{n} settled of {len(rows)} sent") + strip + model_perf + f"""
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


def calibration_svg(bands: list[tuple[float, float, float, int]]) -> str:
    """Reliability plot: model probability (x) vs actual win rate (y), with the
    y=x perfect-calibration diagonal. Each band is a dot sized by sample count;
    on the diagonal = calibrated, below = overconfident. bands: (mean_pred,
    actual, n, _) filtered to n>0. Single series → title carries identity."""
    pts = [(mp, ac, n) for mp, ac, n, *_ in bands if n]
    if not pts:
        return ""
    W = H = 300
    PL, PR, PT, PB = 40, 14, 14, 34
    def x(v): return PL + v * (W - PL - PR)
    def y(v): return PT + (1 - v) * (H - PT - PB)
    grid = "".join(
        f'<line x1="{x(g):.0f}" y1="{PT}" x2="{x(g):.0f}" y2="{H-PB}" '
        f'stroke="var(--divider)" stroke-width="1"/>'
        f'<line x1="{PL}" y1="{y(g):.0f}" x2="{W-PR}" y2="{y(g):.0f}" '
        f'stroke="var(--divider)" stroke-width="1"/>'
        f'<text x="{x(g):.0f}" y="{H-PB+14:.0f}" text-anchor="middle" font-size="9" '
        f'fill="var(--faint)">{int(g*100)}</text>'
        f'<text x="{PL-6:.0f}" y="{y(g)+3:.0f}" text-anchor="end" font-size="9" '
        f'fill="var(--faint)">{int(g*100)}</text>'
        for g in (0.0, 0.25, 0.5, 0.75, 1.0))
    diag = (f'<line x1="{x(0):.0f}" y1="{y(0):.0f}" x2="{x(1):.0f}" y2="{y(1):.0f}" '
            f'stroke="var(--muted)" stroke-width="1.5" stroke-dasharray="4 4"/>')
    nmax = max(n for *_, n in pts)
    dots = ""
    for mp, ac, n in pts:
        r = 4 + 7 * (n / nmax) ** 0.5
        col = "var(--good)" if abs(ac - mp) <= 0.05 else "var(--accent)"
        dots += (f'<g><title>predicted {mp:.0%} → actual {ac:.0%} (n={n})</title>'
                 f'<circle cx="{x(mp):.1f}" cy="{y(ac):.1f}" r="{r:.1f}" '
                 f'fill="{col}" fill-opacity="0.8" stroke="var(--surface)" '
                 f'stroke-width="2"/></g>')
    return (f'<div class="tw"><svg viewBox="0 0 {W} {H}" role="img" '
            f'aria-label="calibration: model probability vs actual win rate" '
            f'style="width:100%;max-width:340px;display:block">{grid}{diag}{dots}'
            f'<text x="{(PL+W-PR)/2:.0f}" y="{H-2}" text-anchor="middle" font-size="10" '
            f'fill="var(--muted)">model probability →</text></svg></div>'
            f'<p class="sub2" style="margin-top:4px">dashed line = perfect calibration; '
            f'dots below it = overconfident. Green = within 5 points.</p>')


def clv_line_svg(points: list[tuple[datetime, float]]) -> str:
    """Cumulative-average CLV (¢) over settled bets — converges to the true edge
    vs the closing line. Zero line = break-even; above = beating the close."""
    if len(points) < 2:
        return ""
    W, H, PL, PR, PT, PB = 620, 170, 42, 12, 12, 24
    t0 = points[0][0].timestamp()
    t1 = points[-1][0].timestamp()
    vals = [v for _, v in points]
    lo, hi = min(min(vals), 0), max(max(vals), 0)
    if hi == lo:
        hi = lo + 1
    def x(ts): return PL + (ts - t0) / max(t1 - t0, 1) * (W - PL - PR)
    def y(v): return PT + (hi - v) / (hi - lo) * (H - PT - PB)
    path = "M" + " L".join(f"{x(p[0].timestamp()):.1f},{y(p[1]):.1f}" for p in points)
    end = points[-1][1]
    col = "var(--good)" if end > 0 else "var(--accent)" if end < 0 else "var(--muted)"
    zero = (f'<line x1="{PL}" y1="{y(0):.0f}" x2="{W-PR}" y2="{y(0):.0f}" '
            f'stroke="var(--divider-strong)" stroke-width="1.5"/>'
            f'<text x="{PL-6}" y="{y(0)+3:.0f}" text-anchor="end" font-size="9" '
            f'fill="var(--faint)">0¢</text>')
    ends = (f'<text x="{PL-6}" y="{y(hi)+3:.0f}" text-anchor="end" font-size="9" '
            f'fill="var(--faint)">{hi:+.0f}</text>'
            f'<text x="{PL-6}" y="{y(lo)+3:.0f}" text-anchor="end" font-size="9" '
            f'fill="var(--faint)">{lo:+.0f}</text>')
    dot = (f'<circle cx="{x(t1):.1f}" cy="{y(end):.1f}" r="4" fill="{col}"/>'
           f'<text x="{x(t1)-6:.0f}" y="{y(end)-7:.0f}" text-anchor="end" '
           f'font-size="11" font-weight="800" fill="{col}">{end:+.1f}¢</text>')
    return (f'<div class="tw"><svg viewBox="0 0 {W} {H}" role="img" '
            f'aria-label="cumulative average CLV over time" '
            f'style="width:100%;min-width:480px;display:block">{zero}{ends}'
            f'<path d="{path}" fill="none" stroke="{col}" stroke-width="2" '
            f'stroke-linejoin="round"/>{dot}</svg></div>'
            f'<p class="sub2" style="margin-top:4px">above 0 = entries beat the '
            f'closing line (a real edge signal); below = adverse selection.</p>')


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

# live-board filter chips: tours (multi-select) + two toggles. Kept in sync with
# series_label so the data-tour attribute matches a chip exactly.
_TOUR_CHIPS = ["ATP", "WTA", "CHALLENGER", "ITF M", "ITF W"]
_TRIG_RANK = {"hit": 0, "near": 1, "armed": 2, "none": 3}  # most-actionable first
# a decider trigger is only an ACTIONABLE play if the decider read favors the
# watch pick — otherwise reaching 1-1 is just a coin flip, not a signal
TRIG_FAVOR = 0.55


def _trig_class(est, is_live: bool) -> str:
    """How close a live match is to the v1 decider trigger (1-1 in Bo3)."""
    s = est.state if est else None
    if s == "1-1":
        return "hit"
    if s in ("1-0", "0-1", "2-1", "1-2"):  # one set from a decider
        return "near"
    return "armed" if is_live else "none"


def _raw_cents(raw: dict, key: str):
    try:
        return round(float(raw.get(key)) * 100)
    except (TypeError, ValueError):
        return None


def _odds_cents(m, quotes: dict) -> tuple[int | None, bool]:
    """Best available Kalshi price (cents) for a market's YES side, and whether
    it is a fresh streamed quote. Falls back to the discovery snapshot (raw
    bid/ask, then last trade) so live odds always render, even between ticks."""
    q = quotes.get(m.ticker)
    if q and q[0] is not None and q[1] is not None:
        return round((q[0] + q[1]) / 2), True
    raw = m.raw or {}
    yb, ya = _raw_cents(raw, "yes_bid_dollars"), _raw_cents(raw, "yes_ask_dollars")
    if yb is not None and ya is not None:
        return round((yb + ya) / 2), False
    lp = _raw_cents(raw, "last_price_dollars")
    return (lp, False) if lp is not None else (None, False)


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
        # fresh score activity = the strongest 'actually live' signal (the bot
        # scores live matches every ~25s), so a game recorded this recently means
        # play is under way; no recent scoring past the start grace ⇒ finished.
        from sqlalchemy import text as _sqltext
        # latest score row per match: (ts, total_games, is_final). games>0 ⇒ play
        # has actually started (a 0-0 poll doesn't count); is_final ⇒ over.
        recent = {r[0]: (r[1], r[2], r[3]) for r in db.execute(_sqltext(
            "SELECT DISTINCT ON (market_ticker) market_ticker, ts, total_games, is_final "
            "FROM match_score_log WHERE ts > now() - interval '18 hours' "
            "ORDER BY market_ticker, ts DESC")).all()}
        FRESH_LIVE = timedelta(minutes=90)

        # LIVE requires positive evidence of play — an explicit live status, or a
        # fresh score with games actually played. Scheduled time alone is NOT
        # enough (a match 50m past its slot at 0-0 hasn't started). A match is
        # DONE on: settlement, discovery-gone, a terminal status word, or a
        # final scoreline.
        ENDED = {"finished", "complete", "ended", "closed", "cancelled", "canceled",
                 "walkover", "wov", "abandoned", "retired", "postponed"}
        LIVE = {"live", "inprogress", "in_progress", "in progress", "interrupted"}

        def _rec(ev):  # (latest ts, total_games, is_final) across the event's sides
            best = None
            for m in ev["sides"]:
                r = recent.get(m.ticker)
                if r and (best is None or r[0] > best[0]):
                    best = r
            return best

        live_evs, soon_evs, done_evs = [], [], []
        for ev_ticker, ev in events.items():
            occ = ev["occ"]
            settled = any(m.result for m in ev["sides"])
            last_seen = max((m.last_seen_at for m in ev["sides"]
                             if m.last_seen_at), default=None)
            gone = discovery_alive and last_seen is not None and last_seen < seen_cutoff
            status = (next(((m.raw or {}).get("_live_status") for m in ev["sides"]
                            if (m.raw or {}).get("_live_status")), "") or "").lower()
            r = _rec(ev)
            is_final = bool(r and r[2])
            if settled or gone or status in ENDED or is_final:
                if now - occ <= timedelta(hours=18):
                    done_evs.append((ev_ticker, ev))
                continue
            games = r[1] if r else 0
            fresh = bool(r and now - r[0] <= FRESH_LIVE)
            playing = games > 0 and fresh
            ev["started"] = games > 0
            if status in LIVE or playing:
                live_evs.append((ev_ticker, ev))
            elif not playing and (now - timedelta(hours=2) <= occ <= now + UPCOMING_HORIZON):
                # scheduled or awaiting start (tennis runs late) — shown as
                # upcoming, never falsely "live"
                soon_evs.append((ev_ticker, ev))
        live_evs.sort(key=lambda e: e[1]["occ"])
        soon_evs.sort(key=lambda e: e[1]["occ"])
        done_evs.sort(key=lambda e: e[1]["occ"], reverse=True)

        all_tickers = [m.ticker for _, ev in live_evs for m in ev["sides"]]
        # quotes for upcoming games too, so their live Kalshi odds also render
        quote_tickers = all_tickers + [m.ticker for _, ev in soon_evs
                                       for m in ev["sides"]]
        quotes = _latest_quotes(db, quote_tickers)
        states = {s.market_ticker: s for s in db.execute(
            select(LiveMatchState).where(
                LiveMatchState.market_ticker.in_(all_tickers))).scalars().all()} \
            if all_tickers else {}
        advised = set(db.execute(
            select(Advisory.market_ticker).where(
                Advisory.market_ticker.in_(all_tickers),
                Advisory.status.in_(["sent", "pending"]))).scalars().all()) \
            if all_tickers else set()

        # re-order the live board: closest-to-trigger first (the actionable ones
        # float to the top on a busy slate), then by scheduled time
        def _ev_est(ev):
            return next((states.get(m.ticker) for m in ev["sides"]
                         if states.get(m.ticker)), None)
        live_evs.sort(key=lambda e: (_TRIG_RANK[_trig_class(_ev_est(e[1]), True)],
                                     e[1]["occ"]))
        from sqlalchemy import text as sqltext

        scorelines = {}
        if all_tickers:
            for r in db.execute(sqltext("""
                SELECT DISTINCT ON (market_ticker) market_ticker, scoreline, sets_a, sets_b
                FROM match_score_log WHERE market_ticker = ANY(:t)
                ORDER BY market_ticker, ts DESC"""), {"t": all_tickers}).all():
                scorelines[r[0]] = (r[1], r[2], r[3])
        # player nationality (for the Kalshi-style flag) on the live sides
        ioc_by_pid = {}
        live_pids = [m.player_a_id for _, ev in live_evs for m in ev["sides"]
                     if m.player_a_id]
        if live_pids:
            for pid, ioc in db.execute(select(Player.id, Player.ioc).where(
                    Player.id.in_(live_pids))).all():
                ioc_by_pid[pid] = ioc
        from bot.models import Scenario

        plans = {}
        plan_ev_tickers = [t for t, _ in live_evs] + [t for t, _ in soon_evs]
        if plan_ev_tickers:
            for sc in db.execute(select(Scenario).where(
                    Scenario.event_ticker.in_(plan_ev_tickers))
                    .order_by(Scenario.created_for)).scalars():
                plans[sc.event_ticker] = sc

        # a scenario whose trigger has FIRED (live match reached the decider) is
        # the single most actionable card — float those to the very top
        def _scen_fired(ev_ticker, ev):
            sc = plans.get(ev_ticker)
            e = _ev_est(ev)
            if sc is None or e is None or e.stale:
                return False
            bo = int((ev["sides"][0].raw or {}).get("_best_of", 3) or 3)
            return (e.state == f"{bo // 2}-{bo // 2}"
                    and sc.model_prob_at_state >= TRIG_FAVOR)
        live_evs.sort(key=lambda e: (
            0 if _scen_fired(e[0], e[1]) else 1,
            _TRIG_RANK[_trig_class(_ev_est(e[1]), True)], e[1]["occ"]))

    series_label = {"KXATPMATCH": "ATP", "KXWTAMATCH": "WTA", "KXWTAGAME": "WTA",
                    "KXATPCHALLENGERMATCH": "CHALLENGER", "KXITFMATCH": "ITF M",
                    "KXITFWMATCH": "ITF W"}

    def match_card(ev_ticker: str, ev: dict, is_live: bool) -> str:
        sides = sorted(ev["sides"], key=lambda m: m.ticker)
        est = next((states.get(m.ticker) for m in sides if states.get(m.ticker)), None)
        rows_html = []
        prices = [_odds_cents(m, quotes)[0] for m in sides[:2]]
        favc = max((p for p in prices if p is not None), default=None)
        for m in sides[:2]:
            name = (m.raw or {}).get("yes_sub_title") or m.ticker.rsplit("-", 1)[-1]
            cents, live_q = _odds_cents(m, quotes)
            if cents is None:
                px = '<span class="px mono">—</span>'
            else:
                dot = ('<span style="color:var(--good)" title="live quote">●</span> '
                       if live_q else "")
                style = 'color:var(--text);font-weight:800' if cents == favc \
                    else 'color:var(--muted)'
                tip = "Kalshi price = implied win %" + ("" if live_q else " (last snapshot)")
                px = (f'<span class="px mono" style="{style}" title="{tip}">'
                      f'{dot}{cents}¢</span>')
            rows_html.append(f'<div class="playerrow"><span class="nm">{esc(name)}</span>'
                             f'{px}</div>')
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
        play = tag("outline", "▲", "advisory") if any(m.ticker in advised for m in sides) else ""
        score_row = ""
        # Kalshi-style live scoreline for live matches (per-set columns + flags),
        # a-perspective of whichever side has a recorded scoreline
        sm = next((m for m in sides if scorelines.get(m.ticker)), None)
        if is_live and sm is not None and scorelines[sm.ticker][0]:
            other = next((m for m in sides if m.ticker != sm.ticker), None)
            a_nm = (sm.raw or {}).get("yes_sub_title") or "Player A"
            b_nm = (other.raw or {}).get("yes_sub_title") if other else "Player B"
            score_row = score_grid(scorelines[sm.ticker], a_nm, b_nm,
                                   ioc_by_pid.get(sm.player_a_id),
                                   ioc_by_pid.get(other.player_a_id) if other else None)
        elif sm is not None and scorelines[sm.ticker][0]:
            sl = scorelines[sm.ticker]
            score_row = (f'<div class="mono" style="font-size:15px;font-weight:800">'
                         f'{esc(sl[0])} <span class="sub2">· {sl[1]}-{sl[2]} sets</span></div>')
        # a scenario = this match is on the bot's watchlist (the /scenarios page);
        # its trigger FIRES when the live match reaches the decider state
        sc = plans.get(ev_ticker)
        scen_badge = tag("accent", "◆", "scenario") if sc is not None else ""
        plan_row = banner = ""
        trig_fired = False
        if sc is not None:
            wm = next((m for m in sides if m.ticker == sc.market_ticker), None)
            wname = (((wm.raw or {}).get("yes_sub_title") if wm else "") or "the pick").split()[-1]
            wmid = _odds_cents(wm, quotes)[0] if wm else None
            bo = int((sides[0].raw or {}).get("_best_of", 3) or 3)
            decider_state = f"{bo // 2}-{bo // 2}"
            # only an actionable trigger if the decider read favors the pick —
            # not merely because the match reached the decider
            trig_fired = (est is not None and not est.stale
                          and est.state == decider_state
                          and sc.model_prob_at_state >= TRIG_FAVOR)
            if trig_fired:
                banner = (f'<div class="trig-banner">◎ SCENARIO TRIGGERED · '
                          f'{esc(wname)} in the decider at '
                          f'{sc.model_prob_at_state:.0%} — the read is live</div>')
            plan_row = (
                f'<div class="planrow"><span class="scen-flag">◆ PLAY</span> '
                f'<strong>{esc(wname)}</strong> '
                f'{trigger_html(sc, est.state if est else None, is_live, wmid)} '
                f'<a href="/scenario/{sc.id}" class="sub2">full scenario →</a></div>')
        tour = series_label.get(ev["series"], "?")
        has_play = any(m.ticker in advised for m in sides)
        trig = _trig_class(est, is_live)
        # 'livecard' class only on the live section — the filter bar counts/filters
        # those, not the "starting soon" cards
        klass = ("card livecard" if is_live else "card") + (" trig-live" if trig_fired else "")
        # timing label: live matches with a future scheduled time started early
        if is_live:
            when = f"started {pt(ev['occ'])}" if ev["occ"] <= now else "live now (early start)"
        elif ev["occ"] <= now:
            when = f"awaiting start · scheduled {pt(ev['occ'])}"
        else:
            when = f"starts {pt(ev['occ'])}"
        return f"""<div class="{klass}" \
data-tour="{esc(tour)}" data-play="{1 if has_play else 0}" \
data-scenario="{1 if sc is not None else 0}" \
data-trigfired="{1 if trig_fired else 0}" data-trig="{trig}">
{banner}
<div style="display:flex;align-items:center;justify-content:space-between">
<span class="kicker" style="margin:0">{tour}</span>
<span>{st} {scen_badge} {play}</span></div>
<a href="/match/{esc(ev_ticker)}" style="text-decoration:none;color:inherit">
<div>{''.join(rows_html)}</div></a>
{score_row}
{plan_row}
<div class="sub2 mono">{when}
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

    # filter bar — only offer tour chips for tours actually on the board
    present = [series_label.get(e[1]["series"], "?") for e in live_evs]
    tour_chips = "".join(
        f'<button class="fchip" data-f="{esc(t)}">{esc(t)}</button>'
        for t in _TOUR_CHIPS if t in present)
    near_n = sum(1 for _, e in live_evs
                 if _trig_class(next((states.get(m.ticker) for m in e["sides"]
                                      if states.get(m.ticker)), None), True)
                 in ("hit", "near"))
    scen_n = sum(1 for t, _ in live_evs if t in plans)
    fired_n = sum(1 for t, e in live_evs if _scen_fired(t, e))
    filter_bar = (f'<div class="filterbar">{tour_chips}'
                  f'<button class="fchip" data-f="trigfired">◎ triggered now ({fired_n})</button>'
                  f'<button class="fchip" data-f="scenario">◆ scenario ({scen_n})</button>'
                  f'<button class="fchip" data-f="play">▲ advisory fired</button>'
                  f'<button class="fchip" data-f="trig">near trigger ({near_n})</button>'
                  f'<span class="sub2" style="margin-left:auto">showing '
                  f'<span id="livecount">{len(live_evs)}</span> of {len(live_evs)}</span>'
                  f'</div>') if live_evs else ""
    body = pagehead("Match Board", "Live Now",
                    f"{len(live_evs)} live · {len(soon_evs)} next 12h") + f"""
<section class="block">{filter_bar}
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
fired. Each player's number is the <strong>live Kalshi price</strong> (cents =
implied win %); a green ● marks a fresh streamed quote, otherwise it's the last
snapshot. Set states come from the estimator (≈ inferred from odds movement,
✓ confirmed by the delayed score). Matches leave the board as soon as their
market settles or closes on Kalshi.</p>"""
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
                          "title": f"paper bet {bet.units:.1f}u @ {bet.price_cents}¢ "
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
    # --- Model read vs raw form: makes SOS-driven disagreements legible ---
    # (raw win rate flatters players who beat weak fields; the model weights
    # opponent quality, so a lower win rate against a stronger field can still
    # be the better player — show both side by side).
    def _mr_cell(pl, prof):
        f, sch = prof.form, prof.schedule
        car = f.win_rate_career
        wr = f"{car.value:.0%} <span class='sub2'>({car.wins}-{car.losses})</span>" \
            if car and car.value is not None else "—"
        r365 = f.win_rate_365
        recent = f"{r365.value:.0%}" if r365 and r365.value is not None else "—"
        if sch and sch.avg_opp_rank:
            field = (f'<span class="tag tag-'
                     f'{"good" if sch.field in ("elite","strong") else "warn" if sch.field=="weak" else "neutral"}">'
                     f'{esc(sch.field)}</span> avg opp rank ~{int(sch.avg_opp_rank)}')
        else:
            field = '<span class="sub2">field strength unknown</span>'
        return (f'<div class="vscol"><div class="vshead">{esc(pl.full_name)}'
                f'{" · lefty" if pl.hand=="L" else ""}</div>'
                f'<div class="vsrow"><span class="k">career win rate</span><span class="v mono">{wr}</span></div>'
                f'<div class="vsrow"><span class="k">last 12 months</span><span class="v mono">{recent}</span></div>'
                f'<div class="vsrow"><span class="k">strength of schedule</span>'
                f'<span class="v" style="font-size:13px">{field}</span></div></div>')
    model_line = ""
    if sc is not None:
        wname = esc((sc.facts or {}).get("match", "").split(" vs ")[0] or "the pick")
        fav = pa.full_name if sc.player_id == pa.id else pb.full_name
        model_line = (f'<p class="prose" style="margin-top:6px">The model favours '
                      f'<strong>{esc(fav)}</strong> at <strong>{sc.prematch_prob:.0%}</strong>. '
                      f"When that disagrees with raw win rate, it's weighting "
                      f"<em>who</em> each player beat: a lower win rate against a "
                      f"stronger field can still be the better player.</p>")
    model_read_html = (f'<section class="block"><div class="blockhead">'
                       f'<h4>Model read vs raw form</h4>'
                       f'<span class="aside">why the pick can differ from win rate</span></div>'
                       f'<div class="rule"></div>{model_line}'
                       f'<div class="vsgrid">{_mr_cell(pa, prof_a)}{_mr_cell(pb, prof_b)}</div></section>')

    body = pagehead("Match", (a.title or "").split(":")[0].replace("Will ", "")
                    or event_ticker,
                    f"{px(a)} / {px(b)}{state_txt}") + f"""
<div class="cards" style="grid-template-columns:repeat(auto-fit,minmax(340px,1fr));margin-bottom:26px">
{profile_col(pa, prof_a, sr_a)}
{profile_col(pb, prof_b, sr_b)}
</div>
{model_read_html}
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
             "text": f"{p or b.event_ticker} · {b.units:.1f}u @ {b.price_cents}¢ · "
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
    app.router.add_get("/scenario/{sid:\\d+}", scenario_detail)
    app.router.add_get("/testrun", testrun)
    app.router.add_get("/testrun/history", testrun_history)
    app.router.add_get("/testrun/{bot}", testrun)
    app.router.add_get("/history", history)
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
