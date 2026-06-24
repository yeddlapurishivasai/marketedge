# MarketEdge Constitution

MarketEdge is a stock market management and analysis application cataloging Indian
(NSE) and US (NASDAQ/NYSE/AMEX) stocks by sector, and running weekly Stage 2
(Weinstein-style) technical analysis over them. This constitution captures the
project's non-negotiable engineering principles. It is grounded strictly in the
existing codebase (`src/MarketEdge.Api`, `src/MarketEdge.Worker`,
`src/MarketEdge.Database`) and supersedes ad-hoc conventions.

## Core Principles

### I. Database Schema Is Owned by the SQL Project (NON-NEGOTIABLE)

The SQL Server schema is managed exclusively by the `src/MarketEdge.Database`
SDK-style database project (Microsoft.Build.Sql, target Sql160) and shipped as a
dacpac (build → publish).

- NO Entity Framework Core migrations may ever be added. EF Core is query-only.
- Every table, index, constraint, and check lives as a `.sql` file under
  `src/MarketEdge.Database/Tables/` (or `Indexes/`), e.g. `JobRuns.sql`,
  `IndianStageAnalysisResults.sql`, `UX_JobRuns_ActiveWeek.sql`.
- Schema changes are made by editing those `.sql` files and re-publishing the
  dacpac, never by code-first model changes.
- Database invariants (e.g. CHECK constraints on `Status`, `Market`,
  `Classification`, `Quadrant`, `ADClassification`; the `UX_*_WeekSymbol` unique
  index; the filtered `UX_JobRuns_ActiveWeek` index) are authoritative and must be
  mirrored, not contradicted, by application code.

### II. EF Core Is Query-Only in the API

The .NET 8 Web API uses EF Core (`MarketEdgeDbContext`) solely to read and write
rows via LINQ. It never manages schema.

- Entities (`Models/AnalysisEntities.cs`) map to existing tables with
  `[Table(...)]` / `[ForeignKey(...)]` attributes; they describe, not define, the
  schema.
- Controllers → Services → DbContext is the required layering. Controllers stay
  thin (validation + delegation); business logic lives in services
  (`Services/*.cs`).
- DTOs (`Models/AnalysisDtos.cs`) are the only shapes crossing the API boundary;
  entities are never returned directly.

### III. Worker Owns Heavy Analysis; Communicates via Queue + Shared DB

Long-running market analysis runs in the Python worker (`src/MarketEdge.Worker`),
never inline in an API request.

- The API enqueues jobs onto an Azure Storage Queue (base64-encoded JSON) and
  records a `JobRuns` row; the worker consumes the queue and writes results back
  to SQL Server via `pyodbc`.
- The API and worker share state ONLY through the SQL Server database and the
  queue. There is no direct API↔worker RPC.
- The worker is a Flask app exposing `/health` and `/status`; the actual work runs
  on a background queue-listener thread.
- yfinance is the market-data source. All price/benchmark/market-cap fetching,
  retries, throttling, and the Stage 2 algorithm live in the worker
  (`stage_analysis.py`).

### IV. Jobs Are Week-Keyed, Idempotent, and Append-Only

The unit of analysis is an ISO week (`YYYY-Www`) per market.

- Results are upserted by the natural key `(WeekNumber, Symbol)` — exactly one row
  per symbol per week. Re-runs and retries overwrite, never duplicate.
- `JobRuns` is an append-only audit log; completed runs never block new triggers.
- At most one active (`queued`/`running`) run may exist per
  `(JobType, Market, WeekNumber)`, enforced both in the service and by the
  `UX_JobRuns_ActiveWeek` filtered unique index. Races fall back to the existing
  in-flight run.
- Job processing must be idempotent: a run already `completed`/`cancelled` is
  skipped; cancellation and timeouts are honored cooperatively mid-run.
- Past weeks are analyzed point-in-time (prices bounded to that week's close);
  future weeks are rejected.

### V. REST API Conventions

- Stock/sector/analysis endpoints are market-scoped under
  `/api/{india|us}/...`; the `{market}` segment is validated to be exactly
  `india` or `us`.
- Generic job endpoints live under `/api/jobs`.
- Invalid market or malformed input returns `400`; missing resources return
  `404`; successful triggers return the `runId`.
- Seed/reference data (sectors, stocks) is sourced from SQL
  (`Scripts/SeedData.sql`), not JSON or code.

## Technology Constraints

- API: .NET 8 Web API, EF Core (query-only), React 19 + TypeScript + Vite SPA
  under `clientapp/` (the SPA is OUT OF SCOPE for analysis specs).
- Worker: Python 3.12, Flask, pandas, yfinance, pyodbc, azure-storage-queue.
- Database: SQL Server 2022, dacpac via Microsoft.Build.Sql; tables
  `IndianSectors`, `IndianStocks`, `USSectors`, `USStocks`,
  `IndianStockFundamentals`, `USStockFundamentals`, `JobRuns`,
  `IndianStageAnalysisResults`, `USStageAnalysisResults`.
- Connection strings use Windows Auth + `TrustServerCertificate=True` locally.
- India and US are symmetric markets backed by parallel tables; logic must treat
  them uniformly (table lookup by market key), not with divergent code paths.

## Development Workflow

- Schema first: change `.sql` files, rebuild/publish the dacpac, then update
  query-only EF entities and worker SQL to match.
- Keep the API DTO contract, the EF entities, and the worker's result dictionary
  in sync — they all describe the same `(Week, Symbol)` result row.
- Existing tests (e.g. `src/MarketEdge.Worker/test_e2e_worker.py`) and builds are
  the baseline; run them before and after changes. Do not introduce new
  test/lint/build tooling unless required.
- Surgical changes only: do not refactor unrelated code or add speculative
  abstractions.

## Governance

This constitution supersedes other practices for the API, Worker, and Database
projects. Any change that adds an EF migration, manages schema from code,
introduces direct API↔worker coupling, breaks week-keyed idempotency, or diverges
the India/US code paths is a constitutional violation and must be justified
explicitly (with a simpler alternative considered) before merge. Specs and plans
under `specs/` must declare a Constitution Check and document any deviation.

**Version**: 1.0.0 | **Ratified**: 2026-06-24 | **Last Amended**: 2026-06-24
