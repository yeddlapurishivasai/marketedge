# Feature Specification: MarketEdge React SPA (Backtracked)

**Feature Branch**: `003-marketedge-spa`

**Created**: 2026-06-24

**Status**: Backtracked (documents existing behavior)

**Input**: Reverse-engineered from the existing React 19 + TypeScript + Vite SPA
under `src/MarketEdge.Api/clientapp/`: `src/main.tsx`, `src/App.tsx`,
`src/api.ts`, `src/format.ts`, `src/pages/AnalysisPage.tsx`,
`src/pages/JobsPage.tsx`, `src/styles.css`, plus `vite.config.ts` and
`package.json`.

> **Scope note**: This spec covers ONLY the browser SPA: routing, the sector and
> stock management screens, the Stage 2 analysis dashboard, and the job-monitoring
> screen, all of which consume the existing REST API. The API behavior itself is
> specified in `specs/001-stage2-analysis-api/` and is referenced here only as the
> backend contract the SPA calls. No backend or worker behavior is (re)defined here.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Pick a market and navigate the app (Priority: P1)

A user lands on the home page, chooses the Indian or US market, and navigates to
Sectors, Stocks, Stage 2 Analysis, or Job Runs for that market. A persistent nav
bar provides branding, a return-home link, and a light/dark theme toggle.

**Why this priority**: Routing and market selection are the entry point; every
other screen is reached through `/:market/...`.

**Independent Test**: Visiting `/` shows two market cards; clicking one routes to
`/:market`, which shows four menu cards linking to `/:market/{sectors|stocks|
analysis|jobs}`. The theme toggle flips `data-theme` and persists to
`localStorage` (`me-theme`).

**Acceptance Scenarios**:

1. **Given** the home route `/`, **When** the user clicks the Indian or US card,
   **Then** the app navigates to `/india` or `/us` respectively.
2. **Given** a market menu `/:market`, **When** the user clicks a menu card,
   **Then** the app routes to the corresponding `/:market/sectors`,
   `/:market/stocks`, `/:market/analysis`, or `/:market/jobs`.
3. **Given** any page, **When** the user toggles the theme, **Then** light/dark
   switches immediately and the choice survives a page reload.
4. **Given** the brand/back links, **When** clicked, **Then** the user returns to
   `/` (or the market menu where a back link is shown).

---

### User Story 2 - Manage sectors (Priority: P1)

For a market, a user views all sectors with stock counts, searches/filters them,
creates a new sector, renames a sector, deletes an empty sector, and drills into a
sector to see its stocks.

**Why this priority**: Sectors are the primary organizing structure for the catalog.

**Independent Test**: `/:market/sectors` lists sectors (name + stock count) from
`GET /api/{market}/sectors`; create/rename/delete call the corresponding
`POST`/`PUT`/`DELETE` endpoints and refresh the list with a success toast.

**Acceptance Scenarios**:

1. **Given** the sectors page, **When** it loads, **Then** sectors are fetched and
   shown with their stock counts; a search box filters the list client-side.
2. **Given** the add form, **When** the user submits a non-empty name, **Then**
   `createSector` is called, the modal closes, the list refreshes, and a
   "Sector created" toast appears.
3. **Given** a sector row, **When** the user renames it, **Then** `renameSector` is
   called and the list reflects the new name.
4. **Given** a sector, **When** the user deletes it, **Then** `deleteSector` is
   called; deleting a sector that still has stocks surfaces the API error
   ("Cannot delete sector with stocks").
5. **Given** a sector row, **When** clicked, **Then** the app routes to
   `/:market/sectors/:sectorId` showing that sector's stocks.

---

### User Story 3 - Manage and move stocks (Priority: P1)

A user searches stocks across a market (or within a sector), paginates results,
creates/edits/deletes a stock, and bulk-moves selected stocks to another sector.

**Why this priority**: Stock CRUD and reorganization are the core catalog
maintenance workflows.

**Independent Test**: `/:market/stocks` lists paged stocks from
`GET /api/{market}/stocks` with `q`, `sectorId`, `page`, `pageSize=50`; selecting
rows and choosing a target sector calls `POST /api/{market}/stocks/move`.

**Acceptance Scenarios**:

1. **Given** the stocks page, **When** the user types a query or picks a sector,
   **Then** results refetch (paged, 50 per page) and pagination controls update
   from `totalCount`.
