# Feature Specification: Stage 2 Analysis — API (Backtracked)

**Feature Branch**: `001-stage2-analysis-api`

**Created**: 2026-06-24

**Status**: Backtracked (documents existing behavior)

**Input**: Reverse-engineered from the existing .NET 8 Web API
(`src/MarketEdge.Api`): `Controllers/JobsController.cs`,
`Services/JobService.cs`, `Models/AnalysisEntities.cs`, `Models/AnalysisDtos.cs`,
plus the shared schema in `src/MarketEdge.Database`.

> **Scope note (UI EXPLICITLY EXCLUDED)**: This specification covers ONLY the
> server-side HTTP API and its job-orchestration / read-model behavior. The React
> SPA under `src/MarketEdge.Api/clientapp/` — its pages, charts, components, and
> any visual presentation — is **out of scope** and intentionally not specified
> here. "User" below means an API client (the SPA, a script, or another service),
> not an end user looking at a screen.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Trigger a Stage 2 analysis run for a market (Priority: P1)

An API client triggers Stage 2 analysis for a market (`india` or `us`). The API
records a job, enqueues it for the worker, and returns a `runId` the client can
poll. The unit of work is the current ISO week unless a past week is requested.

**Why this priority**: Triggering is the entry point of the whole feature; nothing
else is observable until a run has been created and queued.

**Independent Test**: `POST /api/india/analysis/trigger` with an empty/partial
body returns `200` with `{ runId }`, a `JobRuns` row is created with
`Status="queued"`, `JobType="stage2_analysis"`, and the current `WeekNumber`, and
a base64 JSON message lands on the Azure Storage Queue.

**Acceptance Scenarios**:

1. **Given** no in-flight run for the current week/market, **When** the client
   `POST`s to `/api/{market}/analysis/trigger`, **Then** a new `queued` `JobRuns`
   row is created and its `Id` is returned as `runId`, and a queue message is sent.
2. **Given** a run is already `queued` or `running` for the same `(market, week)`,
   **When** the client triggers again, **Then** the existing in-flight run's `Id`
   is returned and NO duplicate run is created (deduplication).
3. **Given** `market` is neither `india` nor `us`, **When** the client triggers,
   **Then** the API returns `400 Bad Request`.
4. **Given** a `weekNumber` in the request body, **When** it does not match
   `^\d{4}-W\d{2}$`, **Then** the API returns `400`; **When** it is chronologically
   after the current week, **Then** the API returns `400` ("in the future").
5. **Given** optional filters (`minMarketCap`, `maxMarketCap`, `sectorIds`,
   `limit`, `testSampleOnly`, `retryFailedOnly`), **When** provided, **Then** they
   are persisted on the run's `Parameters` JSON (sector selections resolved to
   sector *names* under the key `sectors`) and the raw `sectorIds` are forwarded in
   the queue message.

---

### User Story 2 - Poll job runs and progress (Priority: P1)

A client lists and inspects job runs to track status (`queued` → `running` →
`completed`/`failed`/`cancelled`), progress (0–100), metrics, timing, and errors.

**Why this priority**: Without run visibility a client cannot know when results are
ready or whether a run failed.

**Independent Test**: `GET /api/jobs?market=india&jobType=stage2_analysis` returns
a paged, newest-first list of run DTOs; `GET /api/jobs/{id}` returns one run or
`404`.

**Acceptance Scenarios**:

1. **Given** existing runs, **When** the client `GET`s `/api/jobs` with optional
   `market`, `jobType`, `page`, `pageSize`, **Then** matching runs are returned
   ordered by `CreatedAt` descending and paginated.
2. **Given** a run id, **When** the client `GET`s `/api/jobs/{id}`, **Then** the
   run DTO is returned (including parsed `Parameters`/`Metrics` and a computed
   `DurationSeconds` when both `StartedAt` and `CompletedAt` exist), else `404`.
3. **Given** an active run, **When** the client `POST`s `/api/jobs/{id}/cancel`,
   **Then** a `queued`/`running` run is set to `cancelled` and returns
   `{ cancelled: true }`; a `completed`/`failed`/`cancelled` run returns `404`.

---

### User Story 3 - Read the latest Stage 2 summary for a market (Priority: P1)

A client retrieves the most recent completed week's Stage 2 summary: totals,
classification counts, per-sector breakdown, and the top 25 stocks.

**Why this priority**: The summary is the primary consumable read model of the
feature.

**Independent Test**: `GET /api/india/analysis/summary` returns a
`Stage2SummaryDto` for the latest completed run's week, or `404` when no completed
run exists.

**Acceptance Scenarios**:

1. **Given** at least one `completed` run for the market, **When** the client
   `GET`s `/api/{market}/analysis/summary`, **Then** the summary is computed over
   ALL result rows for that run's `WeekNumber` (not just the triggering run).
