# Implementation Plan: MarketEdge React SPA (Backtracked)

**Branch**: `003-marketedge-spa` | **Date**: 2026-06-24 | **Spec**: `./spec.md`

**Input**: Feature specification from `specs/003-marketedge-spa/spec.md`

## Summary

Document and verify the React 19 + TypeScript + Vite SPA under
`src/MarketEdge.Api/clientapp/`: market selection and routing, sector/stock CRUD
and move, the five-tab Stage 2 analysis dashboard (summary cards, recharts bar/line
charts, sortable tables, SVG sector-rotation quadrant + timeline, TradingView
modal, classification/sector filters, trigger modal), and the auto-refreshing job
monitor. Approach: trace `main.tsx → App.tsx → pages/* → api.ts`, capture the route
map, component responsibilities, and every API call, then assert they match the
code and the API contracts.

## Technical Context

**Language/Version**: TypeScript 6 / React 19

**Primary Dependencies**: react-router-dom 7 (routing), recharts 3 (bar/line
charts), lucide-react (icons), native `fetch` via `src/api.ts`; Vite 8 build;
TradingView embedded via iframe

**Storage**: None in the client — all state is component state + `localStorage`
for the theme; data comes from the REST API at `/api`

**Testing**: ESLint (`npm run lint`) and a type-checked build (`tsc -b &&
vite build`) are the baseline; no component test suite exists in-repo

**Target Platform**: Modern browsers; static assets served by the .NET API host
(Vite dev server proxies `/api` to the API in development)

**Project Type**: Web application — frontend SPA. Backend (API/worker) is out of
scope and specified separately.

**Performance Goals**: Parallel initial loads (`Promise.all`), lazy per-tab
fetching (All Stocks), display-only truncation; 5s job polling that auto-stops when
idle. No hard latency targets.

**Constraints**: Presentation-only — NO business/analysis logic in the client; the
SPA calls the API exclusively through `src/api.ts` (never the worker/DB directly);
graceful degradation on fetch failure.

**Scale/Scope**: 7 routes / 7 screen components; 17 API client functions; 5
analysis tabs; ~2,285 (India) / ~6,368 (US) stocks paged at 50/page.

## Constitution Check

*GATE: must hold for the documented behavior.*

- **I. Schema owned by SQL project**: N/A to the client (no DB access).
- **II. EF Core query-only (API)**: N/A to the client; SPA only calls HTTP
  endpoints.
- **III. Worker decoupled via queue + DB**: PASS — the SPA never touches the worker
  or DB; it triggers/monitors analysis purely through the API.
- **IV. Week-keyed, idempotent, append-only**: PASS (consumer side) — the dashboard
  reads the latest COMPLETED run's week; the trigger modal sends current/past ISO
  weeks and a retry-only option consistent with week-keyed upserts.
- **V. REST conventions**: PASS — all calls go through `/api/{india|us}/...` and
  `/api/jobs` via the typed client; markets restricted to `india`/`us`.
- **Presentation-only invariant**: PASS — no Stage 2 computation in the client; it
  renders API DTOs only.

No violations → Complexity Tracking not required.

## Project Structure

### Documentation (this feature)

```text
specs/003-marketedge-spa/
├── plan.md              # This file
├── spec.md              # Backtracked specification
├── tasks.md             # Validation/documentation tasks
└── contracts/
    └── ui.md            # Routes, API client map, tabs, formatting, build config
```

### Source Code (repository root)

```text
src/MarketEdge.Api/clientapp/
├── src/
│   ├── main.tsx              # React root (StrictMode)
│   ├── App.tsx               # Routes + NavBar/Theme + Home/MarketMenu/
│   │                         #   SectorsPage/SectorDetail/StocksPage
│   ├── api.ts                # Typed REST client (base /api) + view types
│   ├── format.ts             # Currency / market-cap / price formatting
│   ├── styles.css            # CSS variables, light/dark theme
│   └── pages/
│       ├── AnalysisPage.tsx  # 5-tab Stage 2 dashboard + trigger modal
│       └── JobsPage.tsx      # Job-run monitor (5s auto-refresh)
├── vite.config.ts            # Dev server :5173, proxy /api → :5063
├── package.json              # React 19, router 7, recharts 3, lucide; scripts
└── tsconfig*.json
```

**Structure Decision**: Existing single-page Vite app embedded in the API project.
No new screens or restructuring; documentation-and-validation only. Backend remains
out of scope.

## Complexity Tracking

No constitutional violations — section intentionally empty.
