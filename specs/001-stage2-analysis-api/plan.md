# Implementation Plan: Stage 2 Analysis — API (Backtracked)

**Branch**: `001-stage2-analysis-api` | **Date**: 2026-06-24 | **Spec**: `./spec.md`

**Input**: Feature specification from `specs/001-stage2-analysis-api/spec.md`

## Summary

Document and verify the existing Stage 2 analysis HTTP surface of
`MarketEdge.Api`: trigger/poll/cancel job runs and read week-keyed Stage 2 read
models (summary, stocks, sector rotation, history). The technical approach is to
trace controller → service → EF Core (query-only) → SQL Server / Azure Storage
Queue, capture the contracts, and assert the documented behavior matches the code.

## Technical Context

**Language/Version**: C# / .NET 8

**Primary Dependencies**: ASP.NET Core Web API, EF Core (query-only,
`MarketEdgeDbContext`), `Azure.Storage.Queues`, `Microsoft.Data.SqlClient`

**Storage**: SQL Server 2022 (schema owned by `src/MarketEdge.Database` dacpac;
tables `JobRuns`, `IndianStageAnalysisResults`, `USStageAnalysisResults`,
`IndianSectors`, `USSectors`); Azure Storage Queue `stage-analysis-jobs`

**Testing**: Manual/HTTP verification against a seeded local DB + worker; no
dedicated API unit-test project exists in-repo (baseline = `dotnet build`)

**Target Platform**: Windows/IIS-or-Kestrel server hosting the API + SPA proxy

**Project Type**: Web service (backend API). Frontend SPA explicitly out of scope.

**Performance Goals**: Endpoints are simple LINQ reads/writes; no special targets
beyond responsive single-week aggregations.

**Constraints**: EF Core query-only (NO migrations); DTO-only responses;
API↔worker decoupled via DB + queue; market endpoints symmetric for `india`/`us`.

**Scale/Scope**: India ≈123 sectors / 2,285 stocks; US ≈153 sectors / 6,368
stocks; one result row per `(WeekNumber, Symbol)`.

## Constitution Check

*GATE: must hold for the documented behavior.*

- **I. Schema owned by SQL project**: PASS — API adds no migrations; entities map
  to dacpac-owned tables.
- **II. EF Core query-only**: PASS — services use LINQ reads/writes; responses are
  DTOs (`MapResult`, `MapJobRun`), never entities.
- **III. Worker decoupled via queue + DB**: PASS — trigger only writes `JobRuns`
  and enqueues a base64 message; no direct worker calls.
- **IV. Week-keyed, idempotent, append-only**: PASS — dedup on active
  `(JobType, Market, WeekNumber)` plus `UX_JobRuns_ActiveWeek`; read models
  collapse the audit log to one snapshot per week.
- **V. REST conventions**: PASS — `/api/{india|us}/...` with `400`/`404`
  semantics; `/api/jobs` for generic runs.

No violations → Complexity Tracking not required.

## Project Structure

### Documentation (this feature)

```text
specs/001-stage2-analysis-api/
├── plan.md              # This file
├── spec.md              # Backtracked specification
├── tasks.md             # Validation/documentation tasks
└── contracts/
    └── api.md           # HTTP contracts + DTO shapes
```

### Source Code (repository root)

```text
src/MarketEdge.Api/
├── Controllers/
│   └── JobsController.cs          # Trigger/poll/cancel + Stage 2 read endpoints
├── Services/
│   ├── JobService.cs              # Orchestration, dedup, aggregation (LINQ)
│   └── IJobService                # Service contract (in JobService.cs)
├── Models/
│   ├── AnalysisEntities.cs        # JobRun, StageAnalysisResultBase (+ India/US)
│   └── AnalysisDtos.cs            # Request + response DTOs
├── Data/
│   └── MarketEdgeDbContext.cs     # DbSets (query-only)
└── Program.cs                     # DI: DbContext, QueueClient, services

src/MarketEdge.Database/           # dacpac — schema source of truth (read-only here)
├── Tables/JobRuns.sql
├── Tables/StageAnalysisResults.sql       # IndianStageAnalysisResults
├── Tables/USStageAnalysisResults.sql
└── Indexes/UX_JobRuns_ActiveWeek.sql
```

**Structure Decision**: Existing web-service layout (Controllers → Services →
DbContext). No new projects or restructuring; documentation-only effort. The
`clientapp/` SPA is excluded.

## Complexity Tracking

No constitutional violations — section intentionally empty.