2. **Given** the create/edit forms, **When** submitted, **Then** `createStock` /
   `updateStock` are called and the list refreshes with a toast.
3. **Given** a stock, **When** deleted (after confirm), **Then** `deleteStock` is
   called and the list refreshes.
4. **Given** selected stocks and a target sector, **When** the user confirms a
   move, **Then** `moveStocks` is called, the selection clears, and a
   "Moved N stock(s) to <sector>" toast appears.
5. **Given** market context, **Then** market caps and prices render with the
   correct currency/format (₹ Cr/Lakh Cr for India, $ M/B/T for US).

---

### User Story 4 - Trigger and configure a Stage 2 analysis run (Priority: P1)

From the Analysis page a user opens a "Run Analysis" modal, configures run options
(sample vs full universe, target ISO week, sectors, stock limit, market-cap
min/max, retry-failed-only), starts the run, and is taken to the Jobs page to
monitor it.

**Why this priority**: Triggering analysis is the primary action the dashboard
exists to enable.

**Independent Test**: Opening the modal and clicking "Start Analysis" calls
`triggerAnalysis(market, request)` with only the chosen fields, resets the form,
and navigates to `/:market/jobs`.

**Acceptance Scenarios**:

1. **Given** the trigger modal, **When** the user selects sample vs full universe,
   **Then** full-universe-only controls (stock limit, market-cap min/max) appear,
   and `testSampleOnly` defaults to true in dev builds (`import.meta.env.DEV`).
2. **Given** a target week selector offering the current week plus the prior 12
   ISO weeks, **When** a past week is chosen, **Then** `weekNumber` is sent; the
   current week is omitted so the server computes it (avoids year-boundary drift).
3. **Given** a week that already has a completed/failed run, **When** the user
   enables "retry failed/pending only", **Then** `retryFailedOnly` is sent.
4. **Given** chosen sector checkboxes / limit / market-cap inputs, **Then** the
   request includes `sectorIds` / `limit` / `minMarketCap` / `maxMarketCap` only
   when set, omitting empty fields.
5. **Given** a successful trigger, **Then** the modal closes, form state resets
   (except `testSampleOnly`), and the app navigates to the Jobs page.

---

### User Story 5 - Explore the Stage 2 analysis dashboard (Priority: P1)

A user reviews the latest completed week's results across five tabs: Overview,
Top 25, By Sector, Sector Rotation, and All Stocks — with summary stat cards,
charts, sortable tables, classification filters, and an embedded TradingView chart
per stock.

**Why this priority**: The dashboard is the main consumable value of the analysis
feature for an end user.

**Independent Test**: On load the page fetches summary, history, and latest runs in
parallel and renders the Overview tab; switching tabs renders the documented
content; the All Stocks tab lazily fetches stocks for the latest run.

**Acceptance Scenarios**:

1. **Given** a completed run exists, **When** the page loads, **Then** summary
   stat cards show total stocks, Stage 2 count, and New / Re-Entry / Continuing /
   Removed counts (with icons), via `fetchStage2Summary`.
2. **Given** the Overview tab, **Then** a per-sector Stage 2 bar chart (top 20
   sectors) and, when ≥2 historical points exist, a Stage 2 count line chart over
   time (`fetchStage2History`) are shown.
3. **Given** the Top 25 / By Sector / All Stocks tabs, **Then** sortable tables
   render (3-state header sort: desc → asc → none; nulls last); Top 25 defaults to
   `rsScore` descending and shows Symbol, Company, Sector, Price, RS Score,
   RS Rank, Momentum, Quadrant, A/D, Classification.
4. **Given** the Sector Rotation tab, **Then** an RS-vs-momentum quadrant scatter
   (`fetchSectorRotation`) plots sectors colored by quadrant
   (leading/weakening/lagging/improving) with hover tooltips, plus a play/pause +
   slider timeline animating weekly snapshots (`fetchRotationHistory`).
5. **Given** the All Stocks tab, **When** a classification filter
   (all/new/continuing/reentry/removed) or sector filter is chosen, **Then**
   `fetchStage2Stocks(market, latestRunId, {classification, sectorId})` refetches.
