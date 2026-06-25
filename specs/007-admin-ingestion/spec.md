# Spec 007 — Admin tab & Data Ingestion screen

## Summary

There is currently no UI to run the data-ingestion pipeline (`src/MarketEdge.Ingestion`).
Operators must drop to a terminal and invoke the Python CLI by hand. This spec adds an
**Admin** area to the SPA, with a **Data Ingestion** screen that lets an operator trigger
ingestion steps per market and monitor them as job runs — reusing the existing `JobRun`
infrastructure and Job Runs page.

## User stories

### US-1 — Trigger ingestion from the UI (P1)
As an operator, I open `Admin → Data Ingestion` for a market and start an ingestion step
(seed tickers, bars, technical, fundamentals, or the full pipeline) with one click, so I
never need a terminal.

**Acceptance**
1. **Given** the Admin screen for a market, **When** I click a step, **Then** a `JobRun`
   of type `data_ingestion` is created in `running` state and the Python CLI runs that
   step for the selected market.
2. **Given** a step is running, **When** I view the screen, **Then** I see it as an active
   run and can open the Job Runs page for live detail.
3. **Given** a step finishes, **Then** the run is marked `completed` (exit 0) or `failed`
   (non-zero), with duration and a tail of the process output captured.

### US-2 — Scope a test run (P2)
As an operator, I can restrict a run to the test-sample universe and/or a ticker limit, so
I can validate quickly (e.g. 200 symbols) before a full run.

**Acceptance**
1. **Given** the "test sample only" toggle, **When** I trigger a step, **Then** the CLI is
   invoked with `--test-sample`.
2. **Given** a numeric limit, **When** I trigger a step, **Then** the CLI is invoked with
   `--limit N`.

### US-3 — Prevent duplicate concurrent runs (P2)
As an operator, I cannot accidentally start the same step for the same market while one is
already running.

**Acceptance**
1. **Given** an in-flight `data_ingestion` run for (market, step), **When** I trigger the
   same step again, **Then** the existing run is returned instead of starting a duplicate.

## Functional requirements

- **FR-001**: The SPA MUST expose an `Admin / Data Ingestion` entry per market (menu card +
  route `/{market}/admin`).
- **FR-002**: The API MUST expose `POST /api/{market}/ingestion/trigger` accepting
  `{ step, testSample?, limit? }` where `step ∈ {seed_tickers, bars, technical, fundamentals, full}`.
- **FR-003**: Triggering MUST create a `JobRun` (`JobType = "data_ingestion"`) and execute
  the ingestion CLI out-of-process, updating the run's status, progress, metrics, and error
  message on completion. Logging MUST NOT block the HTTP response (run in background).
- **FR-004**: `full` MUST run `seed_tickers → bars → technical → fundamentals` in order,
  advancing progress between steps and stopping on the first failing step.
- **FR-005**: A run MUST be idempotent at the data layer — it relies on the ingestion
  pipeline's existing MERGE upserts and the rolling 1-year bar window; re-running a step is
  safe.
- **FR-006**: Only one in-flight `data_ingestion` run per (market, step) is allowed; a
  duplicate trigger returns the existing run.
- **FR-007**: Process wiring (python executable, ingestion working directory) MUST be
  configurable via `Ingestion:PythonPath` / `Ingestion:WorkingDirectory` in
  configuration, defaulting to the repo's `.local/worker-venv` interpreter and
  `src/MarketEdge.Ingestion`.
- **FR-008**: The screen MUST list recent `data_ingestion` runs (status, step, started,
  duration) and link to the Job Runs page for detail.

## Out of scope
- Scheduling/cron of ingestion (manual trigger only here).
- Authentication/authorization for the Admin area (local single-user app).
- New database schema — reuses the existing `JobRuns` table.

## CLI mapping
| step          | CLI invocation                                  |
|---------------|-------------------------------------------------|
| seed_tickers  | `python cli.py seed tickers --market {m}`       |
| bars          | `python cli.py ingest bars --market {m}`        |
| technical     | `python cli.py ingest technical --market {m}`   |
| fundamentals  | `python cli.py ingest fundamentals --market {m}`|

Shared flags appended when set: `--test-sample`, `--limit {N}`.
