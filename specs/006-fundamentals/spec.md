# Feature Specification: Fundamentals Ingestion & Observability

**Feature Branch**: `006-fundamentals`

**Created**: 2026-06-25

**Status**: Draft

**Input**: Add a dedicated specification for **fundamentals** ingestion (analyst
consensus, EPS forecasts, and valuation/financial fundamentals), promoting it from
the best-effort P3 story in `specs/005-data-ingestion/` into a first-class feature.
In addition, establish the project-wide **observability standard**: all logging MUST
use OpenTelemetry and be written to files at an OS-specific location (a fixed path on
the Linux server, a fixed path on local Windows), with logs retained for **7 days**.

> **Scope note**: This feature (a) specifies the fundamentals ingestion process that
> populates the fundamentals tables defined by the dacpac (`{Indian|US}AnalystSnapshot`,
> `{Indian|US}EpsForecasts`, and the valuation columns of `{Indian|US}TickerTechnical`
> / `{Indian|US}StockFundamentals`), and (b) defines the OpenTelemetry file-logging
> standard for **all** MarketEdge components (API, Worker, Ingestion). It does NOT
> introduce new schema beyond what `005` already defines, and it does NOT change the
> Stage 2 algorithm. Authoritative table DDL remains in `specs/005-data-ingestion/contracts/schema.md`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ingest analyst consensus & EPS forecasts as durable base data (Priority: P1)

An operator runs fundamentals ingestion for a market. For each active ticker the
pipeline fetches the analyst consensus card (rating, analyst count, current/next
quarter & year EPS) and per-period EPS forecasts (quarterly and yearly), and upserts
them keyed by `AsOfDate` so a later run preserves prior revisions as point-in-time
history.

**Why this priority**: Fundamentals enrich the base data the analysis layer reads
locally; making them durable and idempotent (not best-effort/transient) is the core
of this feature.

**Independent Test**: Run `ingest fundamentals --market us --limit 50`; assert
`USAnalystSnapshot` has one row per `(Ticker, AsOfDate)` and `USEpsForecasts` rows
carry `PeriodType ∈ {Q, Y}` keyed by `(Ticker, AsOfDate, PeriodType, PeriodEndDate)`,
and that a re-run on the same day updates in place with no duplicates.

**Acceptance Scenarios**:

1. **Given** an analyst card for a ticker on date D, **When** fundamentals ingestion
   runs, **Then** one `{Indian|US}AnalystSnapshot` row `(Ticker, D)` is upserted with
   consensus rating, analyst count, and current/next quarter & year EPS.
2. **Given** EPS forecasts, **When** ingestion runs, **Then** quarterly and yearly
   periods coexist in `{Indian|US}EpsForecasts`, discriminated by `PeriodType`, with
   `PeriodEndDate` in the key so a new `AsOfDate` preserves prior revisions.
3. **Given** a re-run for the same natural key, **Then** the row is updated in place
   and `UpdatedAt` advances; no duplicate row is created.
4. **Given** an invalid `PeriodType`, **Then** the row is rejected by the
   `CHECK (PeriodType IN ('Q','Y'))` constraint.

---

### User Story 2 - All logging uses OpenTelemetry, written to OS-specific files, retained 7 days (Priority: P1)

Every MarketEdge component emits its logs through OpenTelemetry. Records are written
to log **files** under a fixed, OS-specific directory — a known path on the Linux
server and a known path on local Windows — and old files are pruned so that exactly
**7 days** of logs are retained.

**Why this priority**: This is a non-negotiable operational requirement bundled with
this feature. Without a single, predictable, bounded log location, operators cannot
diagnose fundamentals runs (or any component) on the server or locally.