6. **Given** any stock row, **When** the user opens its chart, **Then** a modal
   embeds a TradingView daily chart (`BSE:` for India, `NASDAQ:` for US) with
   10/20/50 EMA + volume studies.
7. **Given** no completed run, **Then** an empty state prompts the user to run an
   analysis; failed fetches degrade gracefully (empty/stale data, no crash).

---

### User Story 6 - Monitor job runs (Priority: P2)

A user views the job-run history for a market, sees status counts, watches active
runs update automatically, inspects a run's details/metrics, and cancels a
queued/running run.

**Independent Test**: `/:market/jobs` lists runs from
`fetchJobRuns({market, pageSize:50})`; while any run is `queued`/`running` the list
auto-refreshes every 5s and stops when none are active; cancel calls `cancelJobRun`.

**Acceptance Scenarios**:

1. **Given** the jobs page, **When** it loads, **Then** runs are listed (ID, Type,
   Status, Progress, Started, Duration) with completed/running/failed status
   counts.
2. **Given** active runs, **Then** the list (and any opened run detail) auto-refresh
   every 5 seconds; refresh stops once no run is `queued`/`running`.
3. **Given** a run row, **When** clicked, **Then** its detail (incl. metrics) is
   fetched via `fetchJobRun` and shown.
4. **Given** a queued/running run, **When** the user confirms cancel, **Then**
   `cancelJobRun` is called and the list/detail refresh.
5. **Given** a finished run, **Then** duration is computed/displayed and progress
   shows 100% for completed runs.

### Edge Cases

- Market route param is taken as-is (`india`/`us`); the SPA assumes valid markets
  reached via the home/menu cards.
- All data fetches fail silently (try/catch → empty/stale state) so a network error
  never crashes the app; the user may see an empty state instead of an error.
- The history line chart renders only with ≥2 points; the rotation timeline
  animation is skipped with <2 snapshots.
- Sector checkboxes invalid for the chosen run mode are pruned when the mode
  changes.
- Long sector/company names are truncated for chart and table layout.
- The Stage 2 dashboard is read-only against the latest COMPLETED run; in-flight
  runs are observed on the Jobs page, not the dashboard.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The SPA MUST provide client-side routes (react-router-dom v7):
  `/`, `/:market`, `/:market/sectors`, `/:market/sectors/:sectorId`,
  `/:market/stocks`, `/:market/analysis`, `/:market/jobs`, with a persistent nav
  bar and a light/dark theme toggle persisted to `localStorage` (`me-theme`,
  `data-theme` attribute).
- **FR-002**: The SPA MUST consume the existing REST API exclusively through the
  typed client in `src/api.ts` against base path `/api` (proxied by Vite to the
  .NET API in dev); it MUST NOT call the worker or the database directly.
- **FR-003**: The sectors screen MUST list sectors with stock counts, support
  client-side search, and create/rename/delete via `createSector`/`renameSector`/
  `deleteSector`, surfacing API errors (e.g. non-empty sector delete) and showing
  toasts.
- **FR-004**: The stocks screens MUST list paged stocks (`pageSize=50`) filterable
  by query `q` and `sectorId`, support create/edit/delete
  (`createStock`/`updateStock`/`deleteStock`) and bulk move (`moveStocks`), and
  render market-aware currency/market-cap formatting via `format.ts`.
- **FR-005**: The Analysis page MUST present a trigger modal whose options map to
  `TriggerAnalysisRequest` (`testSampleOnly`, `weekNumber`, `sectorIds`, `limit`,
  `minMarketCap`, `maxMarketCap`, `retryFailedOnly`), omitting unset fields, and
  MUST call `triggerAnalysis` then navigate to `/:market/jobs`.
- **FR-006**: The trigger week selector MUST offer the current week plus the prior
  12 ISO weeks and MUST send `weekNumber` only for a past week (current week
  omitted); "retry failed/pending only" MUST be offered only when the selected
  week already has a completed/failed run.
- **FR-007**: The Analysis page MUST load `fetchStage2Summary`, `fetchStage2History`,
  and latest runs in parallel on mount, render summary stat cards (totals +
  classification counts), and render an Overview bar chart (top 20 sectors) and a
  history line chart (only with ≥2 points) using `recharts`.
- **FR-008**: The Analysis page MUST provide tabs `overview`, `top25`, `sectors`,
  `rotation`, `stocks`, with sortable tables (3-state header sort, nulls last;
  Top 25 default `rsScore` desc) showing the documented columns.
