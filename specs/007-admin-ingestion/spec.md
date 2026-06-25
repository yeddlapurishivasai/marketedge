# Spec 007 — Admin tab & Data Ingestion screen

## Summary

There is currently no UI to run the data-ingestion pipeline (`src/MarketEdge.Ingestion`).
Operators must drop to a terminal and invoke the Python CLI by hand. This spec adds an
**Admin** area to the SPA, with a **Data Ingestion** screen that lets an operator run the
full ingestion pipeline per market with a single **Ingest Data** action and monitor it as a
job run — reusing the existing `JobRun` infrastructure and Job Runs page.

The single action runs, in order, `ingest bars → ingest technical → ingest fundamentals`.
Ticker seeding is performed automatically inside the bars step (it seeds the universe
before fetching), so there is no separate "seed tickers" action.

## User stories

### US-1 — Ingest data from the UI (P1)
As an operator, I open `Admin → Data Ingestion` for a market and click **Ingest Data**,
which runs the whole pipeline (bars, technicals, fundamentals) for that market, so I never
need a terminal.

**Acceptance**
1. **Given** the Admin screen for a market, **When** I click **Ingest Data**, **Then** a
   single `JobRun` of type `data_ingestion` is created in `running` state and the Python
   CLI runs `ingest bars`, then `ingest technical`, then `ingest fundamentals` for that
   market.
2. **Given** the bars step, **Then** the ticker universe is seeded automatically as part of
   it (no separate seed action).
3. **Given** the pipeline is running, **When** I view the screen, **Then** I see it as an
   active run and can open the Job Runs page for live detail.
4. **Given** the pipeline finishes, **Then** the run is `completed` (all steps exit 0) or
   `failed` (first non-zero step stops the run), with duration and a tail of the output
   captured.

### US-2 — Choose run mode: sample vs full universe (P2)
As an operator, I pick a **Run Mode** — **Sample (200 stocks)** or **Full universe** — before
ingesting, exactly like Stage 2 Analysis, so I can validate quickly on the curated test
sample before committing to a full run.

**Acceptance**
1. **Given** the Run Mode toggle defaults to **Sample (200 stocks)**, **When** I ingest,
   **Then** every CLI step is invoked with `--test-sample` (the curated ~200-symbol sample).
2. **Given** I switch to **Full universe**, **When** I ingest, **Then** the CLI steps run
   without `--test-sample`, covering the entire ticker universe for the market.

### US-3 — Prevent duplicate concurrent runs (P2)
As an operator, I cannot start a second ingestion for a market while one is already running.

**Acceptance**
1. **Given** an in-flight `data_ingestion` run for a market, **When** I click **Ingest
   Data** again, **Then** the existing run is returned instead of starting a duplicate.

## Functional requirements

- **FR-001**: The SPA MUST expose an `Admin / Data Ingestion` entry per market (menu card +
  route `/{market}/admin`).
- **FR-002**: The API MUST expose `POST /api/{market}/ingestion/trigger` accepting
  `{ testSample?, limit? }` and running the full ingestion pipeline. `testSample` selects
  the curated ~200-symbol sample; omitting/false covers the full universe.
- **FR-003**: Triggering MUST create one `JobRun` (`JobType = "data_ingestion"`) and execute
  the ingestion CLI out-of-process, updating the run's status, progress, metrics, and error
  message on completion. Logging MUST NOT block the HTTP response (run in background).
- **FR-004**: The pipeline MUST run `ingest bars → ingest technical → ingest fundamentals`
  in order, advancing progress between steps and stopping on the first failing step. The
  bars step seeds the ticker universe automatically.
- **FR-005**: A run MUST be idempotent at the data layer — it relies on the ingestion
  pipeline's existing MERGE upserts and the rolling 1-year bar window; re-running is safe.
- **FR-006**: Only one in-flight `data_ingestion` run per market is allowed; a duplicate
  trigger returns the existing run.
- **FR-007**: Process wiring (python executable, ingestion working directory) MUST be
  configurable via `Ingestion:PythonPath` / `Ingestion:WorkingDirectory` in
  configuration, defaulting to the repo's `.local/worker-venv` interpreter and
  `src/MarketEdge.Ingestion`.
- **FR-008**: The screen MUST list recent `data_ingestion` runs (status, started, duration)
  and link to the Job Runs page for detail.

## Out of scope
- Scheduling/cron of ingestion (manual trigger only here).
- Authentication/authorization for the Admin area (local single-user app).
- New database schema — reuses the existing `JobRuns` table.

## CLI mapping
The single **Ingest Data** action runs these CLI invocations in order:

| order | CLI invocation                                  | notes                       |
|-------|-------------------------------------------------|-----------------------------|
| 1     | `python cli.py ingest bars --market {m}`        | seeds tickers, then bars    |
| 2     | `python cli.py ingest technical --market {m}`   |                             |
| 3     | `python cli.py ingest fundamentals --market {m}`|                             |

Shared flags appended when set: `--test-sample`, `--limit {N}`.

