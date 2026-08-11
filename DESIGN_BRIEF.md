# DEUCE — Design Brief for a Full Visual Redesign

You are redesigning **DEUCE**, a private tennis-analytics web app (an *advisory-only*
betting-research terminal — it never places trades). Your job is to make every page
**cleaner, calmer, and more professional/polished** while preserving all existing
information and behaviour. This is a **visual reskin and layout refinement**, not a
product rewrite: keep every number, table, and control; change how they *look and feel*.

Read this whole brief before touching anything. Deliver the **design system first**,
then one flagship page as a reference, then roll the system out page by page (order at
the end).

---

## 1. Product context & audience

- **What it is:** a personal dashboard that watches live tennis markets on Kalshi,
  runs a probability model, and tracks paper-betting "bots" as experiments. Read-only
  research; nothing here is an order.
- **Audience:** one expert user (the owner) plus a few invited accounts. They are
  quantitatively literate and want **information density with zero clutter** — a
  Bloomberg/trading-terminal sensibility, not a consumer app.
- **Voice:** precise, honest, understated. Numbers lead; chrome recedes. Never
  decorative for its own sake. Never imply certainty the data doesn't support.
- **Current feel:** dark, orange-accented, functional but inconsistent — dense tables,
  a few over-saturated elements, uneven spacing, ad-hoc inline styles. It works; it
  just isn't *composed*. Your job is to compose it.

---

## 2. Hard constraints (do not violate)

1. **Server-rendered, self-contained.** The app is Python/aiohttp; every page is a
   string of HTML built in `bot/web.py`, wrapped by one `page()` shell with a **single
   inline `<style>` block** (`CSS`) and a small inline `<script>` (`JS`). All styling
   must live in that one CSS string. **No external CSS/JS/CDN**, no build step. The only
   external asset today is a Google Fonts `@import` for **Archivo** — you may keep it,
   swap it, or self-host/inline a subset, but stay self-contained otherwise.
2. **Dark theme only.** `color-scheme: dark`. Do not add a light mode unless asked.
3. **Preserve all data and controls.** Every stat, table column, filter, link, and
   badge must survive. You may re-group, re-order, relabel for clarity, and hide
   secondary detail behind progressive disclosure — but don't delete information.
4. **Advisory-only + honesty.** Keep the "advisory only" tag and the footer disclaimer.
   Never invent data to fill a layout; empty/thin states must read as honest ("—",
   "insufficient sample", "no data yet"), never as fabricated values.
5. **Auth & refresh plumbing stay intact.** Pages are gated (`/login`). The shell
   supports an `X-Fragment` header that returns just the `<main>` body for a **7-second
   in-place auto-refresh** — your markup must tolerate being swapped every 7s without
   layout jank, focus loss on inputs, or flicker. Don't rely on load-time animations
   that would re-fire each refresh.
6. **Accessibility:** WCAG AA contrast on the dark surfaces; visible focus rings;
   semantic colour never the *only* signal (pair with a glyph/label); tables remain
   real `<table>`s; hit targets ≥ 32px.
7. **Responsive:** works from ~360px to wide desktop. Wide tables/charts scroll inside
   their own `overflow-x:auto` container; the page body never scrolls horizontally.

---

## 3. Current design tokens (extend these; don't reinvent)

```
--bg: #131211;  --surface: #1c1a19;  --surface-2: #232120;
--text: #f3f2f2;  --muted: rgba(243,242,242,.56);  --faint: rgba(243,242,242,.40);
--divider: rgba(243,242,242,.13);  --divider-strong: rgba(243,242,242,.20);
--accent: #ff563c;  --accent-fill: #ec3013;      (brand orange)
--good: #35c26e;  --warning: #fab219;  --critical: #ff563c;
--font: "Archivo", system-ui, sans-serif;
--radius: 10px;  --radius-sm: 6px;
--shadow: 0 1px 2px rgba(0,0,0,.35), 0 8px 24px rgba(0,0,0,.16);
mono: a monospace stack is used for all numbers (class `.mono`)
```
Brand mark: a tennis-ball logo (optic yellow-green `#c7e14b` ball, white S-seam) + the
wordmark **DEUCE** with an "advisory only" pill.