- **FR-009**: The Sector Rotation tab MUST render an RS-score (X) vs RS-delta-2w (Y)
  quadrant scatter from `fetchSectorRotation`, colored by `quadrant` with hover
  tooltips (RS, momentum, stock/accum/dist counts), plus a play/pause + slider
  timeline over `fetchRotationHistory` snapshots (1.5s/frame, stops at end).
- **FR-010**: The All Stocks tab MUST lazily fetch `fetchStage2Stocks` for the
  latest run, filtered by a classification selector
  (all/new/continuing/reentry/removed) and an optional sector filter, refetching on
  change.
- **FR-011**: Each Stage 2 stock MUST be openable in a TradingView daily-chart
  modal using exchange-prefixed symbols (`BSE:` for India, `NASDAQ:` for US) with
  10/20/50 EMA and volume studies.
- **FR-012**: The Jobs page MUST list runs (`fetchJobRuns`, `pageSize=50`) with
  status counts and a table (ID, Type, Status, Progress, Started, Duration),
  auto-refresh every 5s while any run is `queued`/`running` (stopping otherwise),
  show run detail via `fetchJobRun`, and cancel via `cancelJobRun`.
- **FR-013**: All data fetches MUST degrade gracefully on failure (empty/stale
  state, toasts where applicable) without crashing the app.
- **FR-014**: The SPA MUST be a static client built by Vite (`tsc -b && vite build`)
  and is OUT OF SCOPE for any business logic — all analysis/computation stays in the
  API and worker.

### Key Entities *(include if feature involves data; client-side view types)*

- **Market**: `'india' | 'us'` — drives routing, currency, exchange prefix.
- **Sector** (`Sector`): `id, sectorName, stockCount`.
- **Stock** (`Stock`): `id, symbol, companyName, sectorId, sectorName?,
  broadSector?, marketCap?, isFno, isTestSample`.
- **PagedResult<T>**: `items, totalCount, page, pageSize`.
- **JobRun** (`JobRun`): `id, jobType, market, weekNumber, status, progress,
  parameters?, metrics?, errorMessage?, startedAt?, completedAt?, createdAt,
  durationSeconds?`.
- **StageAnalysisResult**, **Stage2Summary**, **SectorStage2Count**,
  **SectorRotation**, **Stage2History**, **SectorRotationHistory**,
  **TriggerAnalysisRequest** — TypeScript mirrors of the API DTOs (see
  `contracts/ui.md`). The SPA never defines new persisted data; it only renders
  these.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every route and screen in `App.tsx` (`Home`, `MarketMenu`,
  `SectorsPage`, `SectorDetail`, `StocksPage`, `AnalysisPage`, `JobsPage`) is
  documented with its purpose, controls, and API calls, matching the code.
- **SC-002**: Each documented API call in the SPA corresponds to a real function in
  `src/api.ts` and a real endpoint in a backend spec (Stage 2 analysis in
  `specs/001-stage2-analysis-api/`; sectors/stocks CRUD/move in
  `specs/004-catalog-api/`).
- **SC-003**: The five Analysis tabs and their tables/charts/filters match the
  rendered components and column headers in `AnalysisPage.tsx`.
- **SC-004**: Jobs auto-refresh starts only when an active run exists and stops when
  none remain (verifiable from the polling effect's guard).
- **SC-005**: The SPA contains no business/analysis computation — Stage 2 logic
  lives entirely in the worker/API (constitution: UI is presentation-only).

## Assumptions

- The .NET API is reachable at `/api` (Vite dev proxy targets the local API; in
  production the SPA is served by the API host).
- Markets are restricted to `india`/`us` and are only reached through the home and
  market-menu cards, so route-level market validation is delegated to the API.
- `recharts`, `react-router-dom` v7, and `lucide-react` are the charting, routing,
  and icon libraries (per `package.json`); TradingView is embedded via iframe.
- Sectors/stocks CRUD endpoints (`/api/{market}/sectors`, `/api/{market}/stocks`)
  behave as the `api.ts` client expects; their server contract is specified in
  `specs/004-catalog-api/` and is out of scope here.
- This SPA is presentation-only; all persisted data and analysis come from the API
  and worker, which are specified separately.
