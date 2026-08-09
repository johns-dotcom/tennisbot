"""Web UI: python -m bot web — the advisory delivery surface (no Discord).

Visual language: "Deuce Terminal" (claude.ai/design project 57f12b3b — Modernist
system: Archivo 800 headings, uppercase kickers, square corners, 2px dividers,
red accent) re-tuned for a permanent dark scheme. The mockup's trading controls
(stakes/approvals/bankroll) are deliberately NOT implemented — CLAUDE.md rule 1:
advisory only, never any execution surface.

Storage is UTC; display is US/Pacific. Optional WEB_TOKEN gate.
"""
from __future__ import annotations

import base64
import html
import os
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from aiohttp import web
from sqlalchemy import func, select

from bot import webauth
from bot.db import session as db_session
from bot.log import get_logger
from bot.market.live_status import status_kind
from bot.models import (
    Advisory,
    FeedGap,
    KalshiMarket,
    LiveMatchState,
    MatchReviewQueue,
    Player,
    StateInferenceLog,
    UserBet,
    UserPin,
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
html { -webkit-text-size-adjust: 100%; text-size-adjust: 100%; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 400 15px/1.55 var(--font); -webkit-font-smoothing: antialiased;
  overflow-x: clip; }
img, svg, canvas, video { max-width: 100%; }
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
  /* compact header: brand left, live status right, nav on its own scrolling row
     below a full-width divider — no cramped clutter, no odd group borders */
  header.nav { padding: 10px 14px 0; gap: 6px 12px; }
  .brand { font-size: 16px; margin-right: auto; }
  .brand .tag { display: none; }
  .conn { font-size: 10px; }
  #bell { padding: 3px 9px; }
  nav.links { order: 3; width: 100%; flex-wrap: nowrap; overflow-x: auto;
    -webkit-overflow-scrolling: touch; scrollbar-width: none;
    margin: 9px -14px 0; padding: 9px 14px; border-top: 1px solid var(--divider); }
  nav.links::-webkit-scrollbar { display: none; }
  .navgroup { padding: 0; }
  .navgroup + .navgroup { border-left: none; margin-left: 18px; }
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
/* live-board player/tournament search — top of the board, searches every card */
.livesearch { display: flex; align-items: center; gap: 10px;
  margin: 2px 0 20px; }
.livesearch-box { position: relative; display: flex; align-items: center;
  flex: 1; max-width: 460px; }
.livesearch-ic { position: absolute; left: 13px; font-size: 14px; opacity: .55;
  pointer-events: none; }
.livesearch-box input { width: 100%; box-sizing: border-box; font: inherit;
  font-size: 15px; color: var(--text); background: var(--surface);
  border: 1px solid var(--divider); border-radius: 999px;
  padding: 11px 36px 11px 38px; transition: border-color .12s ease; }
.livesearch-box input::placeholder { color: var(--muted); }
.livesearch-box input:focus { outline: none; border-color: var(--accent); }
.livesearch-box input::-webkit-search-cancel-button { -webkit-appearance: none; }
.livesearch-box #livesearch-x { position: absolute; right: 9px; visibility: hidden;
  background: none; border: none; color: var(--muted); cursor: pointer;
  font-size: 14px; padding: 4px 6px; line-height: 1; border-radius: 999px; }
.livesearch-box #livesearch-x:hover { color: var(--text); }
.livesearch-ct { font-size: 12.5px; color: var(--muted); white-space: nowrap; }
.searchempty { margin: -6px 0 18px; font-size: 13.5px; color: var(--muted);
  line-height: 1.5; max-width: 620px; }
.fchip { background: var(--surface); border: 1px solid var(--divider);
  color: var(--muted); font: inherit; font-size: 12px; padding: 5px 12px;
  border-radius: 999px; cursor: pointer; letter-spacing: .02em;
  transition: color .12s ease, border-color .12s ease, background .12s ease; }
.fchip:hover { color: var(--text); border-color: var(--divider-strong); }
.fchip.on { border-color: var(--accent); color: var(--text);
  background: rgba(255,86,60,.1); }
/* per-tag colour swatches on My Bets */
.swrow { display: inline-flex; gap: 5px; flex-wrap: wrap; margin: 0; }
.sw { width: 17px; height: 17px; border-radius: 5px; padding: 0; cursor: pointer;
  border: 1px solid var(--divider-strong); font-size: 9px; line-height: 15px;
  color: var(--muted); }
.sw:hover { transform: scale(1.12); }
.sw.on { outline: 2px solid var(--text); outline-offset: 1px; }
.planrow { margin-top: 6px; font-size: 12.5px; display: flex; flex-wrap: wrap;
  align-items: center; gap: 7px; }
.trig-live { border-color: var(--warning); box-shadow: 0 0 0 1px var(--warning); }
.trig-banner { background: var(--warning); color: #0a0a0a; font-weight: 800;
  font-size: 11px; letter-spacing: .04em; padding: 6px 10px; border-radius: 5px;
  margin: -2px 0 8px; }
.scen-flag { background: var(--accent); color: #0a0a0a; font-weight: 800;
  font-size: 10px; letter-spacing: .08em; padding: 2px 7px; border-radius: 4px; }
.cmeter { display: inline-flex; align-items: center; gap: 8px; }
.readlist { list-style: none; margin: 0; padding: 0; }
.readlist li { position: relative; padding: 7px 0 7px 18px; font-size: 13.5px;
  line-height: 1.55; color: rgba(243,242,242,.82);
  border-top: 1px solid var(--divider); }
.readlist li:first-child { border-top: none; }
.readlist li::before { content: "›"; position: absolute; left: 2px;
  color: var(--accent); font-weight: 800; }
#refreshdot { width: 6px; height: 6px; border-radius: 50%; background: var(--divider);
  display: inline-block; margin-left: 8px; transition: background .2s ease; }
#refreshdot.on { background: var(--good); }
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
/* ---- phones: a real mobile layout, not a squeezed desktop ---- */
@media (max-width: 640px) {
  h2 { font-size: 22px; }
  main { padding: 14px 12px 46px; }
  /* comfortable tap targets */
  .fchip { padding: 8px 13px; font-size: 12.5px; }
  nav.links a { padding: 8px 0; font-size: 12.5px; }
  .pin, .fav { font-size: 19px; padding: 3px; }
  .betbtn { padding: 6px 12px; }
  /* the filter bar scrolls sideways instead of stacking into many rows */
  .filterbar { flex-wrap: nowrap; overflow-x: auto; -webkit-overflow-scrolling: touch;
    scrollbar-width: none; padding-bottom: 2px; }
  .filterbar::-webkit-scrollbar { display: none; }
  .filterbar .fchip { flex: 0 0 auto; }
  .metric-grid { grid-template-columns: repeat(2, 1fr) !important; }
  .strow { gap: 6px; }
  /* scenario page: the signal/trigger rail goes full-width, not a sticky sidebar */
  .scen-cols { flex-direction: column !important; }
  .scen-rail { position: static !important; max-width: none !important; width: 100%; }
  /* ---- wide financial tables become stacked cards (no sideways scroll) ---- */
  table.t.rt { min-width: 0 !important; }
  table.rt thead { display: none; }
  table.rt, table.rt tbody { display: block; width: 100%; }
  table.rt tr { display: flex; flex-direction: column; gap: 2px;
    background: var(--surface); border: 1px solid var(--divider);
    border-radius: 9px; padding: 11px 13px; margin-bottom: 10px; }
  table.rt td { display: flex; justify-content: space-between; align-items: baseline;
    gap: 14px; padding: 4px 0; border: none; text-align: right; white-space: normal; }
  table.rt td::before { content: attr(data-label); color: var(--muted);
    font-size: 10.5px; letter-spacing: .05em; text-transform: uppercase;
    font-weight: 700; text-align: left; white-space: nowrap; }
  table.rt td.pick { order: -1; justify-content: flex-start; font-weight: 800;
    font-size: 15.5px; border-bottom: 1px solid var(--divider);
    padding-bottom: 7px; margin-bottom: 3px; }
  table.rt td.pick::before { content: none; }
  table.rt td:empty { display: none; }
  /* 16px inputs stop iOS from zooming in when a field is focused */
  input, select, textarea { font-size: 16px; }
  /* profile cards: collapse the deep sections behind a toggle to cut scrolling */
  .prof-toggle { display: block; width: 100%; margin-top: 12px; padding: 10px;
    background: var(--surface-2); border: 1px solid var(--divider); border-radius: 8px;
    color: var(--muted); font: inherit; font-size: 12px; font-weight: 700;
    letter-spacing: .03em; cursor: pointer; }
  .prof-more { display: none; }
  .prof-more.show { display: block; }
}
.prof-toggle { display: none; }  /* desktop shows everything */
/* "how to read these stats" legend/expander above the profile cards */
.statlegend { margin: 0 0 18px; border: 1px solid var(--divider); border-radius: 10px;
  background: var(--surface); overflow: hidden; }
.statlegend > summary { cursor: pointer; padding: 12px 16px; font-size: 13px;
  font-weight: 700; color: var(--muted); list-style: none; user-select: none; }
.statlegend > summary::-webkit-details-marker { display: none; }
.statlegend[open] > summary { border-bottom: 1px solid var(--divider); color: var(--text); }
.statlegend-body { padding: 14px 16px; font-size: 12.5px; color: var(--muted); line-height: 1.6; }
.statlegend-body p { margin: 0 0 12px; }
.leg-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px 26px; }
.leg-grid > div { display: flex; flex-direction: column; gap: 2px; }
.leg-grid b { color: var(--text); font-size: 10.5px; text-transform: uppercase;
  letter-spacing: .06em; }
