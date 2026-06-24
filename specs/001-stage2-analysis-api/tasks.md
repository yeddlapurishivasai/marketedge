---
description: "Validation/documentation tasks for the backtracked Stage 2 Analysis API spec"
---

# Tasks: Stage 2 Analysis — API (Backtracked)

**Input**: Design documents from `specs/001-stage2-analysis-api/`

**Prerequisites**: `plan.md`, `spec.md`, `contracts/api.md`

> **Nature of these tasks**: This is a **backtracking** effort. Tasks document and
> **validate existing behavior** against the code — they do NOT build new features.
> Each task references real files. **The React SPA is out of scope** for every task.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (independent files/areas)
- **[Story]**: Which user story (US1–US5) the task validates

## Phase 1: Setup & Baseline

- [ ] T001 Confirm baseline builds: run `dotnet build src/MarketEdge.Api` and
  record success as the validation baseline.
- [ ] T002 [P] Inventory the routes in `src/MarketEdge.Api/Controllers/JobsController.cs`
  and confirm each is captured in `contracts/api.md` (method, route, query/body).
- [ ] T003 [P] Confirm NO EF Core migrations exist under `src/MarketEdge.Api`
  (Constitution I/II) — search for any `Migrations/` folder or `Migration` types.

## Phase 2: Trigger & Job Lifecycle (US1, US2)

- [ ] T004 [US1] Validate `TriggerStageAnalysisAsync` in `Services/JobService.cs`:
  market validation, week resolution via `GetIsoWeekNumber`, `weekNumber` regex
  `^\d{4}-W\d{2}$`, and future-week rejection — match against spec FR-001/FR-002.
- [ ] T005 [US1] Validate dedup: an active `(stage2_analysis, market, week)` run is
  returned instead of creating a duplicate, and the `DbUpdateException`
  (SQL 2601/2627) fallback path resolves to the in-flight run (FR-003).
  Cross-check `src/MarketEdge.Database/Indexes/UX_JobRuns_ActiveWeek.sql`.
- [ ] T006 [US1] Validate `Parameters` persistence + queue payload: filters
  forwarded, full-sector selection summarized as `"All Sectors"`, message is
  base64 JSON on `stage-analysis-jobs` (FR-004, `contracts/api.md`).
- [ ] T007 [P] [US2] Validate `GET /api/jobs` filtering/ordering/pagination and
  `GET /api/jobs/{id}` mapping incl. `DurationSeconds` (FR-005/FR-006).
- [ ] T008 [P] [US2] Validate `POST /api/jobs/{id}/cancel`: non-terminal → cancelled
  `{ cancelled: true }`; terminal/unknown → `404` (FR-007).

## Phase 3: Stage 2 Read Models (US3, US4, US5)

- [ ] T009 [US3] Validate `GetLatestStage2SummaryAsync`: latest completed run's
  week, totals, classification counts, `BySector` ordering, `Top25` ordering by
  `RSScore` then `MomentumScore`; `404` when no completed run (FR-008).
- [ ] T010 [P] [US4] Validate `GetStage2StocksAsync` filter semantics
  (none → Stage 2; `removed`; else `IsStage2 AND classification`) + `sectorId`
  narrowing + ordering; unknown run → empty list (FR-009).
- [ ] T011 [P] [US5] Validate `GetSectorRotationAsync` aggregation over non-null
  `RSScore`/`RSDelta2w`, `GetQuadrant`, accumulating/distributing counts (FR-010).
- [ ] T012 [P] [US5] Validate `GetStage2HistoryAsync` (maxRuns=10) and
  `GetSectorRotationHistoryAsync` (maxRuns=12) collapse the audit log to one entry
  per week and order ascending (FR-011).
- [ ] T013 [P] Validate India/US symmetry: every market-scoped read selects the
  matching `Indian*`/`US*` entity set and sector table (FR-012).

## Phase 4: Contract & Data Model Sync

- [ ] T014 [P] Diff `Models/AnalysisDtos.cs` against `contracts/api.md` and confirm
  every DTO field is documented with correct nullability.
- [ ] T015 [P] Diff `Models/AnalysisEntities.cs` (`StageAnalysisResultBase`,
  `JobRun`) against the dacpac tables `StageAnalysisResults.sql`,
  `USStageAnalysisResults.sql`, `JobRuns.sql` — column names, types, nullability,
  and CHECK-constrained enums must align with the spec's Key Entities.

## Phase 5: End-to-End Verification (optional, requires environment)

- [ ] T016 With a seeded local SQL Server + running worker + Azurite queue, trigger
  a `testSampleOnly` run for `india`, poll `GET /api/jobs/{id}` to `completed`,
  then verify `summary`, `runs/{id}/stocks`, and `runs/{id}/sector-rotation`
  return the documented shapes (SC-001..SC-004).

## Dependencies

- T001 precedes all validation tasks.
- T004–T006 (trigger) before T016 (E2E).
- Phase 3 read-model tasks may run in parallel once T001 passes.

## Out of Scope (do NOT create tasks for)

- The React SPA under `src/MarketEdge.Api/clientapp/` (pages, charts, components).
- Any new endpoints, schema changes, or EF migrations.