**What to fix about the current system:**
- **Spacing is ad-hoc.** Introduce a strict 4px-based scale (4, 8, 12, 16, 24, 32, 48)
  and use *only* those values. Most "unpolished" feel comes from inconsistent gaps.
- **Type scale is thin.** Define a real scale (e.g. 30/22/19/15/13.5/12/11/10.5) and a
  role for each (page title, section head, body, caption, micro-label). Tighten
  letter-spacing on caps labels; don't over-bold body text.
- **Accent is over-used.** Orange should be *rare* — reserved for the single most
  important thing on a view (the pick, a trigger, the active nav item). Most of the UI
  should be neutral greys with one calm accent moment per screen.
- **Numbers vs text colour.** Numbers/labels use ink tokens (text/muted/faint); colour
  on a value should mean something (good/bad/leader), never decoration.

You may refine token *values* (e.g. warmer/cooler greys, a slightly less hot accent,
better status colours) but keep the dark, warm-neutral character and the brand orange
identity.

---

## 4. Design principles for this app

1. **Density with air.** It's a terminal — pack information, but give every block
   consistent padding and a clear boundary. Calm, not sparse.
2. **One hierarchy per view.** Each page has exactly one "hero" element (the thing the
   user came for). Everything else is visibly secondary.
3. **Tables are first-class.** Most value is tabular. Invest in one excellent table
   style: right-aligned monospace numerics that align on the decimal, quiet 1px row
   dividers (no heavy zebra), sticky/grouped headers, uppercase micro-labels, generous
   row height (~40px), a subtle hover, and grouped-column headers with thin separators
   (the leaderboard already groups Hold / 90¢ / Signal — formalize that pattern).
4. **Colour = meaning.** Establish a fixed semantic palette: `good` (green), `warning`
   (amber), `critical/accent` (orange/red), plus a neutral "leader" emphasis. Comparison
   bars use a **muted warm/cool pair** (already moved to `#b06a55` / `#5f7a9c`) — keep
   them soft; never full-saturation fills edge to edge.
5. **Charts follow data-viz discipline.** Thin marks; recessive grid/axes; a legend for
   ≥2 series; direct labels sparingly; one y-axis only; muted palette; every chart in an
   `overflow-x:auto` wrapper. Label axes.
6. **Progressive disclosure.** Lead with the headline number; tuck derivations, sample
   sizes, and methodology into muted sub-lines or expandable detail.
7. **Consistency over cleverness.** The same concept (a stat tile, a match row, a verdict
   badge) must look identical everywhere it appears.

---

## 5. Component system to define (build these once, reuse everywhere)

Specify each with states (default/hover/active/empty) and exact tokens:

- **App shell / top nav.** Brand + logo left; grouped nav links; a live-feed status dot
  ("● live · updates every 7s"); the signed-in user + Sign out (and admin "Users"). Make
  the nav quieter and the active item unmistakable. Consider a slim, sticky header.
- **Page header.** Kicker (uppercase accent micro-label) + `h2` title + right-aligned
  monospace sub-line (context/counts). Consistent top/bottom rhythm on every page.
- **Section block.** `<section class="block">` with a `blockhead` (h4 + right "aside"
  caption) and a hairline rule. This is the primary content container — standardize its
  padding, heading size, and spacing.
- **Stat tile / statstrip.** A row of KPI tiles (label / big mono value / sub-caption).
  Define column behaviour (fixed N vs auto-fit) and the value/label type roles.
- **Data table (`.t`).** The flagship component — see principle 3. Include the grouped
  two-tier header variant, right-aligned numeric columns, verdict marks (✓/✗/~/·),
  Wilson-floor and ±CI sub-annotations, and a clean empty row.
- **Comparison split-bar.** Two-sided muted bar for head-to-head stats (aces, serve %,
  etc.), leader value emphasized, 2px surface gap, thin height (~6px). Plus the
  broadcast **per-set score grid** (two player rows, per-set columns, current-game box,
  set-winner bold, country flags where available).
