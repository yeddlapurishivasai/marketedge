# Implementation Plan: Stage 2 Analysis — Worker (Backtracked)

**Branch**: `002-stage2-analysis-worker` | **Date**: 2026-06-24 | **Spec**: `./spec.md`

**Input**: Feature specification from `specs/002-stage2-analysis-worker/spec.md`

> **Backtracking plan**: The worker already exists and runs in production. This plan
> describes how to **document and validate the current behavior** — the Stage 2
> algorithm, job lifecycle, persistence, and resilience — NOT how to build it from
> scratch. **The React SPA is out of scope.**

## Summary

Document and verify the Python worker that consumes Stage 2 jobs from the queue,
fetches weekly market data from yfinance, computes the Stage 2 signal and
supporting metrics per stock, upserts results per `(WeekNumber, Symbol)`, and
post-processes the full week (classification, RS rank, weeks-in-stage2). Approach:
trace `app.py → worker.py → stage_analysis.py → db.py`, capture the algorithm and
data contracts, and assert they match the code via `test_e2e_worker.py` and targeted
review.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: Flask, pandas, yfinance, pyodbc, azure-storage-queue,
python-dotenv

**Storage**: SQL Server 2022 (schema owned by `src/MarketEdge.Database` dacpac;
tables `{Indian|US}StageAnalysisResults`, `{Indian|US}Stocks`,
`{Indian|US}Sectors`, `{Indian|US}StockFundamentals`, `JobRuns`); Azure Storage
Queue `stage-analysis-jobs` (Azurite locally)

**Testing**: `src/MarketEdge.Worker/test_e2e_worker.py` (single-sector end-to-end
against a seeded local DB + yfinance) — this is the baseline test.

**Target Platform**: Long-running Python service (Flask + background daemon thread)

**Project Type**: Backend worker service. No UI.

**Performance Goals**: Throughput is intentionally throttled to respect yfinance
rate limits (inter-stock and inter-sector sleeps); correctness over speed. Hard cap
via `MAX_RUN_TIMEOUT` (default 4h).

**Constraints**: Query/DML only (NO schema management); state shared with API only
via DB + queue; idempotent, week-keyed upserts; India/US symmetric via table maps;
NaN/inf persisted as NULL.

**Scale/Scope**: India ≈2,285 stocks / 123 sectors; US ≈6,368 stocks / 153 sectors;
weekly bars, last 60; one result row per `(WeekNumber, Symbol)`.

## Constitution Check

*GATE: must hold for the documented behavior.*

- **I. Schema owned by SQL project**: PASS — worker only runs DML (MERGE/UPDATE/
  SELECT); no DDL.
- **II. EF Core query-only (API)**: N/A to worker (worker uses pyodbc); worker
  likewise performs no schema management.
- **III. Worker owns heavy analysis; queue + DB only**: PASS — analysis runs in the
  worker; the only couplings are the queue and SQL Server.
- **IV. Week-keyed, idempotent, append-only**: PASS — skip completed/cancelled
  runs; upsert by `(WeekNumber, Symbol)`; post-process the full week snapshot.
- **V. REST conventions**: N/A (worker exposes only `/health`, `/status`); market
  handling is symmetric via `MARKET_TABLES`.

No violations → Complexity Tracking not required.

## Project Structure

### Documentation (this feature)

```text
specs/002-stage2-analysis-worker/
├── plan.md              # This file
├── spec.md              # Backtracked specification
├── tasks.md             # Validation/documentation tasks
└── contracts/
    └── algorithm.md     # Stage 2 algorithm + data/persistence contracts
```

### Source Code (repository root)

```text
src/MarketEdge.Worker/
├── app.py               # Flask /health, /status; starts listener thread
├── worker.py            # Queue listener, process_message, orchestration,
│                        #   per-sector loop, post-processing, status updates
├── stage_analysis.py    # Fetching + calculate_stage2 + classify_stocks +
│                        #   compute_rs_ranks + week_exclusive_end
├── db.py                # pyodbc DML: get_stocks, save_single_result (MERGE),
│                        #   update_job_status, week/Stage2 query helpers
├── config.py            # Env-driven configuration
└── test_e2e_worker.py   # Single-sector end-to-end pipeline test

src/MarketEdge.Database/ # dacpac — schema source of truth (read-only here)
├── Tables/StageAnalysisResults.sql / USStageAnalysisResults.sql
├── Tables/JobRuns.sql
└── Indexes/UX_JobRuns_ActiveWeek.sql
```

**Structure Decision**: Existing single-service Python worker layout. No new
modules or restructuring; documentation-and-validation only. No UI.

## Complexity Tracking

No constitutional violations — section intentionally empty.