@media (max-width: 640px) { .leg-grid { grid-template-columns: 1fr; } }
/* ---- bottom tab bar: thumb-zone primary nav on mobile ---- */
.tabbar { display: none; }
@media (max-width: 760px) {
  .tabbar { display: flex; position: fixed; left: 0; right: 0; bottom: 0; z-index: 30;
    background: rgba(19,18,17,.94); backdrop-filter: saturate(140%) blur(12px);
    border-top: 1px solid var(--divider-strong);
    padding: 6px 4px calc(6px + env(safe-area-inset-bottom, 0px)); }
  .tabbar a { flex: 1; display: flex; flex-direction: column; align-items: center;
    gap: 2px; padding: 5px 0; color: var(--muted); text-decoration: none;
    font-size: 9.5px; letter-spacing: .05em; text-transform: uppercase;
    font-weight: 700; -webkit-tap-highlight-color: transparent; }
  .tabbar a .ic { font-size: 17px; line-height: 1; }
  .tabbar a.on { color: var(--text); }
  .tabbar a.on .ic { color: var(--accent); }
  /* keep page content clear of the fixed bar */
  main { padding-bottom: calc(74px + env(safe-area-inset-bottom, 0px)); }
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
/* major sections (live board) get a strong rail + breathing room + an accent
   tab on the header, so each section reads as a distinct band */
section.block.major { margin: 0 0 40px; padding-top: 28px;
  border-top: 2px solid var(--divider-strong); }
section.block.major > .blockhead { margin-bottom: 10px; }
section.block.major > .blockhead h4 { display: inline-flex; align-items: center;
  font-size: 15px; text-transform: uppercase; letter-spacing: .11em; }
section.block.major > .blockhead h4::before { content: ""; width: 4px; height: 15px;
  background: var(--accent); border-radius: 2px; margin-right: 10px; }

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
/* leaderboard: grouped-header separators so Hold / 90¢ / Signal read as blocks */
table.t th.grp { text-align: center; color: var(--text); font-weight: 700;
  font-size: 10px; letter-spacing: .1em; padding-bottom: 4px;
  border-bottom: 1px solid var(--divider); }
table.t .gsep { border-left: 1px solid var(--divider-strong); }
table.t th.gsep, table.t td.gsep { padding-left: 12px; }
/* numeric leaderboard: right-align so digits line up; keep bot name left */
table.t.lb th, table.t.lb td { text-align: right; }
table.t.lb th:first-child, table.t.lb td:first-child { text-align: left; }
table.t.lb th.grp { text-align: center; }
table.t.lb td { padding: 6px 8px; }
table.t.lb td .sub2 { font-size: 10.5px; }

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
.stcap { font-size: 10px; letter-spacing: .08em; text-transform: uppercase;
  font-weight: 700; color: var(--muted); margin: 13px 0 5px; }
.strow { display: flex; flex-wrap: wrap; gap: 5px; }
.st { background: var(--surface-2); border: 1px solid var(--divider);
  border-radius: var(--radius-sm); padding: 5px 9px; min-width: 46px; line-height: 1.2; }
.st .sv { font-weight: 800; font-size: 14.5px; font-variant-numeric: tabular-nums;
  font-feature-settings: "tnum"; }
.st .sl { font-size: 9px; letter-spacing: .05em; text-transform: uppercase;
  color: var(--muted); margin-top: 2px; white-space: nowrap; }
.st .ss { font-size: 9.5px; color: var(--muted); opacity: .8; margin-top: 3px;
  white-space: nowrap; }
.st.hot .sv { color: var(--warning); }
.st.on { border-color: var(--accent); }
.st.on .sl { color: var(--accent); }
.flags { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 6px; }
.flags.r { justify-content: flex-end; }
.flag { display: inline-flex; align-items: center; gap: 4px; font-size: 11px;
  font-weight: 700; padding: 2px 8px; border-radius: 999px; white-space: nowrap;
  background: var(--surface-2); border: 1px solid var(--divider); color: var(--muted); }
.flag.hot { color: var(--warning);
  border-color: color-mix(in srgb, var(--warning) 45%, var(--divider)); }
.flag.good { color: var(--good);
  border-color: color-mix(in srgb, var(--good) 45%, var(--divider)); }
.flag.streak { color: var(--good);
  border-color: color-mix(in srgb, var(--good) 45%, var(--divider)); }
.flag.bad { color: var(--accent);
  border-color: color-mix(in srgb, var(--accent) 45%, var(--divider)); }
/* decision-first verdict headline on a live card */
.verdict { margin: 4px 0 8px; letter-spacing: -.01em; }
.verdict.bet { font-size: 15px; font-weight: 800; color: var(--good);
  background: color-mix(in srgb, var(--good) 12%, transparent);
  border-left: 3px solid var(--good); border-radius: var(--radius-sm);
  padding: 7px 11px; }
.verdict.none { font-size: 12px; font-weight: 600; color: var(--faint);
  padding: 2px 0 4px; }
/* incremental-update flash: a price that moved since the last refresh */
@keyframes flashup   { 0% { background: rgba(74,222,128,.32); } 100% { background: transparent; } }
@keyframes flashdown { 0% { background: rgba(248,113,113,.32); } 100% { background: transparent; } }
.flash-up   { animation: flashup 1.2s ease-out; border-radius: 4px; }
.flash-down { animation: flashdown 1.2s ease-out; border-radius: 4px; }
.playerrow { display: grid; grid-template-columns: 1fr auto; gap: 12px;
  align-items: center; padding: 8px 0; border-bottom: 1px solid var(--divider); }
.playerrow .nm { font-weight: 800; font-size: 15px; }
.playerrow .px { font-weight: 800; font-size: 16px; min-width: 48px; text-align: right; }
.pin { cursor: pointer; user-select: none; font-size: 13px; line-height: 1;
  opacity: .3; filter: grayscale(1); transition: opacity .15s, filter .15s;
  margin-left: 4px; vertical-align: middle; }
.pin:hover { opacity: .65; }
.pin.on { opacity: 1; filter: none; }
.fav { cursor: pointer; user-select: none; font-size: 15px; line-height: 1;
  color: var(--muted); opacity: .5; transition: opacity .15s, color .15s;
  vertical-align: middle; }
.fav:hover { opacity: .85; }
.fav.on { opacity: 1; color: #f5c518; }
.betbtn { cursor: pointer; user-select: none; font-size: 11px; font-weight: 700;
  letter-spacing: .04em; color: var(--muted); border: 1px solid var(--divider-strong);
  border-radius: 5px; padding: 2px 7px; margin-left: 4px; white-space: nowrap; }
.betbtn:hover { color: var(--text); border-color: var(--accent); }
/* action bar carrying a bet button on the match / scenario pages */
.betbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin-bottom: 18px; }
.betbar .betbtn { font-size: 13px; padding: 7px 14px; margin-left: 0; }
.betbar .sub2 { margin: 0; }
/* bet modal */
.modal-back { position: fixed; inset: 0; background: rgba(0,0,0,.6); z-index: 90;
  display: flex; align-items: center; justify-content: center; padding: 20px; }
.modal { background: var(--surface); border: 1px solid var(--divider-strong);
  border-radius: 12px; padding: 22px; width: 100%; max-width: 400px; }
.modal h4 { font-size: 17px; margin: 0 0 4px; }
.modal .side-pick { display: flex; gap: 8px; margin: 14px 0; }
.modal .side-pick button { flex: 1; background: var(--surface-2);
  border: 1px solid var(--divider-strong); color: var(--text); font: inherit;
  font-weight: 700; padding: 10px 8px; border-radius: 8px; cursor: pointer; }
.modal .side-pick button.sel { border-color: var(--accent); color: var(--text);
  background: rgba(200,90,60,.12); }
.modal label { display: block; font-size: 12px; color: var(--muted);
  margin: 12px 0 4px; letter-spacing: .05em; }
.modal input { width: 100%; box-sizing: border-box; background: var(--surface-2);
  border: 1px solid var(--divider); color: var(--text); font: inherit;
  padding: 9px 11px; border-radius: 6px; }
.modal .mrow { display: flex; gap: 12px; }
.modal .mrow > div { flex: 1; }
.modal .cost { font-size: 13px; color: var(--muted); margin-top: 12px; min-height: 18px; }
.modal .mbtns { display: flex; gap: 10px; margin-top: 18px; }
.modal .mbtns button { flex: 1; font: inherit; font-weight: 700; padding: 10px;
  border-radius: 6px; cursor: pointer; border: 1px solid var(--divider-strong); }
.modal .mbtns .go { background: var(--accent); color: #fff; border: none; }
.modal .mbtns .cancel { background: transparent; color: var(--muted); }
/* mobile: the log-a-bet modal becomes a bottom sheet with big controls */
@keyframes sheetup { from { transform: translateY(100%); } to { transform: translateY(0); } }
@media (max-width: 640px) {
  .modal-back { align-items: flex-end; padding: 0; }
  .modal { max-width: none; width: 100%; border-radius: 18px 18px 0 0;
    border-bottom: none; max-height: 92vh; overflow-y: auto;
    padding: 20px 18px calc(20px + env(safe-area-inset-bottom, 0px));
    animation: sheetup .22s cubic-bezier(.2,.8,.2,1); }
  .modal .side-pick button { padding: 13px 8px; }
  .modal .mbtns { margin-top: 20px; }
  .modal .mbtns button { padding: 14px; font-size: 15px; }
}
.empty { color: var(--faint); padding: 20px 0; text-align: center; }
pre.report { font: 12.5px/1.6 ui-monospace, Menlo, monospace; color: var(--text);
  overflow-x: auto; margin: 0; }
footer { color: var(--faint); font-size: 11.5px; margin-top: 30px;
  letter-spacing: .02em; }
"""


def esc(v) -> str:
    return html.escape(str(v)) if v is not None else "—"


def _search_norm(s: str) -> str:
    """Fold to a search key: strip accents (so "Cerundolo" matches "Cerúndolo")
    and lowercase. The client normalizes the typed query the same way, then
    matches each whitespace token as a substring — so any name part hits."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c)).lower()


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


def player_age(dob) -> str:
    """Whole-year age from a date-of-birth, '—' if unknown."""
    if not dob:
        return "—"
    return f"{int((datetime.now(timezone.utc).date() - dob).days / 365.25)}"


def conf_label(conf: float | None) -> str:
    from bot.prob.confidence import confidence_label
    return confidence_label(conf)


def read_list(narrative: str) -> str:
    """Present the gameflow read with HIERARCHY instead of a flat wall of equal
    bullets: a hero (the play + the model number), the actionable rules (entry /
    risk) as callouts, then the supporting case, edges & caveats, and a muted
    context footer. The generator writes the read as an ordered sequence of
    sentences; we classify each by its stable lead-in and group them. Anything
    unrecognised falls through to 'the case', so it degrades gracefully.
    Display-only — the stored narrative is unchanged."""
    import re
    if not narrative:
        return '<p class="sub2">no read generated</p>'
    parts = [p.strip() for p in
             re.split(r'(?<=[.!?])\s+(?=[A-Z(“"])', narrative.strip()) if p.strip()]
    if not parts:
        return f'<p class="prose">{esc(narrative)}</p>'

    def role(s: str) -> str:
        t = s.lower()
        if " is the play" in t:
            return "play"
        if t.startswith("model read:"):
            return "model"
        if "do not chase" in t or "stay away" in t:
            return "risk"
        if t.startswith("first entry") or "the signal" in t:
            return "entry"
        if any(k in t for k in ("temper it", "flatters", "soft field",
                                "size accordingly", "thin sample", "thin decider",
                                "caveat", "insufficient", "recent sample")):
            return "caveat"
        if any(k in t for k in ("fatigue", "played yesterday", "went the distance",
                                "played a set 3", "played a set three")):
            return "edge"
        if t.startswith("last match") or "in a row" in t or "last played" in t:
            return "context"
        return "case"  # supporting stats + decider lines

    b: dict[str, list] = {k: [] for k in
                          ("play", "model", "entry", "risk", "edge",
                           "caveat", "context", "case")}
    for p in parts:
        b[role(p)].append(p)

    LBL = ("text-transform:uppercase;letter-spacing:.07em;font-size:10.5px;"
           "font-weight:700")
    BOX = "padding:8px 12px;margin:6px 0;background:var(--surface);border-radius:0 6px 6px 0"
    out = []

    # hero — the play + the model number, the at-a-glance answer
    hero = []
    if b["play"]:
        hero.append(f'<div style="font-size:16px;font-weight:800;color:var(--accent)">'
                    f'★ {esc(b["play"][0])}</div>')
    for m in b["model"]:
        hero.append(f'<div class="mono" style="font-weight:700;margin-top:3px">{esc(m)}</div>')
    if hero:
        out.append(f'<div style="border-left:3px solid var(--accent);{BOX};'
                   f'margin-bottom:12px">{"".join(hero)}</div>')

    # how to play — entry (green) and risk (amber) rules, the actionable bits
    for e in b["entry"]:
        out.append(f'<div style="border-left:3px solid var(--good);{BOX}">'
                   f'<span class="sub2" style="{LBL};color:var(--good)">Entry</span>'
                   f'<div>{esc(e)}</div></div>')
    for r in b["risk"]:
        out.append(f'<div style="border-left:3px solid var(--warning);{BOX}">'
                   f'<span class="sub2" style="{LBL};color:var(--warning)">Risk rule</span>'
                   f'<div>{esc(r)}</div></div>')

    # the case — the supporting evidence
    if b["case"]:
        lis = "".join(f'<li>{esc(p)}</li>' for p in b["case"])
        out.append(f'<div class="sub2" style="{LBL};margin:14px 0 2px">The case</div>'
                   f'<ul class="readlist">{lis}</ul>')

    # edges & caveats — green pluses, amber hedges
    ec = ([f'<li style="color:var(--good)">{esc(e)}</li>' for e in b["edge"]]
          + [f'<li style="color:var(--warning)">{esc(c)}</li>' for c in b["caveat"]])
    if ec:
        out.append(f'<div class="sub2" style="{LBL};margin:14px 0 2px">Edges &amp; caveats</div>'
                   f'<ul class="readlist">{"".join(ec)}</ul>')

    # context — de-emphasised footer
    if b["context"]:
        out.append(f'<p class="sub2" style="margin-top:12px">'
                   f'{esc(" · ".join(b["context"]))}</p>')

    return f'<div>{"".join(out)}</div>'


def _num(d: dict | None, k: str):
    try:
        return float((d or {}).get(k))
    except (TypeError, ValueError):
        return None


def _oriented_live_stats(detail: dict | None):
    """(yes_stats, opp_stats) from a score-log detail, or (None, None) if the
    feed carried no per-player stats. Prefers the pre-oriented yes/opp keys
    (current recorder); falls back to legacy competitor1/2 + yes_is_c1."""
    if not detail:
        return (None, None)
    if detail.get("yes_statistics") is not None:
        return (detail["yes_statistics"], detail.get("opp_statistics") or {})
    yc1 = detail.get("yes_is_c1")
    c1, c2 = detail.get("competitor1_statistics"), detail.get("competitor2_statistics")
    if yc1 is not None and c1 is not None:
        return (c1, c2 or {}) if yc1 else (c2 or {}, c1)
    return (None, None)


# minimum consecutive-win streak to earn a 🔥 token on the live board
STREAK_TOKEN_MIN = 4

# Tour serve/return benchmarks — (p25, p50, p75, p90) of career rates across
# players with ≥30 stat matches (ATP n=1,975 · WTA n=1,178, computed from the
# match-stats history). These turn a bare "4.6% aces" into a plain-language
# "below average / average / strong / elite" a non-analyst can read at a glance.
_TOUR_BENCH = {
    "atp": {
        "hold": (.694, .737, .776, .807), "ace": (.035, .052, .072, .096),
        "df": (.033, .040, .049, .058), "first_in": (.590, .614, .639, .664),
        "first_won": (.652, .680, .708, .731), "second_won": (.475, .493, .507, .519),
        "bp_saved": (.559, .585, .607, .625),
        "break": (.220, .252, .286, .323), "ret_pts": (.370, .388, .404, .422),
    },
    "wta": {
        "hold": (.539, .590, .635, .673), "ace": (.014, .023, .036, .050),
        "df": (.041, .052, .064, .079), "first_in": (.599, .632, .662, .698),
        "first_won": (.582, .606, .634, .659), "second_won": (.428, .445, .462, .474),
        "bp_saved": (.508, .530, .550, .568),
        "break": (.382, .420, .483, .560), "ret_pts": (.438, .453, .469, .486),
    },
}


_STAT_LEGEND = (
    '<details class="statlegend"><summary>ⓘ How to read these stats</summary>'
    '<div class="statlegend-body">'
    '<p><b style="color:var(--text)">Colour grades each rate against that player’s '
    'tour</b> (ATP and WTA have different baselines): '
    '<span style="color:var(--good)">elite</span> = top 10% · '
    '<span style="color:var(--good)">strong</span> = top 25% · '
    'average = middle 50% · '
    '<span style="color:var(--warning)">below avg</span> = bottom 25%. '
    'Hover any tile for its plain meaning and the tour average. Double faults are '
    'flipped (fewer is better). The small number under a clutch tile is the raw W–L.</p>'
    '<div class="leg-grid">'
    '<div><b>Record</b><span>Past-year &amp; career win–loss · Streak = current run '
    '(shown at 2+)</span></div>'
    '<div><b>Serve · N matches</b><span>N = matches with shot data behind these · '
    'Hold = service games won · Ace / DF = per service point · 1st in = first serves '
    'landed · 1st / 2nd won = points won behind that serve · BP saved = break points '
    'saved</span></div>'
    '<div><b>Return</b><span>Break = share of the opponent’s service games broken · '
    'Ret pts = return points won</span></div>'
    '<div><b>Form</b><span>Win–loss over the last 5 / 10 / 20 matches — momentum, '
    'newest first</span></div>'
    '<div><b>Clutch</b><span>After S1 = win% when they win set 1 · Comeback = win% after '
    '<em>losing</em> set 1 · Win S3 = deciding set after losing set 2 · Straight = share '
    'of their wins that were straight-sets · Deciders / Tiebreaks = record · vs Top50 = '
    'record against top-50 opponents</span></div>'
    '<div><b>Competition</b><span>Strength of the field they’ve faced (elite→weak) '
    'plus average opponent rank — context for the win rate</span></div>'
    '<div><b>Workload</b><span>Days idle since last match + matches / sets in the last 7 '
    'days (fatigue &amp; rust)</span></div>'
    '</div></div></details>')


def _bench_band(tour: str | None, key: str, val: float | None,
                lower_better: bool = False) -> tuple[str | None, str | None]:
    """Map a rate to a plain (qualifier, css_color) vs its tour distribution.
    lower_better inverts (fewer double faults = better). (None, None) if unknown."""
    b = _TOUR_BENCH.get((tour or "").lower(), {}).get(key)
    if not b or val is None:
        return None, None
    p25, p50, p75, p90 = b
    # normalize so "higher x = better" in both directions
    x, (t_avg, t_strong, t_elite) = ((-val, (-p75, -p50, -p25)) if lower_better
                                     else (val, (p25, p75, p90)))
    if x >= t_elite:
        return "elite", "var(--good)"
    if x >= t_strong:
        return "strong", "var(--good)"
    if x >= t_avg:
        return "average", "var(--muted)"
    return "below avg", "var(--warning)"


def _live_flags(me: dict | None, opp: dict | None,
                base: dict | None = None) -> list[tuple[str, str]]:
    """Active in-match performance badges for ONE player, computed purely from
    the live Kalshi score-feed counts (never from price/model). Returns a list of
    (tone, label) where tone is "hot"/"good"/"bad". Thresholds fire only when the
    signal is genuinely notable. Highest priority first; caller may cap the count.

    When `base` (the player's own historical serve rates: {"ace", "df"} per
    service point) is supplied, the ace / double-fault flags become a statistical
    test — they fire when this match's rate is significantly ABOVE that player's
    own norm (z ≥ 2 over the serve points played), and show the fold-change so
    "a lot of aces" means "a lot for THEM", not just a raw count.

    "Playing really well" → 🔥 last-10-point run, 🧱 dominant/unbroken serve,
    💪 point control, 🔨 has broken serve.
    """
    me, opp = me or {}, opp or {}
    base = base or {}
    n = _num
    out: list[tuple[str, str]] = []

    def _z(obs, p, npts):
        # z-score of `obs` successes vs a binomial(npts, p) null; None if not
        # testable (no baseline, too few serve points, degenerate p)
        if obs is None or not p or not npts or npts < 20 or p <= 0 or p >= 1:
            return None
        var = npts * p * (1 - p)
        return (obs - p * npts) / (var ** 0.5) if var > 0 else None

    # big serving — significance vs the player's own norm when we have it, else a
    # raw rate+count fallback. Two tiers: notable vs lights-out / significant.
    aces = n(me, "aces")
    sw = n(me, "service_points_won")
    spts = (sw + (n(me, "service_points_lost") or 0)) if sw is not None else None
    if aces is not None:
        rate = (aces / spts) if spts else None
        za = _z(aces, base.get("ace"), spts)
        if za is not None and za >= 2 and aces >= 4:
            fold = (rate / base["ace"]) if rate else None
            out.append(("hot", f"🎯 {int(aces)} aces"
                        + (f" · {fold:.1f}× norm" if fold and fold >= 1.5 else " ↑")))
        elif aces >= 10 or (aces >= 6 and rate is not None and rate >= 0.18):
            out.append(("hot", f"🎯 {int(aces)} aces"))
        elif aces >= 5 and rate is not None and rate >= 0.12:
            out.append(("good", f"🎯 {int(aces)} aces"))

    # double faults — a fade signal: significantly more errors than this player's
    # own norm (or a raw high count when we have no baseline)
    dfs = n(me, "double_faults")
    if dfs is not None and spts:
        df_rate = dfs / spts
        zd = _z(dfs, base.get("df"), spts)
        if zd is not None and zd >= 2 and dfs >= 3:
            fold = (df_rate / base["df"]) if base.get("df") else None
            out.append(("bad", f"⚠ {int(dfs)} double faults"
                        + (f" · {fold:.1f}× norm" if fold and fold >= 1.5 else " ↑")))
        elif dfs >= 6:
            out.append(("bad", f"⚠ {int(dfs)} double faults"))

    # hot right now — won the bulk of the last 10 points
    l10 = n(me, "points_won_from_last_10")
    if l10 is not None and l10 >= 8:
        out.append(("hot", f"🔥 {int(l10)} of last 10 pts"))

    # serve wall — winning nearly every first-serve point, several games held,
    # and not broken once this match
    fsw, fss = n(me, "first_serve_points_won"), n(me, "first_serve_successful")
    svg = n(me, "service_games_won")
    opp_breaks = n(opp, "breakpoints_won") or 0
    if (fsw is not None and fss and svg is not None and svg >= 4
            and fsw / fss >= 0.80 and opp_breaks == 0):
        out.append(("good", "🧱 serve wall"))

    # in control — dominating the point count over a meaningful sample
    pw, opw = n(me, "points_won"), n(opp, "points_won")
    if pw is not None and opw is not None and (pw + opw) >= 40 \
            and pw / (pw + opw) >= 0.56:
        out.append(("good", "💪 controlling play"))

    # has broken serve more than the opponent
    my_breaks = n(me, "breakpoints_won") or 0
    if my_breaks >= 1 and my_breaks > opp_breaks:
        out.append(("good", f"🔨 {int(my_breaks)} break{'s' if my_breaks != 1 else ''}"))

    return out


def _flags_html(flags: list[tuple[str, str]], right: bool = False) -> str:
    if not flags:
        return ""
    chips = "".join(f'<span class="flag {t}">{esc(lbl)}</span>' for t, lbl in flags[:3])
    return f'<div class="flags{" r" if right else ""}">{chips}</div>'


def _verdict_html(v: dict | None) -> str:
    """Decision-first headline for a live card: the actionable read — side, the
    executable price, the model edge, and an edge-banded suggested size — or an
    explicit 'no edge' with the model's lean. Advisory only; the edge is a
    model-vs-market disagreement, not proven money."""
    if not v:
        return ""
    if v.get("kind") == "bet":
        u = f'{v["units"]:.1f}'.rstrip("0").rstrip(".")
        return (f'<div class="verdict bet" title="model edge vs the executable ask '
                f'— advisory, not proven money">▲ BET <strong>{esc(v["side"])}</strong> '
                f'· <span class="mono">{v["ask"]}¢</span> '
                f'· <span class="mono">+{v["edge"]}%</span> '
                f'· <span class="mono">{u}u</span></div>')
    if v.get("thin"):
        return '<div class="verdict none">no edge · thin data</div>'
    if v.get("side"):
        return (f'<div class="verdict none">no edge · model leans '
                f'{esc(v["side"])} <span class="mono">{v["p"]:.0%}</span></div>')
    return '<div class="verdict none">no edge</div>'


def _fav_btn(pid: int | None, csrf: str, on: bool) -> str:
    """Star toggle to (un)favorite a player. CSRF-protected; a per-user bookmark
    only — it changes nothing the bot does. Renders nothing without a csrf/pid
    (logged-out or an unknown player)."""
    if not csrf or not pid:
        return ""
    return (f'<span class="fav{" on" if on else ""}" data-pid="{int(pid)}" '
            f'data-csrf="{esc(csrf)}" role="button" tabindex="0" '
            f'title="{"Unfavorite" if on else "Favorite this player"}">★</span>')


def _bet_btn(ev_ticker: str, a, b, quotes: dict, csrf: str,
             label: str = "＋ bet") -> str:
    """The shared 'log a bet' control — opens the client-side bet modal
    (openBetModal/bindBets in the base script, so it works on any page that
    renders one). Renders nothing without a csrf or a two-sided market. `a`/`b`
    are the two KalshiMarket sides (ticker-sorted, as everywhere else)."""
    if not csrf or a is None or b is None:
        return ""
    n0 = (a.raw or {}).get("yes_sub_title") or "Player A"
    n1 = (b.raw or {}).get("yes_sub_title") or "Player B"
    p0 = _odds_cents(a, quotes)[0]
    p1 = _odds_cents(b, quotes)[0]
    return (
        f'<span class="betbtn" role="button" tabindex="0" title="Log a bet" '
        f'data-ev="{esc(ev_ticker)}" data-csrf="{esc(csrf)}" '
        f'data-a-tk="{esc(a.ticker)}" data-a-nm="{esc(n0)}" data-a-px="{p0 or ""}" '
        f'data-b-tk="{esc(b.ticker)}" data-b-nm="{esc(n1)}" data-b-px="{p1 or ""}">'
        f'{esc(label)}</span>')


def _fav_player_ids(db, user) -> set[int]:
    """The set of player_ids the current user has favorited (empty if logged out)."""
    if not user or not user.get("id"):
        return set()
    from bot.models import UserFavoritePlayer
    return set(db.execute(select(UserFavoritePlayer.player_id).where(
        UserFavoritePlayer.user_id == user["id"])).scalars().all())


def live_analysis_html(*, is_live: bool, is_final: bool, sets_watch, sets_opp,
                       scoreline, st_state, prematch: float, dec_prob: float,
                       triggers: list, watch_cents, player: str, opp_name: str,
                       detail: dict | None) -> str:
    """In-play synthesis, shown BELOW the read: the model's probability at the
    CURRENT set state (vs prematch), the entry/trigger verdict, live value vs
    the price, and the live serve battle. It is recomputed on every page
    refresh, so it tracks the match as the score moves. The read above is the
    fixed pre-match plan; this is the same thesis measured against what is
    actually happening."""
    def _prob_at(state):
        for t in triggers or []:
            if t.get("state") == state and t.get("prob") is not None:
                return float(t["prob"])
        return prematch if state in (None, "0-0") else None

    def wrap(aside, body):
        border = ';border-color:var(--good)' if is_live else ''
        return (f'<section class="block" style="margin-top:14px{border}">'
                f'<div class="blockhead"><h4>Live play analysis</h4>{aside}</div>'
                f'<div class="rule"></div>{body}</section>')

    if not is_live and not is_final:
        return wrap('<span class="aside">awaiting play</span>',
                    '<p class="sub2">The read above is the pre-match plan. Once the '
                    'match starts, this panel tracks the live model read, the entry '
                    'trigger, and the serve battle — refreshing every few seconds.</p>')
    if is_final:
        return wrap('<span class="aside">final</span>',
                    f'<p>Match final — <span class="mono">{esc(scoreline or "")}</span>. '
                    f'The pre-match read had {esc(player)} at {prematch:.0%}.</p>')

    # --- live ---
    state = (f"{int(sets_watch)}-{int(sets_opp)}"
             if sets_watch is not None and sets_opp is not None else None)
    model_now = _prob_at(state)
    mn_s = f"{model_now:.0%}" if model_now is not None else "—"
    shift = (model_now - prematch) if model_now is not None else None
    shift_s = (f'<span style="color:{"var(--good)" if shift >= 0 else "var(--accent)"}">'
               f'{shift * 100:+.0f} pts</span>' if shift is not None else "—")
    price_s = f"{int(watch_cents)}¢" if watch_cents is not None else "—"
    if watch_cents is not None and model_now is not None:
        edge = model_now * 100 - watch_cents
        val = (f'<span style="color:var(--good);font-weight:800">+{edge:.0f}% value</span>'
               if edge >= 3 else f'<span class="sub2">{edge:+.0f}% · no edge</span>')
    else:
        val = '<span class="sub2">—</span>'

    grid = (
        '<div class="metric-grid" style="grid-template-columns:repeat(3,1fr)">'
        f'<div class="metric"><div class="k">model now</div>'
        f'<div class="v mono">{mn_s}</div>'
        f'<div class="sub2">at {esc(state or "0-0")}</div></div>'
        f'<div class="metric"><div class="k">vs prematch</div>'
        f'<div class="v mono">{prematch:.0%}</div>'
        f'<div class="sub2">{shift_s} since start</div></div>'
        f'<div class="metric"><div class="k">live price</div>'
        f'<div class="v mono">{price_s}</div><div class="sub2">{val}</div></div></div>')

    # entry / trigger verdict for the state we're actually in
    band = watch_cents is not None and 35 <= watch_cents <= 65
    in_trig = any(t.get("state") == state for t in (triggers or []))
    favors = model_now is not None and model_now >= 0.55
    down_set = (sets_watch is not None and sets_opp is not None
                and sets_watch < sets_opp)
    # the read's risk rule outranks the trigger: never chase a dropped set
    if down_set:
        lbl, col, txt = ("HOLD", "var(--warning)",
                         "down a set — only enter if set 2 builds a clear cushion "
                         "(up three games); otherwise stay away")
    elif in_trig and favors and band:
        lbl, col, txt = ("ENTRY LIVE", "var(--good)",
                         f"{state} reached · {esc(player)} favoured {mn_s} · {price_s} in band")
    elif in_trig and favors and not band:
        lbl, col, txt = ("OUT OF BAND", "var(--muted)",
                         f"{esc(player)} favoured but priced {price_s} (outside 35–65¢)")
    else:
        lbl, col, txt = ("WATCHING", "var(--muted)",
                         "entry triggers at the deciding set")
    entry = (f'<div style="border-left:3px solid {col};padding:8px 12px;margin:12px 0 0;'
             f'background:var(--surface)"><span style="font-weight:800;color:{col}">{lbl}</span>'
             f' <span class="sub2">— {txt}</span></div>')

    pe, oe = esc(player.split()[-1]), esc(opp_name.split()[-1])

    # The full serve/game-stats chart now lives in the combined "Live match
    # stats" section below (built in _match_view, with historical win% context).
    # Here we only distil the one-line serve-battle takeaway for the written read.
    serve_txt = ""
    sy, so = _oriented_live_stats(detail)
    if sy is not None:
        sy, so = sy or {}, so or {}
        ay, ao = _num(sy, "aces") or 0, _num(so, "aces") or 0
        if ay != ao:
            big = pe if ay > ao else oe
            serve_txt = (f"{big} is serving bigger — "
                         f"{int(max(ay, ao))} aces to {int(min(ay, ao))}")

    # --- the written read (words): state → model shift → value → serve → verdict ---
    words = []
    if sets_watch is not None and sets_opp is not None:
        if sets_watch > sets_opp:
            words.append(f"{pe} leads by a set at {state}.")
        elif sets_watch < sets_opp:
            words.append(f"{pe} dropped the opening set ({state}).")
        elif sets_watch > 0:
            words.append(f"The match is level at {state} — into the deciding set.")
        else:
            words.append("Still on serve in the opening set.")
    if model_now is not None:
        if shift is not None and shift >= 0.03:
            words.append(f"The model has {pe} up to {mn_s} from {prematch:.0%} prematch — "
                         f"the scoreline has swung their way.")
        elif shift is not None and shift <= -0.03:
            words.append(f"{pe}'s number has slipped to {mn_s} from {prematch:.0%} prematch.")
        else:
            words.append(f"The model still reads {pe} at {mn_s}.")
    if watch_cents is not None and model_now is not None:
        if edge >= 3:
            words.append(f"At {price_s} that leaves {edge:.0f} points of value on {pe}.")
        elif edge <= -3:
            words.append(f"The price ({price_s}) has run past the model — no value on {pe} now.")
        else:
            words.append(f"The price ({price_s}) has caught the model — no edge right now.")
    if serve_txt:
        words.append(serve_txt + ".")
    TAKE = {
        "ENTRY LIVE": f"This is the entry — back {pe} in the 35–65¢ band.",
        "HOLD": "Hold — don't chase the dropped set unless set 2 builds a clear cushion.",
        "OUT OF BAND": f"{pe} is favoured but priced outside the band.",
        "WATCHING": "Watching — the play triggers at the deciding set.",
    }
    if TAKE.get(lbl):
        words.append(TAKE[lbl])
    narrative = (f'<p class="prose" style="margin:0 0 12px">{" ".join(words)}</p>'
                 if words else "")

    aside = (f'<span class="aside" style="color:var(--good)">● live · '
             f'{esc(st_state or state or "in play")} · updates with the match</span>')
    return wrap(aside, narrative + grid + entry)


def bot_activity_html(db, sc, quotes: dict, tier: str) -> str:
    """Which testrun bots are betting this match and WHY — and which aren't and
    WHY NOT. A bot that bet carries the reason it actually recorded at placement.
    A bot that didn't gets an honest reason: bots evaluated on the pre-match read
    (pre/top5 lanes) are re-run through their OWN policy to surface the specific
    gate they failed; the in-play, control, and experiment lanes get their
    structural reason. Never fabricates a pass — if a lane can't be evaluated it
    says so."""
    from bot.models import PaperBet, Scenario
    from bot.paper import decide_bet
    from bot.t2 import BOTS, base_policy, bot_policy

    if sc is None:
        return ""
    ev = sc.event_ticker
    bets = {b.bot: b for b in db.execute(
        select(PaperBet).where(PaperBet.event_ticker == ev)).scalars()}
    q = quotes.get(sc.market_ticker)
    yb, ya = (q[0], q[1]) if q else (None, None)
    p = sc.prematch_prob
    conf = (sc.facts or {}).get("model_confidence")
    top5 = set(db.execute(
        select(Scenario.event_ticker).where(Scenario.created_for == sc.created_for)
        .order_by(Scenario.salience.desc()).limit(5)).scalars().all())

    def gate(bid):
        if p is None or ya is None or yb is None:
            return None
        pol = bot_policy(db, bid) if BOTS[bid]["si"] else base_policy(bid)
        return decide_bet(p, conf if conf is not None else 1.0, ya, yb, tier, pol)

    LANE = {
        "live": "in-play bot — enters only when a live advisory clears mid-match",
        "mid": "in-play, 35–65¢ band — enters on a live toss-up advisory",
        "dec": "decider-only — enters at the deciding set",
        "chalk": "market-favorite control — backs the favorite once it's priced",
        "freshadj": "fatigue/form experiment — bets the day's top plays on the adjusted number",
    }

    betting, skipping = [], []
    for bid, meta in BOTS.items():
        b = bets.get(bid)
        if b is not None:
            why = (b.reasoning or {}).get("policy_reason") or "policy cleared"
            betting.append(
                f'<tr><td><strong>{esc(meta["label"])}</strong></td>'
                f'<td class="mono">{esc(b.side.upper())} @ {b.price_cents}¢ · {b.units:g}u</td>'
                f'<td class="sub2">{esc(why)}</td></tr>')
            continue
        basis = meta["basis"]
        if basis in ("chalk", "freshadj"):
            why = LANE[basis]
        elif basis == "advisory":
            _TIERNAME = {"A": "main-tour", "C": "Challenger", "15": "ITF", "25": "ITF"}
            if meta.get("decider_only"):
                why = LANE["dec"]
            elif meta.get("tiers"):
                names = " / ".join(dict.fromkeys(_TIERNAME.get(t, t) for t in meta["tiers"]))
                why = f"in-play, {names} matches only — none here (this match is a different tier)"
            elif meta.get("tour"):
                why = f"in-play, {'men’s (ATP)' if meta['tour'] == 'atp' else 'women’s (WTA)'} matches only"
            elif meta.get("min_conf"):
                why = f"in-play, model confidence ≥ {meta['min_conf']:.0%} only"
            elif meta.get("move") == "follow":
                why = "in-play, only when the line moved toward the pick (follow the money)"
            elif meta.get("move") == "fade":
                why = "in-play, only when the line moved against the pick (fade the move)"
            elif meta.get("dropped_set1"):
                why = ("in-play, only a pre-match favorite (≥60%) after it drops "
                       "set 1 — and only if the model still rates it the value "
                       "side (Bo3)")
            elif bid in ("mid", "midSI"):
                why = LANE["mid"]
            else:
                why = LANE["live"]
        elif basis == "top5":
            if ev not in top5:
                why = "bets only the day's top-5 scenarios — this match isn't in them"
            else:
                d = gate(bid)
                why = (d.reason if d and not d.place
                       else "in the top-5 and clears — awaiting placement")
        else:  # prematch
            d = gate(bid)
            why = ("no prematch read / live price yet" if d is None
                   else d.reason if not d.place
                   else "clears the gate — awaiting the next placement cycle")
        skipping.append(
            f'<tr><td>{esc(meta["label"])}</td><td class="sub2">{esc(why)}</td></tr>')

    bet_tbl = (f'<div class="vshead">Betting this match<span>{len(betting)} bots</span></div>'
               f'<div class="tw"><table class="t">{"".join(betting)}</table></div>'
               if betting else
               '<p class="sub2">No bot has placed on this match.</p>')
    skip_tbl = (f'<div class="vshead" style="margin-top:14px">Not betting'
                f'<span>{len(skipping)} bots · why not</span></div>'
                f'<div class="tw"><table class="t">{"".join(skipping)}</table></div>'
                if skipping else "")
    return (f'<section class="block"><div class="blockhead"><h4>Bot activity</h4>'
            f'<span class="aside">what the testrun bots did with this match</span></div>'
            f'<div class="rule"></div>{bet_tbl}{skip_tbl}</section>')


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


# Kalshi web deep-link: /markets/<series>/<series-slug>/<event-ticker> (all
# lowercase). Slugs verified from live kalshi.com URLs (note the Challenger slug's
# trailing hyphen — that's how Kalshi actually serves it).
_KALSHI_SERIES_SLUG = {
    "KXATPMATCH": "atp-tennis-match",
    "KXWTAMATCH": "wta-tennis-match",
    "KXITFMATCH": "itf-mens-match",
    "KXITFWMATCH": "itf-womens-match",
    "KXATPCHALLENGERMATCH": "challenger-atp-",
    "KXWTAGAME": "wta-tennis-game",
}


def kalshi_url(ticker: str) -> str:
    """Deep link to the specific match on Kalshi. `ticker` is a MARKET ticker
    (EVENT-SIDE, e.g. KXITFWMATCH-26JUL24IVASVE-IVA); we strip the side suffix to
    the event ticker and route to that match's page. Falls back to the series
    board for an unmapped series."""
    series = ticker.split("-")[0]
    event = ticker.rsplit("-", 1)[0]  # drop the YES/NO side suffix → event ticker
    slug = _KALSHI_SERIES_SLUG.get(series)
    if slug:
        return f"https://kalshi.com/markets/{series.lower()}/{slug}/{event.lower()}"
    return f"https://kalshi.com/markets/{series.lower()}"


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


_FEED_CACHE = {"at": 0.0, "val": None}
_FEED_TTL_S = 15.0


def _feed_status() -> tuple[str, str]:
    """(dot color, label) from the recorder's most recent tick. Rendered in the
    nav on every page, so the max(ts) read is cached for a few seconds — feed
    freshness doesn't need per-request precision and this keeps page() DB-free."""
    import time as _time

    from sqlalchemy import text as sqltext

    now_m = _time.monotonic()
    if _FEED_CACHE["val"] is not None and now_m - _FEED_CACHE["at"] < _FEED_TTL_S:
        return _FEED_CACHE["val"]
    try:
        with db_session() as db:
            last = db.execute(sqltext(
                "SELECT max(ts) FROM market_ticks")).scalar()
    except Exception:
        return "var(--critical)", "DB UNREACHABLE"   # not cached — transient
    if last is None:
        val = ("var(--faint)", "NO FEED YET")
    else:
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - last).total_seconds()
        if age < 300:
            val = ("var(--good)", "FEED LIVE")
        elif age < 3600:
            val = ("var(--warning)", f"FEED IDLE {int(age // 60)}M")
        else:
            val = ("var(--critical)", "FEED STALE")
    _FEED_CACHE["val"], _FEED_CACHE["at"] = val, now_m
    return val


JS = """
function rel(root){(root||document).querySelectorAll('.rel').forEach(function(e){
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
function normSearch(x){return (x||'').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'');}
function applyLiveFilters(){
 var s=lfState(), tours=s.tours||[], shown=0, total=0;
 // accent-fold + tokenize: every whitespace token must appear in the card's
 // names, so a last name, a first name, or "borges kovacevic" all match
 var q=normSearch((s.q||'').trim());
 var qtok=q?q.split(/ +/):[];
 function qmatch(names){for(var i=0;i<qtok.length;i++){if((names||'').indexOf(qtok[i])<0)return false;}return true;}
 document.querySelectorAll('.livecard').forEach(function(c){
  total++;
  var okQ=!qtok.length||qmatch(c.dataset.names);
  var okT=tours.length===0||tours.indexOf(c.dataset.tour)>=0;
  var okP=!s.play||c.dataset.play==='1';
  var okS=!s.scenario||c.dataset.scenario==='1';
  var okF=!s.trigfired||c.dataset.trigfired==='1';
  var okG=!s.trig||c.dataset.trig==='hit'||c.dataset.trig==='near';
  var okV=!s.fav||c.dataset.fav==='1';
  var okW=!s.streak||c.dataset.streak==='1';
  var okB=!s.bet||c.dataset.bet==='1';
  var okC=!s.conf||c.dataset.conf==='1';
  var ok=okQ&&okT&&okP&&okS&&okF&&okG&&okV&&okW&&okB&&okC; c.style.display=ok?'':'none'; if(ok)shown++;});
 // an active text search is an explicit intent — honour a genuine "no matches"
 // (show nothing) rather than the stale-chip fallback that reveals everything
 if(shown===0&&total>0&&!q){document.querySelectorAll('.livecard').forEach(function(c){c.style.display='';});shown=total;}
 document.querySelectorAll('.fchip').forEach(function(ch){var f=ch.dataset.f,on;
  if(f==='play')on=!!s.play; else if(f==='trig')on=!!s.trig;
  else if(f==='trigfired')on=!!s.trigfired;
  else if(f==='scenario')on=!!s.scenario;
  else if(f==='fav')on=!!s.fav; else if(f==='streak')on=!!s.streak;
  else if(f==='bet')on=!!s.bet; else if(f==='conf')on=!!s.conf;
  else on=tours.indexOf(f)>=0;
  ch.classList.toggle('on',on);});
 var vc=document.getElementById('livecount'); if(vc)vc.textContent=shown;
 // GLOBAL search pass: the chip filters above only touch .livecard, but the
 // search box lives at the top of the board and must reach every card —
 // scenarios, pinned, upcoming, finished. Search-only for non-livecards.
 var hits=0;
 document.querySelectorAll('[data-names]').forEach(function(c){
  var m=!qtok.length||qmatch(c.dataset.names);
  if(!c.classList.contains('livecard')) c.style.display=m?'':'none';
  if(q&&c.style.display!=='none') hits++;});
 // collapse any board section left with no visible match while searching, so
 // you don't scroll past empty "Scenarios / Pinned / …" headers
 document.querySelectorAll('section.block.major').forEach(function(sec){
  var cards=sec.querySelectorAll('[data-names]'); if(!cards.length) return;
  var vis=0; cards.forEach(function(c){if(c.style.display!=='none')vis++;});
  sec.style.display=(q&&vis===0)?'none':'';});
 var sc=document.getElementById('searchcount');
 if(sc) sc.textContent = q ? (hits+' match'+(hits===1?'':'es')) : '';
 // a 0-result search collapses every section → a blank board that reads as
 // "broken". Explain it instead: the board only holds live + next-12h games.
 var se=document.getElementById('searchempty');
 if(se){
  if(q&&hits===0){
   var raw=(s.q||'').trim();
   se.textContent='Nothing on the board matches “'+raw+'”. The board only shows '
    +'matches that are live now or starting within 12h — a finished or '
    +'not-yet-scheduled match won’t appear here.';
   se.style.display='';
  } else se.style.display='none';
 }}
function bindFilters(){
 document.querySelectorAll('.fchip').forEach(function(ch){if(ch._b)return;ch._b=true;
  ch.addEventListener('click',function(){var s=lfState(),f=ch.dataset.f;
   if(f==='play')s.play=!s.play; else if(f==='trig')s.trig=!s.trig;
   else if(f==='trigfired')s.trigfired=!s.trigfired;
   else if(f==='scenario')s.scenario=!s.scenario;
   else if(f==='fav')s.fav=!s.fav; else if(f==='streak')s.streak=!s.streak;
   else if(f==='bet')s.bet=!s.bet; else if(f==='conf')s.conf=!s.conf;
   else{s.tours=s.tours||[];var i=s.tours.indexOf(f);if(i>=0)s.tours.splice(i,1);else s.tours.push(f);}
   localStorage.setItem('deuce_lf',JSON.stringify(s));applyLiveFilters();});});
 applyLiveFilters();}
function bindSearch(){
 var inp=document.getElementById('livesearch'); if(!inp) return;
 var xb=document.getElementById('livesearch-x');
 // restore a persisted query (survives the 7s refresh / a full re-render)
 var s=lfState(); if(!inp._b){inp.value=s.q||'';}
 function paintClear(){if(xb)xb.style.visibility=inp.value?'visible':'hidden';}
 paintClear();
 if(inp._b) return; inp._b=true;
 inp.addEventListener('input',function(){
  var st=lfState(); st.q=inp.value; localStorage.setItem('deuce_lf',JSON.stringify(st));
  paintClear(); applyLiveFilters();});
 if(xb) xb.addEventListener('click',function(){
  inp.value=''; var st=lfState(); st.q=''; localStorage.setItem('deuce_lf',JSON.stringify(st));
  paintClear(); applyLiveFilters(); inp.focus();});}
function bindPins(){
 document.querySelectorAll('.pin').forEach(function(p){
  if(p._b) return; p._b=true;
  function toggle(ev){ev.preventDefault();ev.stopPropagation();
   if(p._busy)return; p._busy=true;
   var body='event_ticker='+encodeURIComponent(p.dataset.ev)+'&csrf='+encodeURIComponent(p.dataset.csrf);
   fetch('/pin',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body})
    .then(function(r){p._busy=false; if(r.ok){p.classList.toggle('on');refreshMain();}})
    .catch(function(){p._busy=false;});}
  p.addEventListener('click',toggle);
  p.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' ')toggle(e);});
 });}
function bindFavs(){
 document.querySelectorAll('.fav').forEach(function(p){
  if(p._b) return; p._b=true;
  function toggle(ev){ev.preventDefault();ev.stopPropagation();
   if(p._busy)return; p._busy=true;
   var body='player_id='+encodeURIComponent(p.dataset.pid)+'&csrf='+encodeURIComponent(p.dataset.csrf);
   fetch('/favorite',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body})
    .then(function(r){return r.ok?r.json():null;})
    .then(function(j){p._busy=false;
     if(j){var on=j.favorited; document.querySelectorAll('.fav[data-pid="'+p.dataset.pid+'"]')
      .forEach(function(q){q.classList.toggle('on',on);
       q.title=on?'Unfavorite':'Favorite this player';});}})
    .catch(function(){p._busy=false;});}
  p.addEventListener('click',toggle);
  p.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' ')toggle(e);});
 });}
function bindProfMore(){
 // collapse/expand the deep profile sections on mobile; persist the choice so the
 // 7s auto-refresh doesn't fold it back up
 var open = localStorage.getItem('deuce_profmore')==='1';
 document.querySelectorAll('.prof-more').forEach(function(m){m.classList.toggle('show',open);});
 document.querySelectorAll('.prof-toggle').forEach(function(b){
  b.textContent = open ? '− Less stats' : '＋ More stats';
  if(b._b) return; b._b=true;
  b.addEventListener('click',function(){
   var now = localStorage.getItem('deuce_profmore')!=='1';
   localStorage.setItem('deuce_profmore', now?'1':'0');
   document.querySelectorAll('.prof-more').forEach(function(m){m.classList.toggle('show',now);});
   document.querySelectorAll('.prof-toggle').forEach(function(x){x.textContent = now?'− Less stats':'＋ More stats';});
  });});}
function openBetModal(b){
 var sideT=b.dataset.aTk, sideN=b.dataset.aNm, sidePx=b.dataset.aPx;
 var oppN=b.dataset.bNm;
 var back=document.createElement('div'); back.className='modal-back';
 back.innerHTML=
  '<div class="modal"><h4>Log a bet</h4>'+
  '<div class="sub2">Records a personal bet — the app places no orders.</div>'+
  '<div class="side-pick">'+
   '<button data-tk="'+b.dataset.aTk+'" data-nm="'+b.dataset.aNm+'" data-px="'+b.dataset.aPx+'" class="sel">'+b.dataset.aNm+'</button>'+
   '<button data-tk="'+b.dataset.bTk+'" data-nm="'+b.dataset.bNm+'" data-px="'+b.dataset.bPx+'">'+b.dataset.bNm+'</button>'+
  '</div>'+
  '<div class="mrow"><div><label>PRICE (¢)</label><input id="bp" type="number" inputmode="numeric" min="1" max="99" step="1"></div>'+
  '<div><label>SHARES</label><input id="bs" type="number" inputmode="numeric" min="1" step="1" value="10"></div></div>'+
  '<div><label>TAG (optional — who you tailed; comma-separate for several)</label>'+
   '<input id="btag" list="bettags" maxlength="64" placeholder="e.g. blvr, clutch" '+
   'style="width:100%;box-sizing:border-box"></div>'+
  '<div class="cost" id="bcost"></div>'+
  '<div class="mbtns"><button class="cancel">Cancel</button><button class="go">Place bet</button></div></div>';
 document.body.appendChild(back);
 var picks=back.querySelectorAll('.side-pick button');
 var pxIn=back.querySelector('#bp'), shIn=back.querySelector('#bs'), cost=back.querySelector('#bcost');
 var sel={tk:sideT,nm:sideN,px:sidePx};
 function paintCost(){var p=+pxIn.value,s=+shIn.value;
  if(p>0&&s>0){var c=(p*s/100).toFixed(2); var win=((100-p)*s/100).toFixed(2);
   cost.textContent='Cost $'+c+' · wins $'+win+' if '+sel.nm+' wins';}else cost.textContent='';}
 function setPx(){pxIn.value=sel.px||''; paintCost();}
 setPx();
 picks.forEach(function(pb){pb.addEventListener('click',function(){
  picks.forEach(function(x){x.classList.remove('sel')}); pb.classList.add('sel');
  sel={tk:pb.dataset.tk,nm:pb.dataset.nm,px:pb.dataset.px}; setPx();});});
 pxIn.addEventListener('input',paintCost); shIn.addEventListener('input',paintCost);
 function close(){back.remove();}
 back.addEventListener('click',function(e){if(e.target===back)close();});
 back.querySelector('.cancel').addEventListener('click',close);
 back.querySelector('.go').addEventListener('click',function(){
  var p=Math.round(+pxIn.value), s=Math.round(+shIn.value);
  if(!(p>=1&&p<=99)||!(s>=1)){cost.textContent='Enter a price 1–99¢ and shares ≥ 1.';return;}
  var tg=(back.querySelector('#btag').value||'').trim();
  var body='event_ticker='+encodeURIComponent(b.dataset.ev)+'&market_ticker='+encodeURIComponent(sel.tk)+
   '&player_name='+encodeURIComponent(sel.nm)+'&opponent_name='+encodeURIComponent(oppN)+
   '&price='+p+'&shares='+s+'&tag='+encodeURIComponent(tg)+'&csrf='+encodeURIComponent(b.dataset.csrf);
  fetch('/bet',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body})
   .then(function(r){if(r.ok){cost.textContent='Saved ✓ — see My Bets.';setTimeout(close,700);}
    else cost.textContent='Could not save (try reloading).';})
   .catch(function(){cost.textContent='Could not save (network).';});});
}
function bindBets(){
 document.querySelectorAll('.betbtn').forEach(function(b){
  if(b._b) return; b._b=true;
  b.addEventListener('click',function(e){e.preventDefault();e.stopPropagation();openBetModal(b);});
 });}
function typing(){var a=document.activeElement;
 return a && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName);}
function flashPrice(el, oldv){
 var nv=el.dataset.px; if(nv===undefined||nv===''||oldv===undefined||oldv===''||nv===oldv) return;
 var cls=parseFloat(nv)>parseFloat(oldv)?'flash-up':'flash-down';
 el.classList.remove('flash-up','flash-down'); void el.offsetWidth; el.classList.add(cls);
 el.addEventListener('animationend',function h(){el.classList.remove('flash-up','flash-down');
  el.removeEventListener('animationend',h);});}
// Incremental reconcile: when the same set of cards is present in the same order,
// update only the cards whose content changed (no full-page reflow, scroll kept)
// and flash any price that moved. Falls back to a whole-main swap when the card
// set changes (a match started / ended / moved tiers).
function reconcile(tmp, main){
 var oc=main.querySelectorAll('[data-card]'), nc=tmp.querySelectorAll('[data-card]');
 var ok=function(l){return Array.prototype.map.call(l,function(c){return c.dataset.card;}).join(',');};
 if(!oc.length || ok(oc)!==ok(nc)) return false;   // structure changed → caller does full swap
 var byId={}; Array.prototype.forEach.call(nc,function(c){byId[c.dataset.card]=c;});
 Array.prototype.forEach.call(oc,function(o){
  var n=byId[o.dataset.card]; if(!n) return;
  // sync card-level attributes filters read (data-fav/streak/trig/…); never touch style
  Array.prototype.forEach.call(n.attributes,function(a){
   if(a.name!=='style' && o.getAttribute(a.name)!==a.value) o.setAttribute(a.name,a.value);});
  if(o.innerHTML===n.innerHTML) return;            // nothing changed in this card
  var oldpx={}; o.querySelectorAll('[data-pxk]').forEach(function(e){oldpx[e.dataset.pxk]=e.dataset.px;});
  o.innerHTML=n.innerHTML;                          // only this card repaints
  o.querySelectorAll('[data-pxk]').forEach(function(e){flashPrice(e, oldpx[e.dataset.pxk]);});
 });
 // keep the (non-card) filter bar counts fresh
 var of=main.querySelector('.filterbar'), nf=tmp.querySelector('.filterbar');
 if(of&&nf&&of.innerHTML!==nf.innerHTML) of.innerHTML=nf.innerHTML;
 return true;
}
async function refreshMain(){
 if(document.hidden) return;  // don't burn battery/data while backgrounded
 if(typing()) return;  // don't clobber a field mid-keystroke
 try{var r=await fetch(location.pathname+location.search,{headers:{'X-Fragment':'1'}});
  if(r.ok){var h=await r.text(); var m=document.querySelector('main');
   if(!typing() && h && h.length>50 && h!==m.innerHTML){
    var tmp=document.createElement('div'); tmp.innerHTML=h;
    rel(tmp);  // normalize .rel timestamps to relative form so unchanged cards match
    if(!reconcile(tmp, m)){var y=window.scrollY; m.innerHTML=h; window.scrollTo(0,y);}
    bindWatch(); bindFilters(); bindSearch(); bindPins(); bindBets(); bindFavs(); bindProfMore();}
   pulse();
  }}catch(e){} rel();}
function pulse(){var d=document.getElementById('refreshdot');
 if(d){d.classList.add('on');setTimeout(function(){d.classList.remove('on');},600);}}
var seen=null;
function notify(title, body){
 if(Notification.permission==='granted') new Notification(title,{body:body,silent:false});}
async function pollEvents(){
 if(document.hidden) return;
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
 // catch up immediately when the tab comes back to the foreground
 document.addEventListener('visibilitychange',function(){
  if(!document.hidden){refreshMain();pollEvents();}});
 bindWatch(); bindFilters(); bindSearch(); bindPins(); bindBets(); bindFavs(); bindProfMore();
 var bell=document.getElementById('bell');
 function paint(){bell.textContent=Notification.permission==='granted'?'🔔 alerts on':'🔕 enable alerts';}
 if(!('Notification' in window)){bell.style.display='none';return;} paint();
 bell.addEventListener('click',function(){Notification.requestPermission().then(paint);});});
"""


# Brand mark — a tennis ball (the "deuce" is a tennis term): optic yellow-green
# with the single S-shaped wrapping seam (two symmetric arcs read as a baseball).
# Inline for the header, base64 SVG for the tab.
_LOGO_MARK = (
    '<svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true"'
    ' style="margin-right:9px;flex:none">'
    '<circle cx="16" cy="16" r="14.5" fill="#c7e14b" stroke="#93b02f" stroke-width="1"/>'
    '<path d="M16 2.5C6.5 9 25.5 16 16 29.5" fill="none" stroke="#fff"'
    ' stroke-width="2.3" stroke-linecap="round"/></svg>')
_FAVICON_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
    "<circle cx='16' cy='16' r='15' fill='#c7e14b' stroke='#93b02f' stroke-width='1.5'/>"
    "<path d='M16 1.5C6 8.5 26 15.5 16 30.5' fill='none' stroke='#fff'"
    " stroke-width='3' stroke-linecap='round'/></svg>")
FAVICON = ("data:image/svg+xml;base64,"
           + base64.b64encode(_FAVICON_SVG.encode()).decode())
_FAVICON_LINK = f'<link rel="icon" href="{FAVICON}">'


def page(title: str, active: str, body: str, fragment: bool = False,
         user: dict | None = None) -> str:
    footer = """<footer>All times relative · updates in place every 7s ·
historical data © Jeff Sackmann / Tennis Abstract (CC BY-NC-SA 4.0), personal
research use · advisory only, nothing here is an order.</footer>"""
    if fragment:
        return body + footer
    nav_groups = (
        (("/live", "live", "Live"),
         ("/today", "today", "Today"), ("/scenarios", "scenarios", "Scenarios")),
        (("/testrun", "testrun", "Testrun"), ("/mybets", "mybets", "My Bets")),
        (("/history", "history", "History"), ("/players", "players", "Database")),
        (("/features", "features", "Variables"),
         ("/flags", "flags", "Flags"), ("/system", "system", "System")),
    )
    navs = "".join(
        '<span class="navgroup">' + "".join(
            f'<a href="{href}" class="{"active" if key == active else ""}">{label}</a>'
            for href, key, label in group) + "</span>"
        for group in nav_groups)
    # account group: admins get the user-management link; everyone gets sign-out
    if user:
        acct = '<span class="navgroup">'
        if user.get("is_admin"):
            acct += (f'<a href="/admin/users" class="'
                     f'{"active" if active == "users" else ""}">Users</a>')
        acct += '<a href="/logout">Sign out</a></span>'
        navs += acct
    who = (f'<span class="conn mono" style="color:var(--muted)" '
           f'title="signed in">{esc(user["username"])}</span>' if user else "")
    # mobile bottom tab bar — primary destinations in the thumb zone
    _tabs = (("/live", "live", "◉", "Live"), ("/scenarios", "scenarios", "◆", "Plays"),
             ("/mybets", "mybets", "▤", "Bets"), ("/players", "players", "◍", "Players"))
    tabbar = "".join(
        f'<a href="{href}" class="{"on" if key == active else ""}">'
        f'<span class="ic">{ic}</span>{label}</a>' for href, key, ic, label in _tabs)
    dot, conn = _feed_status()
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · DEUCE</title>{_FAVICON_LINK}<style>{CSS}</style></head>
<body>
<header class="nav">
  <span class="brand">{_LOGO_MARK}DEUCE<span class="tag">advisory only</span></span>
  <nav class="links">{navs}</nav>
  {who}
  <button id="bell" class="conn mono" style="background:none;border:1px solid var(--divider);
   color:var(--muted);cursor:pointer;padding:4px 10px;font:inherit;font-size:11px"></button>
  <span class="conn mono"><span class="dot" style="background:{dot}"></span>{conn}
  <span id="refreshdot" title="live · refreshes every 7s"></span></span>