- **Badges / tags.** Pill system: `good / warning / accent / neutral / outline`, each
  with an optional leading glyph. Status badges must pair colour with an icon+label.
  Includes the live-state badges (● live, ✓ score, ≈ estimate, ○ PRE, "running late").
- **Buttons & forms.** Primary (accent), secondary (outline), ghost. Inputs/selects with
  consistent height, focus ring, and dark-surface styling (login + admin + filter bars).
- **Filter chips / filter bar.** Segmented, one row above content; selected state clear;
  used on Live, Database, History, Today.
- **Charts.** Reliability/calibration curve, monthly-drift line, feature-ΔBrier trend,
  win-prob timeline, bot equity/CLV trends. One shared SVG chart style (see principle 5).
- **Empty / loading / stale states.** A consistent muted treatment; a "stale/refreshing"
  affordance that doesn't jump.

---

## 6. Global layout & motion

- Content max-width ~1200–1280px, centered, with comfortable side gutters; wide tables
  may extend within their scroll container.
- Vertical rhythm: consistent gap between sections (use the spacing scale). Group related
  sections; separate unrelated ones with more space, not heavier rules.
- Motion: near-zero. A subtle hover and focus transition is enough. **No entrance
  animations** (they'd replay on every 7s refresh). A quiet "refreshed" pulse on the
  status dot only.
- Keep the footer disclaimer, but make it a quiet, small, muted line.

---

## 7. Page-by-page instructions

For each page: its purpose, what's on it now, and how to elevate it. Preserve content;
improve hierarchy, grouping, spacing, and component usage.

### `/` — Overview (home)
- **Purpose:** at-a-glance status — advisories today, watched/live counts, estimator hit
  rate, pending items, recent advisories & bets.
- **Redesign:** lead with a clean **KPI statstrip** (5–6 tiles) as the hero. Below, two
  columns: "Recent advisories" and "Recent bets" as compact list cards with clear
  timestamp/side/price. Make it feel like a terminal home: scannable in 3 seconds. One
  accent moment (e.g. live count or a fresh advisory).

### `/live` — Live board
- **Purpose:** the busiest, most-important operational view. Three groups: **Live**,
  **Starting soon**, **Finished** (18h). Cards show the broadcast score grid, both
  sides' Kalshi prices, live-state badge, scenario/trigger callouts, timing label.
- **Redesign:** this is a flagship — invest here. Make live match **cards** crisp and
  uniform: player rows with flag · name · price; the per-set score grid; a single clear
  state badge; a "near-trigger" emphasis for actionable ones (they're sorted
  closest-to-trigger first — reflect that visually, e.g. a left accent border on
  trigger-live cards). Group headers ("Live · N", "Starting soon · next 12h",
  "Finished") with counts. Handle the busy-slate case gracefully (many cards) with a
  tight, consistent grid. Preserve the filter/near-trigger controls and the honest
  "running late / awaiting start / ● live / inferred from odds" labels.

### `/today` — Today's slate
- **Purpose:** the day's matches with the model's pick + links to match data, plus a
  "Finished today" section (winner, which bots bet, score grid).
- **Redesign:** a clean schedule table (time · matchup · model pick % · tour · match-data
  link). Model pick shown as a subtle inline probability bar or chip, not loud. Finished
  section as compact rows with the score grid and small bot-bet chips (✓/✗). Keep the
  honesty legend ("insufficient data" = no pick).

### `/scenarios` — Scenario list
- **Purpose:** the day's gameflow "watch plans," ranked by salience.
- **Redesign:** a ranked list/table of scenarios: watch player, matchup, tier, the
  trigger condition, model prob, salience. Make salience order obvious. Scenario tags as
  small chips. Row links to the detail.

### `/scenario/{id}` — Scenario detail
- **Purpose:** the deep read for one match: the plan, live analysis, and the full match
  data embedded below.
- **Redesign:** strong single-column narrative hierarchy. Top: match title + watch
  player + scheduled time + Kalshi link + scenario tags. Then **The read** (hierarchical
  narrative — hero play + model, entry/risk callouts, case, edges/caveats). Then **Live
  play analysis** (model-now vs prematch, entry verdict, the written read). Then the
  embedded **Full match data** (see `/match`). Use callout blocks (left accent border)
  for the verdict (ENTRY LIVE / HOLD / WATCHING). Keep it readable, not cramped.

### `/match/{event}` — Match data (flagship)
- **Purpose:** everything about one match. Sections in order: **Score** (broadcast grid,
  kept for finished), **Live match stats** (serve & return battle: split bars + historical
  win% context), player **profile cards** (two side-by-side: rank/trajectory, set rates,
  surface splits, SOS, serve/return, clutch, load, prose), **Model read vs raw form**,
  **Head to head**, **Style matchup**, **Game-by-game score**, **Bot activity**, gameflow
  plan.
- **Redesign:** this page has the most components — make it the reference for the whole
  system. Two-up profile cards must be visually balanced and dense-but-legible. The
  serve/return battle bars are already muted (`#b06a55`/`#5f7a9c`, 6px) — keep that
  restraint and align labels/values cleanly. The historical-win% context rows should read
  as quiet sub-detail. Score grid at top. Consistent section blocks throughout.

### `/players` — Database
- **Purpose:** searchable player table + a **Heaters** section (players on 5+ win
  streaks, hottest first).
- **Redesign:** Heaters as a tidy card grid (streak badge, name, tour · country · record)
  with an honest "N of M" count. Below, the player table with the filter bar (tour /
  surface / hand / sort) as clean segmented chips, and a real search input. Table:
  name · tour · country · record · last-seen · age · hand. Right-align numerics.

### `/player/{id}` — Player profile
- **Purpose:** one player's full stat sheet: header (rank, trajectory, country, matches),
  KPI tiles (career/past-year/streak/deciders/skunk/form-delta), set-by-set win rates,
  deciding-set detail, clutch & quality-of-competition, surface splits, recent matches.
- **Redesign:** header band with identity + the KPI statstrip as hero. Then grouped
  sections mirroring `/match` component styles (set rates, clutch, surfaces as small
  tables/tiles). Recent matches as a compact table (date · W/L · opponent · score ·
  context). Keep sample-size honesty (greyed/omitted thin stats).

### `/testrun` — Bots leaderboard
- **Purpose:** all paper-bots side by side; the "which strategy works" view. Grouped
  columns **Hold / 90¢ Take-Profit / Signal**; boards sorted by settled-count, ROI, CLV;
  a master-bot-candidates callout.
- **Redesign:** formalize the grouped two-tier table header (thin separators between
  groups, muted group labels). Right-align all numerics; keep units inline with profit;
  keep verdict marks (✓/✗/~/·), Wilson floors, ±CI. The master-candidates panel as a
  clear callout box. Three boards clearly titled with their sort rationale. This table is
  wide — make the grouped-header + numeric alignment carry the polish.

### `/testrun/{bot}` — Individual bot page
- **Purpose:** one bot's detail: Hold vs 90¢ side-by-side scoreboard (record/win%/profit/
  units/ROI + "exit vs close"), CLV, breakdowns, its bets, and "bot activity" (why it bet
  / didn't).
- **Redesign:** the Hold-vs-TP scoreboard as two clean comparison columns (the `vscol`
  pattern). Then a stat strip (settled/open/avg buy-in/CLV/running). Bets as a compact
  table. Keep the take-profit "exit vs close" read and the honest reasons.

### `/testrun/history` — Bot version history
- **Redesign:** a simple timeline/table of self-improving policy versions with the
  rationale per version. Quiet, chronological.

### `/calibration` — Model calibration
- **Purpose:** reliability by predicted-probability bucket + monthly drift; Brier;
  calibration gap. The property the whole edge thesis rests on.
- **Redesign:** a proper **reliability chart** (predicted vs observed, diagonal ideal
  line, per-bucket points sized by n) as the hero, plus a monthly-drift line chart, plus
  headline Brier/gap tiles. Use the shared chart style; label axes; muted grid.

### `/features` — Variable-strength monitor
- **Purpose:** out-of-sample ΔBrier/Δlog-loss each candidate model variable adds, on an
  expanding window, with a trend across daily snapshots.
- **Redesign:** a ranked table (variable · ΔBrier · Δlog-loss · coef · coverage · verdict)
  with the trend as a small sparkline/heat row per variable. Colour ΔBrier by
  good/neutral/bad. Make "sample still small / watch it settle" framing explicit and calm.

### `/history` — Archive
- **Purpose:** browse any past day's settled slate; who won, the score, which bots bet.
- **Redesign:** a day navigator (prev/next/today chips) + a KPI strip (matches settled /
  bot played / bot record) + settled-match cards (matchup · winner · score grid · bot
  tag). Consistent with Live/Today card styles.

### `/track` — Advisory outcomes
- **Redesign:** a clean P&L / outcomes table for delivered advisories (CLV, result,
  PnL). Align with the leaderboard's numeric styling. A small summary strip on top.

### `/system` — System health
- **Purpose:** service/data-source health and status.
- **Redesign:** status cards per subsystem (ingest freshness by tour/source, feed status,
  worker/loop, DB) with clear good/warn/critical states (colour + icon + one-line
  detail + suggested action). Terminal "ops" feel.

### `/flags` — Data audit
- **Redesign:** a card grid of audit findings, each a severity badge (info/warn/critical)
  + title + explanation + recommended action. Sort by severity. Quiet when all-clear.

### `/login` — Sign in
- **Purpose:** gate. Centered card, username/password, brand mark, "access restricted"
  note.
- **Redesign:** a polished centered auth card — brand/logo, tidy inputs with focus states,
  a single accent primary button, subtle error styling. Make it feel like the front door
  of a professional tool.

### `/admin/users` — Users admin (admin only)
- **Redesign:** an "Add user" form card + an accounts table (username · role badges ·
  added-by · last sign-in · enable/disable/promote actions). Actions as small secondary
  buttons. Clean, utilitarian.

### Non-visual routes (leave alone)
`/api/events` (JSON), `/healthz`, `/logout`, `/report` & `/queue` (redirects).

---

## 8. Deliverable & working method

1. **Design system first.** Produce the refined token block + the shared component CSS
   (nav, page header, section block, table, stat tile, badges, buttons, forms, split-bar,
   score grid, chart primitives, empty/stale states). Return it as the single CSS string
   that drops into `bot/web.py`'s `CSS` constant, plus any tiny `JS` additions.
2. **One flagship reference page.** Rebuild **`/match/{event}`** (or `/live`) fully in the
   new system as the proof, since it exercises the most components. Show before/after.
3. **Roll out** the rest in this priority order (highest visibility first):
   `/live` → `/` → `/match` → `/testrun` → `/today` → `/player` → `/players` →
   `/scenario` → `/calibration` → `/features` → `/testrun/{bot}` → `/history` →
   `/track` → `/system` → `/flags` → `/login` → `/admin/users` → `/scenarios` →
   `/testrun/history`.
4. For each page, deliver the **new HTML structure** for its body builder (the function
   returns a body string wrapped by `page()`), reusing the shared component classes —
   minimal inline styles, everything in the CSS system.
5. **Show your reasoning briefly** per page (what hierarchy you chose and why) and call
   out anything that trades information density for clarity so the owner can veto.

## 9. Definition of done
- One coherent visual system; every page visibly part of the same product.
- Strict spacing scale and type scale applied everywhere; no orphan inline styles.
- Accent used sparingly and meaningfully; neutral, calm default.
- Tables aligned and legible; charts labeled and restrained; comparison bars muted.
- Fully responsive; survives the 7s fragment refresh without jank; AA contrast.
- No data removed; honesty/empty states preserved; advisory-only disclaimer intact.
```
