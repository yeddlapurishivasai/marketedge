---
description: "Validation/documentation tasks for the backtracked MarketEdge SPA spec"
---

# Tasks: MarketEdge React SPA (Backtracked)

**Input**: Design documents from `specs/003-marketedge-spa/`

**Prerequisites**: `plan.md`, `spec.md`, `contracts/ui.md`

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (independent files/areas)
- **[Story]**: Which user story (US1–US6) the task validates

## Phase 1: Setup & Baseline

- [ ] T001 Confirm baseline tooling: from `src/MarketEdge.Api/clientapp/` run
  `npm run lint` and `npm run build` (`tsc -b && vite build`) and record success as
  the validation baseline.
- [ ] T002 [P] Confirm `vite.config.ts` proxy (`/api` → `http://localhost:5063`)
  and `package.json` deps (react 19, react-router-dom 7, recharts 3, lucide-react)
  match `contracts/ui.md`.
- [ ] T003 [P] Confirm the SPA has NO direct worker/DB access — all network calls
  go through `src/api.ts` against base `/api` (Constitution: presentation-only).

## Phase 2: Routing & Shell (US1)

- [ ] T004 [US1] Validate the route table in `App.tsx` matches `contracts/ui.md`
  (`/`, `/:market`, `/:market/sectors`, `/:market/sectors/:sectorId`,
  `/:market/stocks`, `/:market/analysis`, `/:market/jobs`).
- [ ] T005 [P] [US1] Validate `Home`/`MarketMenu` navigation cards and the
  `NavBar` theme toggle persistence (`localStorage['me-theme']`, `data-theme`).

## Phase 3: Catalog Management (US2, US3)

- [ ] T006 [US2] Validate `SectorsPage`: list + stock counts via `fetchSectors`,
  client-side search, and create/rename/delete via `createSector`/`renameSector`/
  `deleteSector` with toasts and the non-empty-delete error surface.
- [ ] T007 [P] [US3] Validate `StocksPage`/`SectorDetail`: paged `fetchStocks`
  (`q`, `sectorId`, `pageSize=50`), create/edit/delete, and bulk `moveStocks`
  selection flow.
- [ ] T008 [P] [US3] Validate market-aware formatting in `format.ts`
  (`currencySymbol`, `formatMarketCap` India Cr/Lakh Cr vs US M/B/T, `formatPrice`).

## Phase 4: Stage 2 Dashboard (US4, US5)

- [ ] T009 [US4] Validate the trigger modal in `AnalysisPage.tsx`: option → field
  mapping for `TriggerAnalysisRequest`, omission of unset fields, sample-vs-full
  controls, dev-default `testSampleOnly`, and post-trigger navigation to
  `/:market/jobs`.
- [ ] T010 [US4] Validate week selection: current + prior 12 ISO weeks; `weekNumber`
  sent only for past weeks; `retryFailedOnly` offered only when the week has a
  completed/failed run (`canRetry`).
- [ ] T011 [P] [US5] Validate on-mount parallel loads (`fetchStage2Summary`,
  `fetchStage2History`, latest runs), summary stat cards, Overview bar chart
  (top 20 sectors) and history line chart (≥2 points) via `recharts`.
- [ ] T012 [P] [US5] Validate the tab set (`overview/top25/sectors/rotation/stocks`)
  and the 3-state sortable tables incl. Top 25 columns + default `rsScore` desc.
- [ ] T013 [US5] Validate the Sector Rotation quadrant SVG (X=`avgRSScore`,
  Y=`avgRSDelta2w`, color by `quadrant`, hover tooltip) and the play/pause + slider
  timeline over `fetchRotationHistory` (1.5s/frame, stops at end).
- [ ] T014 [P] [US5] Validate the All Stocks tab lazy `fetchStage2Stocks` for
  `latestRunId` with classification + sector filters refetching on change.
- [ ] T015 [P] [US5] Validate the TradingView modal symbol/exchange mapping
  (`BSE:`/`NASDAQ:`) and EMA 10/20/50 + Volume studies, and the empty/loading/error
  states.

## Phase 5: Job Monitoring (US6)

- [ ] T016 [US6] Validate `JobsPage`: `fetchJobRuns` list + status count cards +
  table columns (ID/Type/Status/Progress/Started/Duration).
- [ ] T017 [US6] Validate the 5s auto-refresh effect — active only while a run is
  `running`/`queued`, cleared otherwise — plus `fetchJobRun` detail and
  `cancelJobRun` (with confirm) flows.

## Phase 6: Contract Sync

- [ ] T018 [P] Diff the `src/api.ts` view types against the API DTOs in
  `specs/001-stage2-analysis-api/contracts/api.md` and confirm field names /
  nullability align.
- [ ] T019 [P] Confirm every API call documented in `contracts/ui.md` maps to a real
  `src/api.ts` function and a real endpoint (specs 001 or the sectors/stocks
  controllers).

## Phase 7: End-to-End Verification (optional, requires environment)

- [ ] T020 With the API running and a seeded DB, load the SPA (dev or built),
  trigger a `testSampleOnly` India run from the Analysis modal, watch the Jobs page
  auto-refresh to `completed`, then return to the Analysis dashboard and confirm
  summary/top25/rotation/stocks render (SC-001..SC-004).

## Dependencies

- T001 precedes all validation tasks.
- Phase 4 dashboard tasks depend on the API returning a completed run (or fixtures).
- T020 (E2E) depends on Phases 2–6 and a running API.

## Out of Scope (do NOT create tasks for)

- API, worker, or database behavior (specified in specs 001 and 002).
- Any new screens, redesigns, or added client-side computation.
- Styling/CSS refactors beyond confirming theme variables exist.