</header>
<main>
{page(title, active, body, fragment=True)}
</main>
<nav class="tabbar">{tabbar}</nav>
<script>{JS}</script></body></html>"""



def respond(request: web.Request, title: str, active: str, body: str) -> web.Response:
    frag = request.headers.get("X-Fragment") == "1"
    return web.Response(text=page(title, active, body, fragment=frag,
                                  user=request.get("user")),
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
    # Overview page removed — the live board is the landing page. Root and any
    # `next=/` login redirect land there.
    raise web.HTTPFound("/live")


async def today(request: web.Request) -> web.Response:
    """The full slate for the current day: every scheduled/live match, the
    model's pre-match pick for the winner (from the day's precomputed scenario
    reads — no re-fit), and a link to each match's data. Thin-data matches show
    'insufficient data' rather than a fabricated pick (CLAUDE.md sample-size
    rule)."""
    from bot.models import Scenario

    now = datetime.now(timezone.utc)
    today_pac = now.astimezone(PACIFIC).date()
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
            if occ.astimezone(PACIFIC).date() != today_pac:
                continue  # only today's (Pacific) games
            events.setdefault(m.event_ticker, {"occ": occ, "sides": []})["sides"].append(m)

        # model picks: the latest precomputed scenario batch (prematch reads)
        latest_day = db.execute(select(func.max(Scenario.created_for))).scalar()
        scen = {sc.event_ticker: sc for sc in db.execute(
            select(Scenario).where(Scenario.created_for == latest_day)).scalars()} \
            if latest_day else {}

        # today's FINISHED games (settled) — for the results section
        from bot.models import PaperBet
        finished: dict[str, dict] = {}
        for m in db.execute(select(KalshiMarket).where(
                KalshiMarket.result.is_not(None),
                KalshiMarket.settled_at >= now - timedelta(hours=36))).scalars():
            occ_raw = (m.raw or {}).get("occurrence_datetime")
            if not occ_raw:
                continue
            try:
                occ = datetime.fromisoformat(occ_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if occ.astimezone(PACIFIC).date() != today_pac:
                continue
            finished.setdefault(m.event_ticker, {"occ": occ, "sides": []})["sides"].append(m)
        fin_events = list(finished)
        fin_bets: dict[str, list] = {}
        fin_scores: dict[str, str] = {}
        if fin_events:
            for bt in db.execute(select(PaperBet).where(
                    PaperBet.event_ticker.in_(fin_events))).scalars():
                fin_bets.setdefault(bt.event_ticker, []).append(bt)
            # a-perspective (the sorted-first / YES-side ticker) scoreline + sets,
            # so it renders in the same per-set grid as every other page
            a_tk = {ev: sorted(d["sides"], key=lambda m: m.ticker)[0].ticker
                    for ev, d in finished.items() if d["sides"]}
            if a_tk:
                from sqlalchemy import text as _sqltext
                by_tk = {r[0]: (r[1], r[2], r[3]) for r in db.execute(_sqltext(
                    "SELECT DISTINCT ON (market_ticker) market_ticker, scoreline, "
                    "sets_a, sets_b FROM match_score_log "
                    "WHERE market_ticker = ANY(:t) AND is_final "
                    "ORDER BY market_ticker, ts DESC"),
                    {"t": list(a_tk.values())}).all()}
                fin_scores = {ev: by_tk.get(tk) for ev, tk in a_tk.items()}

        pids: set[int] = set()
        for ev in list(events.values()) + list(finished.values()):
            pids.update(m.player_a_id for m in ev["sides"] if m.player_a_id)
        for sc in scen.values():
            pids.update((sc.player_id, sc.opponent_id))
        pids.discard(None)
        names = dict(db.execute(select(Player.id, Player.full_name)
                     .where(Player.id.in_(list(pids)))).all()) if pids else {}

    def _pick_html(sc, matched):
        if sc is None or sc.prematch_prob is None:
            return ('<span class="sub2">insufficient data</span>' if matched
                    else '<span class="sub2">—</span>')
        # derive the winner-pick: the ≥50% side (scenario stores the watch side)
        if sc.prematch_prob >= 0.5:
            pid, prob = sc.player_id, sc.prematch_prob
        else:
            pid, prob = sc.opponent_id, 1 - sc.prematch_prob
        return (f'<strong>{esc(names.get(pid) or "?")}</strong> '
                f'<span class="mono sub2">{prob:.0%}</span>')

    body_rows, n_picks, n_live = [], 0, 0
    for ev_ticker, ev in sorted(events.items(), key=lambda kv: kv[1]["occ"]):
        sides = sorted(ev["sides"], key=lambda m: m.ticker)
        if len(sides) < 2:
            continue
        a, b = sides[0], sides[1]
        na = names.get(a.player_a_id) or (a.raw or {}).get("yes_sub_title") or "?"
        nb = names.get(b.player_a_id) or (b.raw or {}).get("yes_sub_title") or "?"
        matched = bool(a.player_a_id and b.player_a_id)
        status = next(((m.raw or {}).get("_live_status") for m in sides
                       if (m.raw or {}).get("_live_status")), "")
        live = status_kind(status) == "live"
        n_live += live
        sc = scen.get(ev_ticker)
        if sc is not None and sc.prematch_prob is not None:
            n_picks += 1
        pick = _pick_html(sc, matched)
        link = (f'<a href="/match/{esc(ev_ticker)}">match data →</a>' if matched
                else '<span class="sub2">unmatched</span>')
        live_badge = '<span class="scen-flag">● LIVE</span> ' if live else ''
        body_rows.append(
            f'<tr><td class="mono sub2" style="white-space:nowrap">{pt(ev["occ"])}</td>'
            f'<td>{live_badge}{esc(na)} <span class="sub2">vs</span> {esc(nb)}</td>'
            f'<td>{pick}</td><td class="mono">{link}</td></tr>')

    n_games = len(body_rows)
    table = (f'<div class="tw"><table class="t"><tr><th>time</th><th>match</th>'
             f'<th>model pick</th><th></th></tr>{"".join(body_rows)}</table></div>'
             if body_rows else '<p class="empty">No upcoming or live games right now.</p>')

    # ---- finished today: winner, what bots bet (and how it settled), match data ----
    fin_rows = []
    for ev_ticker, ev in sorted(finished.items(), key=lambda kv: kv[1]["occ"],
                                reverse=True):
        sides = sorted(ev["sides"], key=lambda m: m.ticker)
        win_m = next((m for m in sides if m.result == "yes"), None)
        winner_id = win_m.player_a_id if win_m else None
        def _nm(m):
            return names.get(m.player_a_id) or (m.raw or {}).get("yes_sub_title") or "?"
        matched = all(m.player_a_id for m in sides[:2]) and len(sides) >= 2
        if winner_id is not None and matched:
            loser_m = next((m for m in sides if m is not win_m), None)
            result_lbl = (f'<strong style="color:var(--good)">{esc(_nm(win_m))} ✓</strong> '
                          f'<span class="sub2">def</span> {esc(_nm(loser_m)) if loser_m else "?"}')
        else:
            result_lbl = " <span class='sub2'>vs</span> ".join(esc(_nm(m)) for m in sides[:2])
        sc_tuple = fin_scores.get(ev_ticker)
        # per-set grid (a-perspective = sides[0], which matches the sorted order
        # above); no flags on this compact list, same layout as elsewhere
        score_html = ""
        if sc_tuple and sc_tuple[0] and len(sides) >= 2:
            score_html = ('<div style="margin-top:6px">'
                          + score_grid(sc_tuple, _nm(sides[0]).split()[-1],
                                       _nm(sides[1]).split()[-1], None, None)
                          + '</div>')
        # bots that bet + how each settled
        bets = fin_bets.get(ev_ticker, [])
        if bets:
            chips = []
            for bt in sorted(bets, key=lambda b: b.bot):
                mark, col = (("✓", "var(--good)") if bt.status == "won"
                             else ("•", "var(--muted)") if bt.status == "void"
                             else ("✕", "var(--accent)") if bt.status == "lost"
                             else ("·", "var(--muted)"))
                chips.append(f'<span title="{esc(bt.side.upper())} {bt.price_cents}¢ '
                             f'→ {esc(bt.status)}" style="color:{col}">'
                             f'{esc(bt.bot)} {mark}</span>')
            won = sum(1 for b in bets if b.status == "won")
            bots_html = (f'{" · ".join(chips)} '
                         f'<span class="sub2">({won}/{len(bets)} won)</span>')
        else:
            bots_html = '<span class="sub2">no bets</span>'
        link = (f'<a href="/match/{esc(ev_ticker)}">match data →</a>' if matched
                else '<span class="sub2">unmatched</span>')
        fin_rows.append(
            f'<tr><td class="mono sub2" style="white-space:nowrap">{pt(ev["occ"])}</td>'
            f'<td>{result_lbl}{score_html}</td><td>{bots_html}</td>'
            f'<td class="mono">{link}</td></tr>')
    finished_html = ""
    if fin_rows:
        finished_html = (
            f'<div class="blockhead" style="margin-top:26px"><h4>Finished today</h4>'
            f'<span class="aside">{len(fin_rows)} games · winner · bot bets</span></div>'
            f'<div class="rule"></div><div class="tw"><table class="t">'
            f'<tr><th>time</th><th>result</th><th>bots that bet</th><th></th></tr>'
            f'{"".join(fin_rows)}</table></div>')

    sub = (f"{n_games} upcoming/live · {n_picks} with a model read · "
           f"{n_live} live now · {len(fin_rows)} finished")
    legend = ('<p class="sub2" style="margin-top:12px">Model pick is the '
              'pre-match win probability from the day\'s model read. '
              '<em>Insufficient data</em> = below the responsible sample-size '
              'threshold — no pick rather than a fabricated one. In <strong>'
              'Finished</strong>, ✓/✕ marks how each bot\'s bet settled (hover '
              'for side &amp; price). Click <strong>match data</strong> for the '
              'full profile, score, and gameflow.</p>')
    return respond(request, "Today", "today",
                   pagehead("Today", "Today's slate", sub) + table + legend
                   + finished_html)


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
        quotes = _latest_quotes(db, tickers) if tickers else {}
        pids = {sc.player_id for sc, _ in rows} | {sc.opponent_id for sc, _ in rows}
        pids.discard(None)
        hands = dict(db.execute(select(Player.id, Player.hand)
                     .where(Player.id.in_(list(pids)))).all()) if pids else {}

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
        # scenario-type tags (fatigue / lefty / underdog value / live value)
        q = quotes.get(sc.market_ticker)
        price = (round((q[0] + q[1]) / 2)
                 if q and q[0] is not None and q[1] is not None else None)
        live_state = st.state if (st is not None and not st.stale) else None
        tags = scenario_tags(sc, hands.get(sc.player_id), hands.get(sc.opponent_id),
                             price, live_state)
        tag_row = (f'<div style="margin:4px 0 2px;display:flex;gap:6px;flex-wrap:wrap">'
                   f'{scenario_tag_html(tags)}</div>' if tags else "")
        data_tags = " ".join(k for k, _ in tags)
        return f"""<a class="card scard{' scard-best' if best else ''}" href="/scenario/{sc.id}"
data-tags="{data_tags}" style="text-decoration:none;color:inherit;display:block">
<div style="display:flex;align-items:center;justify-content:space-between;gap:12px">
<span class="kicker" style="margin:0">{best_badge}{esc(f.get('event_label') or 'gameflow plan')}</span>
{tag('outline', '◆', f'{sc.salience:.2f}')}</div>
{tag_row}
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


def scenario_narrative(pw: str, opp: str, prof_w, prof_o, facts: dict,
                       dec_prob: float, edge: float | None,
                       yes_stats: dict | None, opp_stats: dict | None,
                       sets_w, sets_o, is_live: bool) -> str:
    """The scenario 'read' as confident, flowing prose. Every number is drawn
    from REAL data — form streaks, deciding-set records (the stored fact block),
    and live in-match serve stats — never fabricated, and with no gambler's-
    fallacy claims (CLAUDE.md rule 3). Sentences appear only when their stat
    genuinely exists, so a thin match yields a short, honest read."""
    pwn = esc(pw.split()[-1]) if pw else "the pick"
    opn = esc(opp.split()[-1]) if opp else "the opponent"
    lines: list[str] = []

    # momentum / win streak
    if prof_w and prof_w.form and (prof_w.form.streak or 0) >= 3:
        lines.append(f"{pwn} rolls in on a <strong>{prof_w.form.streak}-match win "
                     f"streak</strong>.")
    if is_live and sets_w is not None and sets_o is not None and sets_w > sets_o:
        lines.append(f"{pwn} leads {sets_w}–{sets_o} in sets and carries the "
                     f"momentum toward the decider.")

    # deciding-set record — the crux for a gameflow play (from the fact block)
    dw = facts.get("decider_watch")
    if dw and (dw[0] + dw[1]) >= 3:
        w, l = dw
        lines.append(f"When it goes the distance, {pwn} is <strong>{w}–{l} "
                     f"({w/(w+l):.0%})</strong> in deciding sets.")
    do = facts.get("decider_opp")
    if do and (do[0] + do[1]) >= 3 and do[1] >= do[0]:
        lines.append(f"{opn}, by contrast, has lost {do[1]} of their last "
                     f"{do[0]+do[1]} deciding sets — the pressure points are "
                     f"where they leak.")

    # live in-match serve stats (only when the feed carries them)
    if yes_stats:
        dfw, dfo = _num(yes_stats, "double_faults"), _num(opp_stats or {}, "double_faults")
        acew = _num(yes_stats, "aces")
        if dfw is not None and dfo is not None:
            lines.append(f"This match, {pwn} has <strong>{int(dfw)} double "
                         f"fault{'' if dfw == 1 else 's'}</strong> to {opn}'s "
                         f"{int(dfo)} — the cleaner server under pressure.")
        if acew is not None and acew > 0:
            lines.append(f"{pwn} has already struck {int(acew)} "
                         f"ace{'' if acew == 1 else 's'}.")

    # the model read + an honest, confident close
    close = (f"The model makes {pwn} <strong>{dec_prob:.0%}</strong> to win from a "
             f"deciding set")
    if edge is not None and edge >= 3:
        close += (f", a <span style='color:var(--good);font-weight:700'>+{edge:.0f}% "
                  f"edge</span> on the live price")
    close += ". The numbers line up on our side — good luck. \U0001f340"
    lines.append(close)

    return "".join(f'<p style="margin:0 0 10px;font-size:15px;line-height:1.5;'
                   f'text-wrap:pretty">{ln}</p>' for ln in lines)


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
        # latest full score row (with the live serve-stat detail) for the analysis
        from bot.models import MatchScoreLog as _MSL
        live_row = db.execute(select(_MSL).where(
            _MSL.market_ticker == sc.market_ticker)
            .order_by(_MSL.ts.desc()).limit(1)).scalar()
        # profiles for the narrative read (win streak etc.)
        from bot.stats.profile import build_profile as _bp
        _as_of = datetime.now(timezone.utc).date() + timedelta(days=1)
        prof_w = _bp(db, sc.player_id, _as_of) if sc.player_id else None
        prof_o = _bp(db, sc.opponent_id, _as_of) if sc.opponent_id else None
        # both sides' live Kalshi odds + a short game log for full match context
        mkts = db.execute(select(KalshiMarket).where(
            KalshiMarket.event_ticker == sc.event_ticker)
            .order_by(KalshiMarket.ticker)).scalars().all()
        quotes = _latest_quotes(db, [m.ticker for m in mkts]) if mkts else {}
        gamelog = db.execute(sqltext(
            "SELECT scoreline, ts FROM match_score_log WHERE market_ticker = :t "
            "ORDER BY ts DESC LIMIT 6"), {"t": sc.market_ticker}).all()
        wp = db.get(Player, sc.player_id) if sc.player_id else None
        # rank among the day's scenarios by salience (how this one stacks up)
        from bot.models import Scenario as _Scen
        day_ids = db.execute(select(_Scen.id).where(
            _Scen.created_for == sc.created_for)
            .order_by(_Scen.salience.desc())).scalars().all()
    day_rank = (day_ids.index(sc.id) + 1) if sc.id in day_ids else None
    day_total = len(day_ids)

    # "log a bet" from the scenario page (same modal as the live board) + the
    # tag-autocomplete datalist. Only when signed in (csrf present).
    sess = request.get("session_cookie") or ""
    csrf = webauth.csrf_token(sess) if sess else ""
    _user = request.get("user")
    scen_bet_btn = (_bet_btn(sc.event_ticker, mkts[0], mkts[1], quotes, csrf,
                             label="＋ Log a bet")
                    if csrf and len(mkts) >= 2 else "")
    scen_datalist = ""
    if csrf and _user:
        with db_session() as _db:
            scen_datalist = _tags_datalist(_user_bet_tags(_db, _user["id"]))
    scen_bet_row = (f'<div style="padding-top:4px">{scen_bet_btn}</div>'
                    if scen_bet_btn else "")

    f = sc.facts or {}
    conf = f.get("model_confidence")
    from bot.prob.confidence import confidence_band
    cb = confidence_band(conf)

    # live context: the watch side's current price, live state, and edge
    watch_m = next((m for m in mkts if m.ticker == sc.market_ticker), None)
    watch_cents = _odds_cents(watch_m, quotes)[0] if watch_m else None
    est_state = st.state if st else None
    is_live = bool((sl and sl[0]) or (est_state and est_state != "final"))
    pw = (player or "").split()[-1] or "the pick"
    dec_prob = sc.model_prob_at_state
    sc_tags = scenario_tags(sc, wp.hand if wp else None, opp.hand if opp else None,
                            watch_cents, est_state if is_live else None)

    # ===== DEUCE Match layout: header + two-column (content + sticky Signal rail) =====
    MUT, FAINT, INK = "rgba(243,242,242,.56)", "rgba(243,242,242,.40)", "#f3f2f2"
    MONO = "font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace"
    CARD = "background:var(--surface);border:1px solid var(--divider);border-radius:10px;padding:16px"

    def _sechead(title, aside=""):
        return (f'<div style="display:flex;align-items:baseline;justify-content:space-between;'
                f'gap:16px;padding-bottom:8px;border-bottom:1px solid var(--divider-strong)">'
                f'<h4 style="margin:0;font-size:12px;font-weight:600;letter-spacing:.12em;'
                f'text-transform:uppercase">{title}</h4>'
                f'<span style="font-size:12px;{MONO};color:{FAINT}">{esc(aside)}</span></div>')

    # model-vs-price edge (drives the play + the Value tile)
    edge = (dec_prob * 100 - watch_cents) if watch_cents is not None else None
    if edge is not None:
        verdict = (f'<span style="color:var(--good);font-weight:700">+{edge:.0f}% value</span>'
                   if edge >= 3 else f'<span class="sub2">no edge right now ({edge:+.0f}%)</span>')
        price_line = f'Model {dec_prob:.0%} in a decider vs live {watch_cents}¢ → {verdict}'
    else:
        price_line = f'Model {dec_prob:.0%} in a decider · awaiting a live price'
    trig = trigger_html(sc, est_state, is_live,
                        float(watch_cents) if watch_cents is not None else None)

    # --- header block: breadcrumb, title, live/meta chips, right-side model summary ---
    live_chip = (
        f'<span style="display:inline-flex;align-items:center;gap:6px;height:22px;padding:0 8px;'
        f'border-radius:999px;font-size:10.5px;font-weight:600;background:rgba(53,194,110,.12);'
        f'color:#35c26e;border:1px solid rgba(53,194,110,.3)">● live · {esc(est_state or "in play")}</span>'
        if is_live else
        f'<span style="display:inline-flex;align-items:center;height:22px;padding:0 8px;'
        f'border-radius:999px;font-size:10.5px;font-weight:600;color:{MUT};'
        f'border:1px solid var(--divider-strong)">scheduled</span>')
    chips = scenario_tag_html(sc_tags) if sc_tags else ""
    header_html = (
        f'<div style="display:flex;align-items:center;gap:8px;font-size:12px;color:{FAINT};{MONO}">'
        f'<a href="/scenarios" style="color:{MUT}">Scenarios</a><span>/</span>'
        f'<span>{esc(f.get("event_label") or "")}</span></div>'
        f'<div style="margin-top:12px;display:flex;align-items:flex-end;justify-content:space-between;'
        f'gap:24px;flex-wrap:wrap">'
        f'<div><h2 style="margin:0;font-size:clamp(21px,6.5vw,30px);font-weight:600;letter-spacing:-.01em">'
        f'{esc(player)} <span style="color:{FAINT};font-weight:400">vs</span> {esc(opp_name)}</h2>'
        f'<div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:8px;align-items:center">'
        f'{live_chip}{chips}<span style="font-size:12px;color:{FAINT};{MONO}">'
        f'scheduled {pt(sc.scheduled_start)}</span></div></div>'
        f'<div style="text-align:right;{MONO};font-size:12px;color:{FAINT};line-height:1.7">'
        f'<div style="color:{MUT}">model {dec_prob:.0%} in a decider</div>'
        f'<div>entry band 35–65¢ · {("+%.0f%% value" % edge) if edge is not None and edge >= 3 else "watching"}</div>'
        f'<div><a href="/match/{esc(sc.event_ticker)}" style="color:{MUT}">↗ full match data</a></div>'
        f'</div></div>')

    # --- The play (accent left-border callout) ---
    play_html = (
        f'<section style="background:var(--surface);border:1px solid rgba(255,86,60,.28);'
        f'border-left:2px solid var(--accent);border-radius:0 12px 12px 0;padding:20px 24px">'
        f'<div style="display:flex;align-items:center;justify-content:space-between;gap:16px;'
        f'flex-wrap:wrap"><span style="font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;'
        f'color:var(--accent)">The play · {"entry live" if is_live else "pre-match"}</span>'
        f'<span style="font-size:11px;{MONO};color:{FAINT}">re-checked each game</span></div>'
        f'<div style="margin-top:8px;font-size:22px;font-weight:600">Back '
        f'<span style="color:var(--accent)">{esc(player)}</span> — entry 35–65¢</div>'
        f'<div style="margin-top:8px;font-size:13.5px;color:{MUT}">Trigger at the deciding set. '
        f'{price_line}. Sizing is yours; DEUCE never places trades.</div></section>')

    # --- live match (score grid + Kalshi odds + game log) ---
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
        grid = (score_grid(sl, pw, (opp_name or "").split()[-1],
                           wp.ioc if wp else None, opp.ioc if opp else None)
                if sl and sl[0] else "")
        live_html = (
            f'<section style="display:flex;flex-direction:column;gap:16px">'
            f'{_sechead("Live match", est_state or "current odds")}'
            f'<div style="{CARD}">{grid}'
            f'<div class="vshead" style="margin-top:8px">Kalshi odds<span>implied win %</span></div>'
            f'{odds_html or "<div class=sub2>no live price</div>"}{log_html}</div></section>')

    # --- the read: confident, stat-driven narrative prose (real numbers only),
    #     with the deterministic fact trail beneath it + live-state analysis ---
    _ys, _os = _oriented_live_stats(live_row.detail if live_row else None)
    narr = scenario_narrative(
        player or pw, opp_name, prof_w, prof_o, f, dec_prob or 0.0, edge, _ys, _os,
        live_row.sets_a if live_row else None,
        live_row.sets_b if live_row else None, is_live)
    read_html = (
        f'<section style="display:flex;flex-direction:column;gap:16px">'
        f'{_sechead("The read", "stat-driven · every number is real")}'
        f'<div style="{CARD}">{narr}'
        f'<div style="margin-top:8px;padding-top:12px;border-top:1px solid var(--divider)">'
        f'<div class="sub2" style="margin-bottom:6px;letter-spacing:.05em">THE FACTS BEHIND IT</div>'
        f'{read_list(sc.narrative)}</div></div>'
        f'{live_analysis_html(is_live=is_live, is_final=bool(live_row and live_row.is_final), sets_watch=live_row.sets_a if live_row else None, sets_opp=live_row.sets_b if live_row else None, scoreline=live_row.scoreline if live_row else (sl[0] if sl else None), st_state=est_state, prematch=sc.prematch_prob or 0.0, dec_prob=dec_prob or 0.0, triggers=f.get("triggers") or [], watch_cents=watch_cents, player=player or pw, opp_name=opp_name, detail=live_row.detail if live_row else None)}'
        f'</section>')

    # --- full match data (profiles, form, model-read, H2H, game-by-game) ---
    mv = _match_view(sc.event_ticker, include_scenario_plan=False, csrf=csrf)
    match_data_html = (
        f'<section style="display:flex;flex-direction:column;gap:16px">'
        f'{_sechead("Full match data", "profiles · form · style · game-by-game")}'
        f'{mv[2]}</section>') if mv is not None else ""

    # --- right rail: Signal panel (tiles + strength meter) ---
    def _tile(label, n, value, sub, color=INK, border=True):
        bb = "border-bottom:1px solid var(--divider);" if border else ""
        return (f'<div style="padding:14px 16px;{bb}">'
                f'<div style="display:flex;justify-content:space-between;gap:8px">'
                f'<span style="font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;'
                f'color:{MUT}">{label}</span>'
                f'<span style="font-size:10.5px;{MONO};color:{FAINT}">{n}</span></div>'
                f'<div style="margin-top:6px;font-size:clamp(24px,7vw,30px);font-weight:500;{MONO};'
                f'letter-spacing:-.02em;color:{color}">{value}</div>'
                f'<div style="margin-top:4px;font-size:11px;color:{FAINT}">{sub}</div></div>')
    if edge is not None:
        val_v = f"{edge:+.0f}%"
        val_color = "#35c26e" if edge >= 3 else MUT
        val_sub = f"model {dec_prob:.0%} vs {watch_cents}¢"
    else:
        val_v, val_color, val_sub = "—", MUT, "awaiting live price"
    band_sub = (f"live {watch_cents}¢" if watch_cents is not None else "no live price")
    signal_panel = (
        f'<div style="background:var(--surface);border:1px solid var(--divider);'
        f'border-radius:10px;overflow:hidden">'
        f'<div style="display:flex;justify-content:space-between;padding:12px 16px;'
        f'border-bottom:1px solid var(--divider)"><span style="font-size:11px;font-weight:600;'
        f'letter-spacing:.12em;text-transform:uppercase">Signal</span>'
        f'<span style="font-size:10.5px;{MONO};color:{FAINT}">@everyone</span></div>'
        f'{_tile("Probability", "pre-match", f"{sc.prematch_prob:.0%}", f"{esc(pw)} to win the match")}'
        f'{_tile("In a decider", "if set 3", f"{dec_prob:.0%}", "triggers the advisory", "#c7e14b")}'
        f'{_tile("Value", "live", val_v, val_sub, val_color)}'
        f'{_tile("Entry band", "live", "35–65¢", band_sub, "#ff563c")}'
        f'<div style="padding:14px 16px"><div style="font-size:10.5px;letter-spacing:.12em;'
        f'text-transform:uppercase;color:{MUT};margin-bottom:8px">Data-depth confidence</div>'
        f'{conf_meter(conf)}<div style="margin-top:6px;font-size:11px;color:{FAINT}">{esc(cb.note)}</div>'
        f'</div></div>')
    trigger_panel = (
        f'<div style="{CARD};display:flex;flex-direction:column;gap:12px">'
        f'<div style="font-size:11px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;'
        f'color:{MUT}">Trigger status</div><div>{trig}</div>'
        f'<p class="sub2" style="margin:0">Alerts the moment the deciding set (1-1) is reached '
        f'with {esc(pw)} favoured and priced 35–65¢.</p>'
        f'{scen_bet_row}'
        f'<div style="display:flex;gap:8px;padding-top:4px;align-items:center">'
        f'<a href="/match/{esc(sc.event_ticker)}" style="flex:1;text-align:center;height:32px;'
        f'line-height:32px;border-radius:6px;border:1px solid var(--divider-strong);color:var(--text);'
        f'font-size:12px;font-weight:500">Raw data ↗</a>'
        f'<span style="flex:1;text-align:center;font-size:12px">{kalshi_link(sc.market_ticker)}</span>'
        f'</div></div>')

    match_label = f.get("match") or sc.event_ticker
    body = (
        f'{header_html}'
        f'<div class="scen-cols" style="display:flex;flex-wrap:wrap;gap:24px;'
        f'align-items:flex-start;margin-top:24px">'
        f'<div style="flex:1 1 480px;min-width:0;display:flex;flex-direction:column;gap:32px">'
        f'{play_html}{live_html}{read_html}{match_data_html}</div>'
        f'<aside class="scen-rail" style="position:sticky;top:76px;flex:1 1 280px;'
        f'max-width:320px;min-width:0;'
        f'display:flex;flex-direction:column;gap:16px">{signal_panel}{trigger_panel}</aside></div>'
        f'{scen_datalist}')
    return respond(request, f"Scenario · {match_label}", "scenarios", body)


TP_LIMIT = 90  # take-profit limit price (cents) for the TP variant
USD_PER_UNIT = 10  # a testrun "unit" of stake = $10 (i.e. 10 Kalshi contracts)
MYBETS_USD_PER_UNIT = 500  # personal-ledger "unit" on the My Bets page = $500


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
    bot = request.match_info.get("bot", "leaderboard")  # /testrun home = leaderboard
    bot = {"t1": "pre", "t2": "pre"}.get(bot, bot)  # legacy 2-bot ids
    if bot == "leaderboard":
        return await bots_leaderboard(request)
    return await _testrun_view(request, bot=bot)


CLV_SIG_MIN_N = 20   # CLV samples needed before we'll call an edge significant


def _wilson_lo(w: int, n: int, z: float = 1.96) -> float | None:
    """Lower bound of the Wilson 95% score interval for a win rate — the
    honest 'at least this good' floor that shrinks the small-sample mirage."""
    if not n:
        return None
    phat = w / n
    denom = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * (((phat * (1 - phat)) + z * z / (4 * n)) / n) ** 0.5
    return max(0.0, (centre - margin) / denom)


def _inv_norm(p: float) -> float:
    """Inverse standard-normal CDF (Acklam approximation) — no scipy dependency."""
    import math
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow = 0.02425
    if p < plow:
        q = (-2 * math.log(p)) ** 0.5
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p <= 1 - plow:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q = (-2 * math.log(1 - p)) ** 0.5
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
            ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


def _bonferroni_z(k_tests: int, family: float = 0.05) -> float:
    """Two-sided z-threshold with a Bonferroni correction for k simultaneous
    tests — so with ~16 bots a single 'significant' result isn't a fluke."""
    k = max(1, k_tests)
    return _inv_norm(1 - (family / k) / 2)


def _clv_verdict(clv_list: list, z_thresh: float = 2.0) -> tuple:
    """Is a bot's mean CLV significantly ≠ 0? (one-sample t vs 0, with the
    caller's multiple-comparison-corrected z-threshold). Returns
    (mean, marker, colour, is_master_candidate). 'Significantly positive CLV' is
    the bar a signal must clear to feed the master bot. Under CLV_SIG_MIN_N
    samples → inconclusive, never a candidate."""
    import statistics
    n = len(clv_list)
    if n == 0:
        return (None, "", "var(--muted)", False)
    mean = sum(clv_list) / n
    if n < CLV_SIG_MIN_N:
        return (mean, "·", "var(--muted)", False)          # too few to judge
    s = statistics.stdev(clv_list) if n > 1 else 0.0
    t = (mean / (s / n ** 0.5)) if s > 0 else (0.0 if mean == 0 else 9.9)
    if abs(t) < z_thresh:
        return (mean, "~", "var(--muted)", False)          # not significant
    if mean > 0:
        return (mean, "✓", "var(--good)", True)            # beats the close
    return (mean, "✗", "var(--accent)", False)             # trails the close


async def calibration(request: web.Request) -> web.Response:
    """Model reliability over time: predicted win probability vs actual win rate.
    Sourced from settled scenario reads (prematch_prob for the watch side vs
    whether that side won). The one property the whole edge thesis relies on —
    tracked so drift is visible."""
    from bot.models import KalshiMarket, Scenario

    with db_session() as db:
        rows = db.execute(
            select(Scenario.created_for, Scenario.prematch_prob, KalshiMarket.result)
            .join(KalshiMarket, KalshiMarket.ticker == Scenario.market_ticker)
            .where(KalshiMarket.result.in_(["yes", "no"]),
                   Scenario.prematch_prob.is_not(None))).all()
    pts = [(float(p), 1 if res == "yes" else 0) for _, p, res in rows]

    def _reliability(sample):
        buckets = []
        brier = 0.0
        for lo in range(0, 100, 10):
            hi = lo + 10
            b = [(x, y) for x, y in sample if lo / 100 <= x < hi / 100
                 or (hi == 100 and x == 1.0)]
            if b:
                pred = sum(x for x, _ in b) / len(b)
                act = sum(y for _, y in b) / len(b)
                buckets.append((f"{lo}-{hi}%", len(b), pred, act))
        brier = (sum((x - y) ** 2 for x, y in sample) / len(sample)) if sample else None
        # weighted mean |predicted - actual| across buckets
        tot = sum(n for _, n, _, _ in buckets)
        cal_err = (sum(n * abs(pr - ac) for _, n, pr, ac in buckets) / tot) if tot else None
        return buckets, brier, cal_err

    buckets, brier, cal_err = _reliability(pts)
    rel_rows = "".join(
        f'<tr><td class="mono">{lbl}</td><td class="mono sub2">{n}</td>'
        f'<td class="mono">{pred:.0%}</td>'
        f'<td class="mono" style="color:{"var(--good)" if abs(pred-act)<=0.05 else "var(--warning)" if abs(pred-act)<=0.10 else "var(--accent)"}">{act:.0%}</td>'
        f'<td class="mono sub2">{(act-pred)*100:+.0f}pt</td></tr>'
        for lbl, n, pred, act in buckets)

    # by-month drift: calibration error per generation month
    from collections import defaultdict
    by_month = defaultdict(list)
    for d, p, res in rows:
        by_month[d.strftime("%Y-%m")].append((float(p), 1 if res == "yes" else 0))
    month_rows = ""
    for mo in sorted(by_month):
        _, mbrier, mcal = _reliability(by_month[mo])
        month_rows += (f'<tr><td class="mono">{mo}</td>'
                       f'<td class="mono sub2">{len(by_month[mo])}</td>'
                       f'<td class="mono">{mbrier:.3f}</td>'
                       f'<td class="mono" style="color:{"var(--good)" if mcal and mcal<=0.05 else "var(--warning)"}">{mcal:.1%}</td></tr>'
                       if mbrier is not None and mcal is not None else "")

    verdict = ("insufficient settled predictions yet" if not pts else
               f"<strong style=\"color:{'var(--good)' if cal_err and cal_err <= 0.05 else 'var(--warning)'}\">"
               f"mean calibration gap {cal_err:.1%}</strong> · Brier {brier:.3f} · "
               f"{len(pts)} settled predictions — "
               f"{'well-calibrated' if cal_err and cal_err <= 0.05 else 'watch for drift'}")

    body = pagehead("Strategy Lab", "Model calibration",
                    "predicted win % vs actual — the reliability check") + f"""
<p class="prose" style="margin:0 0 14px">Each settled scenario contributes its
pre-match probability (for the watch side) and whether that side won. A
calibrated model's predicted % matches the actual win rate in every bucket —
the property the edge thesis depends on. {verdict}.</p>
<div class="blockhead"><h4>Reliability by probability bucket</h4>
<span class="aside">predicted vs actual</span></div><div class="rule"></div>
<div class="tw"><table class="t"><tr><th>predicted</th><th>n</th>
<th>mean pred</th><th>actual won</th><th>gap</th></tr>
{rel_rows or '<tr><td colspan="5" class="empty">No settled predictions yet.</td></tr>'}
</table></div>
<div class="blockhead" style="margin-top:26px"><h4>Calibration drift by month</h4>
<span class="aside">lower gap = better; watch the trend</span></div><div class="rule"></div>
<div class="tw"><table class="t"><tr><th>month</th><th>n</th><th>Brier</th>
<th>calibration gap</th></tr>{month_rows or '<tr><td colspan="4" class="empty">—</td></tr>'}
</table></div>"""
    return respond(request, "Calibration", "testrun", body)


async def features(request: web.Request) -> web.Response:
    """Variable-strength monitor: for each candidate model variable, its
    out-of-sample lift (ΔBrier / Δlog-loss) over the base surface-Elo, on an
    EXPANDING window (fixed anchor → today) so the test sample grows daily.
    Snapshots are written each ingest; this reads them so drift/stabilization
    is visible as the sample accumulates."""
    from bot.prob.feature_eval import load_snapshots
    with db_session() as db:
        snaps = load_snapshots(db, limit=40)
    if not snaps or "features" not in snaps[-1]:
        body = pagehead("Model", "Variable strength",
                        "expanding-window feature monitor") + (
            '<section class="block"><p class="prose">No snapshot yet — the daily '
            'ingest writes the first one on its next run. Each snapshot measures, '
            'out-of-sample, how much each variable improves the model beyond the '
            'base surface-Elo, on a window that grows every day.</p></section>')
        return respond(request, "Variable strength", "features", body)

    latest = snaps[-1]
    feats = latest["features"]
    order = sorted(feats, key=lambda f: -feats[f]["d_brier"])

    def pc(v):
        return ("var(--good)" if v > 0.0005 else
                "var(--accent)" if v < -0.0005 else "var(--muted)")
    rows = []
    for f in order:
        v = feats[f]
        verdict = ("real" if v["d_brier"] > 0.001 else
                   "marginal" if v["d_brier"] > 0.0003 else
                   "hurts" if v["d_brier"] < -0.0003 else "no signal")
        rows.append(
            f'<tr><td class="pname">{esc(f)}</td>'
            f'<td class="mono" style="color:{pc(v["d_brier"])}">{v["d_brier"] * 100:+.3f}%</td>'
            f'<td class="mono" style="color:{pc(v["d_logloss"])}">{v["d_logloss"] * 100:+.3f}%</td>'
            f'<td class="mono sub2">{v["beta"]:+.4f}</td>'
            f'<td class="mono sub2">{v["coverage"] * 100:.0f}%</td>'
            f'<td class="sub2">{verdict}</td></tr>')

    # trend: ΔBrier per variable across the recent snapshots (watch it settle)
    trend_days = [s["as_of"][5:] for s in snaps if "features" in s]
    trend_rows = []
    for f in order:
        cells = "".join(
            f'<td class="mono sub2" style="color:{pc(s["features"].get(f, {}).get("d_brier", 0))}">'
            f'{s["features"].get(f, {}).get("d_brier", 0) * 100:+.2f}</td>'
            for s in snaps if "features" in s)
        trend_rows.append(f'<tr><td class="pname">{esc(f)}</td>{cells}</tr>')
    n_cols = len([s for s in snaps if "features" in s])
    nsize = "".join(f'<td class="mono sub2">{s.get("n_test", 0) // 1000}k</td>'
                    for s in snaps if "features" in s)

    body = pagehead("Model", "Variable strength",
                    f"expanding window · {latest.get('n_test', 0):,} test matches · "
                    f"base Brier {latest.get('base_brier', 0):.4f}") + f"""
<p class="prose" style="margin:0 0 14px">Out-of-sample lift each candidate variable
adds <strong>beyond the base surface-Elo</strong> (higher ΔBrier = better). Measured
on an expanding window from {esc(latest.get('anchor', '—'))} to today, so the sample
grows daily — a variable is only trustworthy once its lift <em>and</em> its coefficient
settle. Nothing here is wired into the live model; it's the evidence gate for that
decision. <a href="/calibration">calibration ↗</a></p>
<section class="block"><div class="blockhead"><h4>Latest snapshot</h4>
<span class="aside">{esc(latest['as_of'])}</span></div><div class="rule"></div>
<div class="tw"><table class="t"><tr><th>variable</th><th>ΔBrier</th><th>Δlog-loss</th>
<th>coef</th><th>coverage</th><th>verdict</th></tr>{''.join(rows)}</table></div></section>
<section class="block" style="margin-top:18px"><div class="blockhead"><h4>Trend · ΔBrier (%)</h4>
<span class="aside">{n_cols} snapshots · left→right = older→newer · watch it settle</span></div>
<div class="rule"></div><div class="tw"><table class="t">
<tr><th>variable</th>{''.join(f'<th class="mono sub2">{esc(d)}</th>' for d in trend_days)}</tr>
{''.join(trend_rows)}
<tr><td class="sub2">test n</td>{nsize}</tr></table></div></section>"""
    return respond(request, "Variable strength", "features", body)


# Leaderboard is expensive (aggregates ~millions of market_ticks rows), so it's
# cached and refreshed OFF the event loop — the 7s auto-refresh must never fire
# the heavy query on the loop (that saturated it → app-wide "upstream error").
_LB_CACHE: dict = {"at": None, "body": None, "refreshing": False}
_LB_FRESH_S = 120


async def bots_leaderboard(request: web.Request) -> web.Response:
    import asyncio
    body = _LB_CACHE["body"]
    age = ((datetime.now(timezone.utc) - _LB_CACHE["at"]).total_seconds()
           if _LB_CACHE["at"] else 1e9)
    if body is None:
        # cold start: compute once, off-loop so it can't block other requests
        body = await asyncio.to_thread(_leaderboard_body)
        _LB_CACHE.update(at=datetime.now(timezone.utc), body=body)
    elif age > _LB_FRESH_S and not _LB_CACHE["refreshing"]:
        _LB_CACHE["refreshing"] = True

        async def _refresh():
            try:
                b = await asyncio.to_thread(_leaderboard_body)
                _LB_CACHE.update(at=datetime.now(timezone.utc), body=b)
            except Exception as e:  # keep serving stale on failure
                log.error("leaderboard refresh failed", error=str(e))
            finally:
                _LB_CACHE["refreshing"] = False
        asyncio.create_task(_refresh())      # serve stale now, swap in when ready
    return respond(request, "Bots Leaderboard", "testrun", body)


