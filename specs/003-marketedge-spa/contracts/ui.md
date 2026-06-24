# UI Contracts: MarketEdge SPA (Backtracked)

Source of truth: `src/MarketEdge.Api/clientapp/src/` — `App.tsx`, `api.ts`,
`format.ts`, `pages/AnalysisPage.tsx`, `pages/JobsPage.tsx`, `vite.config.ts`,
`package.json`. This documents the EXISTING SPA. The backend it calls is specified
in `specs/001-stage2-analysis-api/`.

## Routes (`App.tsx`, react-router-dom v7)

| Path | Component | Purpose |
|------|-----------|---------|
| `/` | `Home` | Choose India or US market |
| `/:market` | `MarketMenu` | Links to sectors / stocks / analysis / jobs |
| `/:market/sectors` | `SectorsPage` | List + CRUD sectors |
| `/:market/sectors/:sectorId` | `SectorDetail` | Stocks in a sector; bulk move |
| `/:market/stocks` | `StocksPage` | Search/paginate + CRUD stocks |
| `/:market/analysis` | `AnalysisPage` | Stage 2 dashboard + trigger |
| `/:market/jobs` | `JobsPage` | Job-run monitoring |

Shell: `NavBar` (brand link to `/`, theme toggle). Theme persisted in
`localStorage['me-theme']`; applied as `document.documentElement[data-theme]`.

## API client (`api.ts`, base `/api`)

| Function | Method + endpoint |
|----------|-------------------|
| `fetchSectors(market, testSampleOnly?)` | GET `/{market}/sectors[?testSampleOnly=true]` |
| `createSector(market, name)` | POST `/{market}/sectors` |
| `renameSector(market, id, name)` | PUT `/{market}/sectors/{id}` |
| `deleteSector(market, id)` | DELETE `/{market}/sectors/{id}` |
| `fetchStocks(market, {q,sectorId,page,pageSize})` | GET `/{market}/stocks?...` |
| `createStock(market, data)` | POST `/{market}/stocks` |
| `updateStock(market, id, data)` | PUT `/{market}/stocks/{id}` |
| `deleteStock(market, id)` | DELETE `/{market}/stocks/{id}` |
| `moveStocks(market, ids, target)` | POST `/{market}/stocks/move` |
| `fetchJobRuns({market,jobType,page,pageSize})` | GET `/jobs?...` |
| `fetchJobRun(id)` | GET `/jobs/{id}` |
| `cancelJobRun(id)` | POST `/jobs/{id}/cancel` |
| `triggerAnalysis(market, request?)` | POST `/{market}/analysis/trigger` |
| `fetchStage2Summary(market)` | GET `/{market}/analysis/summary` |
| `fetchStage2Stocks(market, runId, {classification,sectorId})` | GET `/{market}/analysis/runs/{runId}/stocks?...` |
| `fetchSectorRotation(market, runId)` | GET `/{market}/analysis/runs/{runId}/sector-rotation` |
| `fetchStage2History(market, maxRuns=10)` | GET `/{market}/analysis/history?maxRuns=` |
| `fetchRotationHistory(market, maxRuns=12)` | GET `/{market}/analysis/rotation-history?maxRuns=` |

Client view types mirror the API DTOs: `Sector`, `Stock`, `PagedResult<T>`,
`JobRun`, `TriggerAnalysisRequest`, `StageAnalysisResult`, `SectorStage2Count`,
`Stage2Summary`, `SectorRotation`, `Stage2History`, `SectorRotationHistory`.

## Analysis page tabs (`AnalysisPage.tsx`)

`tab ∈ {overview, top25, sectors, rotation, stocks}` (labels: Overview, Top 25,
By Sector, Sector Rotation, All Stocks).

- **overview**: summary stat cards (Total, Stage 2, New/Re-Entry/Continuing/
  Removed); per-sector Stage 2 bar chart (top 20); Stage 2 line chart over history
  (only if ≥2 points). Charts via `recharts`.
- **top25**: sortable table — Symbol, Company, Sector, Price, RS Score, RS Rank,
  Momentum, Quadrant, A/D, Classification. Default sort `rsScore` desc.
- **sectors**: sortable table — Sector, Stage 2 count, total, …
- **rotation**: SVG quadrant scatter (X = `avgRSScore`, Y = `avgRSDelta2w`),
  colored by `quadrant`, hover tooltip (RS, momentum, stockCount,
  accumulating/distributing); timeline play/pause + range slider over
  `rotationHistory` (1.5 s/frame, stops at last frame).
- **stocks**: lazily fetched for `latestRunId`; classification filter
  (all/new/continuing/reentry/removed) + optional sector filter → refetch.

Sort hook: 3-state per column — first click desc, second asc, third clears; nulls
sorted last; strings via `localeCompare`.

Trigger modal → `TriggerAnalysisRequest`:
- `testSampleOnly` (sample vs full; defaults to `import.meta.env.DEV`),
- `weekNumber` (current + prior 12 ISO weeks; sent only for past weeks),
- `sectorIds` (checkboxes), `limit`, `minMarketCap`, `maxMarketCap` (full-universe
  only), `retryFailedOnly` (only when the week has a completed/failed run).
On success: reset form (keep `testSampleOnly`), close modal, navigate to
`/:market/jobs`.

TradingView modal: iframe to `s.tradingview.com/widgetembed` with `symbol` =
`BSE:{symbol}` (India) / `NASDAQ:{symbol}` (US), `interval=D`, EMA 10/20/50 +
Volume studies.

## Jobs page (`JobsPage.tsx`)

- Table columns: ID, Type, Status, Progress, Started, Duration, (actions).
- Status count cards: completed / running / failed.
- Auto-refresh: `setInterval` every 5000 ms, active only while some run is
  `running`/`queued`; cleared on unmount / when none active. Selected run detail
  refreshed via `fetchJobRun` while active.
- Row click → `fetchJobRun(id)` detail (incl. metrics). Cancel (confirm) →
  `cancelJobRun(id)` then reload.
- Helpers: `formatDuration`, `formatDate`, `formatJobType`.

## Formatting (`format.ts`)

- `currencySymbol(market)` → `₹` (india) / `$` (us).
- `formatMarketCap(value, market)`: India → Lakh Cr (1e12) / Cr (1e7) / L (1e5);
  US → T (1e12) / B (1e9) / M (1e6).
- `formatPrice(value, market)` → `{symbol}{value.toFixed(2)}`.

## Build / tooling (`package.json`, `vite.config.ts`)

- React 19, react-router-dom 7, recharts 3, lucide-react; Vite 8 + TS.
- Scripts: `dev` (vite), `build` (`tsc -b && vite build`), `lint` (eslint),
  `preview`.
- Dev server port 5173, proxy `/api` → `http://localhost:5063` (`changeOrigin`).