2. **Given** the week's results, **Then** `TotalStocks` = all result rows,
   `Stage2Count` = rows with `IsStage2=true`, and `NewAdditions`/`ReEntries`/
   `Continuing`/`Removed` = counts by `Classification`.
3. **Given** Stage 2 rows, **Then** `BySector` lists sectors by Stage 2 count
   descending (each with Stage 2 and total counts), and `Top25` is the 25 Stage 2
   rows ordered by `RSScore` desc then `MomentumScore` desc.
4. **Given** no completed run, **Then** the API returns `404`.

---

### User Story 4 - Read Stage 2 stocks for a run, with filters (Priority: P2)

A client lists the analyzed stocks for a given run's week, optionally filtered by
`classification` and `sectorId`.

**Independent Test**:
`GET /api/{market}/analysis/runs/{runId}/stocks?classification=new&sectorId=5`
returns matching `StageAnalysisResultDto` rows ordered by `RSScore` then
`MomentumScore` descending.

**Acceptance Scenarios**:

1. **Given** a run id, **When** no `classification` filter is given, **Then** only
   `IsStage2=true` rows for the run's week are returned.
2. **Given** `classification="removed"`, **Then** rows with
   `Classification="removed"` are returned (these are NOT Stage 2 this week);
   **Given** any other classification, **Then** rows with `IsStage2=true AND
   Classification=<value>` are returned.
3. **Given** `sectorId`, **Then** results are further restricted to that sector.
4. **Given** an unknown `runId`, **Then** an empty list is returned.

---

### User Story 5 - Read sector rotation and history (Priority: P2)

A client retrieves sector-level rotation (average RS metrics + quadrant) for a run,
plus week-over-week history for Stage 2 counts and sector rotation.

**Independent Test**: `GET /api/{market}/analysis/runs/{runId}/sector-rotation`,
`GET /api/{market}/analysis/history`, and
`GET /api/{market}/analysis/rotation-history` each return the documented shapes.

**Acceptance Scenarios**:

1. **Given** a run, **When** the client requests sector-rotation, **Then** stocks
   with non-null `RSScore` and `RSDelta2w` are grouped by sector, averaged, and
   assigned a quadrant from `(AvgRSScore>0, AvgRSDelta2w>0)`, with accumulating/
   distributing counts, ordered by `AvgRSScore` descending.
2. **Given** multiple completed runs, **When** the client requests `history`
   (default `maxRuns=10`) or `rotation-history` (default `maxRuns=12`), **Then**
   the audit log is collapsed to ONE entry per week (the latest completed run owns
   the week), ordered chronologically ascending.
3. **Given** an invalid market on these endpoints, **Then** `400` is returned
   (history/rotation-history validate market; per-run endpoints derive market from
   the run).

### Edge Cases

- Duplicate concurrent triggers for the same week race on the
  `UX_JobRuns_ActiveWeek` filtered unique index; the API catches the unique
  violation (SQL errors 2601/2627) and returns the existing in-flight run instead
  of erroring.
- Supplied `sectorIds` are resolved to sector names and stored in `Parameters`
  under the key `sectors` (the raw ids are only forwarded in the queue message). A
  selection covering every sector for the market is recorded as `"All Sectors"`
  rather than the full name list.
- A run whose `WeekNumber` has no result rows yet yields empty summaries/lists,
  not errors.
- `Force` on the trigger request is accepted but deprecated/ignored (results are
  upserted per week; every trigger is its own audit run).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The API MUST expose `POST /api/{market}/analysis/trigger` that
  validates `market ∈ {india, us}` (else `400`), creates a `stage2_analysis`
  `JobRuns` row for the resolved ISO week, enqueues a base64-encoded JSON message,
  and returns `{ runId }`.
- **FR-002**: The trigger MUST resolve the target week as the current ISO week
  (`YYYY-Www`) unless `request.WeekNumber` is supplied; a supplied week MUST match
  `^\d{4}-W\d{2}$` and MUST NOT be in the future (else `400`).
- **FR-003**: The trigger MUST deduplicate: if an active (`queued`/`running`) run
  exists for `(JobType=stage2_analysis, market, week)`, it MUST return that run's
  id without creating a new run, and MUST also fall back to the existing run on a
  unique-index race (SQL 2601/2627).
- **FR-004**: The trigger MUST persist supplied filters (`minMarketCap`,
  `maxMarketCap`, `sectorIds`, `limit`, `testSampleOnly`, `retryFailedOnly`) into
  the run's `Parameters` JSON and include them in the queue message; full-sector
  selections MUST be summarized as `"All Sectors"`.
- **FR-005**: The API MUST expose `GET /api/jobs` returning runs filtered by
  optional `market`/`jobType`, ordered by `CreatedAt` desc, paginated by
  `page`/`pageSize` (defaults 1/20).
- **FR-006**: The API MUST expose `GET /api/jobs/{id}` returning the run DTO (with
  parsed `Parameters`/`Metrics` and computed `DurationSeconds`) or `404`.