def _leaderboard_body() -> str:
    """Build the leaderboard HTML (heavy, synchronous). Runs in a worker thread
    via bots_leaderboard; opens its own DB session inside the thread."""
    from bot.models import KalshiMarket, PaperBet
    from bot.track import advisory_outcome, clv_cents
    from bot.t2 import BOTS, MIN_BASIS, bot_state

    with db_session() as db:
        rows = db.execute(
            select(PaperBet, KalshiMarket.result, KalshiMarket.close_yes_cents)
            .join(KalshiMarket, KalshiMarket.ticker == PaperBet.market_ticker)).all()
        # learned state for each self-improving bot (None for fixed bots)
        si_state = {b: bot_state(db, b) for b in BOTS if BOTS[b].get("si")}
        # max bid our side reached during each match → whether the 90¢ take-profit
        # would have triggered (same signal the per-bot page uses)
        touched: dict[str, tuple] = {}
        if rows:
            from sqlalchemy import text as _sq
            tks = list({b.market_ticker for b, _, _ in rows})
            since = min(b.created_at for b, _, _ in rows)
            for r in db.execute(_sq(
                "SELECT market_ticker, max(yes_bid) yb, max(no_bid) nb "
                "FROM market_ticks WHERE market_ticker = ANY(:t) AND kind='quote' "
                "AND ts >= :since GROUP BY market_ticker"),
                    {"t": tks, "since": since}).all():
                touched[r[0]] = (r[1], r[2])

    def _tp_pnl(o, side, price, u, ticker):
        """P&L under the 90¢ take-profit exit: sell at 90 if our side ever bid
        there (or it wins anyway), else the full loss. Caps upside at 90."""
        if price >= TP_LIMIT:  # already ≥90, nothing to take
            return (100 - price) * u if o == "won" else -price * u
        yb, nb = touched.get(ticker, (None, None))
        hit = ((side == "yes" and (yb or 0) >= TP_LIMIT)
               or (side == "no" and (nb or 0) >= TP_LIMIT))
        return (TP_LIMIT - price) * u if (hit or o == "won") else -price * u

    per = {b: {"w": 0, "l": 0, "tpw": 0, "tpl": 0, "open": 0, "pnl": 0, "tp": 0,
               "stake": 0, "clv": [], "roi_list": [], "roi_tp_list": [],
               "px_sum": 0, "n_all": 0, "first": None, "move": [],
               "exit_vc": []} for b in BOTS}
    for b, res, close in rows:
        if b.bot not in per:
            continue
        d = per[b.bot]
        d["px_sum"] += b.price_cents; d["n_all"] += 1  # avg buy-in over ALL bets
        mv = (b.reasoning or {}).get("market_move")  # line drift since the open
        if mv is not None:
            d["move"].append(mv)
        if b.created_at and (d["first"] is None or b.created_at < d["first"]):
            d["first"] = b.created_at  # earliest bet placed
        o = advisory_outcome(b.side, res)
        u = b.units or 1.0
        if o == "won":
            d["w"] += 1
        elif o == "lost":
            d["l"] += 1
        else:
            d["open"] += 1; continue
        # profit per exit (cents ×units). Units are derived from these in the row
        # as profit ÷ $-per-unit, so the units column exactly tracks its profit.
        hold = (100 - b.price_cents) * u if o == "won" else -b.price_cents * u
        d["pnl"] += hold
        tp_pnl_i = _tp_pnl(o, b.side, b.price_cents, u, b.market_ticker)
        d["tp"] += tp_pnl_i
        d["stake"] += b.price_cents * u
        # per-bet return on stake (won: (100-price)/price · lost: -1) → ROI CI
        d["roi_list"].append((100 - b.price_cents) / b.price_cents if o == "won"
                             else -1.0)
        # take-profit record: a win if it settled a winner OR our side ever hit
        # 90¢ (we'd have sold), a loss otherwise — a DIFFERENT record from hold
        if b.price_cents >= TP_LIMIT:
            tp_win = o == "won"
        else:
            _yb, _nb = touched.get(b.market_ticker, (None, None))
            _hit = ((b.side == "yes" and (_yb or 0) >= TP_LIMIT)
                    or (b.side == "no" and (_nb or 0) >= TP_LIMIT))
            tp_win = _hit or o == "won"
            # market-value read on the 90¢ exit: for bets that actually SOLD at
            # 90¢, how the 90¢ exit compares to where our side ultimately closed.
            # +ve = sold above the close (good sell); -ve = exited early.
            if _hit and close is not None:
                our_close = close if b.side == "yes" else 100 - close
                d["exit_vc"].append(TP_LIMIT - our_close)
        d["tpw" if tp_win else "tpl"] += 1
        d["roi_tp_list"].append(tp_pnl_i / (b.price_cents * u))
        c = clv_cents(b.side, b.price_cents, close)
        if c is not None:
            d["clv"].append(c)

    # multiple-comparison correction: the z-bar scales with how many bots have
    # enough data to be tested, so one lucky "significant" bot isn't promoted
    _k_tested = sum(1 for b in BOTS if len(per[b]["clv"]) >= CLV_SIG_MIN_N)
    _zsig = _bonferroni_z(_k_tested)

    def rowhtml(bid):
        m = BOTS[bid]
        d = per[bid]
        n = d["w"] + d["l"]
        roi = d["pnl"] / d["stake"] if d["stake"] else None
        clv = sum(d["clv"]) / len(d["clv"]) if d["clv"] else None
        def _pc(v):
            return ("var(--good)" if v > 0 else
                    "var(--accent)" if v < 0 else "var(--muted)")
        pcol = _pc(d["pnl"])
        # win-rate cell, shared by both exits: greyed "provisional" on a thin
        # sample, else the point estimate + its Wilson 95% floor (stat honesty)
        def _wr_cell(w, l):
            nn = w + l
            if not nn:
                return "—", "var(--muted)"
            wr = w / nn
            prov = nn < CLV_SIG_MIN_N
            col = ("var(--muted)" if prov else
                   "var(--good)" if wr >= 0.7 else
                   "var(--warning)" if wr >= 0.6 else "var(--text)")
            s = (f"{wr:.0%} <span class='sub2'>prov.</span>" if prov else
                 f"{wr:.0%} <span class='sub2'>(≥{_wilson_lo(w, nn):.0%})</span>")
            return s, col
        wr_s, wrc = _wr_cell(d["w"], d["l"])
        tpwr_s, tpwrc = _wr_cell(d["tpw"], d["tpl"])
        # CLV verdict: does this bot BEAT THE CLOSE, significantly? (the real
        # experiment question — win rate is noisy, CLV is the leading signal)
        _clv_mean, clv_mark, clvc, _cand = _clv_verdict(d["clv"], _zsig)
        avg_buy = d["px_sum"] / d["n_all"] if d["n_all"] else None
        buy_s = f"{avg_buy:.0f}¢" if avg_buy is not None else "—"
        # units = dollar profit ÷ $-per-unit stake, shown alongside each profit
        # so the unit increase reads next to it (constant-scale mirror of $)
        def _profit_cell(pnl):
            if not n:
                return "—"
            return (f'${dol(pnl):+.2f} '
                    f'<span class="sub2">{dol(pnl) / USD_PER_UNIT:+.1f}u</span>')
        profit_s = _profit_cell(d["pnl"])
        tp_s = _profit_cell(d["tp"])
        # which exit wins for this bot — a green ▲ on the better one
        better_hold = n and d["pnl"] > d["tp"]
        better_tp = n and d["tp"] > d["pnl"]
        # ROI with a 95% CI (normal approx over per-bet returns) — so a +ROI on
        # a thin sample shows its uncertainty rather than reading as settled
        import statistics

        def _roi_cell(val, per_bet):
            if val is None:
                return "—"
            s = f"{val:+.1%}"
            if len(per_bet) >= 5:
                ci = 1.96 * statistics.stdev(per_bet) / len(per_bet) ** 0.5 if len(per_bet) > 1 else 0
                s += f' <span class="sub2">±{ci:.0%}</span>'
            return s
        roi_s = _roi_cell(roi, d["roi_list"])
        roi_tp = d["tp"] / d["stake"] if d["stake"] else None
        roi_tp_s = _roi_cell(roi_tp, d["roi_tp_list"])
        # take-profit record (differs from hold: spikes to 90¢ that later lost
        # count as tp wins) + its win rate
        ntp = d["tpw"] + d["tpl"]
        tp_rec_s = f'{d["tpw"]}-{d["tpl"]}' if ntp else "—"
        clv_s = (f"{clv:+.1f}¢ {clv_mark}" if clv is not None else "—")
        # market-value of the 90¢ take-profit exit vs where our side closed
        evc = d["exit_vc"]
        evc_avg = sum(evc) / len(evc) if evc else None
        evc_s = (f'{evc_avg:+.0f}¢ <span class="sub2">·{len(evc)}</span>'
                 if evc_avg is not None else "—")
        evc_c = _pc(evc_avg) if evc_avg is not None else "var(--muted)"
        # avg line movement on our side since the open (negative = we bought
        # into fades — the adverse-selection tell)
        mv = sum(d["move"]) / len(d["move"]) if d["move"] else None
        mv_s = f"{mv:+.1f}¢" if mv is not None else "—"
        mvc = ("var(--good)" if mv and mv > 0 else
               "var(--accent)" if mv and mv < 0 else "var(--muted)")
        first_s = d["first"].astimezone(PACIFIC).strftime("%b %d") if d["first"] else "—"
        # self-improving status: learned vN (adapted) vs default (settled/threshold)
        if m["si"]:
            st = si_state.get(bid) or {}
            ver = st.get("version", "") or ""
            it = ver.split(".")[-1] if "." in ver else "0"
            if it not in ("", "0"):
                learns = f' · <span class="tag tag-good">learned v{it}</span>'
            else:
                learns = f' · <span class="sub2">default ({n}/{MIN_BASIS})</span>'
        else:
            learns = ""
        return (f'<tr><td><a href="/testrun/{bid}"><strong>{esc(m["label"])}</strong></a>'
                f'{learns}<div class="sub2 mono">first {first_s}</div></td>'
                f'<td class="mono sub2">{buy_s}</td>'
                # ── Hold ──
                f'<td class="mono gsep">{d["w"]}-{d["l"]}</td>'
                f'<td class="mono" style="color:{wrc}">{wr_s}</td>'
                f'<td class="mono" style="color:{pcol};font-weight:800">{profit_s}'
                f'{" ▲" if better_hold else ""}</td>'
                f'<td class="mono">{roi_s}</td>'
                # ── 90¢ Take-Profit ──
                f'<td class="mono gsep">{tp_rec_s}</td>'
                f'<td class="mono" style="color:{tpwrc}">{tpwr_s}</td>'
                f'<td class="mono" style="color:{_pc(d["tp"])};font-weight:800">{tp_s}'
                f'{" ▲" if better_tp else ""}</td>'
                f'<td class="mono">{roi_tp_s}</td>'
                f'<td class="mono" style="color:{evc_c}">{evc_s}</td>'
                # ── Signal ──
                f'<td class="mono gsep" style="color:{clvc}">{clv_s}</td>'
                f'<td class="mono" style="color:{mvc}">{mv_s}</td>'
                f'<td class="mono sub2 gsep">{d["open"]}</td></tr>')

    group_row = ('<tr><th colspan="2"></th>'
                 '<th class="grp gsep" colspan="4">Hold</th>'
                 '<th class="grp gsep" colspan="5">90¢ Take-Profit</th>'
                 '<th class="grp gsep" colspan="2">Signal</th>'
                 '<th class="gsep"></th></tr>')
    header_row = (group_row +
                  "<tr><th>bot</th><th>avg buy-in</th>"
                  '<th class="gsep">record</th><th>win rate</th>'
                  '<th>profit · units</th><th>ROI</th>'
                  '<th class="gsep">record</th><th>win rate</th>'
                  '<th>profit · units</th><th>ROI</th><th>vs close</th>'
                  '<th class="gsep">CLV</th><th>adv move</th>'
                  '<th class="gsep">open</th></tr>')

    def _settled(b):
        return per[b]["w"] + per[b]["l"]

    def _roi(b):
        d = per[b]
        return d["pnl"] / d["stake"] if d["stake"] else None

    # board 1: most-proven first (biggest settled record)
    order = sorted(BOTS, key=lambda b: -_settled(b))

    # board 2: ranked by ROI, only bots past a settled floor (so a lucky 3-0 bot
    # doesn't top a proven −ROI bot)
    ROI_MIN_SETTLED = 10
    roi_pool = [b for b in BOTS if _settled(b) >= ROI_MIN_SETTLED and _roi(b) is not None]
    roi_order = sorted(roi_pool, key=lambda b: -_roi(b))
    roi_rows = ("".join(rowhtml(b) for b in roi_order) or
                f'<tr><td colspan="14" class="empty">No bot has {ROI_MIN_SETTLED}+ '
                f'settled bets yet.</td></tr>')

    # board 3: ranked by CLV — the true edge question (does the bot beat the
    # closing line?). Only bots past the significance-test bar, so a thin sample
    # can't top the board on noise.
    def _clv_avg(b):
        c = per[b]["clv"]
        return sum(c) / len(c) if c else None
    clv_pool = [b for b in BOTS if len(per[b]["clv"]) >= CLV_SIG_MIN_N]
    clv_order = sorted(clv_pool, key=lambda b: -_clv_avg(b))
    clv_rows = ("".join(rowhtml(b) for b in clv_order) or
                f'<tr><td colspan="14" class="empty">No bot has {CLV_SIG_MIN_N}+ '
                f'bets with a closing line yet.</td></tr>')

    # forward-selection readout: which signals BEAT THE CLOSE with significance —
    # the only bots that should feed the master. Honest when nothing qualifies.
    n_established = sum(1 for b in BOTS if _settled(b) >= CLV_SIG_MIN_N)
    cands = []
    for bid in BOTS:
        mean, _mk, _c, is_cand = _clv_verdict(per[bid]["clv"], _zsig)
        if is_cand:
            cands.append((bid, mean, _settled(bid)))
    cands.sort(key=lambda x: -x[1])
    if cands:
        chips = " · ".join(
            f'<strong>{esc(BOTS[b]["label"])}</strong> '
            f'<span class="mono" style="color:var(--good)">+{m:.1f}¢</span> '
            f'<span class="sub2">(n={n})</span>' for b, m, n in cands)
        master = (f'<p style="margin:0">✓ <strong>{len(cands)} signal'
                  f'{"s" if len(cands) != 1 else ""} beating the close</strong> '
                  f'(significant positive CLV): {chips}. These are the '
                  f'forward-selection candidates for the master bot.</p>')
        mcol = "var(--good)"
    else:
        master = (f'<p style="margin:0">No signal has a statistically significant '
                  f'positive CLV yet ({n_established} bot'
                  f'{"s" if n_established != 1 else ""} past the {CLV_SIG_MIN_N}-bet '
                  f'bar). The master bot is built by forward-selecting signals that '
                  f'<em>beat the close</em> — none qualify yet, so keep accumulating '
                  f'rather than building one from noise.</p>')
        mcol = "var(--muted)"
    master_html = (
        f'<div style="border-left:3px solid {mcol};padding:10px 14px;margin:0 0 18px;'
        f'background:var(--surface);border-radius:0 8px 8px 0">'
        f'<div class="sub2" style="text-transform:uppercase;letter-spacing:.07em;'
        f'font-size:10.5px;font-weight:700;margin-bottom:4px">Master-bot candidates</div>'
        f'{master}</div>')

    body = pagehead("Strategy Lab", "Bots — Leaderboard",
                    "all side by side · hold vs 90¢ take-profit") + f"""
<p class="prose" style="margin:0 0 14px">Bots on the same model, differing by
<strong>when</strong> they bet and <strong>which one indicator</strong> gates them
(tier · confidence · line-move · …) — so each bot's record isolates that signal.
<strong>CLV</strong> (entry vs the closing line) is the primary metric: ✓ = beats
the close significantly, ✗ = trails it, ~ = not significant, · = too few bets.
Win rate shows its Wilson 95% floor, or <em>prov.</em> until {CLV_SIG_MIN_N}
settled bets. ▲ marks the better exit (hold vs 90¢ take-profit). <strong>vs close</strong>
= market value of the 90¢ exit (mean 90¢ − our-side close over bets that sold there;
+ve = sold above the close). Click a name for
its page · <a href="/calibration">model calibration ↗</a></p>
{master_html}
<div class="blockhead"><h4>All bots · most-proven first</h4>
<span class="aside">sorted by settled-bet count</span></div>
<div class="tw"><table class="t lb">
{header_row}
{''.join(rowhtml(b) for b in order)}
</table></div>
<div class="blockhead" style="margin-top:28px"><h4>Ranked by ROI</h4>
<span class="aside">profit ÷ stake (hold basis) · only bots with ≥{ROI_MIN_SETTLED} settled bets</span></div>
<div class="tw"><table class="t lb">
{header_row}
{roi_rows}
</table></div>
<div class="blockhead" style="margin-top:28px"><h4>Ranked by CLV</h4>
<span class="aside">entry vs closing line · the true edge signal · only bots with ≥{CLV_SIG_MIN_N} priced bets</span></div>
<div class="tw"><table class="t lb">
{header_row}
{clv_rows}
</table></div>"""
    return body


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
        # units = net profit in $-per-unit stake units, so it MIRRORS the dollar
        # profit exactly (matches the leaderboard convention) — more profit is
        # always more units
        un = dol(pc) / USD_PER_UNIT
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

    def _exit_vs_close():
        """Market-value read on the 90¢ take-profit exit: for bets that actually
        SOLD at 90¢ (our side touched 90¢ after a sub-90¢ entry), compare the 90¢
        exit to where our side ultimately closed on Kalshi. Positive = we sold
        ABOVE the close (the market fell after our exit — a good sell); negative =
        we sold below the close (left value on the table by exiting early)."""
        vals = []
        for b, _ in finished:
            if (b.price_cents or 0) >= TP_LIMIT:
                continue  # never a TP exit — behaves as hold
            yb, nb = touched.get(b.market_ticker, (None, None))
            hit = (b.side == "yes" and (yb or 0) >= TP_LIMIT) or \
                  (b.side == "no" and (nb or 0) >= TP_LIMIT)
            if not hit:
                continue  # our side never reached 90¢ → no exit happened
            cl = closes.get(b.market_ticker)
            if cl is None:
                continue
            our_close = cl if b.side == "yes" else 100 - cl
            vals.append(TP_LIMIT - our_close)
        if not vals:
            return None, 0
        return sum(vals) / len(vals), len(vals)

    exit_avg, exit_n = _exit_vs_close()

    def _mode_col(label: str, sub: str, w, l, pc, un, roi, extra: str = "") -> str:
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
                + extra
                + '</div>')
    if exit_avg is not None:
        ecol = ("var(--good)" if exit_avg > 0 else
                "var(--accent)" if exit_avg < 0 else "var(--text)")
        exit_row = ('<div class="vsrow"><span class="k">Exit vs close</span>'
                    f'<span class="v mono" style="color:{ecol}">'
                    f'{exit_avg:+.0f}¢ <span class="muted">·{exit_n}</span></span></div>')
    else:
        exit_row = ('<div class="vsrow"><span class="k">Exit vs close</span>'
                    '<span class="v mono muted">— ·0</span></div>')
    scoreboard = (f'<div class="vsgrid">'
                  f'{_mode_col("Hold", "ride to settlement (100¢/0¢)", *mode_stats(False))}'
                  f'{_mode_col("90¢ Take-Profit", "limit exit at 90¢", *mode_stats(True), extra=exit_row)}'
                  f'</div>'
                  f'<p class="muted" style="margin:.4rem 0 0;font-size:.75rem">'
                  f'Exit vs close = mean(90¢ − our-side close) over the {exit_n} bet'
                  f'{"" if exit_n == 1 else "s"} that actually sold at 90¢. '
                  f'Positive = sold above where the market closed (good exit); '
                  f'negative = sold early and left value.</p>')
    avg_buy = (sum(b.price_cents for b, _ in bets) / len(bets)) if bets else None
    shared = statstrip([
        ("Settled", str(nfin), "same picks · both exits"),
        ("Open", str(len(open_bets)), "awaiting result"),
        ("Avg buy-in", f"{avg_buy:.0f}¢" if avg_buy is not None else "—",
         f"mean entry · {len(bets)} bet{'' if len(bets) == 1 else 's'}"),
        ("CLV", f'<span style="color:{clv_color}">{avg_clv:+.1f}¢</span>'
         if avg_clv is not None else "—",
         f"entry vs close · beat {beat}/{len(clv_vals)}" if clv_vals
         else "vs match-start line"),
        ("Running", f"{days}d", "since first bet · target 70%"),
    ], cols=5)
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

    # ---- full bet ledger: every bet this bot has made, newest first ----
    def _res_badge(b):
        st = b.status
        if st == "won":
            return tag("good", "✓", "won")
        if st == "lost":
            return tag("accent", "✕", "lost")
        if st == "void":
            return tag("neutral", "–", "void")
        return '<span class="tag tag-neutral">• open</span>'

    def _pnlc(v):
        return ("var(--good)" if (v or 0) > 0 else
                "var(--accent)" if (v or 0) < 0 else "var(--muted)")

    ledger_rows = []
    for b, player in bets:
        mt = (b.reasoning or {}).get("match") or (player or "—")
        if len(mt) > 34:
            mt = mt[:33] + "…"
        d = (b.created_at.astimezone(PACIFIC).strftime("%m-%d %H:%M")
             if b.created_at else "—")
        clv = clv_by_id.get(b.id)
        pnl = b.pnl_cents
        ledger_rows.append(
            f'<tr><td class="mono sub2">{d}</td>'
            f'<td>{esc(mt)}</td>'
            f'<td class="mono">{esc(b.side or "—")}</td>'
            f'<td class="mono">{b.price_cents}¢</td>'
            f'<td class="mono">{b.model_prob:.0%}</td>'
            f'<td class="mono">{b.edge * 100:+.0f}%</td>'
            f'<td class="mono">{(b.units or 1):.1f}u</td>'
            f'<td class="mono sub2">{esc(b.state_at_placement or "0-0")}</td>'
            f'<td>{_res_badge(b)}</td>'
            f'<td class="mono" style="color:{_pnlc(pnl)}">'
            f'{f"${dol(pnl):+.2f}" if pnl is not None else "—"}</td>'
            f'<td class="mono" style="color:{_pnlc(clv)}">'
            f'{f"{clv:+d}¢" if clv is not None else "—"}</td></tr>')
    ledger_body = ("".join(ledger_rows) or
                   '<tr><td colspan="11" class="empty">No bets yet.</td></tr>')
    nb = len(bets)
    ledger_html = f"""<section class="block"><div class="blockhead">
<h4>History · all bets</h4><span class="aside">{nb} bet{"" if nb == 1 else "s"} · \
newest first{" (last 300)" if nb >= 300 else ""} · \
<a href="{hist_link}">post-game log →</a></span></div>
<div class="rule"></div>
<div class="tw" style="max-height:520px;overflow:auto">
<table class="t"><tr><th>date</th><th>match</th><th>side</th><th>px</th><th>prob</th>
<th>edge</th><th>u</th><th>state</th><th>result</th><th>P&amp;L</th><th>CLV</th></tr>
{ledger_body}</table></div></section>"""

    if is_live_bot:
        bets_html = bets_section("Live-game bets", "fired in-play when an advisory "
                                 "cleared the policy mid-match", "advisory", "live_open")
    elif is_top5:
        bets_html = bets_section("Top-5 daily bets", "the day's highest-salience "
                                 "plays that cleared policy", "prematch", "pre_open")
    elif meta["basis"] == "chalk":
        bets_html = bets_section("Control bets", "market favorite on every scenario "
                                 "— ignores the model", "chalk", "chalk_open")
    elif meta["basis"] == "freshadj":
        bets_html = bets_section("Fresh/form-adjusted bets", "top plays gated on the "
                                 "fatigue + year-form adjusted probability", "freshadj",
                                 "fresh_open")
    else:
        bets_html = bets_section("Pre-game bets", "placed before the match, off the "
                                 "model's opening read", "prematch", "pre_open")

    # --- By tour: this bot's record segmented by series (ATP/WTA/Challenger/ITF) ---
    _TOURS = (("KXATPMATCH", "ATP"), ("KXWTAMATCH", "WTA"), ("KXWTAGAME", "WTA"),
              ("KXATPCHALLENGERMATCH", "Challenger"),
              ("KXITFMATCH", "ITF M"), ("KXITFWMATCH", "ITF W"))

    def _tour_of(tk: str) -> str:
        return next((lab for pre, lab in _TOURS if tk.startswith(pre)), "Other")

    tour_agg: dict[str, list] = {}
    for b, _ in settled:
        lab = _tour_of(b.market_ticker)
        stt, _ = hold_effs[b.id]
        hpnl = hold_effs[b.id][1]
        tpnl = tp_effs[b.id][1]
        agg = tour_agg.setdefault(lab, [0, 0, 0.0, 0.0])  # w, l, hold$, tp$
        if stt in WON:
            agg[0] += 1
        elif stt == "lost":
            agg[1] += 1
        if hpnl is not None:
            agg[2] += hpnl
        if tpnl is not None:
            agg[3] += tpnl
    tour_rows = ""

    def _pcol(v):
        return "var(--good)" if v > 0 else "var(--accent)" if v < 0 else "var(--muted)"
    # ATP=men, WTA=women, plus the ITF gender split — the men/women factor is
    # visible here, and each row compares the two exit rules side by side
    for lab in ("ATP", "WTA", "Challenger", "ITF M", "ITF W", "Other"):
        if lab not in tour_agg:
            continue
        w, l, hpnl, tpnl = tour_agg[lab]
        tot = w + l
        wr = f"{w / tot:.0%}" if tot else "—"
        wrc = ("var(--good)" if tot and w / tot >= 0.7 else
               "var(--warning)" if tot and w / tot >= 0.6 else "var(--text)")
        better = " ▲" if hpnl > tpnl else ""
        better_tp = " ▲" if tpnl > hpnl else ""
        tour_rows += (f'<tr><td>{lab}</td><td class="mono">{w}-{l}</td>'
                      f'<td class="mono" style="color:{wrc}">{wr}</td>'
                      f'<td class="mono" style="color:{_pcol(hpnl)}">${dol(hpnl):+.2f}{better}</td>'
                      f'<td class="mono" style="color:{_pcol(tpnl)}">${dol(tpnl):+.2f}{better_tp}</td></tr>')
    tour_html = (f'<section class="block"><div class="blockhead"><h4>By tour</h4>'
                 f'<span class="aside">men (ATP) vs women (WTA) · hold vs 90¢ exit</span></div>'
                 f'<div class="rule"></div><div class="tw"><table class="t">'
                 f'<tr><th>tour</th><th>record</th><th>win rate</th>'
                 f'<th>P&amp;L · hold</th><th>P&amp;L · 90¢</th></tr>{tour_rows}'
                 f'</table></div></section>' if tour_rows else "")

    body = pagehead("Strategy Lab", title,
                    f'{n} settled · <a href="{hist_link}">post-game log →</a> · '
                    f'<a href="/track">advisory track record →</a>') \
        + switcher + strip + exit_note + method_html + learned_html \
        + watching_html + timeline_html + f"""
{ledger_html}
{comparison_html}
<section class="block"><div class="blockhead"><h4>Tuning breakdown</h4>
<span class="aside">where the record comes from — the improvement signal</span></div>
<div class="rule"></div>{breakdown}</section>
{tour_html}
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
    bot = {"t1": "pre", "t2": "pre"}.get(bot, bot)
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
        # broadcast per-set grid (a-perspective = the YES-side market); no flags
        # here (IOCs aren't loaded on this page) but the same per-set layout
        a_sn = yes_name.split()[-1] if yes_name else "A"
        b_sn = _opponent_surname(title, a_sn) or "B"
        score = (f'<div style="margin-top:6px">'
                 f'{score_grid((line, sa, sb), a_sn, b_sn, None, None)}</div>'
                 if line else "")
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
<div class="sub2">won by <strong>{esc(winner) or '—'}</strong></div>
{score}
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


def scenario_triggers(sc) -> list[dict]:
    """A scenario's trigger set — watch-side set-states, each with the model prob
    there. From facts.triggers (takes set 1 / drops set 1 / decider); falls back
    to just the decider for scenarios generated before triggers were stored."""
    ts = (sc.facts or {}).get("triggers") if sc is not None else None
    if ts:
        return ts
    return [{"kind": "decider", "label": "deciding set", "state": "1-1",
             "prob": sc.model_prob_at_state if sc is not None else None}]


def fired_trigger(sc, watch_state: str | None) -> dict | None:
    """The scenario trigger currently HIT: the watch side reached that set-state
    AND the model still favours the pick there (≥ TRIG_FAVOR)."""
    if sc is None or not watch_state:
        return None
    for t in scenario_triggers(sc):
        if t.get("state") == watch_state and (t.get("prob") or 0) >= TRIG_FAVOR:
            return t
    return None


def scenario_tags(sc, hand_watch, hand_opp, price, live_state) -> list[tuple]:
    """Classify a scenario into the useful 'types' as (key, label) badges, from
    its facts + player hands + current price + live state:
      fatigue — opponent carries recent load (favours the pick)
      lefty   — a left-hander is involved (matchup factor)
      underdog— the watch pick is a priced underdog the model likes (value)
      value   — LIVE: model's prob at the current set-state beats the price now
    """
    f = sc.facts or {}
    tags = []
    if (f.get("fatigue_opp") or {}).get("played"):
        tags.append(("fatigue", "⚡ fatigue edge"))
    if "L" in (hand_watch, hand_opp):
        tags.append(("lefty", "◀ lefty"))
    model_pre = (sc.prematch_prob or 0) * 100
    if price is not None and price < 45 and model_pre - price >= 5:
        tags.append(("underdog", f"↑ underdog value +{model_pre - price:.0f}%"))
    if live_state and price is not None:
        tp = next((t["prob"] for t in scenario_triggers(sc)
                   if t.get("state") == live_state and t.get("prob") is not None), None)
        if tp is not None and tp * 100 - price >= 5:
            tags.append(("value", f"◎ live value +{tp * 100 - price:.0f}%"))
    return tags


def scenario_tag_html(tags) -> str:
    _cls = {"fatigue": "warn", "lefty": "neutral", "underdog": "good", "value": "accent"}
    return "".join(tag(_cls.get(k, "neutral"), "", lbl) for k, lbl in tags)


def trigger_html(sc, watch_state: str | None, is_live: bool,
                 watch_mid: float | None) -> str:
    """Plan-vs-reality: which of the scenario's triggers is armed / HIT, with the
    model-vs-market read once one fires. `watch_state` is the live set-state in
    the WATCH player's perspective (so 1-0 = watch took set 1)."""
    hit = fired_trigger(sc, watch_state)
    if hit:
        badge = tag("accent", "◎", f"trigger HIT · {esc(hit['label'])} ({hit['state']})")
        if watch_mid is not None and hit.get("prob") is not None:
            gap = hit["prob"] * 100 - watch_mid
            verdict = "value" if gap >= 3 else "no value"
            badge += " " + tag("good" if gap >= 3 else "neutral", "±",
                               f"model {hit['prob']:.0%} vs {watch_mid:.0f}¢ → {verdict}")
        return badge
    if watch_state == "final":
        return tag("neutral", "·", "plan done")
    armed = [t for t in scenario_triggers(sc) if (t.get("prob") or 0) >= TRIG_FAVOR]
    watch_for = " / ".join(t["state"] for t in armed) or "1-1"
    if is_live:
        return tag("warn", "◉", f"triggers armed · watching {watch_for}")
    labels = " · ".join(f"{esc(t['label'])} {t['state']}" for t in armed) or "deciding set 1-1"
    return tag("neutral", "○", f"plan set · {labels}")


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


# Cached win-probability model for the live "Most Confident" spotlight.
# fit_from_db walks ~875k matches (~25s), but ratings only change on the daily
# ingest — so the fitted model is cached for a long TTL and rebuilt off the event
# loop. The board never blocks on it: until the first build lands the spotlight
# is simply omitted; thereafter a stale model is served while a refresh runs.
_MODEL_CACHE: dict = {"at": None, "model": None, "building": False}
_MODEL_TTL_S = 1800


def _fit_live_model():
    from bot.prob.elo import SetElo
    with db_session() as db:
        m = SetElo()
        m.fit_from_db(db)
    return m


async def _live_model():
    import asyncio
    age = ((datetime.now(timezone.utc) - _MODEL_CACHE["at"]).total_seconds()
           if _MODEL_CACHE["at"] else 1e9)
    if (_MODEL_CACHE["model"] is None or age > _MODEL_TTL_S) \
            and not _MODEL_CACHE["building"]:
        _MODEL_CACHE["building"] = True

        async def _build():
            try:
                mdl = await asyncio.to_thread(_fit_live_model)
                _MODEL_CACHE.update(at=datetime.now(timezone.utc), model=mdl)
            except Exception as e:
                log.error("live model fit failed", error=str(e))
            finally:
                _MODEL_CACHE["building"] = False
        asyncio.create_task(_build())      # serve stale/None now, swap in when ready
    return _MODEL_CACHE["model"]