**Independent Test**: Run any component with no log-path override on Windows; assert a
log file appears under `C:\ProgramData\MarketEdge\logs\` containing OpenTelemetry
structured records (with `service.name`, `trace_id`, severity). Simulate 8 daily
rotations; assert only 7 dated files remain. Repeat on Linux asserting
`/var/log/marketedge/`.

**Acceptance Scenarios**:

1. **Given** a component starts on **Linux**, **When** it logs, **Then** records are
   written to files under `/var/log/marketedge/` (the server location).
2. **Given** a component starts on **Windows (local)**, **When** it logs, **Then**
   records are written to files under `C:\ProgramData\MarketEdge\logs\` (the local
   location).
3. **Given** the `MARKETEDGE_LOG_DIR` environment variable is set, **When** a
   component starts, **Then** that directory overrides the OS default and is created
   if missing.
4. **Given** more than 7 days of log files exist, **When** the next daily rotation
   occurs, **Then** files older than 7 days are removed and at most 7 daily files
   remain per component.
5. **Given** a log record is emitted, **Then** it carries OpenTelemetry resource and
   record attributes — at minimum `service.name`, `service.namespace=marketedge`,
   `deployment.environment`, severity, timestamp (UTC), and, when inside a span,
   `trace_id` / `span_id`.

---

### User Story 3 - Ingest valuation / financial fundamentals (Priority: P2)

For each active ticker the pipeline records the headline valuation/financial
fundamentals available from the source (market cap, and where available P/E, EPS
(TTM), dividend yield, beta, shares outstanding) so consumers can filter and rank on
fundamentals locally without live calls.

**Why this priority**: Valuation context is useful for screening but secondary to the
analyst/EPS card and the logging standard.

**Independent Test**: Run `ingest fundamentals --market india --limit 50`; assert the
ticker technical / fundamentals rows carry `MarketCap` (and any available valuation
fields) and that unavailable fields are stored as `NULL`.

**Acceptance Scenarios**:

1. **Given** an active ticker, **When** fundamentals ingestion runs, **Then** market
   cap (and any available valuation metrics) are upserted for that ticker keyed by its
   natural key.
2. **Given** a value is unavailable from the source, **Then** it is stored as `NULL`
   (never a sentinel or NaN).
3. **Given** a re-run, **Then** values are updated in place with `UpdatedAt` advanced;
   no duplicate rows.

---

### User Story 4 - Fundamentals runs are resilient and produce a logged summary (Priority: P2)

Because fundamentals endpoints are version-dependent and flaky, a single ticker (or a
single fundamentals sub-fetch) failing MUST never abort the run. Each failure is
logged with context via OpenTelemetry and the run ends with a structured summary
(counts of succeeded/skipped/failed per fundamentals type).

**Why this priority**: Resilience makes fundamentals safe to run alongside the P1
bars path without jeopardizing it.

**Independent Test**: Force one ticker's analyst fetch to raise; assert the run
completes, the failure is logged with the ticker and exception, and the end-of-run
summary reports it as failed while others succeed.

**Acceptance Scenarios**:

1. **Given** a ticker's fundamentals fetch raises, **When** ingestion runs, **Then**
   the error is logged (with ticker + cause), the ticker is counted as failed, and the
   run continues.
2. **Given** a run completes, **Then** a single structured summary log record reports
   per-type counts (analyst / EPS / valuation: succeeded, skipped, failed) and total
   duration.

### Edge Cases

- A ticker present in the master but with no fundamentals from the source is counted
  as "skipped" (no error) and leaves existing fundamentals rows untouched.
- The source may return `MultiIndex` / differently-shaped frames across versions; the
  pipeline normalizes them defensively and skips on shape mismatch rather than failing.
- NaN/inf values are converted to `NULL` before insert.
- The log directory may not exist or may not be writable on first run; the component
  MUST create it, and if it cannot, fall back to a documented temp location and emit a
  startup warning (it MUST NOT crash solely because logging setup failed).
- Concurrent components writing to the same OS log directory MUST use per-service file
  names so they do not clobber each other.
- Daily rotation at the server's local midnight is acceptable; record timestamps are
  UTC regardless.

## Requirements *(mandatory)*

### Functional Requirements — Fundamentals

- **FR-001**: The pipeline MUST expose `ingest fundamentals --market {india|us}` (with
  the shared universe selectors `--limit`, `--test-sample`, `--sectors`) and treat
  India and US symmetrically via the market → table-set map (no divergent code paths).
- **FR-002**: Analyst consensus MUST be upserted into `{Indian|US}AnalystSnapshot`
  keyed by `(Ticker, AsOfDate)` with consensus rating, analyst count, and
  current/next quarter & year EPS.
- **FR-003**: EPS forecasts MUST be upserted into `{Indian|US}EpsForecasts` keyed by
  `(Ticker, AsOfDate, PeriodType, PeriodEndDate)` with `PeriodType ∈ {Q, Y}`,
  preserving prior revisions across `AsOfDate` values.
- **FR-004**: Valuation/financial fundamentals (market cap, and any available P/E, EPS
  TTM, dividend yield, beta, shares outstanding) MUST be upserted for the ticker;
  unavailable fields MUST be `NULL`.
- **FR-005**: All fundamentals writes MUST be idempotent upserts on the natural key;
  re-runs update in place and never duplicate.
- **FR-006**: Fundamentals ingestion MUST be DML only — it MUST NOT manage schema
  (Principle I). It uses only tables defined by the `005` dacpac contract.
- **FR-007**: A single-ticker (or single sub-fetch) failure MUST be caught, logged
  with context, counted, and skipped; it MUST NEVER abort the whole run.
- **FR-008**: Every run MUST emit a structured end-of-run summary (per-type
  succeeded/skipped/failed counts and total duration) through the logging system.
- **FR-009**: Fundamentals ingestion MUST reuse the existing yfinance retry/backoff +
  throttle settings (`YFINANCE_BATCH_SIZE`, `YFINANCE_BATCH_DELAY`,
  `YFINANCE_MAX_RETRIES`) so it respects the same rate limits as the rest of `005`.
- **FR-010**: The per-symbol fundamentals loop MUST be parallelised across
  `FUNDAMENTALS_THREADS` worker threads (default 8; set 1 to force sequential), each with
  its own DB connection, since the step is dominated by sequential per-ticker yfinance
  network calls. Per-symbol error isolation (FR-007) and idempotent upserts (FR-005) MUST
  still hold under concurrency.
- **FR-011**: When invoked by the queue runner with `--run-id` and a
  `[--progress-start, --progress-end]` band, the step MUST report live in-band progress to
  `JobRuns.Progress` as symbols complete (throttled), so a long fundamentals step no longer
  sits at a single percentage. Absent those flags (manual/inline runs) it MUST be a no-op.

### Observability & Logging Requirements *(mandatory, cross-cutting)*

These apply to **all** MarketEdge components that produce logs — the .NET API
(`src/MarketEdge.Api`), the Python Worker (`src/MarketEdge.Worker`), and the Python
Ingestion CLI (`src/MarketEdge.Ingestion`).

- **OBS-001**: All logging MUST be emitted through **OpenTelemetry**. Components MUST
  configure an OpenTelemetry `LoggerProvider` with a MarketEdge `Resource` and route
  their native logging through it — Python via the OpenTelemetry logging handler
  bridging the stdlib `logging` module; .NET via the OpenTelemetry logging provider on
  `ILogger`. Ad-hoc `print`/`Console.WriteLine` logging is NOT permitted.
- **OBS-002**: Logs MUST be written to **files** (the durable sink of record). A
  file-backed OpenTelemetry log exporter MUST serialize records as newline-delimited
  JSON (one OTel `LogRecord` per line) including resource + record attributes. (An
  additional OTLP exporter MAY be enabled but the file sink is mandatory.)
- **OBS-003**: The log directory MUST resolve by operating system to a fixed location:
  - **Linux (server)**: `/var/log/marketedge/`
  - **Windows (local)**: `C:\ProgramData\MarketEdge\logs\`
  The environment variable `MARKETEDGE_LOG_DIR` MUST override this default on any OS.
  The chosen directory MUST be created on startup if it does not exist.
- **OBS-004**: Each component MUST write to a **per-service** file name so components
  sharing a directory do not collide, e.g. `marketedge-fundamentals.log`,
  `marketedge-worker.log`, `marketedge-api.log`. The service name MUST match the
  OpenTelemetry `service.name`.
- **OBS-005**: Log files MUST rotate **daily** and retain **exactly 7 days** of
  history (a daily rotation keeping 7 backups); files older than 7 days MUST be
  deleted automatically. The retention window MUST be configurable via
  `MARKETEDGE_LOG_RETENTION_DAYS` (default `7`).
- **OBS-006**: Every record MUST carry, at minimum: UTC timestamp, severity/level,
  message body, `service.name`, `service.namespace = marketedge`,
  `deployment.environment` (`local` on Windows dev / `server` on Linux, overridable),
  and — when emitted inside an active span — `trace_id` and `span_id` for correlation.
  Market-scoped operations SHOULD include a `market` attribute.
- **OBS-007**: Logging configuration MUST be centralized (one setup helper per
  language) and read from environment variables (`MARKETEDGE_LOG_DIR`,
  `MARKETEDGE_LOG_RETENTION_DAYS`, `OTEL_SERVICE_NAME`, `MARKETEDGE_ENVIRONMENT`,
  standard `OTEL_*`) so all components behave identically.
- **OBS-008**: Logging setup MUST be resilient: if the target directory is not
  writable, the component MUST fall back to a documented temp directory
  (`<temp>/marketedge-logs`), emit a single startup warning, and continue — a logging
  misconfiguration MUST NOT crash the component.
- **OBS-009**: Log records MUST NOT contain secrets (connection strings, credentials,
  tokens); connection details MUST be redacted to host/database only.

### Key Entities *(include if feature involves data)*

- **Analyst snapshot** — `{Indian|US}AnalystSnapshot` (keyed `(Ticker, AsOfDate)`).
- **EPS forecasts** — `{Indian|US}EpsForecasts` (keyed
  `(Ticker, AsOfDate, PeriodType, PeriodEndDate)`, `PeriodType ∈ {Q, Y}`).
- **Valuation fundamentals** — market cap (and available valuation metrics) on
  `{Indian|US}TickerTechnical` / `{Indian|US}StockFundamentals`.
- **Log record (operational, not DB)** — an OpenTelemetry `LogRecord`: timestamp,
  severity, body, trace/span ids, and resource/record attributes, serialized as JSON
  lines to the OS-specific log directory.

Authoritative table DDL: `specs/005-data-ingestion/contracts/schema.md`.
Logging/telemetry contract: `./contracts/telemetry.md`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After a fundamentals ingest for a market, every ticker with source data
  has an analyst snapshot and/or EPS forecast rows; re-running produces zero duplicate
  rows on any fundamentals table (upsert invariant holds).
- **SC-002**: A single-ticker fundamentals failure never aborts a run; it is logged
  with context and counted, and the run still completes with a summary.
- **SC-003**: With no overrides, logs are written to `/var/log/marketedge/` on Linux
  and `C:\ProgramData\MarketEdge\logs\` on Windows, as OpenTelemetry JSON-line records
  carrying `service.name` and (in-span) trace correlation ids.
- **SC-004**: After continuous operation, no log file older than 7 days remains in the
  log directory for any component (7-day retention holds).
- **SC-005**: Setting `MARKETEDGE_LOG_DIR` redirects all components' logs to that
  directory on any OS; an unwritable directory degrades to a temp fallback without
  crashing the component.
- **SC-006**: No MarketEdge component logs via `print` / `Console.WriteLine`; all log
  output flows through the OpenTelemetry logging pipeline.

## Assumptions

- The fundamentals tables (`{Indian|US}AnalystSnapshot`, `{Indian|US}EpsForecasts`)
  and the ticker master/technical tables already exist per `005`; this feature adds no
  new schema. Valuation fields are written to existing columns
  (`{Indian|US}TickerTechnical.MarketCap` / `{Indian|US}StockFundamentals`).
- yfinance remains the upstream source; its fundamentals APIs are flaky and
  version-dependent, hence the resilience requirements (FR-007/FR-008).
- OpenTelemetry is added as a dependency: Python components use the OpenTelemetry SDK
  + logging handler; the .NET API uses the OpenTelemetry logging provider. This is an
  observability addition, not a change to the schema-owns-the-dacpac principle.
- The Linux server path `/var/log/marketedge/` and Windows path
  `C:\ProgramData\MarketEdge\logs\` are writable by the service account (or overridden
  via `MARKETEDGE_LOG_DIR`); both are machine-wide, standard log locations for their OS.
- "Server" deployments are Linux; "local" development is Windows. Environment naming
  (`server` / `local`) is overridable via `MARKETEDGE_ENVIRONMENT`.