- **FR-007**: The API MUST expose `POST /api/jobs/{id}/cancel` that sets a
  non-terminal run to `cancelled` (stamping `ErrorMessage` and `CompletedAt`) and
  returns `{ cancelled: true }`; terminal/unknown runs MUST return `404`.
- **FR-008**: The API MUST expose `GET /api/{market}/analysis/summary` computing a
  `Stage2SummaryDto` over the latest completed run's week, or `404` if none.
- **FR-009**: The API MUST expose
  `GET /api/{market}/analysis/runs/{runId}/stocks` supporting `classification` and
  `sectorId` filters with the documented Stage 2 / removed semantics, ordered by
  `RSScore` then `MomentumScore` descending.
- **FR-010**: The API MUST expose
  `GET /api/{market}/analysis/runs/{runId}/sector-rotation` aggregating per-sector
  average RS metrics, quadrant, and accumulation/distribution counts over rows with
  non-null `RSScore` and `RSDelta2w`.
- **FR-011**: The API MUST expose `GET /api/{market}/analysis/history` and
  `GET /api/{market}/analysis/rotation-history`, each collapsing the run audit log
  to one entry per week (latest completed run wins) and returning chronologically
  ascending series.
- **FR-012**: All market-scoped analysis endpoints MUST treat `india` and `us`
  symmetrically by selecting the corresponding entity set
  (`IndianStageAnalysisResults` / `USStageAnalysisResults`) and sector tables.
- **FR-013**: The API MUST use EF Core in query-only mode against the existing
  schema — NO migrations, NO schema management — and MUST return DTOs, never
  entities, across the boundary.
- **FR-014**: The API MUST share state with the worker ONLY via the SQL Server
  database and the Azure Storage Queue; it MUST NOT call the worker directly.

### Key Entities *(include if feature involves data)*

- **JobRun** (`JobRuns` table): an audit record of one analysis trigger.
  Attributes: `Id`, `JobType` (`stage2_analysis`), `Market` (`india`/`us`),
  `WeekNumber` (`YYYY-Www`), `Status` (`queued`/`running`/`completed`/`failed`/
  `cancelled`), `Progress` (0–100), `Parameters` (JSON), `Metrics` (JSON),
  `ErrorMessage`, `StartedAt`, `CompletedAt`, `CreatedAt`. Owns many stage-analysis
  result rows via `RunId`.
- **StageAnalysisResult** (`IndianStageAnalysisResults` / `USStageAnalysisResults`;
  EF base `StageAnalysisResultBase`): one analyzed `(WeekNumber, Symbol)` row.
  Identity/lineage: `Id`, `RunId`, `WeekNumber`, `Symbol`, `CompanyName`,
  `SectorId`, `SectorName`. Price/MA: `ClosePrice`, `MA10`, `MA30`, `MarketCap`.
  Stage 2: `IsStage2`, `Classification`
  (`new`/`continuing`/`reentry`/`removed`), `WeeksInStage2`. Relative strength:
  `RSScore`, `RSRank`, `RS1w`–`RS3w`, `RSDelta1w`–`RSDelta3w`. Momentum:
  `MomentumScore`, `ROC1w`–`ROC3w`. Rotation: `Quadrant`
  (`leading`/`weakening`/`lagging`/`improving`). Accumulation/distribution:
  `ADRatio`, `ADClassification` (`accumulating`/`distributing`/`neutral`).
- **DTOs**: `JobRunDto`, `TriggerAnalysisRequest`, `StageAnalysisResultDto`,
  `Stage2SummaryDto`, `SectorStage2CountDto`, `SectorRotationDto`,
  `Stage2HistoryDto`, `SectorRotationHistoryDto` — the only shapes returned to
  clients (see `contracts/api.md`).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of the endpoints in `JobsController` are documented with their
  method, route, inputs, validation, and response shape, matching the code.
- **SC-002**: Triggering twice for the same `(market, week)` yields exactly one
  active run and the same `runId` both times (verifiable against `JobRuns`).
- **SC-003**: Every market-scoped endpoint rejects a market other than `india`/`us`
  with `400` (where the controller validates) and otherwise resolves the correct
  per-market tables.
- **SC-004**: The summary, history, and rotation-history read models reflect a
  single snapshot per week even when multiple completed runs exist for that week.
- **SC-005**: No EF Core migration exists anywhere in `src/MarketEdge.Api`
  (query-only invariant holds).

## Assumptions

- "User" denotes an API client; the React SPA is explicitly out of scope and is
  treated only as one possible consumer of these contracts.
- The Azure Storage Queue and SQL Server schema (dacpac) already exist and are
  published; this spec documents API behavior against them, not their provisioning.
- ISO week computation uses `FirstFourDayWeek` / Monday-first rules as implemented
  in `JobService.GetIsoWeekNumber`.
- Result aggregation reads back the full week snapshot, consistent with the worker
  upserting results per `(WeekNumber, Symbol)`.