async def live(request: web.Request) -> web.Response:
    now = datetime.now(timezone.utc)
    user = request.get("user") or {}
    sess = request.get("session_cookie") or ""
    csrf = webauth.csrf_token(sess) if sess else ""
    model = await _live_model()
    as_of_live = now.date() + timedelta(days=1)
    with db_session() as db:
        markets = db.execute(select(KalshiMarket).where(
            KalshiMarket.status.in_(["active", "open"]))).scalars().all()
        pinned_set = {p.event_ticker for p in db.execute(
            select(UserPin).where(UserPin.user_id == user["id"])).scalars().all()} \
            if user.get("id") else set()
        fav_ids = _fav_player_ids(db, user)
        bet_tags = _user_bet_tags(db, user.get("id"))
        # current win streaks for players on the board — from the nightly stats
        # cache (form.streak > 0 = consecutive wins), same source as the Database
        # "Heaters". A win-streak token surfaces a hot player on live/upcoming cards.
        streaks: dict[int, int] = {}
        serve_base: dict[int, dict] = {}   # pid -> {"ace","df"} historical rates
        _bpids = [m.player_a_id for m in markets if m.player_a_id]
        if _bpids:
            from sqlalchemy import text as _sqltext2

            from bot.models import PlayerStatsCache
            _mx = db.execute(select(func.max(PlayerStatsCache.as_of))).scalar()
            if _mx is not None:
                for pid, s, ace, df in db.execute(_sqltext2(
                    "SELECT player_id, (payload->'form'->>'streak'), "
                    "(payload->'serve_return'->>'ace_pct'), "
                    "(payload->'serve_return'->>'df_pct') "
                    "FROM player_stats_cache WHERE as_of = :mx "
                    "AND player_id = ANY(:pids)"),
                        {"mx": _mx, "pids": _bpids}).all():
                    try:
                        si = int(s)
                        if si >= STREAK_TOKEN_MIN:
                            streaks[pid] = si
                    except (TypeError, ValueError):
                        pass
                    # serve baselines for the statistical-significance ace/DF flags
                    try:
                        b = {"ace": float(ace) if ace else None,
                             "df": float(df) if df else None}
                        if b["ace"] or b["df"]:
                            serve_base[pid] = b
                    except (TypeError, ValueError):
                        pass

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
        # final scoreline. Status-code classification lives in one place
        # (bot.market.live_status): pre-match/ended are enumerated, everything
        # else present is live — so a never-before-seen live code (the recurring
        # cause of "live match shows starting soon") no longer regresses.

        def _rec(ev):  # (latest ts, total_games, is_final) across the event's sides
            best = None
            for m in ev["sides"]:
                r = recent.get(m.ticker)
                if r and (best is None or r[0] > best[0]):
                    best = r
            return best

        # estimator (odds-based) state for EVERY market on the board — the only
        # "live" signal for matches Kalshi gives neither a score feed NOR a live
        # status ('not_started' lingers while play is under way on lower tiers,
        # and scores are absent on ITF/some Challengers). Loaded before the
        # grouping so it can inform it. The stored `stale` flag is unreliable
        # (reads False on day-old rows), so gate on tick recency here.
        _all_tk = [m.ticker for ev in events.values() for m in ev["sides"]]
        states = {s.market_ticker: s for s in db.execute(
            select(LiveMatchState).where(
                LiveMatchState.market_ticker.in_(_all_tk))).scalars().all()} \
            if _all_tk else {}

        def _est_live(ev) -> bool:
            # a fresh estimator that has inferred a COMPLETED set (state past
            # 0-0, not final) = play is under way. 0-0 is excluded: during set 1
            # the set-state is indistinguishable from pre-match, and pre-match
            # markets also tick — so 0-0 alone must not read as live.
            for m in ev["sides"]:
                s = states.get(m.ticker)
                if (s and s.last_tick_at and now - s.last_tick_at <= FRESH_LIVE
                        and s.state not in (None, "0-0", "final")):
                    return True
            return False

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
            games = r[1] if r else 0
            fresh = bool(r and now - r[0] <= FRESH_LIVE)
            playing = games > 0 and fresh and not is_final
            ev["started"] = games > 0
            # An authoritative END signal — a posted Kalshi result, a final
            # scoreline, discovery-gone, or a terminal status word — means the
            # match is over. It OUTRANKS a live status code, because those codes
            # ('P'/'live'/…) linger stale on finished matches (hundreds do). The
            # ONE thing that overrides a (possibly lagging) end signal is ACTUAL
            # fresh scoring right now.
            skind = status_kind(status)
            done_signal = settled or is_final or gone or skind == "ended"
            # a live status code counts only when nothing says the match is over;
            # ANY present, non-pre-match, non-ended code is live (durable default)
            status_live = skind == "live" and not done_signal
            if playing:
                # games advancing right now — live even if an end-flag lags
                live_evs.append((ev_ticker, ev))
            elif done_signal:
                if now - occ <= timedelta(hours=18):
                    done_evs.append((ev_ticker, ev))
            elif status_live or _est_live(ev):
                # live per the feed OR the odds-based estimator inferred a
                # completed set — trust it. Covers matches Kalshi shows as
                # 'not_started' with no score poll while play is actually under
                # way (the recurring "started game stuck in starting soon").
                live_evs.append((ev_ticker, ev))
            elif occ <= now + UPCOMING_HORIZON:
                # still an open, undecided market (fresh discovery, not done, no
                # live signal) → upcoming. Kalshi's occurrence_datetime can be
                # badly stale for rescheduled matches (hours or DAYS off), so we
                # DON'T drop past-slot markets it still actively lists — freshness
                # (via done_signal/gone) is the authority for "still on", not occ.
                # Genuinely-begun matches are caught above; finished ones by
                # done_signal — so this only ever shows real, open markets.
                if occ <= now - timedelta(minutes=20):
                    ev["late"] = True
                soon_evs.append((ev_ticker, ev))
        live_evs.sort(key=lambda e: e[1]["occ"])
        soon_evs.sort(key=lambda e: e[1]["occ"])
        done_evs.sort(key=lambda e: e[1]["occ"], reverse=True)

        # auto-unpin ended matches — a pin is a "watch this ongoing match" tool,
        # so once a game finishes (or its market closes and leaves the board) the
        # pin is dropped. Anything still live OR upcoming is kept; everything else
        # (done, settled, discovery-gone, no longer an open market) is removed.
        if pinned_set:
            still_active = ({t for t, _ in live_evs}
                            | {t for t, _ in soon_evs}) & pinned_set
            ended = pinned_set - still_active
            if ended:
                from sqlalchemy import delete as _delete
                db.execute(_delete(UserPin).where(
                    UserPin.user_id == user["id"],
                    UserPin.event_ticker.in_(ended)))
                db.commit()
                pinned_set = still_active

        all_tickers = [m.ticker for _, ev in live_evs for m in ev["sides"]]
        # quotes for upcoming games too, so their live Kalshi odds also render
        quote_tickers = all_tickers + [m.ticker for _, ev in soon_evs
                                       for m in ev["sides"]]
        quotes = _latest_quotes(db, quote_tickers)
        # states already loaded (before grouping) for every market on the board
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
        details = {}     # ticker -> latest score-feed detail JSON (for live flags)
        details_ts = {}  # ticker -> ts of that detail (freshness gate)
        if all_tickers:
            for r in db.execute(sqltext("""
                SELECT DISTINCT ON (market_ticker) market_ticker, scoreline, sets_a, sets_b, detail, ts
                FROM match_score_log WHERE market_ticker = ANY(:t)
                ORDER BY market_ticker, ts DESC"""), {"t": all_tickers}).all():
                scorelines[r[0]] = (r[1], r[2], r[3])
                d = r[4]
                if d is not None:
                    if isinstance(d, str):
                        import json as _json
                        try:
                            d = _json.loads(d)
                        except ValueError:
                            d = None
                    if d:
                        details[r[0]] = d
                        details_ts[r[0]] = r[5]
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

        # a scenario whose trigger has FIRED (any of its set-state triggers, on
        # the watch side) is the single most actionable card — float to the top
        def _scen_fired(ev_ticker, ev):
            sc = plans.get(ev_ticker)
            if sc is None:
                return False
            w = states.get(sc.market_ticker)
            if w is None or w.stale:
                return False
            return fired_trigger(sc, w.state) is not None
        live_evs.sort(key=lambda e: (
            0 if _scen_fired(e[0], e[1]) else 1,
            _TRIG_RANK[_trig_class(_ev_est(e[1]), True)], e[1]["occ"]))

        # --- Most Confident: the live matches where the model's read is strongest.
        # The model probability is conditioned on completed sets from the delayed
        # score feed (0-0 → the pre-match read); market price never enters it
        # (CLAUDE.md rule 2 — only the discrete set score may). Ranked by conviction
        # (how far the pick is from a coin flip); Minimal-data reads are excluded so
        # a thin-history longshot can't masquerade as a confident call.
        verdicts = {}  # ev_ticker -> decision-first read for the card headline
        confident_model = {}  # ev_ticker -> {"name","pick","conf","band"}
        # Best Value: same model probability compared to the EXECUTABLE market
        # price (the ask on the side we'd buy — never the midpoint). This is a
        # downstream comparison, not a model input: price never reaches the engine
        # (CLAUDE.md rule 2). Edge here is a disagreement measure, NOT proven money
        # (our forward CLV on these has been ~flat/negative — adverse selection).
        value_model = {}  # ev_ticker -> {"name","edge","model_cents","ask_cents","band"}
        if model is not None:
            from bot.prob.confidence import confidence_band as _cband
            from bot.prob.model import MatchState as _MS
            from bot.scenarios import SERIES_TIER
            from bot.stats.surface import live_match_surface as _lms

            def _ask_cents(m):
                q = quotes.get(m.ticker)
                if q and q[1] is not None:
                    return q[1]
                return _raw_cents((m.raw or {}), "yes_ask_dollars")
            surf_cache: dict = {}
            for t, ev in live_evs:
                sd = sorted(ev["sides"], key=lambda m: m.ticker)
                if len(sd) < 2 or not sd[0].player_a_id or not sd[1].player_a_id:
                    continue
                a_pid, b_pid = sd[0].player_a_id, sd[1].player_a_id
                sa = sb = 0
                smt = next((m for m in sd if scorelines.get(m.ticker)), None)
                if smt is not None:
                    _, s_a, s_b = scorelines[smt.ticker]
                    sa, sb = ((s_a or 0, s_b or 0) if smt is sd[0]
                              else (s_b or 0, s_a or 0))
                try:
                    ms = _MS(sets_a=sa, sets_b=sb, best_of=3)
                except ValueError:
                    ms = _MS()
                key = (a_pid, b_pid)
                if key not in surf_cache:
                    surf_cache[key] = _lms(db, a_pid, b_pid, as_of_live)
                tier = SERIES_TIER.get(ev.get("series", ""), None)
                try:
                    pred = model.predict(a_pid, b_pid, surf_cache[key], tier,
                                         ms, as_of_live)
                except Exception:
                    continue
                band = _cband(pred.confidence)
                pick_a = pred.p_a >= 0.5
                pick = pred.p_a if pick_a else 1 - pred.p_a
                # best executable buy vs model (drives both the verdict and Best Value)
                best = None
                for m, mp in ((sd[0], pred.p_a), (sd[1], 1 - pred.p_a)):
                    ask = _ask_cents(m)
                    if ask is None:
                        continue
                    mc = round(mp * 100)
                    edge = mc - ask
                    if best is None or edge > best[0]:
                        nm = (m.raw or {}).get("yes_sub_title") or "the pick"
                        best = (edge, mc, ask, nm)
                # decision-first verdict — computed for EVERY live card (incl pinned):
                # a real edge on an executable price → BET with an edge-banded size;
                # otherwise an explicit 'no edge' (with the model's lean). Advisory
                # only; the edge is a disagreement measure, not proven money.
                fav_name = ((sd[0] if pick_a else sd[1]).raw or {}).get("yes_sub_title") or ""
                # model's favored-side confidence — only trusted off non-critical
                # (thin) data; drives the 70%+ high-confidence flag on the card
                fav_p = pick if band.tier != "critical" else None
                if (band.tier != "critical" and best is not None
                        and best[0] >= 4 and 5 <= best[2] <= 95):
                    e = best[0]
                    units = 0.5 if e < 7 else 1.0 if e < 10 else 1.5 if e < 14 else 2.0
                    verdicts[t] = {"kind": "bet", "side": best[3], "ask": best[2],
                                   "edge": e, "units": units,
                                   "fav": fav_name, "fav_p": fav_p}
                else:
                    verdicts[t] = {"kind": "none", "side": fav_name, "p": pick,
                                   "thin": band.tier == "critical",
                                   "fav": fav_name, "fav_p": fav_p}
                # spotlight tiers render pinned in their own tier + skip minimal data
                if t in pinned_set or band.tier == "critical":
                    continue
                if pick >= 0.65:                   # clear read → confidence spotlight
                    name = ((sd[0] if pick_a else sd[1]).raw or {}).get("yes_sub_title") \
                        or "the pick"
                    confident_model[t] = {"name": name, "pick": pick,
                                          "conf": pred.confidence, "band": band}
                if best is not None and best[0] >= 7:   # ≥7¢ model-vs-market gap
                    value_model[t] = {"name": best[3], "edge": best[0],
                                      "model_cents": best[1], "ask_cents": best[2],
                                      "band": band}

    series_label = {"KXATPMATCH": "ATP", "KXWTAMATCH": "WTA", "KXWTAGAME": "WTA",
                    "KXATPCHALLENGERMATCH": "CHALLENGER", "KXITFMATCH": "ITF M",
                    "KXITFWMATCH": "ITF W"}

    def match_card(ev_ticker: str, ev: dict, is_live: bool,
                   model_note: str = "") -> str:
        sides = sorted(ev["sides"], key=lambda m: m.ticker)
        est = next((states.get(m.ticker) for m in sides if states.get(m.ticker)), None)
        rows_html = []
        names_acc: list[str] = []  # player names → the card's client-side search string
        prices = [_odds_cents(m, quotes)[0] for m in sides[:2]]
        favc = max((p for p in prices if p is not None), default=None)
        # active live performance flags (aces / hot form) per side — computed from
        # the one market that carries a score feed. yes_statistics is oriented to
        # the ticker-sorted FIRST side (sides[0]), exactly like the match-detail
        # page maps it to pa — NOT to whichever market happened to log the row
        # (those can differ, which would swap the two players' flags). Suppressed
        # once the match is final or the feed has gone quiet, so a frozen
        # end-of-match snapshot doesn't keep flashing "live" badges.
        flags_for: dict[str, list] = {}
        det_ticker = next((m.ticker for m in sides if details.get(m.ticker)), None)
        det_ts = details_ts.get(det_ticker) if det_ticker else None
        feed_fresh = det_ts is not None and (now - det_ts).total_seconds() <= 300
        is_final_state = est is not None and getattr(est, "state", None) == "final"
        if is_live and feed_fresh and not is_final_state and len(sides) >= 2 \
                and det_ticker is not None:
            ys, os_ = _oriented_live_stats(details[det_ticker])
            if ys is not None:
                b0 = serve_base.get(sides[0].player_a_id)
                b1 = serve_base.get(sides[1].player_a_id)
                flags_for[sides[0].ticker] = _live_flags(ys, os_, b0)
                flags_for[sides[1].ticker] = _live_flags(os_, ys, b1)
        for m in sides[:2]:
            name = (m.raw or {}).get("yes_sub_title") or m.ticker.rsplit("-", 1)[-1]
            names_acc.append(name)
            cents, live_q = _odds_cents(m, quotes)
            # data-pxk/data-px let the client flash the price when it changes on
            # the next incremental refresh (green up / red down)
            if cents is None:
                px = f'<span class="px mono" data-pxk="{esc(m.ticker)}">—</span>'
            else:
                dot = ('<span style="color:var(--good)" title="live quote">●</span> '
                       if live_q else "")
                style = 'color:var(--text);font-weight:800' if cents == favc \
                    else 'color:var(--muted)'
                tip = "Kalshi price = implied win %" + ("" if live_q else " (last snapshot)")
                px = (f'<span class="px mono" style="{style}" title="{tip}" '
                      f'data-pxk="{esc(m.ticker)}" data-px="{cents}">'
                      f'{dot}{cents}¢</span>')
            # win-streak token (form; shows on live + upcoming) then live flags
            st_badge = ([("streak", f"🔥 W{streaks[m.player_a_id]}")]
                        if m.player_a_id and m.player_a_id in streaks else [])
            fl = _flags_html(st_badge + flags_for.get(m.ticker, []))
            fav = _fav_btn(m.player_a_id, csrf,
                           bool(m.player_a_id and m.player_a_id in fav_ids))
            head = (f'{fav} <span class="nm">{esc(name)}</span>' if fav
                    else f'<span class="nm">{esc(name)}</span>')
            nm_cell = f'<div>{head}{fl}</div>'
            rows_html.append(f'<div class="playerrow">{nm_cell}{px}</div>')
        late = ev.get("late")
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
        # DECIDER flag — match is in a deciding set (1-1 Bo3 / 2-2 Bo5) per the
        # estimator; marks where the separate set-form decider read applies
        dec_flag = (tag("accent", "⚑", "decider")
                    if est is not None and not getattr(est, "stale", False)
                    and getattr(est, "state", None) in ("1-1", "2-2") else "")
        score_row = ""
        # Kalshi-style live scoreline for live matches (per-set columns + flags),
        # a-perspective of whichever side has a recorded scoreline
        sm = next((m for m in sides if scorelines.get(m.ticker)), None)
        # broadcast per-set grid whenever a scoreline exists — live OR finished,
        # so the score reads the same everywhere
        if sm is not None and scorelines[sm.ticker][0]:
            other = next((m for m in sides if m.ticker != sm.ticker), None)
            a_nm = (sm.raw or {}).get("yes_sub_title") or "Player A"
            b_nm = (other.raw or {}).get("yes_sub_title") if other else "Player B"
            score_row = score_grid(scorelines[sm.ticker], a_nm, b_nm,
                                   ioc_by_pid.get(sm.player_a_id),
                                   ioc_by_pid.get(other.player_a_id) if other else None)
        elif is_live and est is not None and getattr(est, "state", None) not in (None, "final"):
            # no per-game feed (Kalshi provides live scores mainly for ATP/WTA, not
            # most ITF) — show the SET state we inferred from the odds instead of a blank
            score_row = (f'<div class="mono" style="font-size:15px;font-weight:800">'
                         f'<span style="color:var(--accent)">●</span> sets {esc(est.state)}'
                         f' <span class="sub2">· inferred from odds — no live game '
                         f'feed for this match</span></div>')
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
            # watch-side live state (sc.market_ticker's YES = the watch pick) so
            # asymmetric triggers (1-0 took set 1 vs 0-1 dropped it) orient right
            w_est = states.get(sc.market_ticker)
            watch_state = w_est.state if (w_est and not w_est.stale) else None
            ft = fired_trigger(sc, watch_state)  # any trigger, not just the decider
            trig_fired = ft is not None
            if trig_fired:
                banner = (f'<div class="trig-banner">◎ SCENARIO TRIGGERED · '
                          f'{esc(wname)} — {esc(ft["label"])} at '
                          f'{ft["prob"]:.0%} — the read is live</div>')
            plan_row = (
                f'<div class="planrow"><span class="scen-flag">◆ PLAY</span> '
                f'<strong>{esc(wname)}</strong> '
                f'{trigger_html(sc, watch_state, is_live, wmid)} '
                f'<a href="/scenario/{sc.id}" class="sub2">full scenario →</a></div>')
        tour = series_label.get(ev["series"], "?")
        has_play = any(m.ticker in advised for m in sides)
        trig = _trig_class(est, is_live)
        # per-user pin toggle (bookmark only — changes nothing the bot does)
        is_pinned = ev_ticker in pinned_set
        pin_btn = (f'<span class="pin{" on" if is_pinned else ""}" '
                   f'data-ev="{esc(ev_ticker)}" data-csrf="{csrf}" role="button" '
                   f'tabindex="0" title="{"Unpin" if is_pinned else "Pin for easy viewing"}">'
                   f'📌</span>') if csrf else ""
        # "log a bet" control — opens a client-side modal to record a manual bet
        # (price + shares) on either side; a personal ledger, places no orders.
        bet_btn = ""
        if csrf and len(sides) >= 2:
            s0, s1 = sides[0], sides[1]
            n0 = (s0.raw or {}).get("yes_sub_title") or "Player A"
            n1 = (s1.raw or {}).get("yes_sub_title") or "Player B"
            p0 = _odds_cents(s0, quotes)[0]
            p1 = _odds_cents(s1, quotes)[0]
            bet_btn = (
                f'<span class="betbtn" role="button" tabindex="0" title="Log a bet" '
                f'data-ev="{esc(ev_ticker)}" data-csrf="{csrf}" '
                f'data-a-tk="{esc(s0.ticker)}" data-a-nm="{esc(n0)}" data-a-px="{p0 or ""}" '
                f'data-b-tk="{esc(s1.ticker)}" data-b-nm="{esc(n1)}" data-b-px="{p1 or ""}">'
                f'＋ bet</span>')
        # decision-first verdict + high-confidence model flag (≥70% on the favored
        # side, off non-thin data) — a scannable "the model is sure" game flag
        _verdict = verdicts.get(ev_ticker)
        conf_flag = ""
        if is_live and _verdict and (_verdict.get("fav_p") or 0) >= 0.70 \
                and _verdict.get("fav"):
            conf_flag = tag("good", "🎯",
                            f"{_verdict['fav_p']:.0%} {_verdict['fav'].split()[-1]}")
        # per-card flags for the live filter bar: a favorited player, a player on
        # a win streak, an actionable BET verdict, or a ≥70% model read
        has_fav = any(m.player_a_id in fav_ids for m in sides[:2] if m.player_a_id)
        has_streak = any(m.player_a_id in streaks for m in sides[:2] if m.player_a_id)
        has_bet = bool(is_live and _verdict and _verdict.get("kind") == "bet")
        has_conf = bool(conf_flag)
        # 'livecard' class only on the live section — the filter bar counts/filters
        # those, not the "starting soon" cards
        klass = ("card livecard" if is_live else "card") + (" trig-live" if trig_fired else "")
        # timing label: live matches with a future scheduled time started early
        if is_live:
            when = f"started {pt(ev['occ'])}" if ev["occ"] <= now else "live now (early start)"
        elif late and (now - ev["occ"]) > timedelta(hours=8):
            # occ this far past but market still open → the slot was almost
            # certainly rescheduled and Kalshi didn't update it; don't overstate
            when = "awaiting start · scheduled time unconfirmed"
        elif late:
            when = f"scheduled {pt(ev['occ'])} · running late"
        elif ev["occ"] <= now:
            when = f"awaiting start · scheduled {pt(ev['occ'])}"
        else:
            when = f"starts {pt(ev['occ'])}"
        search_str = _search_norm(" ".join(names_acc + [tour]))
        return f"""<div class="{klass}" data-card="{esc(ev_ticker)}" \
data-names="{esc(search_str)}" \
data-tour="{esc(tour)}" data-play="{1 if has_play else 0}" \
data-scenario="{1 if sc is not None else 0}" \
data-fav="{1 if has_fav else 0}" data-streak="{1 if has_streak else 0}" \
data-bet="{1 if has_bet else 0}" data-conf="{1 if has_conf else 0}" \
data-trigfired="{1 if trig_fired else 0}" data-trig="{trig}">
{banner}
<div style="display:flex;align-items:center;justify-content:space-between">
<span class="kicker" style="margin:0">{tour}</span>
<span>{conf_flag} {st} {dec_flag} {scen_badge} {play} {bet_btn} {pin_btn}</span></div>
{_verdict_html(_verdict) if is_live else ""}
<a href="/match/{esc(ev_ticker)}" style="text-decoration:none;color:inherit">
<div>{''.join(rows_html)}</div></a>
{model_note}
{score_row}
{plan_row}
<div class="sub2 mono">{when}
· <a href="/match/{esc(ev_ticker)}" class="sub2">match data →</a>
· {kalshi_link(sides[0].ticker)}</div>
</div>"""

    def done_card(ev_ticker: str, ev: dict) -> str:
        sides = sorted(ev["sides"], key=lambda m: m.ticker)
        rows_html = []
        names_acc = []
        for m in sides[:2]:
            name = (m.raw or {}).get("yes_sub_title") or m.ticker.rsplit("-", 1)[-1]
            names_acc.append(name)
            won = m.result == "yes"
            mark = tag("good", "✓", "won") if won else ""
            style = "" if won or not any(x.result for x in sides) \
                else "color:var(--muted)"
            rows_html.append(f'<div class="playerrow"><span class="nm" '
                             f'style="{style}">{esc(name)}</span><span>{mark}</span></div>')
        d_tour = series_label.get(ev['series'], '?')
        d_search = _search_norm(" ".join(names_acc + [d_tour]))
        return f"""<div class="card" data-card="{esc(ev_ticker)}" \
data-names="{esc(d_search)}" style="opacity:.75">
<div style="display:flex;align-items:center;justify-content:space-between">
<span class="kicker" style="margin:0">{series_label.get(ev['series'], '?')}</span>
{tag('neutral', '·', 'finished')}</div>
<div>{''.join(rows_html)}</div>
<div class="sub2 mono">was scheduled {pt(ev['occ'])} · {esc(ev_ticker)}</div>
</div>"""

    # Pinned section — the user's saved matches, pulled to the top. Ordered
    # live → soon → finished so the ones in play surface first. A pin whose market
    # has fully closed/left the board simply isn't shown (nothing to render).
    pinned_ordered = ([(t, e, True) for t, e in live_evs if t in pinned_set]
                      + [(t, e, False) for t, e in soon_evs if t in pinned_set]
                      + [(t, e, None) for t, e in done_evs if t in pinned_set])
    pinned_cards = "".join(
        done_card(t, e) if live is None else match_card(t, e, live)
        for t, e, live in pinned_ordered)
    pinned_section = (
        f'<section class="block major"><div class="blockhead"><h4>📌 Pinned</h4>'
        f'<span class="aside">your saved matches · click 📌 on any card to pin</span>'
        f'</div><div class="rule"></div><div class="cards">{pinned_cards}</div>'
        f'</section>') if pinned_cards else ""

    ev_by_t = {t: e for t, e in live_evs}
    ev_all = dict(ev_by_t)
    ev_all.update({t: e for t, e in soon_evs})
    live_ticker_set = {t for t, _ in live_evs}

    # ◆ Scenarios — the day's curated plays, pulled to the top so users can scan
    # them and pick a game to watch. Fired triggers first, then by salience.
    # Upcoming plays are included too, so a watchlist can be built before first ball.
    scen_ordered = [(t, ev_all[t]) for t in
                    ([x[0] for x in live_evs] + [x[0] for x in soon_evs])
                    if t in plans and t not in pinned_set]
    scen_ordered.sort(key=lambda te: (0 if _scen_fired(te[0], te[1]) else 1,
                                       -(plans[te[0]].salience or 0.0)))
    scenario_set = {t for t, _ in scen_ordered}
    scen_cards = "".join(match_card(t, e, t in live_ticker_set) for t, e in scen_ordered)
    scenario_section = (
        f'<section class="block major"><div class="blockhead"><h4>◆ Scenarios</h4>'
        f"<span class=\"aside\">the day's plays — pick a game to watch · triggered "
        f'first, then by salience</span></div>'
        f'<div class="rule"></div><div class="cards">{scen_cards}</div>'
        f'</section>') if scen_cards else ""

    # Most Confident — strongest model reads (scenarios excluded; shown above).
    conf_items = sorted(((t, v) for t, v in confident_model.items()
                         if t not in scenario_set),
                        key=lambda kv: kv[1]["pick"], reverse=True)[:6]
    confident_set = {t for t, _ in conf_items}

    def _mnote(info):
        return (f'<div class="planrow"><span class="scen-flag" '
                f'style="background:var(--good);color:#0c130c">◎ MODEL</span> '
                f'<strong>{esc(info["name"])}</strong> to win · '
                f'<span class="mono" style="font-weight:800">{info["pick"]:.0%}</span> '
                f'<span class="sub2">· {esc(info["band"].label.lower())} data '
                f'confidence</span></div>')
    confident_cards = "".join(
        match_card(t, ev_by_t[t], True, model_note=_mnote(confident_model[t]))
        for t, _ in conf_items)
    confident_section = (
        f'<section class="block major"><div class="blockhead"><h4>Most Confident</h4>'
        f'<span class="aside">strongest model reads among live matches · '
        f'win probability, not market price</span></div>'
        f'<div class="rule"></div><div class="cards">{confident_cards}</div>'
        f'</section>') if confident_cards else ""

    # Best Value — where the model most disagrees with the market. A match already
    # in Most Confident isn't repeated here; ranked by the model-minus-ask edge.
    val_items = sorted(((t, v) for t, v in value_model.items()
                        if t not in confident_set and t not in scenario_set),
                       key=lambda kv: kv[1]["edge"], reverse=True)[:6]
    value_set = {t for t, _ in val_items}

    def _vnote(info):
        return (f'<div class="planrow"><span class="scen-flag" '
                f'style="background:var(--accent);color:#fff">± VALUE</span> '
                f'<strong>{esc(info["name"])}</strong> · model '
                f'<span class="mono" style="font-weight:800">{info["model_cents"]}¢</span> '
                f'vs ask <span class="mono">{info["ask_cents"]}¢</span> '
                f'<span class="sub2">· +{info["edge"]}¢ edge · {esc(info["band"].label.lower())} '
                f'data · unproven</span></div>')
    value_cards = "".join(
        match_card(t, ev_by_t[t], True, model_note=_vnote(value_model[t]))
        for t, _ in val_items)
    value_section = (
        f'<section class="block major"><div class="blockhead"><h4>Best Value</h4>'
        f'<span class="aside">largest model-vs-market gaps (model % − executable '
        f'ask) · a disagreement signal, not proven profit</span></div>'
        f'<div class="rule"></div><div class="cards">{value_cards}</div>'
        f'</section>') if value_cards else ""

    # pinned & spotlighted matches render only in their own tier — drop them
    # from the regular sections so nothing shows twice
    hoisted = pinned_set | scenario_set | confident_set | value_set
    live_disp = [(t, e) for t, e in live_evs if t not in hoisted]
    soon_disp = [(t, e) for t, e in soon_evs if t not in hoisted]
    done_disp = [(t, e) for t, e in done_evs if t not in pinned_set]
    live_cards = "".join(match_card(t, e, True) for t, e in live_disp)
    soon_cards = "".join(match_card(t, e, False) for t, e in soon_disp)
    done_cards = "".join(done_card(t, e) for t, e in done_disp[:12])

    # filter bar — only offer tour chips for tours actually on the board
    present = [series_label.get(e[1]["series"], "?") for e in live_disp]
    tour_chips = "".join(
        f'<button class="fchip" data-f="{esc(t)}">{esc(t)}</button>'
        for t in _TOUR_CHIPS if t in present)
    near_n = sum(1 for _, e in live_disp
                 if _trig_class(next((states.get(m.ticker) for m in e["sides"]
                                      if states.get(m.ticker)), None), True)
                 in ("hit", "near"))
    fired_n = sum(1 for t, e in live_disp if _scen_fired(t, e))
    # count over ALL live events — hoisted cards (scenarios/confident/value) are
    # still .livecard and get filtered too, so the count must include them
    fav_n = sum(1 for _, e in live_evs
                if any(m.player_a_id in fav_ids for m in e["sides"][:2] if m.player_a_id))
    streak_n = sum(1 for _, e in live_evs
                   if any(m.player_a_id in streaks for m in e["sides"][:2] if m.player_a_id))
    bet_n = sum(1 for t, _ in live_evs if (verdicts.get(t) or {}).get("kind") == "bet")
    conf_n = sum(1 for t, _ in live_evs
                 if (verdicts.get(t) or {}).get("fav_p") and verdicts[t]["fav_p"] >= 0.70)
    fav_chip = (f'<button class="fchip" data-f="fav">★ favorites ({fav_n})</button>'
                if fav_n else "")
    streak_chip = (f'<button class="fchip" data-f="streak">🔥 win streak ({streak_n})</button>'
                   if streak_n else "")
    bet_chip = (f'<button class="fchip" data-f="bet">▲ value ({bet_n})</button>'
                if bet_n else "")
    conf_chip = (f'<button class="fchip" data-f="conf">🎯 70%+ ({conf_n})</button>'
                 if conf_n else "")
    # free-text search over player names + tour. Rendered at the TOP of the board
    # (not in this filter bar) and searches EVERY card — scenarios, pinned,
    # confident, value, all-live, upcoming, finished — so any game is one type away.
    search_row = ('<div class="livesearch">'
                  '<div class="livesearch-box">'
                  '<span class="livesearch-ic">🔎</span>'
                  '<input id="livesearch" type="search" autocomplete="off" '
                  'autocorrect="off" autocapitalize="off" spellcheck="false" '
                  'placeholder="Search any player or tournament…" '
                  'aria-label="Search matches">'
                  '<button id="livesearch-x" type="button" title="Clear search" '
                  'aria-label="Clear search">✕</button></div>'
                  '<span id="searchcount" class="livesearch-ct"></span>'
                  '</div>'
                  '<div id="searchempty" class="searchempty" style="display:none"></div>') \
        if (live_evs or soon_evs) else ""
    filter_bar = (f'<div class="filterbar">{tour_chips}'
                  f'<button class="fchip" data-f="trigfired">◎ triggered now ({fired_n})</button>'
                  f'{bet_chip}{conf_chip}'
                  f'<button class="fchip" data-f="play">▲ advisory fired</button>'
                  f'<button class="fchip" data-f="trig">near trigger ({near_n})</button>'
                  f'{fav_chip}{streak_chip}'
                  f'<span class="sub2" style="margin-left:auto">showing '
                  f'<span id="livecount">{len(live_disp)}</span> of {len(live_disp)}</span>'
                  f'</div>') if live_disp else ""
    npin = len(pinned_ordered)
    body = pagehead("Match Board", "Live Now",
                    f"{len(live_evs)} live · {len(soon_evs)} next 12h"
                    + (f" · {len(scenario_set)} plays" if scenario_set else "")
                    + (f" · {npin} pinned" if npin else "")) \
        + search_row \
        + pinned_section + scenario_section + confident_section + value_section + f"""
<section class="block major"><div class="blockhead"><h4>All Live</h4>
<span class="aside">every live match the bot watches</span></div>
<div class="rule"></div>
{filter_bar}
<div class="cards">{live_cards or
    ('<div class="card"><div class="empty">All live matches are pinned or spotlighted above.</div></div>'
     if any(t in hoisted for t, _ in live_evs)
     else '<div class="card"><div class="empty">No tennis in the playing window right now.</div></div>')}
</div></section>
<section class="block major"><div class="blockhead"><h4>Starting soon</h4>
<span class="aside">next 12 hours · scheduled times — tennis runs early and late</span></div>
<div class="rule"></div>
<div class="cards">{soon_cards or
    '<div class="card"><div class="empty">Nothing scheduled.</div></div>'}
</div></section>
{f'<section class="block major"><div class="blockhead"><h4>Recently finished</h4></div><div class="rule"></div><div class="cards">{done_cards}</div></section>' if done_cards else ''}
<p class="prose">Every match the bot watches appears here whether or not a play
fired. Each player's number is the <strong>live Kalshi price</strong> (cents =
implied win %); a green ● marks a fresh streamed quote, otherwise it's the last
snapshot. Set states come from the estimator (≈ inferred from odds movement,
✓ confirmed by the delayed score). Matches leave the board as soon as their
market settles or closes on Kalshi.</p>""" + _tags_datalist(bet_tags)
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
    user = request.get("user") or {}
    sess = request.get("session_cookie") or ""
    csrf = webauth.csrf_token(sess) if sess else ""

    def base_filters(query):
        if tour:
            query = query.where(Player.tour == tour)
        if hand:
            query = query.where(Player.hand == hand)
        return query

    with db_session() as db:
        fav_ids = _fav_player_ids(db, user)
        # candidate players by search or activity, then annotate record
        if q:
            found = db.execute(base_filters(select(Player).where(
                Player.normalized_name.ilike(f"%{normalize_name(q)}%")))
                .limit(200)).scalars().all()
            heading = f'results for "{esc(q)}"'
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

        # Heaters — players riding a win streak, from the nightly stats cache
        # (form.streak > 0 = current consecutive wins). Read the top few via a
        # JSONB filter so we never load the whole cache. Skipped during search.
        heaters = []
        heaters_total = 0
        HEATERS_MAX = 500  # effectively "everyone on a 5+ streak" (hard cap only
        # as a runaway guard); shown hottest-first
        if not q:
            from sqlalchemy import text as _sqltext

            from bot.models import PlayerStatsCache
            mx = db.execute(select(func.max(PlayerStatsCache.as_of))).scalar()
            if mx is not None:
                tourf = "AND p.tour = :tour" if tour else ""
                params = {"mx": mx, "minst": 5}
                if tour:
                    params["tour"] = tour
                _where = ("WHERE c.as_of = :mx "
                          "AND (c.payload->'form'->>'streak') ~ '^[0-9]+$' "
                          "AND (c.payload->'form'->>'streak')::int >= :minst "
                          f"{tourf}")
                heaters_total = db.execute(_sqltext(
                    "SELECT count(*) FROM player_stats_cache c "
                    "JOIN players p ON p.id = c.player_id " + _where),
                    params).scalar() or 0
                hrows = db.execute(_sqltext(
                    "SELECT c.player_id, (c.payload->'form'->>'streak')::int AS streak "
                    "FROM player_stats_cache c JOIN players p ON p.id = c.player_id "
                    + _where +
                    f" ORDER BY streak DESC, c.player_id LIMIT {HEATERS_MAX}"),
                    params).all()
                hid = [r[0] for r in hrows]
                if hid:
                    hp = {p.id: p for p in db.execute(
                        select(Player).where(Player.id.in_(hid))).scalars()}
                    hlast = dict(db.execute(
                        select(Player.id, func.max(Match.match_date))
                        .join(Match, ((Match.winner_id == Player.id)
                                      | (Match.loser_id == Player.id)))
                        .where(Player.id.in_(hid), Match.is_duplicate.is_(False))
                        .group_by(Player.id)).all())
                    hw = dict(db.execute(select(Match.winner_id, func.count()).where(
                        Match.winner_id.in_(hid), Match.is_duplicate.is_(False))
                        .group_by(Match.winner_id)).all())
                    hl = dict(db.execute(select(Match.loser_id, func.count()).where(
                        Match.loser_id.in_(hid), Match.is_duplicate.is_(False))
                        .group_by(Match.loser_id)).all())
                    for pid, streak in hrows:
                        if pid in hp:
                            heaters.append((hp[pid], streak, hlast.get(pid),
                                            hw.get(pid, 0), hl.get(pid, 0)))

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
<td style="text-align:center;width:26px">{_fav_btn(p.id, csrf, p.id in fav_ids)}</td>
<td><a href="/player/{p.id}" style="text-decoration:none">
<span class="pname">{esc(p.full_name)}</span></a></td>
<td>{tag('neutral', '·', p.tour.upper())}</td>
<td class="mono sub2">{esc(p.ioc or '—')}</td>
<td class="mono">{w}-{l}</td>
<td class="mono sub2">{esc(ls) if ls else '—'}</td>
<td class="mono sub2">{player_age(p.dob)}</td>
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
    heaters_html = ""
    if heaters:
        cards = "".join(
            f'<a href="/player/{p.id}" class="card" '
            f'style="text-decoration:none;display:block;color:inherit;position:relative">'
            f'<div style="position:absolute;top:12px;right:12px">'
            f'{_fav_btn(p.id, csrf, p.id in fav_ids)}</div>'
            f'<div style="font-size:22px;font-weight:800;color:var(--good)">🔥 W{streak}</div>'
            f'<div class="title" style="margin:4px 0 2px;font-size:15px">{esc(p.full_name)}</div>'
            f'<div class="sub2">{p.tour.upper()} · {esc(p.ioc or "—")} · {w}-{l}'
            f'{f" · last {ls}" if ls else ""}</div></a>'
            for p, streak, ls, w, l in heaters)
        heaters_html = (
            '<div class="blockhead"><h4>🔥 Heaters</h4>'
            '<span class="aside">players riding a win streak (5+) · '
            + (f'hottest {len(heaters)} of {heaters_total}'
               if heaters_total > len(heaters) else f'{len(heaters)} shown')
            + '</span></div>'
            '<div class="rule"></div>'
            '<div class="cards" style="grid-template-columns:'
            f'repeat(auto-fill,minmax(190px,1fr));margin-bottom:26px">{cards}</div>')

    body = pagehead("Database", "Players", f"{heading} · {len(plist)} shown") + heaters_html + f"""
<form method="get" action="/players" style="display:flex;gap:10px;flex-wrap:wrap;
 align-items:center;margin:0 0 18px">
<input name="q" value="{esc(q)}" placeholder="Search any player…"
 style="flex:1;min-width:220px;background:var(--surface);border:1px solid var(--divider);
 color:var(--text);font:inherit;padding:10px 14px">
{filters}
<button class="tag tag-outline" type="submit" style="cursor:pointer;padding:9px 16px">Apply</button>
</form>
<div class="tw"><table class="t">
<tr><th></th><th>player</th><th>tour</th><th>country</th><th>record</th><th>last match</th><th>age</th><th>hand</th></tr>
{rows or '<tr><td colspan="8" class="empty">No players match these filters.</td></tr>'}
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
    user = request.get("user") or {}
    sess = request.get("session_cookie") or ""
    csrf = webauth.csrf_token(sess) if sess else ""
    as_of = datetime.now(timezone.utc).date() + timedelta(days=1)
    with db_session() as db:
        p = db.get(Player, pid)
        if p is None:
            return web.Response(status=404, text="no such player")
        is_fav = pid in _fav_player_ids(db, user)
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
    _favc = _fav_btn(p.id, csrf, is_fav)
    fav_line = (f'<div style="margin:-6px 0 14px;font-size:13px">{_favc} '
                f'<span class="sub2">{"favorited" if is_fav else "favorite this player"}'
                f'</span></div>') if _favc else ""
    body = pagehead(p.tour.upper() + (f" · {p.ioc}" if p.ioc else ""),
                    p.full_name, f"{prof.matches_in_db} matches in DB{age}") + fav_line + strip + f"""
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


def _match_view(event_ticker: str, include_scenario_plan: bool = True,
                csrf: str = ""):
    """Build the full match-data view sections (profiles, model read, H2H, style,
    live stats, game-by-game, price chart, optional gameflow plan). Returns
    (title, sub, sections_html) or None if the event isn't a matched two-sided
    market. Shared by the /match page AND embedded in the scenario page."""
    from bot.models import Scenario
    from bot.stats.profile import (
        build_profile,
        compute_matchup,
        compute_set_rates_both,
        load_history,
    )

    as_of = datetime.now(timezone.utc).date() + timedelta(days=1)
    with db_session() as db:
        sides = db.execute(select(KalshiMarket).where(
            KalshiMarket.event_ticker == event_ticker)
            .order_by(KalshiMarket.ticker)).scalars().all()
        if len(sides) < 2 or any(m.player_a_id is None for m in sides[:2]):
            return None
        a, b = sides[0], sides[1]
        pa, pb = db.get(Player, a.player_a_id), db.get(Player, b.player_a_id)
        hist_a, hist_b = load_history(db, pa.id), load_history(db, pb.id)
        prof_a, prof_b = build_profile(db, pa.id, as_of), build_profile(db, pb.id, as_of)
        sr_a = compute_set_rates_both(hist_a, as_of)
        sr_b = compute_set_rates_both(hist_b, as_of)
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
        # watch-side state (scenario's market) for correct asymmetric-trigger orientation
        watch_est = (db.execute(select(LiveMatchState).where(
            LiveMatchState.market_ticker == sc.market_ticker)).scalar()
            if sc is not None else None)
        # which bots bet this match (and why) and which didn't (and why not)
        from bot.scenarios import SERIES_TIER
        _tier = SERIES_TIER.get((a.raw or {}).get("_series", ""), "15")
        bot_activity = bot_activity_html(db, sc, quotes, _tier)
        # this match's surface (resolved from the tournament in the title) — used
        # to highlight the relevant surface split on the profile cards
        from bot.models import Match as _Match
        from bot.models import PlayerRanking as _PR
        from bot.stats.surface import resolve_surface

        # ranking trajectory from the weekly snapshots: career-high + movement
        def _rank_traj(pid):
            snaps = db.execute(select(_PR.as_of, _PR.rank).where(
                _PR.player_id == pid).order_by(_PR.as_of)).all()
            if not snaps:
                return None
            ranks = [r for _, r in snaps]
            return {"cur": ranks[-1], "high": min(ranks),
                    "move": ranks[0] - ranks[-1], "n": len(ranks)}
        rank_traj = {pa.id: _rank_traj(pa.id), pb.id: _rank_traj(pb.id)}
        _tt = (a.title or "").rsplit(":", 1)
        _tourney = (_tt[-1].replace(" match?", "").replace(" match", "").strip()
                    if len(_tt) > 1 else "")
        match_surface = resolve_surface(db, _tourney)
        if not match_surface and a.match_id:  # fall back to the matched Match's surface
            _mrow = db.get(_Match, a.match_id)
            match_surface = _mrow.surface if _mrow else None

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
        return (f"{(q[0] + q[1]) / 2:.0f}¢"
                if q and q[0] is not None and q[1] is not None else "—")

    def _wl(stat):  # "59% (17-12)" or "—"
        return (f"{stat.value:.0%} <span class='sub2'>({stat.wins}-{stat.losses})</span>"
                if stat is not None and stat.value is not None else "—")

    def profile_col(p, prof, sr):
        f, d = prof.form, prof.deciding
        # each set shows THREE windows, freshest first: last 3 months (primary) →
        # past year → career
        _lbl = 'font-size:10.5px;letter-spacing:.05em'
        cells = "".join(
            f'<div class="metric"><div class="k">set {n}</div>'
            f'<div class="v mono">{_wl(r90)}</div>'
            f'<div class="sub2" style="{_lbl}">LAST 3 MO</div>'
            f'<div class="mono" style="margin-top:5px">{_wl(r365)}</div>'
            f'<div class="sub2" style="{_lbl}">PAST YEAR</div>'
            f'<div class="mono" style="margin-top:5px">{_wl(rcar)}</div>'
            f'<div class="sub2" style="{_lbl}">CAREER</div>'
            f'</div>'
            for n, (r90, r365, rcar) in sorted(sr.items()) if n <= 3)
        # handedness — lefties highlighted (the notable matchup factor); honest
        # "unknown" when we don't have it rather than assuming right-handed
        hand_lbl = {"R": "right-handed",
                    "L": '<span style="color:var(--accent);font-weight:700">left-handed ◀</span>'
                    }.get(p.hand, '<span class="sub2">hand unknown</span>')
        # rank + trajectory (career-high, recent movement from the snapshots)
        rj = rank_traj.get(p.id) or {}
        _traj = ""
        if p.rank and rj.get("n", 0) > 1:
            mv = rj["move"]
            arrow = (f'<span style="color:var(--good)">▲{mv}</span>' if mv > 0
                     else f'<span style="color:var(--accent)">▼{abs(mv)}</span>' if mv < 0 else "±0")
            _traj = f' <span class="sub2">(CH #{rj["high"]} · {arrow})</span>'
        elif p.rank and rj.get("high") and rj["high"] < p.rank:
            _traj = f' <span class="sub2">(CH #{rj["high"]})</span>'
        rank_lbl = (f'<span style="font-weight:700">#{p.rank}</span>{_traj} · '
                    if p.rank else "")
        # --- scannable tile groups (replaces the old run-on prose lines) --------
        # Every stat is one compact tile: big value on top, muted label below, so
        # the eye scans a grid instead of parsing sentences. Tiles omit silently
        # when the underlying stat is absent/omitted (honest thin-sample handling).
        def _tile(sv, sl, cls="", sub="", tip=""):
            ss = f'<div class="ss mono">{sub}</div>' if sub else ""
            t = f' title="{esc(tip)}"' if tip else ""
            return (f'<div class="st {cls}"{t}><div class="sv">{sv}</div>'
                    f'<div class="sl">{sl}</div>{ss}</div>')

        def _pt(val, fmt, label, cls=""):  # percentage/number tile; omit if None
            return _tile(fmt.format(val), label, cls) if val is not None else ""

        def _grp(cap, tiles):
            body = "".join(t for t in tiles if t)
            return (f'<div class="stcap">{esc(cap)}</div>'
                    f'<div class="strow">{body}</div>') if body else ""

        def _cv(stat):  # conditional/clutch pct value, or None when omitted/thin
            return (stat.value if stat and not stat.is_omitted
                    and stat.value is not None else None)

        def _rt(stat, label):  # record tile ("7-3"); omit if omitted/thin
            return (_tile(f"{stat.wins}-{stat.losses}", label)
                    if stat and not stat.is_omitted and stat.value is not None else "")

        # RECORD — overall match record + current streak (a 1-match streak is
        # noise, so only surface a run of 2+)
        streak_v = (("W" + str(f.streak)) if f.streak >= 2
                    else ("L" + str(abs(f.streak))) if f.streak <= -2 else "—")
        record_grp = _grp("Record", [
            _tile(f"{f.win_rate_365.wins}-{f.win_rate_365.losses}", "Past yr",
                  tip="Win-loss record over the past 12 months"),
            _tile(f"{f.win_rate_career.wins}-{f.win_rate_career.losses}", "Career",
                  tip="Career win-loss record in the database"),
            _tile(streak_v, "Streak",
                  tip="Current run of consecutive wins (W) or losses (L)"),
        ])

        # SERVE / RETURN — each rate carries a tour-relative band + plain tooltip,
        # so a non-analyst reads "strong / average / below avg" instead of a bare %
        def _bt(val, fmt, label, key, plain, lower_better=False):
            if val is None:
                return ""
            q, col = _bench_band(p.tour, key, val, lower_better)
            b = _TOUR_BENCH.get((p.tour or "").lower(), {}).get(key)
            tip = plain + (f" · tour avg {fmt.format(b[1])}" if b else "")
            sub = f'<span style="color:{col}">{esc(q)}</span>' if q else ""
            vstyle = f'color:{col}' if col else ""
            return (f'<div class="st" title="{esc(tip)}">'
                    f'<div class="sv" style="{vstyle}">{fmt.format(val)}</div>'
                    f'<div class="sl">{esc(label)}</div>'
                    f'<div class="ss">{sub}</div></div>')
        sr = prof.serve_return
        serve_grp = ret_grp = ""
        if sr and sr.n_matches:
            serve_grp = _grp(f"Serve · {sr.n_matches} matches", [
                _bt(sr.hold_pct, "{:.0%}", "Hold", "hold", "Service games held"),
                _bt(sr.ace_pct, "{:.1%}", "Ace", "ace", "Aces per service point"),
                _bt(sr.df_pct, "{:.1%}", "DF", "df",
                    "Double faults per service point", lower_better=True),
                _bt(sr.first_in_pct, "{:.0%}", "1st in", "first_in",
                    "First serves that land in"),
                _bt(sr.first_win_pct, "{:.0%}", "1st won", "first_won",
                    "Points won behind the 1st serve"),
                _bt(sr.second_win_pct, "{:.0%}", "2nd won", "second_won",
                    "Points won behind the 2nd serve"),
                _bt(sr.bp_saved_pct, "{:.0%}", "BP saved", "bp_saved",
                    "Break points saved on serve"),
            ])
            ret_grp = _grp("Return", [
                _bt(sr.break_pct, "{:.0%}", "Break", "break",
                    "Share of opponent service games broken"),
                _bt(sr.return_pts_win_pct, "{:.0%}", "Ret pts", "ret_pts",
                    "Return points won"),
            ])

        # FORM — recent-match spectrum (+ this surface)
        form_grp = _grp("Form", [
            _rt(f.last5, "Last 5"),
            _rt(f.last10, "Last 10"),
            _rt(f.last20, "Last 20"),
            (_rt(f.last10_surface, esc(f.surface.lower())) if f.surface else ""),
        ])

        # CLUTCH — set-1 conditionals, deciders, dominance, quality of wins
        cond = prof.conditional
        cl = prof.clutch
        sk = (d.skunk_share_of_wins_365 if not d.skunk_share_of_wins_365.is_omitted
              else d.skunk_share_of_wins_career)
        tb = prof.clutch.tiebreak if prof.clutch else None

        def _rec_of(stat):  # "W-L" when the stat carries a record, else ""
            return (f"{stat.wins}-{stat.losses}" if stat and stat.wins is not None
                    and stat.losses is not None else "")

        def _ptc(stat, label, tip=""):  # clutch %-tile with its W-L record beneath
            v = _cv(stat)
            return _tile(f"{v:.0%}", label, sub=_rec_of(stat), tip=tip) if v is not None else ""
        clutch_tiles = []
        if cond:
            clutch_tiles += [
                _ptc(cond.win_given_set1_won, "After S1",
                     "Win rate in matches where they took the first set"),
                _ptc(cond.win_given_set1_lost, "Comeback",
                     "Win rate after dropping the first set"),
                _ptc(cond.set3_given_lost_set2, "Win S3",
                     "Win rate in the deciding set after losing set 2"),
            ]
        # straight-set share is a % of WINS (not a W-L) — show n as "skunks/wins"
        if _cv(sk) is not None:
            _skn = sk.n or 0
            clutch_tiles.append(_tile(f"{sk.value:.0%}", "Straight",
                                      sub=(f"{round(sk.value * _skn)}/{_skn} W" if _skn else ""),
                                      tip="Share of their wins that came in straight sets"))
        clutch_tiles.append(_ptc(d.best, "Deciders", "Record in deciding (final) sets"))
        clutch_tiles.append(_ptc(tb, "Tiebreaks", "Tiebreaks won"))
        # only show vs-Top-50 with a non-trivial sample (a 0-1 isn't a stat)
        if cl and cl.vs_top50 and not cl.vs_top50.is_omitted and (cl.vs_top50.n or 0) >= 3:
            clutch_tiles.append(_ptc(cl.vs_top50, "vs Top50",
                                     "Record against top-50 opponents"))
        clutch_grp = _grp("Clutch", clutch_tiles)

        # SURFACE — career clay/hard/grass split; the match surface is accented
        ss = p.surface_stats or {}
        _msurf = (match_surface or "").lower()
        surf_tiles = []
        for key, lbl in (("hard", "Hard"), ("clay", "Clay"), ("grass", "Grass")):
            v = ss.get(key) or {}
            tot = (v.get("w") or 0) + (v.get("l") or 0)
            if tot:
                surf_tiles.append(_tile(f'{v["w"]/tot:.0%}', f'{lbl} ({tot})',
                                        cls="on" if key == _msurf else ""))
        surf_cap = (f"Surface · on {match_surface}" if match_surface else "Surface")
        surf_grp = _grp(surf_cap, surf_tiles)

        # COMPETITION — strength of field per window (categorical → stays inline)
        _fcol = {"elite": "var(--good)", "strong": "var(--good)",
                 "mid": "var(--text)", "weak": "var(--warning)"}

        def _comp(sb):
            if not sb or not sb.avg_opp_rank:
                return '<span class="sub2">—</span>'
            return (f'<strong style="color:{_fcol.get(sb.field, "var(--muted)")}">'
                    f'{esc(sb.field)}</strong> '
                    f'<span class="mono sub2">#{int(sb.avg_opp_rank)}</span>')
        sw = prof.schedule_windows or {}
        comp_line = ""
        if any(sw.get(k) and sw[k].avg_opp_rank for k in ("last90", "last365", "career")):
            comp_line = (f'<div class="prose" style="margin-top:10px">Competition — '
                         f'3 mo: {_comp(sw.get("last90"))} · '
                         f'past year: {_comp(sw.get("last365"))} · '
                         f'career: {_comp(sw.get("career"))}</div>')

        # WORKLOAD — idle days, return-from-layoff, acute decider load, 7d volume
        la = prof.layoff
        rl = prof.recent_load or {}
        wbits = []
        if la:
            if la.days_since_last_match is not None:
                _c = "var(--warning)" if la.days_since_last_match >= 21 else "var(--muted)"
                wbits.append(f'<span style="color:{_c}">{la.days_since_last_match}d idle</span>')
            if la.return_layoff_days and la.record_since_return:
                w, l = la.record_since_return
                wbits.append(f'back from {la.return_layoff_days}d · '
                             f'<span class="mono">{w}-{l}</span> since')
            if la.deciders_last_3d:
                wbits.append(f'<span style="color:var(--warning)">{la.deciders_last_3d} '
                             f'deciders in 3d</span>')
        if rl.get("m"):
            lb = [f'{rl["m"]} match{"es" if rl["m"] != 1 else ""}', f'{rl["sets"]} sets']
            if rl.get("min"):
                lb.append(f'{rl["min"]} min')
            _c = ("var(--warning)" if rl["m"] >= 3 or (rl.get("min") or 0) >= 360
                  else "var(--muted)")
            wbits.append(f'<span style="color:{_c}">{" / ".join(lb)} (7d)</span>')
        work_line = (f'<div class="prose" style="margin-top:6px">Workload: '
                     f'{" · ".join(wbits)}</div>' if wbits else "")

        # plain-English one-line "read" — the takeaway a non-analyst wants first,
        # synthesised from the benchmarked bands + form + fitness
        def _read_line():
            bits: list[str] = []
            if sr and sr.n_matches:
                aq = _bench_band(p.tour, "ace", sr.ace_pct)[0]
                hq = _bench_band(p.tour, "hold", sr.hold_pct)[0]
                dq = _bench_band(p.tour, "df", sr.df_pct, lower_better=True)[0]
                bq = _bench_band(p.tour, "break", sr.break_pct)[0]
                if aq in ("elite", "strong") and hq in ("elite", "strong"):
                    bits.append("big, reliable server")
                elif aq in ("elite", "strong"):
                    bits.append("big server")
                elif hq == "below avg":
                    bits.append("serve holds below tour average")
                elif hq in ("elite", "strong"):
                    bits.append("holds serve comfortably")
                if dq == "below avg":
                    bits.append("prone to double faults")
                if bq in ("elite", "strong"):
                    bits.append("dangerous on return")
                elif bq == "below avg" and "serve holds below tour average" not in bits:
                    bits.append("below average on return")
            if f.streak >= 3:
                bits.append(f"won {f.streak} in a row")
            elif f.streak <= -3:
                bits.append(f"lost {abs(f.streak)} in a row")
            la_ = prof.layoff
            if la_ and la_.return_layoff_days and (la_.days_since_last_match or 99) < 30:
                bits.append(f"just back from a {la_.return_layoff_days}-day layoff")
            elif la_ and (la_.days_since_last_match or 0) >= 21:
                bits.append(f"{la_.days_since_last_match} days without a match")
            if not bits:
                return ""
            txt = "; ".join(bits)
            return (f'<div class="prose" style="margin:8px 0 2px;font-size:13.5px;'
                    f'color:var(--text)">{esc(txt[0].upper() + txt[1:])}.</div>')
        read_line = _read_line()

        return f"""<div class="card">
<a href="/player/{p.id}" style="text-decoration:none">
<div class="title">{esc(p.full_name)}</div></a>
<div class="sub2">{rank_lbl}{p.tour.upper()} · {esc(p.ioc or '')}
{f"· {player_age(p.dob)}y" if p.dob else ""} · {hand_lbl} ·
{prof.matches_in_db} matches in DB</div>
{read_line}
<div class="metric-grid" style="grid-template-columns:repeat(3,1fr)">{cells}</div>
{record_grp}
{serve_grp}
{ret_grp}
{form_grp}
<button class="prof-toggle" type="button">＋ More stats</button>
<div class="prof-more">
{clutch_grp}
{surf_grp}
{comp_line}
{work_line}
</div>
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
        wmid = ((wq[0] + wq[1]) / 2
                if wq and wq[0] is not None and wq[1] is not None else None)
        trig = trigger_html(sc, watch_est.state if watch_est else None,
                            est is not None, wmid)
        scenario_html = f"""<section class="block"><div class="blockhead">
<h4>Gameflow plan</h4><span class="aside">generated {sc.created_for}</span></div>
<div class="rule"></div>
<p style="margin:0 0 10px">{trig}</p>
{read_list(sc.narrative)}</section>"""
    yes_name = (a.raw or {}).get("yes_sub_title", "side A")
    chart_html = price_chart_svg(chart_points, marks, f"{yes_name} to win")

    style_html = ""
    if style_notes:
        style_html = f"""<section class="block"><div class="blockhead">
<h4>Style matchup</h4><span class="aside">from Match Charting shot data</span></div>
<div class="rule"></div>""" + "".join(
            f'<div class="prose">{esc(nt)}</div>' for nt in style_notes) + "</section>"

    # Combined live match stats: the broadcast-style serve/game-stat comparison
    # (split bars, YES=orange vs sibling=blue) PLUS the historical win% behind
    # the two live serve signals — "when they serve this many aces, they win X%"
    # and the same facing an opponent, for aces and double faults. Orientation
    # (YES↔competitor) is fixed at record time; skip if the feed carried none.
    livestats_html = ""
    if live_row and live_row.detail:
        sa, sb = _oriented_live_stats(live_row.detail)
        if sa is not None:
            from bot.stats.profile import serve_conditional_winrate
            sa, sb = sa or {}, sb or {}
            na, nb = esc(pa.full_name.split()[-1]), esc(pb.full_name.split()[-1])
            # muted warm/cool pair — easy on the eyes, still CVD-distinguishable
            # (a full-saturation orange/blue on every row was too loud)
            A, B = "#b06a55", "#5f7a9c"

            def g(d, k):
                return _num(d, k)
            ay, ao = _num(sa, "aces") or 0, _num(sb, "aces") or 0
            dy, do = _num(sa, "double_faults") or 0, _num(sb, "double_faults") or 0
            pta, ptb = _num(sa, "points_won"), _num(sb, "points_won")

            def _cnt(v):
                return "—" if v is None else f"{int(v)}"

            def pr(num, den):
                # (split value, "X% (num/den)") for a made/attempted stat — every
                # number is a real feed count; None → no data for this stat yet
                if num is None or not den:
                    return None, "—"
                return num / den * 100, f'{num / den * 100:.0f}% <span class="sub2">({int(num)}/{int(den)})</span>'

            # serve points played (feed gives won + lost); needed for 1st-serve-in %
            def _spts(s):
                w = g(s, "service_points_won")
                return (int(w) + int(g(s, "service_points_lost") or 0)) if w is not None else None
            # 1st serve in %, 1st/2nd serve points won
            fsa_v, fsa_d = pr(g(sa, "first_serve_successful"), _spts(sa))
            fsb_v, fsb_d = pr(g(sb, "first_serve_successful"), _spts(sb))
            f1a_v, f1a_d = pr(g(sa, "first_serve_points_won"), g(sa, "first_serve_successful"))
            f1b_v, f1b_d = pr(g(sb, "first_serve_points_won"), g(sb, "first_serve_successful"))
            s2a_v, s2a_d = pr(g(sa, "second_serve_points_won"), g(sa, "second_serve_successful"))
            s2b_v, s2b_d = pr(g(sb, "second_serve_points_won"), g(sb, "second_serve_successful"))

            # Feed semantics: total_breakpoints = a player's break-point OPPORTUNITIES
            # on return; breakpoints_won = those converted. So a player's break
            # points FACED on serve = the OPPONENT's return opportunities.
            # break points SAVED on serve = faced − opponent's conversions
            def _bp_saved(opp):
                faced = g(opp, "total_breakpoints")  # = own faced on serve
                if faced is None:
                    return pr(None, None)
                return pr(max(0, int(faced) - int(g(opp, "breakpoints_won") or 0)), faced)
            bsa_v, bsa_d = _bp_saved(sb)
            bsb_v, bsb_d = _bp_saved(sa)

            # service games won / played (played = won + times broken = opp breaks)
            def _svc_games(own, opp):
                won = g(own, "service_games_won")
                if won is None:
                    return pr(None, None)
                return pr(won, int(won) + int(g(opp, "breakpoints_won") or 0))
            sga_v, sga_d = _svc_games(sa, sb)
            sgb_v, sgb_d = _svc_games(sb, sa)

            # RETURN: return points won = opp serves made − opp serve points won
            def _ret_pts(opp, made_k, won_k):
                made, won = g(opp, made_k), g(opp, won_k)
                if made is None or won is None:
                    return pr(None, None)
                return pr(max(0, int(made) - int(won)), made)
            r1a_v, r1a_d = _ret_pts(sb, "first_serve_successful", "first_serve_points_won")
            r1b_v, r1b_d = _ret_pts(sa, "first_serve_successful", "first_serve_points_won")
            r2a_v, r2a_d = _ret_pts(sb, "second_serve_successful", "second_serve_points_won")
            r2b_v, r2b_d = _ret_pts(sa, "second_serve_successful", "second_serve_points_won")
            # break points won on return = own conversions / own opportunities
            bwa_v, bwa_d = pr(g(sa, "breakpoints_won"), g(sa, "total_breakpoints"))
            bwb_v, bwb_d = pr(g(sb, "breakpoints_won"), g(sb, "total_breakpoints"))

            # return games won (= own breaks) / played (= opp service games =
            # opp service games won + times opp was broken = own breaks)
            def _ret_games(own, opp):
                won, ogw = g(own, "breakpoints_won"), g(opp, "service_games_won")
                if won is None or ogw is None:
                    return pr(None, None)
                return pr(won, int(ogw) + int(won))
            rga_v, rga_d = _ret_games(sa, sb)
            rgb_v, rgb_d = _ret_games(sb, sa)

            def bar(label, av, bv, ad, bd, lower_better=False):
                av, bv = av or 0, bv or 0
                tot = av + bv
                ap = (av / tot * 100) if tot else 50
                a_win = (av < bv) if lower_better else (av > bv)
                b_win = (bv < av) if lower_better else (bv > av)
                asf = f"color:{A};font-weight:800" if a_win else "color:var(--muted)"
                bsf = f"color:{B};font-weight:800" if b_win else "color:var(--muted)"
                return (
                    '<div style="display:flex;justify-content:space-between;'
                    'align-items:center;font-size:12.5px;margin:11px 0 4px">'
                    f'<span class="mono" style="{asf}">{ad}</span>'
                    f'<span class="sub2">{label}</span>'
                    f'<span class="mono" style="{bsf}">{bd}</span></div>'
                    '<div style="display:flex;gap:3px;height:6px;'
                    'background:var(--surface-2);border-radius:4px;overflow:hidden">'
                    f'<div style="width:{ap:.1f}%;background:{A}"></div>'
                    f'<div style="width:{100 - ap:.1f}%;background:{B}"></div></div>')

            def subhead(t):
                return (f'<div class="sub2" style="text-transform:uppercase;'
                        f'letter-spacing:.08em;font-size:10px;font-weight:700;'
                        f'margin:16px 0 4px;color:var(--muted)">{t}</div>')

            def _wrs(s):
                return (f'{s.value:.0%} <span class="sub2">({s.wins}–{s.losses})</span>'
                        if s and s.value is not None else '<span class="sub2">—</span>')

            def ctx(key):
                # thresholds = the live counts; a's self vs a's opp (= b's live
                # count), and the mirror for b
                ta = int(ay) if key == "ace" else int(dy)
                tb = int(ao) if key == "ace" else int(do)
                unit = "aces" if key == "ace" else "DF"

                def ph(t):
                    return "clean" if t <= 0 else f"{t}+ {unit}"
                a_self = serve_conditional_winrate(hist_a, as_of, key=key, side="self", thresh=ta)
                a_opp = serve_conditional_winrate(hist_a, as_of, key=key, side="opp", thresh=tb)
                b_self = serve_conditional_winrate(hist_b, as_of, key=key, side="self", thresh=tb)
                b_opp = serve_conditional_winrate(hist_b, as_of, key=key, side="opp", thresh=ta)
                lines = []
                if a_self.value is not None or a_opp.value is not None:
                    lines.append(f'<div><span style="color:{A}">●</span> {na} '
                                 f'serving {ph(ta)} {_wrs(a_self)} · facing {ph(tb)} {_wrs(a_opp)}</div>')
                if b_self.value is not None or b_opp.value is not None:
                    lines.append(f'<div><span style="color:{B}">●</span> {nb} '
                                 f'serving {ph(tb)} {_wrs(b_self)} · facing {ph(ta)} {_wrs(b_opp)}</div>')
                if not lines:
                    return ""
                return ('<div style="margin:6px 0 2px;padding:7px 11px;'
                        'background:var(--surface);border-radius:6px;'
                        'font-size:11.5px;line-height:1.7">'
                        '<div class="sub2" style="text-transform:uppercase;'
                        'letter-spacing:.06em;font-size:9.5px;margin-bottom:2px">'
                        'historical win% at this serve volume</div>'
                        + "".join(lines) + '</div>')

            # --- MATCH INFO: clean head-to-head table (left value · label · right
            #     value), each figure a live count from the Kalshi score feed ---
            def _cnt2(v):
                return "—" if v is None else str(int(v))

            def _frac(nn, dd):
                if nn is None or dd is None:
                    return "—", 0.0
                return f"{int(nn)}/{int(dd)}", float(nn)

            def _games_won(s):  # total games won — use the feed's aggregate, which
                # includes tiebreak games and actual breaks. Fall back to
                # holds + breaks only if the feed omits the aggregate. (Do NOT
                # recompute from service_games_won + breakpoints_won: that drops
                # tiebreak games and conflates break points with break games.)
                gw = g(s, "games_won")
                if gw is not None:
                    return int(gw)
                sg, bp = g(s, "service_games_won"), g(s, "breakpoints_won")
                return None if sg is None and bp is None else int(sg or 0) + int(bp or 0)

            def _row(label, a_str, b_str, a_val, b_val, lower_better=False):
                if a_str == "—" and b_str == "—":
                    return ""
                a_lead = (a_val < b_val) if lower_better else (a_val > b_val)
                b_lead = (b_val < a_val) if lower_better else (b_val > a_val)
                aw = ("color:var(--text);font-weight:800" if a_lead
                      else "color:var(--muted)")
                bw = ("color:var(--text);font-weight:800" if b_lead
                      else "color:var(--muted)")
                return (
                    '<div style="display:grid;grid-template-columns:1fr auto 1fr;'
                    'align-items:center;padding:10px 0;border-bottom:1px solid var(--divider)">'
                    f'<span class="mono" style="{aw};font-size:17px">{a_str}</span>'
                    f'<span class="sub2" style="text-align:center;padding:0 14px">{label}</span>'
                    f'<span class="mono" style="{bw};font-size:17px;text-align:right">{b_str}</span>'
                    '</div>')

            gwa, gwb = _games_won(sa), _games_won(sb)
            svga, svgb = g(sa, "service_games_won"), g(sb, "service_games_won")
            f1a_s, f1a_n = _frac(g(sa, "first_serve_points_won"), g(sa, "first_serve_successful"))
            f1b_s, f1b_n = _frac(g(sb, "first_serve_points_won"), g(sb, "first_serve_successful"))
            s2a_s, s2a_n = _frac(g(sa, "second_serve_points_won"), g(sa, "second_serve_successful"))
            s2b_s, s2b_n = _frac(g(sb, "second_serve_points_won"), g(sb, "second_serve_successful"))
            bpa_s, bpa_n = _frac(g(sa, "breakpoints_won"), g(sa, "total_breakpoints"))
            bpb_s, bpb_n = _frac(g(sb, "breakpoints_won"), g(sb, "total_breakpoints"))
            # active in-match performance flags (aces / hot form) per player,
            # straight from the live feed counts — but only while the feed is
            # genuinely live (not final, and updated within the last 5 min), so a
            # frozen end-of-match snapshot doesn't keep flashing "serve wall".
            _fls = ((datetime.now(timezone.utc) - live_row.ts).total_seconds()
                    if getattr(live_row, "ts", None) else None)
            _flags_ok = (not live_row.is_final) and _fls is not None and _fls <= 300
            # serve baselines (this player's own ace/DF rate) → significance flags
            _ba = ({"ace": prof_a.serve_return.ace_pct, "df": prof_a.serve_return.df_pct}
                   if prof_a.serve_return else None)
            _bb = ({"ace": prof_b.serve_return.ace_pct, "df": prof_b.serve_return.df_pct}
                   if prof_b.serve_return else None)
            fa_html = _flags_html(_live_flags(sa, sb, _ba)) if _flags_ok else ""
            fb_html = _flags_html(_live_flags(sb, sa, _bb), right=True) if _flags_ok else ""
            header = (
                '<div style="display:flex;justify-content:space-between;align-items:flex-start;'
                'gap:12px;padding-bottom:10px;border-bottom:1px solid var(--divider-strong);'
                'margin-bottom:2px">'
                f'<div><div style="font-size:16px;font-weight:600">{_flag(pa.ioc)} {na}</div>'
                f'{fa_html}</div>'
                f'<div style="text-align:right"><div style="font-size:16px;font-weight:600">'
                f'{nb} {_flag(pb.ioc)}</div>{fb_html}</div></div>')

            # one combined section, all in the clean head-to-head row style: raw
            # counts, then serve %s, the historical win% context, then return %s.
            # (No bar charts; each stat appears once, deduped.)
            def _subrow(t):
                return (f'<div class="sub2" style="text-transform:uppercase;'
                        f'letter-spacing:.08em;font-size:10px;font-weight:700;'
                        f'color:var(--muted);padding:16px 0 2px">{t}</div>')

            def _grp(title, rws):
                body = "".join(r for r in rws if r)
                return (_subrow(title) + body) if body else ""

            counts = "".join(r for r in [
                _row("Aces", _cnt2(ay), _cnt2(ao), ay, ao),
                _row("Double faults", _cnt2(dy), _cnt2(do), dy, do, lower_better=True),
                _row("Games won", _cnt2(gwa), _cnt2(gwb), gwa or 0, gwb or 0),
                _row("Points won", _cnt2(pta), _cnt2(ptb), pta or 0, ptb or 0),
            ] if r)
            serve = _grp("Serve", [
                _row("1st serve in", fsa_d, fsb_d, fsa_v or 0, fsb_v or 0),
                _row("1st-serve pts won", f1a_d, f1b_d, f1a_v or 0, f1b_v or 0),
                _row("2nd-serve pts won", s2a_d, s2b_d, s2a_v or 0, s2b_v or 0),
                _row("Service games won", _cnt2(svga), _cnt2(svgb), svga or 0, svgb or 0),
                _row("Break pts saved", bsa_d, bsb_d, bsa_v or 0, bsb_v or 0),
            ])
            ctxb = "".join(c for c in (ctx("ace"), ctx("df")) if c)
            ret = _grp("Return", [
                _row("1st return pts won", r1a_d, r1b_d, r1a_v or 0, r1b_v or 0),
                _row("2nd return pts won", r2a_d, r2b_d, r2a_v or 0, r2b_v or 0),
                _row("Break pts won", bwa_d, bwb_d, bwa_v or 0, bwb_v or 0),
                _row("Return games", rga_d, rgb_d, rga_v or 0, rgb_v or 0),
            ])
            # feed-freshness aside — the feed goes quiet the moment a match ends
            # (Kalshi stops sending live_data), so a snapshot can sit frozen for a
            # long time. Be honest about how recent this data is.
            _age_s = None
            if getattr(live_row, "ts", None):
                _age_s = (datetime.now(timezone.utc) - live_row.ts).total_seconds()

            def _ago(sec):
                m = int(sec // 60)
                if m < 1:
                    return f"{int(sec)}s ago"
                return f"{m}m ago" if m < 60 else f"{m // 60}h {m % 60}m ago"
            if live_row.is_final:
                feed_aside = ('<span class="aside">final · last score from the '
                              'Kalshi feed</span>')
            elif _age_s is not None and _age_s > 180:
                feed_aside = (f'<span class="aside" style="color:var(--warning)">'
                              f'⚠ feed idle · last update {_ago(_age_s)}</span>')
            elif _age_s is not None:
                feed_aside = (f'<span class="aside">live from the Kalshi score feed '
                              f'· updated {_ago(_age_s)}</span>')
            else:
                feed_aside = '<span class="aside">live from the Kalshi score feed</span>'
            livestats_html = (
                '<section class="block"><div class="blockhead"><h4>Match info</h4>'
                f'{feed_aside}</div>'
                f'<div class="rule"></div>{header}{counts}{serve}{ctxb}{ret}'
                '<p class="sub2" style="margin-top:12px">Every figure is a live count '
                'from Kalshi’s score feed for this match. Percentages show '
                'made/attempted; win% rows read this match against each player’s '
                'historical record at that volume.</p></section>')

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
        def _wr(stat):
            return (f"{stat.value:.0%} <span class='sub2'>({stat.wins}-{stat.losses})</span>"
                    if stat and stat.value is not None else "—")
        wr = _wr(f.win_rate_career)
        recent = _wr(f.win_rate_365)
        recent90 = _wr(f.win_rate_90)
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
                f'<div class="vsrow"><span class="k">last 3 months</span><span class="v mono">{recent90}</span></div>'
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
    # DECIDER flag — a SEPARATE signal that weights deciding-set win rates more
    # heavily than the base model. Deliberately isolated from the calibrated bet
    # pipeline (set-form is non-predictive on its own; this is a labelled lens).
    decider_line = ""
    if sc is not None and sc.prematch_prob is not None:
        from bot.prob.decider import DECIDER_MIN_N, DECIDER_SET_WEIGHT, decider_read
        from bot.prob.model import MatchState
        from bot.prob.state_adjust import condition_on_state

        def _dec_wr(prof):
            b = prof.deciding.best if prof and prof.deciding else None
            return (b.value if b and not b.is_omitted and b.value is not None
                    and (b.n or 0) >= DECIDER_MIN_N else None)
        pm_a = sc.prematch_prob if sc.player_id == pa.id else 1 - sc.prematch_prob
        base_dec = condition_on_state(pm_a, MatchState(1, 1, 3))   # A at a Bo3 decider
        wr_a, wr_b = _dec_wr(prof_a), _dec_wr(prof_b)
        blended, sig = decider_read(base_dec, wr_a, wr_b, DECIDER_SET_WEIGHT)
        if sig is not None:
            fav_p = pa if blended >= 0.5 else pb
            fav_prob = max(blended, 1 - blended)
            base_fav = base_dec if blended >= 0.5 else 1 - base_dec
            da = prof_a.deciding.best
            dbk = prof_b.deciding.best
            decider_line = (
                f'<p class="prose" style="margin-top:10px;padding-top:10px;'
                f'border-top:1px solid var(--divider)">'
                f'<span class="tag tag-outline">⚑ DECIDER READ</span> '
                f'if it reaches a deciding set: <strong>{esc(fav_p.full_name)} '
                f'{fav_prob:.0%}</strong> '
                f'<span class="sub2">(base model {base_fav:.0%} → '
                f'{int(DECIDER_SET_WEIGHT*100)}% weight on deciding-set form)</span><br>'
                f'<span class="sub2">deciding-set record — {esc(pa.full_name.split()[-1])} '
                f'{da.wins}-{da.losses} ({da.value:.0%}) · '
                f'{esc(pb.full_name.split()[-1])} {dbk.wins}-{dbk.losses} ({dbk.value:.0%}) · '
                f'separate signal, not the calibrated bet model</span></p>')
    model_read_html = (f'<section class="block"><div class="blockhead">'
                       f'<h4>Model read vs raw form</h4>'
                       f'<span class="aside">why the pick can differ from win rate</span></div>'
                       f'<div class="rule"></div>{model_line}{decider_line}'
                       f'<div class="vsgrid">{_mr_cell(pa, prof_a)}{_mr_cell(pb, prof_b)}</div></section>')

    # Broadcast-style scoreboard — the same per-set grid as the live view, KEPT
    # for finished matches (labeled final) so a match's score is always shown
    # here in this format, not just while it's in play.
    score_html = ""
    if score_rows and score_rows[0].scoreline:
        top = score_rows[0]
        grid = score_grid((top.scoreline, top.sets_a, top.sets_b),
                          pa.full_name.split()[-1], pb.full_name.split()[-1],
                          pa.ioc, pb.ioc)
        if grid:
            badge = ('<span class="aside">final</span>' if top.is_final else
                     '<span class="aside" style="color:var(--good)">● live</span>')
            score_html = (f'<section class="block"><div class="blockhead">'
                          f'<h4>Score</h4>{badge}</div>'
                          f'<div class="rule"></div>{grid}</section>')

    # --- head-to-head aligned comparison — the matchup on SHARED rows with the
    #     stronger side bold. This is the takeaway (esp. on mobile, where two
    #     side-by-side tile cards are impossible to compare). ---
    def _h2h_rows():
        rows = []

        def add(label, va, vb, fmt, key=None, lower=False):
            if va is None and vb is None:
                return
            a_on = va is not None and vb is not None and ((va < vb) if lower else (va > vb))
            b_on = va is not None and vb is not None and ((vb < va) if lower else (vb > va))

            def cell(v, on, tour, align):
                if v is None:
                    return f'<div class="mono sub2" style="text-align:{align}">—</div>'
                _, col = _bench_band(tour, key, v, lower) if key else (None, None)
                w = ("font-weight:800;color:var(--text)" if on
                     else f"color:{col or 'var(--muted)'}")
                return f'<div class="mono" style="{w};text-align:{align}">{fmt.format(v)}</div>'
            rows.append(
                '<div style="display:grid;grid-template-columns:1fr auto 1fr;'
                'align-items:center;padding:9px 0;border-bottom:1px solid var(--divider)">'
                f'{cell(va, a_on, pa.tour, "left")}'
                f'<span class="sub2" style="text-align:center;padding:0 14px">{esc(label)}</span>'
                f'{cell(vb, b_on, pb.tour, "right")}</div>')
        s1, s2 = prof_a.serve_return, prof_b.serve_return
        if s1 and s2 and (s1.n_matches or s2.n_matches):
            add("Hold", s1.hold_pct, s2.hold_pct, "{:.0%}", "hold")
            add("Ace rate", s1.ace_pct, s2.ace_pct, "{:.1%}", "ace")
            add("Double faults", s1.df_pct, s2.df_pct, "{:.1%}", "df", lower=True)
            add("1st-serve won", s1.first_win_pct, s2.first_win_pct, "{:.0%}", "first_won")
            add("2nd-serve won", s1.second_win_pct, s2.second_win_pct, "{:.0%}", "second_won")
            add("Break", s1.break_pct, s2.break_pct, "{:.0%}", "break")
            add("Return pts", s1.return_pts_win_pct, s2.return_pts_win_pct, "{:.0%}", "ret_pts")
        add("Form (last 10)", prof_a.form.last10.value, prof_b.form.last10.value, "{:.0%}")
        da, db = prof_a.deciding.best, prof_b.deciding.best
        add("Deciding sets", None if da.is_omitted else da.value,
            None if db.is_omitted else db.value, "{:.0%}")
        return "".join(rows)
    _h2h_body = _h2h_rows()
    la, lb = esc(pa.full_name.split()[-1]), esc(pb.full_name.split()[-1])
    h2h_compare_html = (
        f'<section class="block"><div class="blockhead"><h4>Matchup</h4>'
        f'<span class="aside">bold = stronger side</span></div><div class="rule"></div>'
        f'<div style="display:grid;grid-template-columns:1fr auto 1fr;padding-bottom:8px;'
        f'border-bottom:1px solid var(--divider-strong);margin-bottom:2px">'
        f'<div style="font-weight:700;font-size:14px">{la}</div><span></span>'
        f'<div style="font-weight:700;font-size:14px;text-align:right">{lb}</div></div>'
        f'{_h2h_body}</section>') if _h2h_body else ""

    title = ((a.title or "").split(":")[0].replace("Will ", "") or event_ticker)
    sub = f"{px(a)} / {px(b)}{state_txt}"
    # "log a bet" action — records a personal bet on either side from this page
    # (same modal as the live board). Only when signed in (csrf present).
    _betbtn = _bet_btn(event_ticker, a, b, quotes, csrf)
    bet_bar = (f'<div class="betbar">{_betbtn}'
               f'<span class="sub2">log a personal bet on this match — '
               f'records to My Bets, places no order</span></div>') if _betbtn else ""
    sections = f"""
{bet_bar}
{score_html}
{livestats_html}
{h2h_compare_html}
{_STAT_LEGEND}
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
{scorelog_html}
{bot_activity}
{scenario_html if include_scenario_plan else ""}
<p class="sub2 mono">{esc(a.ticker)} · {esc(b.ticker)}</p>"""
    return title, sub, sections


def _partial_match_view(event_ticker: str):
    """Degraded view for a real two-sided market where at least one side isn't
    matched to a DB player — common for obscure ITF/low-tier entrants Sackmann
    doesn't cover. Shows the live scoreline and the side(s) we DO have, with an
    honest banner naming the unmatched player, instead of a hard 404. Returns
    (title, sub, html) or None if this isn't a two-sided market at all."""
    from sqlalchemy import text as _sqltext

    with db_session() as db:
        sides = db.execute(select(KalshiMarket).where(
            KalshiMarket.event_ticker == event_ticker)
            .order_by(KalshiMarket.ticker)).scalars().all()
        if len(sides) < 2:
            return None
        a, b = sides[0], sides[1]

        def side_info(m):
            p = db.get(Player, m.player_a_id) if m.player_a_id else None
            name = (p.full_name if p else None) or (m.raw or {}).get("yes_sub_title") or "?"
            return {"name": name, "pid": (p.id if p else None),
                    "ioc": (p.ioc if p else None), "matched": p is not None}
        ia, ib = side_info(a), side_info(b)
        if ia["matched"] and ib["matched"]:
            return None  # both matched — _match_view should have handled it

        # latest recorded scoreline (a-perspective of whichever side has one)
        rows = db.execute(_sqltext(
            "SELECT DISTINCT ON (market_ticker) market_ticker, scoreline, sets_a, sets_b "
            "FROM match_score_log WHERE market_ticker = ANY(:t) "
            "ORDER BY market_ticker, ts DESC"),
            {"t": [a.ticker, b.ticker]}).all()
        by_tk = {r[0]: (r[1], r[2], r[3]) for r in rows}
        grid = ""
        sm = a if by_tk.get(a.ticker, (None,))[0] else (b if by_tk.get(b.ticker, (None,))[0] else None)
        if sm is not None:
            other = b if sm is a else a
            si, oi = (ia, ib) if sm is a else (ib, ia)
            grid = score_grid(by_tk[sm.ticker], si["name"].split()[-1],
                              oi["name"].split()[-1], si["ioc"], oi["ioc"])

        _tt = (a.title or "").rsplit(":", 1)
        tourney = (_tt[-1].replace(" match?", "").replace(" match", "").strip()
                   if len(_tt) > 1 else "")
        title = f"{ia['name']} vs {ib['name']}"
        sub = tourney or event_ticker

        def side_line(info):
            if info["matched"]:
                return (f'<a href="/player/{info["pid"]}" class="nm" '
                        f'style="text-decoration:none">{_flag(info["ioc"])} '
                        f'{esc(info["name"])}</a> '
                        f'<span class="sub2">— in database</span>')
            return (f'<span class="nm">{esc(info["name"])}</span> '
                    f'<span class="sub2" style="color:var(--warning)">— not in '
                    f'database (queued for review)</span>')

        missing = [i["name"] for i in (ia, ib) if not i["matched"]]
        banner = (f'<div style="border:1px solid var(--warning);border-radius:8px;'
                  f'padding:12px 14px;margin-bottom:18px;background:'
                  f'rgba(210,150,60,.06)">'
                  f'<div style="font-weight:700;color:var(--warning);'
                  f'margin-bottom:4px">Limited view</div>'
                  f'<p class="prose" style="margin:0">'
                  f'{esc(" and ".join(missing))} '
                  f'{"is" if len(missing) == 1 else "are"} not in the player '
                  f'database yet — no history, model read, or matchup can be built '
                  f'for this match. Sackmann doesn\'t cover this entrant; it\'s been '
                  f'queued for manual review. The live score is shown below when a '
                  f'feed exists.</p></div>')
        grid_html = (f'<section class="block"><div class="blockhead"><h4>Score</h4>'
                     f'<span class="aside" style="color:var(--good)">● live</span></div>'
                     f'<div class="rule"></div>{grid}</section>' if grid else "")
        sections = (f"{banner}"
                    f'<section class="block"><div class="blockhead"><h4>Players</h4>'
                    f'</div><div class="rule"></div>'
                    f'<p class="prose">{side_line(ia)}</p>'
                    f'<p class="prose">{side_line(ib)}</p></section>'
                    f"{grid_html}"
                    f'<p class="sub2 mono">{esc(a.ticker)} · {esc(b.ticker)}</p>')
        return title, sub, sections


async def match_detail(request: web.Request) -> web.Response:
    event = request.match_info["event"]
    sess = request.get("session_cookie") or ""
    csrf = webauth.csrf_token(sess) if sess else ""
    res = _match_view(event, csrf=csrf)
    if res is None:
        res = _partial_match_view(event)
    if res is None:
        return web.Response(status=404, text="match not found or unmatched")
    title, sub, sections = res
    # datalist so the bet modal's tag input autocompletes the user's tags
    user = request.get("user")
    datalist = ""
    if csrf and user:
        with db_session() as db:
            datalist = _tags_datalist(_user_bet_tags(db, user["id"]))
    return respond(request, "Match", "scenarios",
                   pagehead("Match", title, sub) + sections + datalist)


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
async def auth_guard(request: web.Request, handler):
    """Gate every route on a valid session. Unauthenticated visitors get the
    login page (browser) or a 401 (API). /healthz and the login/logout routes
    themselves are the only open paths."""
    path = request.path
    if path == "/healthz":
        return await handler(request)
    # resolve current user → a plain dict for handlers + nav (session closed here)
    with db_session() as db:
        u = webauth.current_user(request, db)
        if u is not None:
            request["user"] = {"id": u.id, "username": u.username,
                               "is_admin": bool(u.is_admin)}
            request["session_cookie"] = request.cookies.get(webauth.SESSION_COOKIE)
    if path in ("/login", "/logout"):
        return await handler(request)
    if request.get("user") is None:
        if path.startswith("/api/"):
            return web.json_response({"error": "unauthorized"}, status=401)
        raise web.HTTPFound("/login?next=" + quote(request.path_qs, safe=""))
    return await handler(request)


def _secure_cookie(request: web.Request) -> bool:
    proto = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip()
    return request.secure or proto == "https"


_AUTH_INP = ("width:100%;box-sizing:border-box;background:var(--surface);"
             "border:1px solid var(--divider);color:var(--text);font:inherit;"
             "padding:10px 12px;border-radius:6px;margin-top:4px")
_AUTH_BTN = ("width:100%;margin-top:16px;background:var(--accent);color:#fff;"
             "border:none;font:inherit;font-weight:700;padding:11px;"
             "border-radius:6px;cursor:pointer")


def _login_html(next_url: str, error: str = "") -> str:
    err = (f'<p style="color:var(--accent);margin:0 0 12px;font-size:13px">'
           f'{esc(error)}</p>' if error else "")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in · DEUCE</title>{_FAVICON_LINK}<style>{CSS}</style></head><body>
<main style="max-width:380px;margin:12vh auto;padding:0 20px">
<div class="brand" style="font-size:22px;margin-bottom:18px">{_LOGO_MARK}DEUCE<span class="tag">advisory only</span></div>
<section class="block"><div class="blockhead"><h4>Sign in</h4></div>
<div class="rule"></div>{err}
<form method="post" action="/login">
<input type="hidden" name="next" value="{esc(next_url)}">
<label class="sub2">Username</label>
<input name="username" autocomplete="username" autofocus required style="{_AUTH_INP}">
<label class="sub2" style="margin-top:10px;display:block">Password</label>
<input name="password" type="password" autocomplete="current-password" required style="{_AUTH_INP}">
<button type="submit" style="{_AUTH_BTN}">Sign in</button>
</form></section>
<p class="sub2" style="text-align:center;margin-top:14px">Access is restricted —
contact an admin for an account.</p>
</main></body></html>"""


async def pin_toggle(request: web.Request) -> web.Response:
    """Toggle a per-user pin on a match (by event_ticker). CSRF-protected; a
    bookmark only — it changes nothing about what the bot watches or bets."""
    user = request.get("user")
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    sess = request.get("session_cookie") or ""
    data = await request.post()
    if not webauth.csrf_ok(sess, data.get("csrf")):
        return web.json_response({"error": "csrf"}, status=403)
    ev = (data.get("event_ticker") or "").strip()
    if not ev:
        return web.json_response({"error": "missing event_ticker"}, status=400)
    with db_session() as db:
        existing = db.execute(select(UserPin).where(
            UserPin.user_id == user["id"],
            UserPin.event_ticker == ev)).scalars().first()
        if existing:
            db.delete(existing)
            pinned = False
        else:
            db.add(UserPin(user_id=user["id"], event_ticker=ev))
            pinned = True
        db.commit()
    return web.json_response({"pinned": pinned})


async def favorite_toggle(request: web.Request) -> web.Response:
    """Toggle a per-user favorite on a player (by player_id). CSRF-protected; a
    bookmark only — it changes nothing about what the bot watches or bets."""
    user = request.get("user")
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    sess = request.get("session_cookie") or ""
    data = await request.post()
    if not webauth.csrf_ok(sess, data.get("csrf")):
        return web.json_response({"error": "csrf"}, status=403)
    try:
        pid = int(data.get("player_id") or 0)
    except (TypeError, ValueError):
        pid = 0
    if not pid:
        return web.json_response({"error": "missing player_id"}, status=400)
    from bot.models import UserFavoritePlayer
    with db_session() as db:
        existing = db.execute(select(UserFavoritePlayer).where(
            UserFavoritePlayer.user_id == user["id"],
            UserFavoritePlayer.player_id == pid)).scalars().first()
        if existing:
            db.delete(existing)
            fav = False
        else:
            db.add(UserFavoritePlayer(user_id=user["id"], player_id=pid))
            fav = True
        db.commit()
    return web.json_response({"favorited": fav})


async def bet_create(request: web.Request) -> web.Response:
    """Record a personal manual bet (side + entry price + shares). A ledger entry
    only — the app is advisory-only and places NO orders."""
    user = request.get("user")
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    sess = request.get("session_cookie") or ""
    data = await request.post()
    if not webauth.csrf_ok(sess, data.get("csrf")):
        return web.json_response({"error": "csrf"}, status=403)
    mt = (data.get("market_ticker") or "").strip()
    ev = (data.get("event_ticker") or "").strip()
    name = (data.get("player_name") or "").strip()[:128] or "?"
    opp = (data.get("opponent_name") or "").strip()[:128] or None
    try:
        price = int(round(float(data.get("price", ""))))
        shares = int(round(float(data.get("shares", ""))))
    except (TypeError, ValueError):
        return web.json_response({"error": "bad price/shares"}, status=400)
    if not mt or not (1 <= price <= 99) or shares < 1:
        return web.json_response({"error": "invalid bet"}, status=400)
    # a bet can be tailed from several sources → comma-separated tags
    tags = _bet_tags(data.get("tag"))
    tag = (", ".join(tags))[:64] or None
    with db_session() as db:
        from bot.models import AppUser, UserTag
        au = db.get(AppUser, user["id"])
        # unit = the first tag's own unit size when set, else the personal unit
        unit = au.mybets_unit_usd if au and au.mybets_unit_usd else 500
        if tags:
            uts = {t.tag: t.unit_usd for t in db.execute(select(UserTag).where(
                UserTag.user_id == user["id"], UserTag.tag.in_(tags))).scalars()
                if t.unit_usd}
            for t in tags:
                if uts.get(t):
                    unit = uts[t]
                    break
        # the side bought = YES on the player's own market ticker; stamp the unit
        # size in effect so a later change never rescales this bet's units
        db.add(UserBet(user_id=user["id"], event_ticker=ev, market_ticker=mt,
                       side="yes", player_name=name, opponent_name=opp,
                       entry_price_cents=price, shares=shares, unit_usd=unit,
                       tag=tag))
        db.commit()
    return web.json_response({"ok": True})


async def bet_set_unit(request: web.Request) -> web.Response:
    """Change the user's current My Bets unit size. Applies only to bets placed
    from now on — past bets keep the unit they were stamped with, so each change
    starts a fresh, accurately-tracked epoch on the ledger."""
    user = request.get("user")
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    sess = request.get("session_cookie") or ""
    data = await request.post()
    if not webauth.csrf_ok(sess, data.get("csrf")):
        return web.json_response({"error": "csrf"}, status=403)
    try:
        unit = int(round(float(data.get("unit", ""))))
    except (TypeError, ValueError):
        raise web.HTTPFound("/mybets")
    if 1 <= unit <= 1_000_000:
        from bot.models import AppUser
        with db_session() as db:
            au = db.get(AppUser, user["id"])
            if au is not None:
                au.mybets_unit_usd = unit
                db.commit()
    raise web.HTTPFound("/mybets")


async def bet_delete(request: web.Request) -> web.Response:
    """Delete one of the caller's own bets (mistake/removal)."""
    user = request.get("user")
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    sess = request.get("session_cookie") or ""
    data = await request.post()
    if not webauth.csrf_ok(sess, data.get("csrf")):
        return web.json_response({"error": "csrf"}, status=403)
    try:
        bid = int(data.get("id", "0") or 0)
    except ValueError:
        return web.json_response({"error": "bad id"}, status=400)
    with db_session() as db:
        b = db.get(UserBet, bid)
        if b is not None and b.user_id == user["id"]:
            db.delete(b)
            db.commit()
    raise web.HTTPFound("/mybets")


def cash_out_bet(db, b, price_cents: int, shares: int | None = None) -> None:
    """Realize a cash-out on bet `b` at `price_cents`.

    Selling the whole position exits it in place. Selling FEWER shares scales out:
    the sold slice is split off into its own cashed-out ledger row (same entry,
    tag, placed-at and stamped unit, pointing back at `b`), and `b` keeps running
    with the shares that are left. Repeated part-sells each get their own row, so
    every slice keeps the price it was actually sold at."""
    sh = b.shares if shares is None else max(1, min(int(shares), b.shares))
    now = datetime.now(timezone.utc)
    if sh >= b.shares:
        b.exit_price_cents = price_cents
        b.exit_at = now
        return
    db.add(UserBet(
        user_id=b.user_id, event_ticker=b.event_ticker,
        market_ticker=b.market_ticker, side=b.side, player_name=b.player_name,
        opponent_name=b.opponent_name, entry_price_cents=b.entry_price_cents,
        shares=sh, unit_usd=b.unit_usd, exit_price_cents=price_cents, exit_at=now,
        parent_bet_id=b.id, note=b.note, tag=b.tag,
        created_at=b.created_at))
    b.shares -= sh


def uncash_bet(db, b) -> None:
    """Undo a cash-out. A slice sold off a larger position folds its shares back
    into that position (identical entry price, so there is nothing to blend) and
    disappears; anything else simply reverts to 'held'. If the position it came
    from is gone, or has since been exited or re-priced, the slice stays its own
    row and just reopens."""
    if b.parent_bet_id:
        p = db.get(UserBet, b.parent_bet_id)
        if (p is not None and p is not b and p.user_id == b.user_id
                and p.exit_price_cents is None
                and p.entry_price_cents == b.entry_price_cents):
            p.shares += b.shares
            db.delete(b)
            return
    b.exit_price_cents = None
    b.exit_at = None


async def bet_cashout(request: web.Request) -> web.Response:
    """Cash one of the caller's bets out at a given price — all of it, or just
    part of the position (scale out) — or clear the exit to revert it to 'held'."""
    user = request.get("user")
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    sess = request.get("session_cookie") or ""
    data = await request.post()
    if not webauth.csrf_ok(sess, data.get("csrf")):
        return web.json_response({"error": "csrf"}, status=403)
    try:
        bid = int(data.get("id", "0") or 0)
    except ValueError:
        return web.json_response({"error": "bad id"}, status=400)
    action = data.get("action") or "set"
    with db_session() as db:
        b = db.get(UserBet, bid)
        if b is not None and b.user_id == user["id"]:
            if action == "clear":
                uncash_bet(db, b)
            else:
                try:
                    px = int(round(float(data.get("exit_price", ""))))
                except (TypeError, ValueError):
                    raise web.HTTPFound("/mybets")
                raw = (data.get("exit_shares") or "").strip()
                try:
                    sh = int(round(float(raw))) if raw else None
                except (TypeError, ValueError):
                    raise web.HTTPFound("/mybets")
                if 0 <= px <= 100:
                    cash_out_bet(db, b, px, sh)
            db.commit()
    raise web.HTTPFound("/mybets")


async def bet_edit(request: web.Request) -> web.Response:
    """Change a bet (correct its entry/shares) or ADD onto it (extra shares at a
    price → blended cost basis). Only the caller's own bets; a ledger edit only."""
    user = request.get("user")
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    sess = request.get("session_cookie") or ""
    data = await request.post()
    if not webauth.csrf_ok(sess, data.get("csrf")):
        return web.json_response({"error": "csrf"}, status=403)
    try:
        bid = int(data.get("id", "0") or 0)
    except ValueError:
        raise web.HTTPFound("/mybets")
    action = data.get("action") or "edit"
    with db_session() as db:
        b = db.get(UserBet, bid)
        if b is None or b.user_id != user["id"]:
            raise web.HTTPFound("/mybets")
        if action == "tag":
            # a bet may carry several comma-separated tails; normalize the list
            b.tag = (", ".join(_bet_tags(data.get("tag"))))[:64] or None
            db.commit()
            raise web.HTTPFound("/mybets")
        if action == "add":
            try:
                aps = int(round(float(data.get("add_shares", ""))))
                app_ = int(round(float(data.get("add_price", ""))))
            except (TypeError, ValueError):
                raise web.HTTPFound("/mybets")
            if aps >= 1 and 1 <= app_ <= 99:
                total = b.shares + aps
                # weighted-average entry (blended cost basis)
                b.entry_price_cents = int(round(
                    (b.shares * b.entry_price_cents + aps * app_) / total))
                b.shares = total
                db.commit()
        else:  # edit — set entry price and share count directly
            try:
                price = int(round(float(data.get("price", ""))))
                shares = int(round(float(data.get("shares", ""))))
            except (TypeError, ValueError):
                raise web.HTTPFound("/mybets")
            if 1 <= price <= 99 and shares >= 1:
                b.entry_price_cents = price
                b.shares = shares
                db.commit()
    raise web.HTTPFound("/mybets")


async def bet_tag_color(request: web.Request) -> web.Response:
    """Set (or clear) the colour for one of the user's bet tags. Cosmetic only."""
    import re as _re
    user = request.get("user")
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    sess = request.get("session_cookie") or ""
    data = await request.post()
    if not webauth.csrf_ok(sess, data.get("csrf")):
        return web.json_response({"error": "csrf"}, status=403)
    tag = (data.get("tag") or "").strip()[:64]
    color = (data.get("color") or "").strip()[:16]
    if color and not _re.match(r"^#[0-9a-fA-F]{6}$", color):
        color = ""
    if tag:
        from bot.models import UserTag
        with db_session() as db:
            row = db.execute(select(UserTag).where(
                UserTag.user_id == user["id"], UserTag.tag == tag)).scalars().first()
            if row is None:
                db.add(UserTag(user_id=user["id"], tag=tag, color=color or None))
            else:
                row.color = color or None
            db.commit()
    ref = data.get("ref") or "/mybets"
    raise web.HTTPFound(ref if ref.startswith("/mybets") else "/mybets")


async def bet_tag_unit(request: web.Request) -> web.Response:
    """Set the unit size ($/unit) for one of the user's bet tags. Applies only to
    bets logged with that tag from now on (past bets keep their stamped unit)."""
    user = request.get("user")
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    sess = request.get("session_cookie") or ""
    data = await request.post()
    if not webauth.csrf_ok(sess, data.get("csrf")):
        return web.json_response({"error": "csrf"}, status=403)
    tag = (data.get("tag") or "").strip()[:64]
    try:
        unit = int(round(float(data.get("unit", ""))))
    except (TypeError, ValueError):
        raise web.HTTPFound("/mybets")
    if tag and 1 <= unit <= 1_000_000:
        from bot.models import UserTag
        with db_session() as db:
            row = db.execute(select(UserTag).where(
                UserTag.user_id == user["id"], UserTag.tag == tag)).scalars().first()
            if row is None:
                db.add(UserTag(user_id=user["id"], tag=tag, unit_usd=unit))
            else:
                row.unit_usd = unit
            # "all" → also RE-STAMP every existing bet carrying this tag (fixes a
            # unit that was recorded before the tag had its own size). Multi-tag
            # bets store a comma-joined string, so an exact "tag == tag" SQL match
            # would miss them — filter in Python over each bet's parsed tag list.
            if data.get("action") == "all":
                for b in db.execute(select(UserBet).where(
                        UserBet.user_id == user["id"])).scalars():
                    if tag in _bet_tags(b.tag):
                        b.unit_usd = unit
            db.commit()
    raise web.HTTPFound("/mybets")


async def mybets(request: web.Request) -> web.Response:
    """The user's personal bet ledger: summary P&L / win-rate / CLV (mirroring the
    bot leaderboard), open positions marked to the live market, and settled
    history. Outcomes & closing lines are read live off each KalshiMarket."""
    from bot.track import advisory_outcome, clv_cents

    user = request.get("user")
    sess = request.get("session_cookie") or ""
    csrf = webauth.csrf_token(sess) if sess else ""
    uid = user["id"] if user else None

    with db_session() as db:
        rows = db.execute(
            select(UserBet, KalshiMarket)
            .join(KalshiMarket, KalshiMarket.ticker == UserBet.market_ticker,
                  isouter=True)
            .where(UserBet.user_id == uid)
            .order_by(UserBet.created_at.desc())).all() if uid else []
        open_tks = [b.market_ticker for b, _ in rows]
        quotes = _latest_quotes(db, open_tks) if open_tks else {}
        from bot.models import AppUser as _AU
        _au = db.get(_AU, uid) if uid else None
        current_unit = _au.mybets_unit_usd if _au and _au.mybets_unit_usd else 500
        bet_tags = _user_bet_tags(db, uid)
        from bot.models import UserTag
        _uts = list(db.execute(select(UserTag).where(
            UserTag.user_id == uid)).scalars()) if uid else []
        tag_colors = {t.tag: t.color for t in _uts if t.color}
        tag_units = {t.tag: t.unit_usd for t in _uts if t.unit_usd}

        settled, openp = [], []   # (bet, dict of computed fields)
        n_w = n_l = 0
        n_cashed = n_held = 0
        profit = cost_sum = 0.0
        clvs, roi_list = [], []
        unreal = mtm = 0.0
        for b, mk in rows:
            entry = b.entry_price_cents
            sh = b.shares
            cost = sh * entry / 100.0
            close = mk.close_yes_cents if mk else None
            clv = clv_cents(b.side, entry, close)
            if b.exit_price_cents is not None:
                # CASHED OUT — realized at the sell price, regardless of the result
                ex = b.exit_price_cents
                pnl = sh * (ex - entry) / 100.0
                outcome = "won" if pnl > 0 else "lost" if pnl < 0 else "void"
                n_cashed += 1
                n_w += pnl > 0
                n_l += pnl < 0
                profit += pnl
                cost_sum += cost
                if pnl != 0:
                    roi_list.append((ex - entry) / entry)
                if clv is not None:
                    clvs.append(clv)
                settled.append((b, {"outcome": outcome, "exit": "cashed",
                                    "exit_px": ex, "pnl": pnl, "cost": cost,
                                    "clv": clv}))
                continue
            outcome = advisory_outcome(b.side, mk.result if mk else None)
            if outcome in ("won", "lost", "void"):
                # HELD to settlement — outcome from the match result
                if outcome == "won":
                    pnl = sh * (100 - entry) / 100.0
                    n_w += 1
                elif outcome == "lost":
                    pnl = -sh * entry / 100.0
                    n_l += 1
                else:
                    pnl = 0.0
                n_held += 1
                profit += pnl
                cost_sum += cost
                if outcome != "void":
                    roi_list.append((100 - entry) / entry if outcome == "won" else -1.0)
                if clv is not None:
                    clvs.append(clv)
                settled.append((b, {"outcome": outcome, "exit": "held",
                                    "exit_px": None, "pnl": pnl, "cost": cost,
                                    "clv": clv}))
            else:
                cur = _odds_cents(mk, quotes)[0] if mk else None
                cur_val = sh * cur / 100.0 if cur is not None else None
                u = sh * (cur - entry) / 100.0 if cur is not None else None
                if cur_val is not None:
                    mtm += cur_val
                if u is not None:
                    unreal += u
                openp.append((b, {"cost": cost, "cur": cur, "mtm": cur_val,
                                  "unreal": u}))

        # --- Theoretical exits: apply two fixed policies to EVERY bet whose match
        # has settled, ignoring what the user actually did — (a) hold to the result,
        # (b) sell at 90¢ whenever our side ever reached a 90¢ bid (else the full
        # loss). Same 90¢ take-profit rule the bot leaderboard uses.
        settled_rows = [(b, mk) for b, mk in rows
                        if mk and mk.result in ("yes", "no")]
        max_bid: dict[str, int] = {}
        if settled_rows:
            from sqlalchemy import text as _sq
            _tks = list({b.market_ticker for b, _ in settled_rows})
            _since = min(b.created_at for b, _ in settled_rows)
            for r in db.execute(_sq(
                    "SELECT market_ticker, max(yes_bid) yb, max(no_bid) nb "
                    "FROM market_ticks WHERE market_ticker = ANY(:t) AND kind='quote' "
                    "AND ts >= :since GROUP BY market_ticker"),
                    {"t": _tks, "since": _since}).all():
                max_bid[r[0]] = r[1]  # yes_bid — our bets are always the YES side
        hold_p = tp_p = th_cost = 0.0
        hold_w = hold_l = tp_w = tp_l = 0
        for b, mk in settled_rows:
            entry, sh = b.entry_price_cents, b.shares
            won = (mk.result == b.side)
            th_cost += sh * entry / 100.0
            hp = sh * (100 - entry) / 100.0 if won else -sh * entry / 100.0
            hold_p += hp
            hold_w += won
            hold_l += not won
            if entry >= TP_LIMIT:            # already ≥90 — nothing left to take
                tp = hp
                tp_win = won
            else:
                hit = (max_bid.get(b.market_ticker) or 0) >= TP_LIMIT
                tp_win = hit or won
                tp = (sh * (TP_LIMIT - entry) / 100.0) if tp_win else -sh * entry / 100.0
            tp_p += tp
            tp_w += tp_win
            tp_l += not tp_win
        n_theo = len(settled_rows)

    units_current = profit / current_unit  # for the theoretical footnote

    def money(x):
        return f"${x:,.2f}" if x >= 0 else f"-${abs(x):,.2f}"

    # tag filter (?tag=) — drill into one tail's bets separately. The full lists
    # feed the always-on per-tag overview; the display is scoped to the selection.
    from urllib.parse import quote as _q
    sel = (request.query.get("tag") or "").strip()

    def _match(b):
        if not sel:
            return True
        if sel == "__untagged__":
            return not _bet_tags(b.tag)
        return sel in _bet_tags(b.tag)
    d_openp = [x for x in openp if _match(x[0])]
    d_settled = [x for x in settled if _match(x[0])]
    d_w = sum(1 for b, dd in d_settled if dd["outcome"] == "won")
    d_l = sum(1 for b, dd in d_settled if dd["outcome"] == "lost")
    d_profit = sum(dd["pnl"] for b, dd in d_settled)
    d_mtm = sum(dd["mtm"] for b, dd in d_openp if dd["mtm"] is not None)

    # unit-size control — changing it starts a NEW tracked epoch; past bets keep
    # the unit they were stamped with (no retroactive rescale)
    unit_control = (
        f'<section class="block major"><div class="blockhead"><h4>Personal unit size</h4>'
        f'<span class="aside">stake per unit for your personal bets (the ones '
        f'without a tag) · each tag can set its own below</span></div><div class="rule"></div>'
        f'<div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">'
        f'<div class="mono" style="font-size:22px;font-weight:800">${current_unit:,}</div>'
        f'<form method="post" action="/bet/unit" '
        f'style="display:flex;gap:8px;align-items:center">'
        f'<input type="hidden" name="csrf" value="{csrf}">'
        f'<span class="sub2">$</span>'
        f'<input name="unit" type="number" min="1" step="1" value="{current_unit}" '
        f'style="width:100px;box-sizing:border-box;background:var(--surface-2);'
        f'border:1px solid var(--divider);color:var(--text);font:inherit;'
        f'padding:7px 9px;border-radius:6px">'
        f'<button type="submit" class="tag tag-outline" style="cursor:pointer;'
        f'padding:7px 14px">Set unit</button></form></div></section>')

    # one aggregated performance summary over ALL settled bets. Unit sizes vary
    # per bet/epoch, so $ P&L is summed directly and normalized to NET UNITS
    # (each bet's pnl ÷ its own stamped unit) — no per-unit-size breakdown.
    def _unit_of(b):
        # tag unit WINS for tagged bets — a bet's tag is authoritative for its
        # unit size (first tag with a set unit), so blvr bets always size at the
        # blvr unit without re-stamping. Untagged / personal bets keep their
        # stamped unit (the epoch model).
        for t in _bet_tags(b.tag):
            if tag_units.get(t):
                return tag_units[t]
        return b.unit_usd or 500
    if d_settled:
        a_w = a_l = a_cashed = a_held = 0
        a_profit = a_cost = a_units = 0.0
        a_clvs: list = []
        for b, d in d_settled:
            a_profit += d["pnl"]
            a_cost += d["cost"]
            a_units += d["pnl"] / _unit_of(b)
            a_w += d["outcome"] == "won"
            a_l += d["outcome"] == "lost"
            a_cashed += d["exit"] == "cashed"
            a_held += d["exit"] != "cashed"
            if d["clv"] is not None:
                a_clvs.append(d["clv"])
        a_ns = a_w + a_l
        a_wr = a_w / a_ns if a_ns else None
        a_er = a_profit / a_cost if a_cost else None
        a_acv = sum(a_clvs) / len(a_clvs) if a_clvs else None
        a_dates = [b.created_at for b, _ in d_settled]
        strip = statstrip([
            ("Record", f'{a_w}-{a_l}',
             f"{a_wr:.0%} win rate" if a_wr is not None else "—"),
            ("Realized P&L",
             f'<span style="color:var(--{"good" if a_profit>=0 else "accent"})">'
             f'{money(a_profit)}</span>', f'{a_units:+.1f}u net'),
            ("ROI", f"{a_er:+.0%}" if a_er is not None else "—", "on staked $"),
            ("Exits", f'{a_cashed} cashed · {a_held} held', ""),
            ("Avg CLV", f"{a_acv:+.1f}¢" if a_acv is not None else "—",
             f'{len(a_clvs)} priced'),
        ])
        epoch_sections = (
            f'<section class="block major"><div class="blockhead"><h4>Performance</h4>'
            f'<span class="aside mono">{pt(min(a_dates))} – {pt(max(a_dates))} · '
            f'{len(d_settled)} settled · net units normalize each bet to its own '
            f'unit size</span></div>'
            f'<div class="rule"></div>{strip}</section>')
    else:
        epoch_sections = (
            '<section class="block major"><div class="blockhead"><h4>Performance</h4>'
            '</div><div class="rule"></div><div class="card"><div class="empty">'
            'No settled bets yet.</div></div></section>')

    def _clv_cell(c):
        if c is None:
            return '<span class="sub2">—</span>'
        col = "good" if c > 0 else "accent" if c < 0 else "muted"
        return f'<span class="mono" style="color:var(--{col})">{c:+d}¢</span>'

    # theoretical exit policies (hold-all vs cash-at-90-all), over settled matches
    hold_roi = hold_p / th_cost if th_cost else None
    tp_roi = tp_p / th_cost if th_cost else None

    def _pcol(x):
        return "good" if x >= 0 else "accent"

    def _theo_row(label, sub, pnl, roi_v, w, l):
        return (f'<tr><td>{label}<div class="sub2">{sub}</div></td>'
                f'<td class="mono" style="color:var(--{_pcol(pnl)})">{money(pnl)}</td>'
                f'<td class="mono">{roi_v:+.0%}</td>'
                f'<td class="mono">{w}-{l}</td></tr>')
    if n_theo:
        theo_html = (
            f'<section class="block major"><div class="blockhead">'
            f'<h4>Theoretical exits</h4><span class="aside">every settled bet under a '
            f'fixed policy · ignores what you actually did · {n_theo} bets</span></div>'
            f'<div class="rule"></div>'
            f'<div class="tw"><table class="t"><thead><tr><th>Policy</th>'
            f'<th>P&amp;L</th><th>ROI</th><th>Record</th></tr></thead><tbody>'
            + _theo_row("Hold every bet", "settle on the match result",
                        hold_p, hold_roi, hold_w, hold_l)
            + _theo_row("Cash out at 90¢", "sell whenever your side bid 90¢, else the loss",
                        tp_p, tp_roi, tp_w, tp_l)
            + '</tbody></table></div>'
            f'<p class="sub2" style="margin-top:8px">Your actual realized result is '
            f'{money(profit)}. These two rows show what the same bets would have '
            f'returned under each fixed exit rule (P&amp;L in $ — units vary by epoch '
            f'above).</p></section>')
    else:
        theo_html = ""

    def _usz(cost, unit):  # bet size expressed in units (e.g. "0.85u", "2u")
        u = cost / (unit or 500)
        return (f"{u:.2f}".rstrip("0").rstrip(".") or "0") + "u"

    def _units_str(u):  # signed net units (e.g. "+2.5u", "-10.87u")
        return (f"{u:+.2f}".rstrip("0").rstrip(".") if u else "0") + "u"

    # ids of positions that have had part of them sold off — their open row shows
    # only what is LEFT, so flag it rather than let the share count look wrong
    scaled_ids = {b.parent_bet_id for b, _ in rows if b.parent_bet_id}

    def _shares_cell(b):   # what's LEFT, plus what was already sold off
        if b.id not in scaled_ids:
            return str(b.shares)
        sold = sum(x.shares for x, _ in rows if x.parent_bet_id == b.id)
        return f'{b.shares}<div class="sub2">{sold} sold</div>'

    # open positions — marked to the live market, each with a cash-out control
    if d_openp:
        orows = "".join(
            f'<tr><td class="pick">{esc(b.player_name)}{_tagchip(b.tag, tag_colors)}'
            f'<div class="sub2">vs {esc(b.opponent_name or "?")}</div></td>'
            f'<td class="mono" data-label="Entry">{b.entry_price_cents}¢</td>'
            f'<td class="mono" data-label="Shares">{_shares_cell(b)}</td>'
            f'<td class="mono" data-label="Size" title="${d["cost"]:,.2f} staked · ${_unit_of(b):,}/unit">'
            f'{_usz(d["cost"], _unit_of(b))}<div class="sub2">${_unit_of(b):,}/u</div></td>'
            f'<td class="mono" data-label="Now">{(str(d["cur"])+"¢") if d["cur"] is not None else "—"}</td>'
            f'<td class="mono" data-label="Cost">{money(d["cost"])}</td>'
            f'<td class="mono" data-label="Value">{money(d["mtm"]) if d["mtm"] is not None else "—"}</td>'
            f'<td class="mono" data-label="Unreal." style="color:var(--{"good" if (d["unreal"] or 0)>=0 else "accent"})">'
            f'{money(d["unreal"]) if d["unreal"] is not None else "—"}</td>'
            f'<td data-label="Cash out">{_cashout_form(b, csrf, d["cur"])}</td>'
            f'<td data-label="" style="white-space:nowrap">{_edit_form(b, csrf)}'
            f'<a class="sub2" href="/match/{esc(b.event_ticker)}">match →</a>'
            f' {_del_form(b.id, csrf)}</td></tr>'
            for b, d in d_openp)
        open_html = (f'<section class="block major"><div class="blockhead">'
                     f'<h4>Open positions</h4><span class="aside">marked to the '
                     f'live market · {money(d_mtm)} value · held unless you cash out '
                     f'· sell fewer shares than you hold to scale out</span></div>'
                     f'<div class="rule"></div>'
                     f'<div class="tw"><table class="t rt"><thead><tr>'
                     f'<th>Pick</th><th>Entry</th><th>Shares</th><th>Size</th><th>Now</th>'
                     f'<th>Cost</th><th>Value</th><th>Unreal.</th><th>Cash out</th><th></th></tr></thead>'
                     f'<tbody>{orows}</tbody></table></div></section>')
    else:
        open_html = ('<section class="block major"><div class="blockhead">'
                     '<h4>Open positions</h4></div><div class="rule"></div>'
                     '<div class="card"><div class="empty">No open bets. Use the '
                     '“＋ bet” button on the Live page to log one.</div></div></section>')

    # settled history
    def _exit_cell(b, d):
        if d["exit"] == "cashed":
            part = bool(b.parent_bet_id)
            return (f'<span class="tag tag-outline">cashed {d["exit_px"]}¢</span> '
                    f'{_reopen_form(b.id, csrf, part)}'
                    + ('<div class="sub2">part of a larger position</div>'
                       if part else ''))
        return '<span class="sub2">held → settled</span>'
    if d_settled:
        hrows = "".join(
            f'<tr><td class="pick">{esc(b.player_name)}{_tagchip(b.tag, tag_colors)} <span class="sub2">vs {esc(b.opponent_name or "?")}</span></td>'
            f'<td class="sub2 mono" data-label="Placed">{pt(b.created_at)}</td>'
            f'<td data-label="Result">{tag("good","✓","won") if d["outcome"]=="won" else tag("neutral","·","push") if d["outcome"]=="void" else tag("accent","✕","lost")}</td>'
            f'<td class="mono" data-label="Entry">{b.entry_price_cents}¢ × {b.shares}'
            f'<div class="sub2">{money(d["cost"])} buy-in</div></td>'
            f'<td class="mono" data-label="Size" '
            f'title="{money(d["cost"])} staked at ${_unit_of(b):,}/unit">'
            f'{_usz(d["cost"], _unit_of(b))}<div class="sub2">${_unit_of(b):,}/u</div></td>'
            f'<td data-label="Exit">{_exit_cell(b, d)}</td>'
            f'<td class="mono" data-label="P&amp;L" style="color:var(--{"good" if d["pnl"]>=0 else "accent"})">{money(d["pnl"])}</td>'
            f'<td data-label="CLV">{_clv_cell(d["clv"])}</td>'
            f'<td data-label="" style="white-space:nowrap">{_edit_form(b, csrf)}'
            f'<a class="sub2" href="/match/{esc(b.event_ticker)}">match →</a>'
            f' {_del_form(b.id, csrf)}</td></tr>'
            for b, d in d_settled)
        hist_html = (f'<section class="block major"><div class="blockhead">'
                     f'<h4>History</h4><span class="aside">settled &amp; cashed-out · '
                     f'{d_w}-{d_l}, {money(d_profit)} · each exit is its own line, '
                     f'so a scaled-out position appears once per slice</span></div>'
                     f'<div class="rule"></div>'
                     f'<div class="tw"><table class="t rt"><thead><tr>'
                     f'<th>Pick</th><th>Placed</th><th>Result</th><th>Entry</th>'
                     f'<th>Size</th><th>Exit</th><th>P&amp;L</th><th>CLV</th><th></th></tr></thead>'
                     f'<tbody>{hrows}</tbody></table></div></section>')
    else:
        hist_html = ('<section class="block major"><div class="blockhead">'
                     '<h4>History</h4></div><div class="rule"></div>'
                     '<div class="card"><div class="empty">No settled bets yet.</div>'
                     '</div></section>')

    # --- performance grouped by tag (e.g. people you tail) ---
    def _tp_new():
        return {"w": 0, "l": 0, "push": 0, "profit": 0.0, "cost": 0.0,
                "units": 0.0, "staked_u": 0.0,
                "clvs": [], "settled": 0, "open": 0, "open_val": 0.0}
    # a bet tailed from several sources counts fully toward EACH of its tags
    # (per-source view), so tag totals can overlap; the overall ledger counts once
    tag_perf: dict[str, dict] = {}
    for b, d in settled:
        for k in (_bet_tags(b.tag) or [""]):
            g = tag_perf.setdefault(k, _tp_new())
            g["settled"] += 1
            g["profit"] += d["pnl"]
            g["cost"] += d["cost"]
            g["units"] += d["pnl"] / _unit_of(b)
            g["staked_u"] += d["cost"] / _unit_of(b)
            if d["outcome"] == "won":
                g["w"] += 1
            elif d["outcome"] == "lost":
                g["l"] += 1
            else:
                g["push"] += 1
            if d["clv"] is not None:
                g["clvs"].append(d["clv"])
    for b, d in openp:
        for k in (_bet_tags(b.tag) or [""]):
            g = tag_perf.setdefault(k, _tp_new())
            g["open"] += 1
            if d["mtm"] is not None:
                g["open_val"] += d["mtm"]
    has_tags = any(k for k in tag_perf)
    tag_section = ""
    if has_tags:
        def _tagname(k):   # clickable → drills into that tag's bets (?tag=)
            if k:
                return (f'<a href="/mybets?tag={_q(k)}" style="text-decoration:none">'
                        f'<span class="tag tag-outline" style="{_tag_color_style(tag_colors.get(k))}">'
                        f'🏷 {esc(k)}</span></a>')
            return ('<a href="/mybets?tag=__untagged__" class="sub2" '
                    'style="text-decoration:none">personal →</a>')

        def _unit_cell(k):   # per-tag unit size (personal shown read-only)
            if not k:
                return f'<span class="mono sub2">${current_unit:,} · personal</span>'
            val = tag_units.get(k) or ""
            return (f'<form method="post" action="/bet/tagunit" class="swrow" '
                    f'style="align-items:center;gap:5px">'
                    f'<input type="hidden" name="csrf" value="{csrf}">'
                    f'<input type="hidden" name="tag" value="{esc(k)}">'
                    f'<span class="sub2">$</span>'
                    f'<input name="unit" type="number" min="1" step="1" value="{val}" '
                    f'placeholder="{current_unit}" style="width:66px;background:var(--surface-2);'
                    f'border:1px solid var(--divider);color:var(--text);font:inherit;'
                    f'padding:4px 6px;border-radius:5px">'
                    f'<button type="submit" name="action" value="set" class="sub2" '
                    f'title="sets the unit for FUTURE {esc(k)} bets" '
                    f'style="background:var(--surface-2);border:1px solid var(--divider-strong);'
                    f'color:var(--text);cursor:pointer;padding:4px 8px;border-radius:5px">set</button>'
                    f'<button type="submit" name="action" value="all" class="sub2" '
                    f'onclick="return confirm(\'Re-stamp every {esc(k)} bet to this unit '
                    f'size? This updates the recorded unit on past bets too.\')" '
                    f'title="also re-stamp ALL existing {esc(k)} bets to this unit" '
                    f'style="background:transparent;border:1px solid var(--divider-strong);'
                    f'color:var(--muted);cursor:pointer;padding:4px 8px;border-radius:5px">'
                    f'all</button></form>')

        def _swatches(k):   # per-tag colour picker (palette of submit swatches)
            if not k:
                return ""
            cur = tag_colors.get(k)
            btns = (f'<button name="color" value="" class="sw{" on" if not cur else ""}" '
                    f'style="background:transparent" title="no colour">✕</button>')
            for c in TAG_PALETTE:
                btns += (f'<button name="color" value="{c}" '
                         f'class="sw{" on" if cur == c else ""}" '
                         f'style="background:{c}" title="{c}"></button>')
            return (f'<form method="post" action="/bet/tagcolor" class="swrow">'
                    f'<input type="hidden" name="csrf" value="{csrf}">'
                    f'<input type="hidden" name="tag" value="{esc(k)}">{btns}</form>')
        order = sorted(tag_perf.items(),
                       key=lambda kv: (kv[1]["settled"] + kv[1]["open"]), reverse=True)
        trows = ""
        for k, g in order:
            ns = g["w"] + g["l"]
            wr = f' <span class="sub2">{g["w"]/ns:.0%}</span>' if ns else ""
            roi = f'{g["profit"]/g["cost"]:+.0%}' if g["cost"] else "—"
            acv = f'{sum(g["clvs"])/len(g["clvs"]):+.1f}¢' if g["clvs"] else "—"
            pcol = "good" if g["profit"] >= 0 else "accent"
            ucol = "good" if g["units"] >= 0 else "accent"
            usub = (f'<div class="sub2" style="color:var(--{ucol})">{_units_str(g["units"])}</div>'
                    if g["settled"] else "")
            pnl = (f'<span class="mono" style="color:var(--{pcol})">{money(g["profit"])}</span>{usub}'
                   if g["settled"] else '<span class="sub2">—</span>')
            vol = (f'{money(g["cost"])}<div class="sub2">{g["staked_u"]:.1f}u</div>'
                   if g["settled"] else '<span class="sub2">—</span>')
            openc = (f'{g["open"]} · {money(g["open_val"])}' if g["open"]
                     else '<span class="sub2">—</span>')
            trows += (f'<tr><td class="pick">{_tagname(k)}</td>'
                      f'<td class="mono" data-label="Record">{g["w"]}-{g["l"]}{wr}</td>'
                      f'<td class="mono" data-label="Volume">{vol}</td>'
                      f'<td data-label="P&amp;L">{pnl}</td><td class="mono" data-label="ROI">{roi}</td>'
                      f'<td class="mono" data-label="Avg CLV">{acv}</td>'
                      f'<td class="mono" data-label="Open">{openc}</td>'
                      f'<td data-label="Unit">{_unit_cell(k)}</td>'
                      f'<td data-label="Colour">{_swatches(k)}</td></tr>')
        tag_section = (
            '<section class="block major"><div class="blockhead">'
            '<h4>Performance by tag</h4><span class="aside">tailed bets &amp; your '
            'own tags · drill in · set a colour &amp; its own unit size</span></div>'
            '<div class="rule"></div><div class="tw"><table class="t rt"><thead><tr>'
            '<th>Tag</th><th>Record</th><th>Volume</th><th>P&amp;L</th><th>ROI</th><th>Avg CLV</th>'
            f'<th>Open</th><th>Unit</th><th>Colour</th></tr></thead><tbody>{trows}</tbody></table></div>'
            '<p class="sub2" style="margin-top:8px">Tag a bet from its “edit” control '
            '(or when you log one) — e.g. the handle you tailed. P&amp;L is realized '
            'on settled bets; Open is current live value still at risk. A bet tailed '
            'from several sources (comma-separate the tags) counts fully toward '
            '<i>each</i> tag here, so these rows can add up to more than your overall '
            'ledger — which still counts every bet once.</p></section>')

    # tag filter chips (All · each tag · untagged) — always visible so you can switch
    chip_bar = ""
    if has_tags:
        chips = [f'<a class="fchip{"" if sel else " on"}" href="/mybets">All bets</a>']
        for k in sorted(x for x in tag_perf if x):
            _cst = f';color:{tag_colors[k]}' if tag_colors.get(k) else ''
            chips.append(f'<a class="fchip{" on" if sel == k else ""}" style="{_cst}" '
                         f'href="/mybets?tag={_q(k)}">🏷 {esc(k)}</a>')
        if "" in tag_perf:
            chips.append(f'<a class="fchip{" on" if sel == "__untagged__" else ""}" '
                         f'href="/mybets?tag=__untagged__">personal</a>')
        chip_bar = f'<div class="filterbar" style="margin:0 0 18px">{"".join(chips)}</div>'

    # --- Performance detail (bottom of page): windows + breakdowns so you can see
    #     what's actually been working. Uses the full settled ledger. ---
    def _seg(items):
        w = sum(1 for _, dd in items if dd["outcome"] == "won")
        l = sum(1 for _, dd in items if dd["outcome"] == "lost")
        profit = sum(dd["pnl"] for _, dd in items)
        cost = sum(dd["cost"] for _, dd in items)
        # unit-normalised so different unit sizes (per-tag / per-epoch) are comparable
        units = sum(dd["pnl"] / _unit_of(b) for b, dd in items)
        staked_u = sum(dd["cost"] / _unit_of(b) for b, dd in items)
        clvs = [dd["clv"] for _, dd in items if dd["clv"] is not None]
        return {"n": len(items), "w": w, "l": l, "profit": profit, "cost": cost,
                "units": units, "staked_u": staked_u,
                "roi": (profit / cost) if cost else None,
                "clv": (sum(clvs) / len(clvs)) if clvs else None}

    def _seg_cells(s, first, label):
        pcol = "good" if s["profit"] >= 0 else "accent"
        ucol = "good" if s["units"] >= 0 else "accent"
        roi = f'{s["roi"]:+.0%}' if s["roi"] is not None else "—"
        clv = f'{s["clv"]:+.1f}¢' if s["clv"] is not None else "—"
        return (f'<tr><td class="pick">{first}</td>'
                f'<td class="mono" data-label="Bets">{s["n"]}</td>'
                f'<td class="mono" data-label="Record">{s["w"]}-{s["l"]}</td>'
                f'<td class="mono" data-label="Volume">{money(s["cost"])}'
                f'<div class="sub2">{s["staked_u"]:.1f}u</div></td>'
                f'<td class="mono" data-label="P&amp;L" style="color:var(--{pcol})">{money(s["profit"])}</td>'
                f'<td class="mono" data-label="Units" style="color:var(--{ucol})">{_units_str(s["units"])}</td>'
                f'<td class="mono" data-label="ROI">{roi}</td>'
                f'<td class="mono" data-label="CLV">{clv}</td></tr>')

    def _perf_table(colname, groups):
        # groups: list of (label, items); drops empties, keeps given order
        rows = "".join(_seg_cells(_seg(it), esc(lbl), lbl) for lbl, it in groups if it)
        if not rows:
            return ""
        return (f'<div class="tw" style="margin-top:14px"><table class="t rt"><thead><tr>'
                f'<th>{esc(colname)}</th><th>Bets</th><th>Record</th><th>Volume</th>'
                f'<th>P&amp;L</th><th>Units</th><th>ROI</th><th>CLV</th>'
                f'</tr></thead><tbody>{rows}</tbody></table></div>')

    perf_detail = ""
    if settled:
        _now = datetime.now(timezone.utc)

        def _lvl(b):
            ev = b.event_ticker or ""
            if ev.startswith("KXATPCHALLENGER"):
                return "ATP Challenger"
            if ev.startswith("KXWTACHALLENGER"):
                return "WTA Challenger"
            if ev.startswith("KXITFWMATCH"):
                return "ITF Women"
            if ev.startswith("KXITFMATCH"):
                return "ITF Men"
            if ev.startswith("KXWTA"):
                return "WTA"
            if ev.startswith("KXATP"):
                return "ATP"
            return "Other"

        def _band(b):
            p = b.entry_price_cents
            return ("Underdog (<35¢)" if p < 35 else "Toss-up (35–55¢)" if p < 55
                    else "Favorite (55–75¢)" if p < 75 else "Chalk (75¢+)")
        # time windows (overlapping)
        win_groups = []
        for lbl, days in (("Last 7 days", 7), ("Last 30 days", 30), ("All-time", None)):
            cut = (_now - timedelta(days=days)) if days else None
            win_groups.append((lbl, [x for x in settled
                                     if cut is None or x[0].created_at >= cut]))
        # dimension breakdowns
        def _by(keyfn, order=None):
            buckets: dict = {}
            for x in settled:
                buckets.setdefault(keyfn(x[0]), []).append(x)
            keys = ([k for k in order if k in buckets] if order
                    else sorted(buckets, key=lambda k: len(buckets[k]), reverse=True))
            return [(k, buckets[k]) for k in keys]
        band_order = ["Underdog (<35¢)", "Toss-up (35–55¢)",
                      "Favorite (55–75¢)", "Chalk (75¢+)"]
        exit_groups = [
            ("Held to result", [x for x in settled if x[1]["exit"] != "cashed"]),
            ("Cashed out", [x for x in settled if x[1]["exit"] == "cashed"]),
        ]
        # "what's working" — best / worst ROI segment with a real sample (≥3)
        cand = ([(f'{lbl}', _seg(it)) for lbl, it in _by(_lvl)]
                + [(lbl, _seg(it)) for lbl, it in _by(_band)])
        cand = [(n, s) for n, s in cand if s["n"] >= 3 and s["roi"] is not None]
        working = ""
        if cand:
            best = max(cand, key=lambda x: x[1]["roi"])
            worst = min(cand, key=lambda x: x[1]["roi"])
            bits = [f'<strong style="color:var(--good)">{esc(best[0])}</strong> '
                    f'is your best ({best[1]["roi"]:+.0%} ROI, {best[1]["n"]} bets)']
            if worst[0] != best[0]:
                bits.append(f'<strong style="color:var(--accent)">{esc(worst[0])}</strong> '
                            f'your worst ({worst[1]["roi"]:+.0%}, {worst[1]["n"]} bets)')
            working = (f'<p class="prose" style="margin-top:14px">What\'s working: '
                       f'{" · ".join(bits)}. <span class="sub2">Segments with ≥3 '
                       f'settled bets only.</span></p>')
        # headline totals across the whole settled ledger
        tot = _seg(settled)
        _ns = tot["w"] + tot["l"]
        _pcol = "good" if tot["profit"] >= 0 else "accent"
        _ucol = "good" if tot["units"] >= 0 else "accent"
        summary = statstrip([
            ("Record", f'{tot["w"]}-{tot["l"]}',
             f'{tot["w"]/_ns:.0%} win rate · {tot["n"]} bets' if _ns else f'{tot["n"]} bets'),
            ("Net units", f'<span style="color:var(--{_ucol})">{_units_str(tot["units"])}</span>',
             "up/down, unit-normalised"),
            ("Realized P&L",
             f'<span style="color:var(--{_pcol})">{money(tot["profit"])}</span>',
             "on settled bets"),
            ("Volume", money(tot["cost"]),
             f'{tot["staked_u"]:.1f}u staked'),
            ("ROI", f'{tot["roi"]:+.0%}' if tot["roi"] is not None else "—", "P&L ÷ volume"),
            ("Avg CLV", f'{tot["clv"]:+.1f}¢' if tot["clv"] is not None else "—",
             "vs the closing line"),
        ])
        perf_detail = (
            '<section class="block major"><div class="blockhead">'
            '<h4>Performance detail</h4><span class="aside">what\'s been working · '
            'realized on settled bets</span></div><div class="rule"></div>'
            + summary
            + '<div class="sub2" style="text-transform:uppercase;letter-spacing:.08em;'
            'font-size:10px;font-weight:700;color:var(--muted);margin-top:6px">By window</div>'
            + _perf_table("Window", win_groups)
            + '<div class="sub2" style="text-transform:uppercase;letter-spacing:.08em;'
            'font-size:10px;font-weight:700;color:var(--muted);margin-top:18px">'
            'By entry price</div>'
            + _perf_table("Entry price", _by(_band, band_order))
            + '<div class="sub2" style="text-transform:uppercase;letter-spacing:.08em;'
            'font-size:10px;font-weight:700;color:var(--muted);margin-top:18px">'
            'By tour / level</div>'
            + _perf_table("Level", _by(_lvl))
            + '<div class="sub2" style="text-transform:uppercase;letter-spacing:.08em;'
            'font-size:10px;font-weight:700;color:var(--muted);margin-top:18px">'
            'By exit</div>'
            + _perf_table("Exit", exit_groups)
            + working + '</section>')

    head = pagehead("Ledger", "My Bets",
                    "personal manual bets · advisory app places no orders")
    if sel:
        # focused view: this tag's isolated performance + only its bets
        g = tag_perf.get("" if sel == "__untagged__" else sel, _tp_new())
        ns = g["w"] + g["l"]
        pcol = "good" if g["profit"] >= 0 else "accent"
        gucol = "good" if g["units"] >= 0 else "accent"
        strip = statstrip([
            ("Record", f'{g["w"]}-{g["l"]}',
             f'{g["w"]/ns:.0%} win rate' if ns else "—"),
            ("Net units",
             f'<span style="color:var(--{gucol})">{_units_str(g["units"])}</span>',
             "up/down"),
            ("Realized P&L",
             f'<span style="color:var(--{pcol})">{money(g["profit"])}</span>',
             f'{g["settled"]} settled'),
            ("Volume", money(g["cost"]), f'{g["staked_u"]:.1f}u staked'),
            ("ROI", f'{g["profit"]/g["cost"]:+.0%}' if g["cost"] else "—", "on staked $"),
            ("Avg CLV", f'{sum(g["clvs"])/len(g["clvs"]):+.1f}¢' if g["clvs"] else "—",
             f'{len(g["clvs"])} priced'),
            ("Open", f'{g["open"]}',
             money(g["open_val"]) if g["open"] else "—"),
        ])
        _fc = tag_colors.get(sel)
        flabel = ("personal bets" if sel == "__untagged__"
                  else f'<span style="{_tag_color_style(_fc)}">🏷 {esc(sel)}</span>')
        focus = (f'<section class="block major"><div class="blockhead"><h4>{flabel}</h4>'
                 f'<span class="aside">this tag only · '
                 f'<a href="/mybets" class="sub2">← all bets</a></span></div>'
                 f'<div class="rule"></div>{strip}</section>')
        body = head + chip_bar + focus + open_html + hist_html + _tags_datalist(bet_tags)
    else:
        body = (head + chip_bar + unit_control + open_html + tag_section
                + epoch_sections + hist_html + theo_html + perf_detail
                + _tags_datalist(bet_tags))
    return respond(request, "My Bets", "mybets", body)


def _user_bet_tags(db, uid) -> list[str]:
    """Distinct tags the user has used (for autocomplete), newest-used first."""
    if not uid:
        return []
    rows = db.execute(select(UserBet.tag).where(
        UserBet.user_id == uid, UserBet.tag.is_not(None))
        .order_by(UserBet.created_at.desc())).scalars().all()
    seen, out = set(), []
    for raw in rows:
        for t in _bet_tags(raw):   # a bet may carry several tags
            if t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
    return out


def _tags_datalist(tags: list[str]) -> str:
    """A shared <datalist id='bettags'> so every tag input autocompletes existing
    tags (and users can still type a brand-new one)."""
    return ('<datalist id="bettags">'
            + "".join(f'<option value="{esc(t)}"></option>' for t in tags)
            + '</datalist>')


# curated palette for colour-coding My Bets tags (readable on the dark surface)
TAG_PALETTE = ["#e0564a", "#e08a3c", "#e0c33c", "#5bbf6a",
               "#4ea1e0", "#9b7ae0", "#e07ab0"]


def _tag_color_style(color: str | None) -> str:
    """Inline style that tints a tag chip's text + border to its colour."""
    return f";color:{color};border-color:{color}" if color else ""


def _bet_tags(tag) -> list[str]:
    """A bet can be tailed from more than one source, so its tag field holds a
    comma-separated list. Split it, trimmed, de-duped (case-insensitive), order
    preserved."""
    if not tag:
        return []
    out, seen = [], set()
    for t in str(tag).split(","):
        t = t.strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def _tagchip(tag: str | None, colors: dict | None = None) -> str:
    """One chip per tag on the bet (multiple when tailed from several sources)."""
    colors = colors or {}
    return "".join(
        f' <span class="tag tag-outline" style="font-size:10px'
        f'{_tag_color_style(colors.get(t))}" title="tailed from {esc(t)}">🏷 {esc(t)}</span>'
        for t in _bet_tags(tag))


def _del_form(bid: int, csrf: str) -> str:
    return (f'<form method="post" action="/bet/delete" style="display:inline" '
            f'onsubmit="return confirm(\'Delete this bet?\')">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            f'<input type="hidden" name="id" value="{bid}">'
            f'<button type="submit" class="sub2" style="background:none;border:none;'
            f'color:var(--muted);cursor:pointer;padding:0 0 0 6px">✕</button></form>')


def _cashout_form(b, csrf: str, cur: int | None) -> str:
    """Inline 'sell N shares at M¢' control for an open position — prefilled with
    the whole position at the current price. Typing fewer shares (or tapping ½)
    scales out: that slice realizes now and the rest keeps running."""
    inp = ("background:var(--surface-2);border:1px solid var(--divider);"
           "color:var(--text);font:inherit;padding:4px 6px;border-radius:5px")
    return (f'<form method="post" action="/bet/cashout" style="display:flex;gap:4px;'
            f'align-items:center">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            f'<input type="hidden" name="id" value="{b.id}">'
            f'<input type="hidden" name="action" value="set">'
            f'<input name="exit_shares" type="number" min="1" max="{b.shares}" '
            f'step="1" value="{b.shares}" '
            f'title="shares to sell — fewer than {b.shares} leaves the rest open" '
            f'style="width:58px;{inp}" required>'
            f'<button type="button" class="sub2" title="sell half" '
            f'onclick="this.form.exit_shares.value={max(1, b.shares // 2)}" '
            f'style="background:none;border:none;color:var(--muted);cursor:pointer;'
            f'padding:0 1px">½</button>'
            f'<span class="sub2">@</span>'
            f'<input name="exit_price" type="number" min="0" max="100" step="1" '
            f'value="{cur if cur is not None else ""}" placeholder="¢" '
            f'style="width:52px;{inp}" required>'
            f'<button type="submit" class="sub2" style="background:var(--surface-2);'
            f'border:1px solid var(--divider-strong);color:var(--text);cursor:pointer;'
            f'padding:4px 8px;border-radius:5px;white-space:nowrap">cash out</button>'
            f'</form>')


def _reopen_form(bid: int, csrf: str, partial: bool = False) -> str:
    """Revert a cashed-out bet back to 'held' (undo). A part-sold slice folds its
    shares back into the position it came from."""
    title = ("Undo — fold these shares back into the open position" if partial
             else "Revert to held")
    return (f'<form method="post" action="/bet/cashout" style="display:inline">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            f'<input type="hidden" name="id" value="{bid}">'
            f'<input type="hidden" name="action" value="clear">'
            f'<button type="submit" class="sub2" title="{title}" '
            f'style="background:none;border:none;color:var(--muted);cursor:pointer;'
            f'padding:0 0 0 4px">↺</button></form>')


def _edit_form(b, csrf: str) -> str:
    """Expandable per-bet control: CHANGE the entry price/shares, or ADD onto the
    position (extra shares at a price → blended average entry)."""
    inp = ("background:var(--surface);border:1px solid var(--divider);"
           "color:var(--text);font:inherit;padding:4px 6px;border-radius:5px")
    btn = ("background:var(--surface-2);border:1px solid var(--divider-strong);"
           "color:var(--text);cursor:pointer;padding:4px 8px;border-radius:5px")
    return (
        f'<details style="display:inline-block;vertical-align:middle">'
        f'<summary class="sub2" style="cursor:pointer;list-style:none;padding:0 6px">'
        f'edit</summary>'
        f'<div style="margin-top:6px;padding:10px;background:var(--surface-2);'
        f'border:1px solid var(--divider);border-radius:6px;text-align:left;'
        f'min-width:220px">'
        f'<form method="post" action="/bet/edit" style="display:flex;gap:6px;'
        f'align-items:center;flex-wrap:wrap;margin-bottom:8px">'
        f'<input type="hidden" name="csrf" value="{csrf}">'
        f'<input type="hidden" name="id" value="{b.id}">'
        f'<input type="hidden" name="action" value="edit">'
        f'<span class="sub2">price</span>'
        f'<input name="price" type="number" min="1" max="99" value="{b.entry_price_cents}" '
        f'style="width:54px;{inp}">'
        f'<span class="sub2">shares</span>'
        f'<input name="shares" type="number" min="1" value="{b.shares}" '
        f'style="width:60px;{inp}">'
        f'<button type="submit" class="sub2" style="{btn}">save</button></form>'
        f'<form method="post" action="/bet/edit" style="display:flex;gap:6px;'
        f'align-items:center;flex-wrap:wrap">'
        f'<input type="hidden" name="csrf" value="{csrf}">'
        f'<input type="hidden" name="id" value="{b.id}">'
        f'<input type="hidden" name="action" value="add">'
        f'<span class="sub2">add</span>'
        f'<input name="add_shares" type="number" min="1" placeholder="shares" '
        f'style="width:60px;{inp}">'
        f'<span class="sub2">@</span>'
        f'<input name="add_price" type="number" min="1" max="99" placeholder="¢" '
        f'style="width:54px;{inp}">'
        f'<button type="submit" class="sub2" style="{btn}">add onto</button></form>'
        # tag this bet (who you tailed) — free text; existing tags autocomplete.
        # comma-separate to credit several sources (each counts fully per-source).
        f'<form method="post" action="/bet/edit" style="display:flex;gap:6px;'
        f'align-items:center;flex-wrap:wrap;margin-top:8px">'
        f'<input type="hidden" name="csrf" value="{csrf}">'
        f'<input type="hidden" name="id" value="{b.id}">'
        f'<input type="hidden" name="action" value="tag">'
        f'<span class="sub2">tag</span>'
        f'<input name="tag" list="bettags" value="{esc(b.tag or "")}" '
        f'placeholder="tail(s), comma-sep" maxlength="64" style="width:140px;{inp}">'
        f'<button type="submit" class="sub2" style="{btn}">set</button></form>'
        f'</div></details>')


async def login(request: web.Request) -> web.Response:
    if request.get("user"):
        raise web.HTTPFound("/")
    if request.method == "GET":
        return web.Response(text=_login_html(request.query.get("next") or "/"),
                            content_type="text/html")
    from bot.models import AppUser
    data = await request.post()
    nxt = data.get("next") or "/"
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"  # only ever redirect to a local path
    ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
          or (request.remote or ""))
    if webauth.throttled(ip):
        return web.Response(status=429, content_type="text/html",
                            text=_login_html(nxt, "Too many attempts — wait a few minutes."))
    username = webauth.normalize_username(data.get("username", ""))
    pw = data.get("password", "") or ""
    uid = None
    with db_session() as db:
        u = db.execute(select(AppUser).where(AppUser.username == username)).scalars().first()
        if u is not None and u.is_active and webauth.verify_password(pw, u.password_hash):
            u.last_login_at = datetime.now(timezone.utc)
            uid = u.id
            db.commit()
    if uid is None:
        webauth.record_failure(ip)
        return web.Response(status=401, content_type="text/html",
                            text=_login_html(nxt, "Invalid username or password."))
    webauth.clear_failures(ip)
    resp = web.HTTPFound(nxt)
    resp.set_cookie(webauth.SESSION_COOKIE, webauth.make_session_token(uid),
                    max_age=webauth.SESSION_TTL, httponly=True,
                    secure=_secure_cookie(request), samesite="Lax", path="/")
    return resp


async def logout(request: web.Request) -> web.Response:
    resp = web.HTTPFound("/login")
    resp.del_cookie(webauth.SESSION_COOKIE, path="/")
    return resp


async def admin_users(request: web.Request) -> web.Response:
    """Admin-only: list accounts and add/enable/disable/promote them. All
    mutations are POST and carry a session-bound CSRF token."""
    user = request.get("user")
    if not user or not user.get("is_admin"):
        return web.Response(status=403, text="forbidden — admin only")
    from bot.models import AppUser
    sess = request.get("session_cookie") or ""
    csrf = webauth.csrf_token(sess)
    msg = err = ""
    if request.method == "POST":
        data = await request.post()
        if not webauth.csrf_ok(sess, data.get("csrf")):
            err = "Session expired — reload the page and try again."
        else:
            action = data.get("action")
            with db_session() as db:
                if action == "create":
                    uname = webauth.normalize_username(data.get("username", ""))
                    pw = data.get("password", "") or ""
                    if not uname or len(pw) < 8:
                        err = "Username is required and the password must be 8+ characters."
                    elif db.execute(select(AppUser).where(
                            AppUser.username == uname)).scalars().first():
                        err = f"A user named “{uname}” already exists."
                    else:
                        db.add(AppUser(username=uname,
                                       password_hash=webauth.hash_password(pw),
                                       is_admin=bool(data.get("is_admin")),
                                       is_active=True, created_by=user["username"]))
                        db.commit()
                        msg = f"Added user “{uname}”."
                elif action in ("toggle_active", "toggle_admin"):
                    target = db.get(AppUser, int(data.get("user_id", "0") or 0))
                    if target is None:
                        err = "User not found."
                    elif target.id == user["id"]:
                        err = "You can’t change your own access."
                    elif action == "toggle_active":
                        target.is_active = not target.is_active
                        db.commit()
                        msg = f"{'Enabled' if target.is_active else 'Disabled'} “{target.username}”."
                    else:
                        target.is_admin = not target.is_admin
                        db.commit()
                        msg = f"{'Granted' if target.is_admin else 'Revoked'} admin for “{target.username}”."

    with db_session() as db:
        users = db.execute(select(AppUser).order_by(AppUser.created_at)).scalars().all()

        def frm(action, uid, label):
            return (f'<form method="post" action="/admin/users" style="display:inline">'
                    f'<input type="hidden" name="csrf" value="{csrf}">'
                    f'<input type="hidden" name="action" value="{action}">'
                    f'<input type="hidden" name="user_id" value="{uid}">'
                    f'<button class="fchip" style="cursor:pointer;border:1px solid '
                    f'var(--divider);background:none;color:var(--text);font:inherit">'
                    f'{label}</button></form>')
        rows = []
        for uu in users:
            you = ' <span class="sub2">(you)</span>' if uu.id == user["id"] else ""
            badges = (('<span class="tag tag-good">admin</span> ' if uu.is_admin else "")
                      + ('<span class="tag tag-neutral">active</span>' if uu.is_active
                         else '<span class="tag tag-accent">disabled</span>'))
            last = (uu.last_login_at.astimezone(PACIFIC).strftime("%b %d %H:%M")
                    if uu.last_login_at else "never")
            if uu.id == user["id"]:
                actions = '<span class="sub2">—</span>'
            else:
                actions = (frm("toggle_active", uu.id,
                               "Disable" if uu.is_active else "Enable") + " "
                           + frm("toggle_admin", uu.id,
                                 "Revoke admin" if uu.is_admin else "Make admin"))
            rows.append(f'<tr><td class="pname">{esc(uu.username)}{you}</td>'
                        f'<td>{badges}</td>'
                        f'<td class="mono sub2">{esc(uu.created_by or "—")}</td>'
                        f'<td class="mono sub2">{last}</td><td>{actions}</td></tr>')

    banner = ""
    if msg:
        banner = (f'<div style="border-left:3px solid var(--good);padding:8px 12px;'
                  f'margin:0 0 14px;background:var(--surface)">{esc(msg)}</div>')
    elif err:
        banner = (f'<div style="border-left:3px solid var(--accent);padding:8px 12px;'
                  f'margin:0 0 14px;background:var(--surface)">{esc(err)}</div>')

    add_form = f"""<section class="block"><div class="blockhead"><h4>Add user</h4>
<span class="aside">creates an account that can sign in immediately</span></div>
<div class="rule"></div>
<form method="post" action="/admin/users" style="max-width:420px">
<input type="hidden" name="csrf" value="{csrf}">
<input type="hidden" name="action" value="create">
<label class="sub2">Username</label>
<input name="username" autocomplete="off" required style="{_AUTH_INP}">
<label class="sub2" style="margin-top:10px;display:block">Password <span class="sub2">(8+ characters)</span></label>
<input name="password" type="password" autocomplete="new-password" minlength="8" required style="{_AUTH_INP}">
<label style="display:flex;align-items:center;gap:8px;margin-top:12px;font-size:13px">
<input type="checkbox" name="is_admin" value="1"> Grant admin (can manage users)</label>
<button type="submit" style="{_AUTH_BTN}">Add user</button>
</form></section>"""

    body = pagehead("Access", "Users", f"{len(users)} account"
                    f"{'' if len(users) == 1 else 's'}") + banner + add_form + f"""
<section class="block" style="margin-top:18px"><div class="blockhead"><h4>Accounts</h4></div>
<div class="rule"></div><div class="tw"><table class="t">
<tr><th>username</th><th>role</th><th>added by</th><th>last sign-in</th><th></th></tr>
{''.join(rows)}</table></div></section>"""
    return respond(request, "Users", "users", body)


def make_app() -> web.Application:
    app = web.Application(middlewares=[auth_guard])
    app.router.add_get("/", home)
    app.router.add_get("/scenarios", scenarios)
    app.router.add_get("/scenario/{sid:\\d+}", scenario_detail)
    app.router.add_get("/testrun", testrun)
    app.router.add_get("/testrun/history", testrun_history)
    app.router.add_get("/testrun/{bot}", testrun)
    app.router.add_get("/history", history)
    app.router.add_get("/testrun-tp", testrun_tp)
    app.router.add_get("/calibration", calibration)
    app.router.add_get("/features", features)
    app.router.add_get("/players", players)
    app.router.add_get("/player/{pid:\\d+}", player_detail)
    app.router.add_get("/match/{event}", match_detail)
    app.router.add_get("/api/events", api_events)
    app.router.add_get("/flags", flags)
    app.router.add_get("/track", track)
    app.router.add_get("/live", live)
    app.router.add_get("/today", today)
    app.router.add_get("/system", system)
    app.router.add_get("/report", legacy_redirect)
    app.router.add_get("/queue", legacy_redirect)
    app.router.add_get("/healthz", healthz)
    # auth
    app.router.add_get("/login", login)
    app.router.add_post("/login", login)
    app.router.add_get("/logout", logout)
    app.router.add_get("/admin/users", admin_users)
    app.router.add_post("/admin/users", admin_users)
    app.router.add_post("/pin", pin_toggle)
    app.router.add_post("/favorite", favorite_toggle)
    app.router.add_get("/mybets", mybets)
    app.router.add_post("/bet", bet_create)
    app.router.add_post("/bet/delete", bet_delete)
    app.router.add_post("/bet/cashout", bet_cashout)
    app.router.add_post("/bet/unit", bet_set_unit)
    app.router.add_post("/bet/edit", bet_edit)
    app.router.add_post("/bet/tagcolor", bet_tag_color)
    app.router.add_post("/bet/tagunit", bet_tag_unit)

    async def _bootstrap(_app):
        try:
            with db_session() as db:
                webauth.bootstrap_admin(db)
        except Exception as e:  # never block web startup on this
            log.error("admin bootstrap failed", error=str(e))
    app.on_startup.append(_bootstrap)
    return app


def main() -> int:
    port = int(os.environ.get("PORT", 8080))
    log.info("web ui starting", port=port)
    web.run_app(make_app(), host="0.0.0.0", port=port, print=None)
    return 0
