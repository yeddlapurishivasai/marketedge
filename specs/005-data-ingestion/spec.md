# Feature Specification: Market Data Ingestion (Base Data)

**Feature Branch**: `005-data-ingestion`

**Created**: 2026-06-25

**Status**: Draft

**Input**: Adapt the "MarketEdge v2 Database Schema" prompt (a PostgreSQL/Alembic
proposal) to the existing MarketEdge stack. Per the constitution, the schema is
owned by the `src/MarketEdge.Database` SQL Server dacpac (no migrations,
EF/pyodbc query-only). This feature adds a **base-data layer**: a set of
market-data tables plus an **ingestion pipeline** that fetches and stores prices,
technicals, and fundamentals so that downstream analysis (Stage 2 / sector
rotation) reads from local SQL Server instead of calling yfinance live.

> **Scope note**: This feature covers (a) the new SQL Server tables in the dacpac
> and (b) the ingestion process that populates them. It does NOT change the Stage 2
> algorithm itself; it only becomes the data source the worker consumes (see
> `specs/002-stage2-analysis-worker/` for the consumer-side changes). The React SPA
> is out of scope.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ingest the daily price history that analysis depends on (Priority: P1)

An operator runs the ingestion pipeline for a market. It selects the active ticker
universe, fetches daily OHLCV history (including the market benchmark), and upserts
it into the per-market daily bars table so that any later analysis has a complete,
point-in-time price base without touching yfinance.

**Why this priority**: Daily bars + the benchmark are the minimum base data the
sector-rotation/Stage 2 worker needs. Without them the consumer cannot run offline.

**Independent Test**: Run `ingest bars --market india --interval 1d` against a seeded
catalog; assert `IndianBars1D` is populated for each active ticker and the benchmark
(`^NSEI`), with one row per `(Ticker, BarDate)` and no duplicates on re-run.

**Acceptance Scenarios**:

1. **Given** the active ticker universe for a market, **When** daily ingestion runs,
   **Then** each ticker's daily OHLCV (open, high, low, close, volume, adj_close) is
   upserted into `{Indian|US}Bars1D` keyed by `(Ticker, BarDate)`.
2. **Given** a ticker already has bars up to date D, **When** ingestion re-runs,
   **Then** only missing/newer bars are written and existing rows are not duplicated
   (idempotent upsert by `(Ticker, BarDate)`).
3. **Given** the configured 1-year window (`DAILY_LOOKBACK_DAYS`, default 365),
   **When** ingestion runs, **Then** only bars on or after `today - lookback` are
   stored and any bars older than the window are pruned, so the stored history is a
   strict rolling 1-year window that never grows beyond the cap across repeated runs.
3. **Given** the market benchmark symbol (`^NSEI` India / `^GSPC` US), **When**
   ingestion runs, **Then** the benchmark is ingested as a ticker row and its daily
   bars are stored like any other ticker so consumers read it locally.
4. **Given** a fetch fails for a ticker, **When** ingestion runs, **Then** it retries
   with backoff and, on persistent failure, skips that ticker (counted) without
   aborting the whole run.

---

### User Story 2 - Maintain the master ticker list (Priority: P1)

The pipeline keeps a per-market master list of tickers (one row per symbol) with
exchange, active/F&O flags, and a count of available bars, so every time-series row
has a valid parent to reference and analysis can filter to active symbols.

**Why this priority**: Every non-master table references the ticker master via a
foreign key; it must exist and stay current first.

**Independent Test**: Run `seed tickers --market us`; assert `USTickers` has one row
per catalog symbol with `Exchange`, `Active`, `IsFno` set, and that
`BarsAvailable` reflects the count in `USBars1D` after a bars ingest.

**Acceptance Scenarios**:

1. **Given** the existing stock catalog (`{Indian|US}Stocks`), **When** the ticker
   master is seeded, **Then** `{Indian|US}Tickers` has exactly one row per symbol
   with `Active = 1` by default and `CreatedAt`/`UpdatedAt` stamped.
2. **Given** a re-seed, **When** it runs, **Then** existing tickers are updated in
   place (upsert by `Ticker`) and `UpdatedAt` advances; no duplicates are created.
3. **Given** bars exist for a ticker, **When** the master is refreshed, **Then**
   `BarsAvailable` equals the number of `{Indian|US}Bars1D` rows for that ticker.

---

### User Story 3 - Ingest the daily technical / RS snapshot (Priority: P2)

For each active ticker the pipeline records a daily technical snapshot (close, day %,
52-week high and distance from it, market cap, and relative-strength values) keyed by
`(Ticker, AsOfDate)`, so the latest fundamentals and RS context are available locally
to consumers (e.g. market-cap filtering) without live calls.

**Why this priority**: Market cap and headline technicals are needed by the
consumer's filters; they are valuable but can follow bars.

**Independent Test**: Run `ingest technical --market india`; assert
`IndianTickerTechnical` has one row per `(Ticker, AsOfDate)` with `Close`,
`MarketCap`, and the RS fields populated, and `UpdatedAt` stamped.

**Acceptance Scenarios**:

1. **Given** an active ticker, **When** technical ingestion runs for date D, **Then**
   a single row `(Ticker, D)` is upserted with `Close`, `DayPct`, `Open/High/Low`,
   `High52w`, `From52wHigh`, `MarketCap`, and any RS fields.
2. **Given** a re-run for the same `(Ticker, AsOfDate)`, **Then** the row is updated
   in place and `UpdatedAt` advances (no duplicate).
3. **Given** a value is unavailable, **Then** it is stored as `NULL` (never a sentinel
   or NaN).

---

### User Story 4 - Ingest analyst snapshot & EPS forecasts (Priority: P3)

The pipeline stores the headline analyst consensus card and per-period EPS forecasts
(quarterly and yearly) keyed so that revision history is preserved by `AsOfDate`.

**Why this priority**: Fundamental context enriches the base data but is not required
for the core Stage 2 / rotation computation.

**Independent Test**: Run `ingest fundamentals --market us`; assert
`USAnalystSnapshot` has one row per `(Ticker, AsOfDate)` and `USEpsForecasts` rows
carry `PeriodType ∈ {Q, Y}` with the `(Ticker, AsOfDate, PeriodType, PeriodEndDate)`
key.

**Acceptance Scenarios**:

1. **Given** an analyst card for a ticker on date D, **Then** one
   `{Indian|US}AnalystSnapshot` row `(Ticker, D)` is upserted with consensus rating,
   analyst count, and current/next quarter & year EPS.
2. **Given** EPS forecasts, **Then** quarterly and yearly periods coexist in
   `{Indian|US}EpsForecasts`, discriminated by `PeriodType`, with `PeriodEndDate` in
   the key so a new `AsOfDate` preserves prior revisions.
3. **Given** an invalid `PeriodType`, **Then** the row is rejected by the `CHECK
   (PeriodType IN ('Q','Y'))` constraint.

### Edge Cases

- A symbol present in the catalog but missing from the market-data source is recorded
  as a ticker with `Active = 1` and `BarsAvailable = 0`; it is skipped (counted) for
  time-series ingestion rather than failing the run.
- yfinance may return `MultiIndex` columns even for single tickers; the pipeline
  flattens/deduplicates columns before persistence.
- NaN/inf values are converted to `NULL` before insert (SQL compatibility).
- Daily bars store both `Close` and `AdjClose` (adjusted vs. unadjusted close).
- Currency is implicit by market (INR for `Indian*`, USD for `US*`); monetary columns
  are documented with a currency comment, never a separate column.
- Re-running ingestion must never create a second row for an existing natural key
  (every base table upserts).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The schema MUST define **10 SQL Server tables** in the
  `src/MarketEdge.Database` dacpac — a per-market (`Indian*` / `US*`) pair for each of:
  Tickers, TickerTechnical, AnalystSnapshot, EpsForecasts, Bars1D — with
  identical structure per pair except the ticker length and the currency comment.
- **FR-002**: India ticker columns MUST be `NVARCHAR(30)` (to fit suffixes such as
  `.NS` / `.BO`, e.g. `RELIANCE.NS`); US ticker columns MUST be `NVARCHAR(20)`.
- **FR-003**: Every non-master table MUST declare a foreign key
  `Ticker → {Indian|US}Tickers(Ticker)`; the ticker master MUST be created/seeded
  before any dependent table is populated.
- **FR-004**: All snapshot tables (`*TickerTechnical`, `*AnalystSnapshot`,
  `*EpsForecasts`) MUST include `UpdatedAt DATETIME2 NOT NULL DEFAULT GETUTCDATE()`;
  the master tables MUST include `CreatedAt` and `UpdatedAt`.
- **FR-005**: All `CreatedAt` / `UpdatedAt` columns MUST default to `GETUTCDATE()` so
  audit timestamps are stored in UTC.
- **FR-006**: Monetary/price columns MUST use `DECIMAL(18,4)`; per-share/EPS columns
  `DECIMAL(10,4)`; day/percent columns `DECIMAL(8,4)`; `MarketCap` and `Volume` MUST
  be `BIGINT`. Currency is implicit by market and noted in a column comment.
- **FR-007**: Constraint and index names MUST be deterministic and follow the project
  convention (`PK_*`, `FK_*`, `CK_*`, `UX_*`, `IX_*`) so dacpac publishes are stable.
- **FR-008**: `{Indian|US}EpsForecasts` MUST enforce `CHECK (PeriodType IN ('Q','Y'))`
  and carry an index on `(Ticker, PeriodType, PeriodEndDate)`. `*Bars1D` MUST carry an
  index on `(BarDate)`.
- **FR-009**: The ingestion pipeline MUST expose a CLI with at least
  `seed tickers`, `ingest bars` (daily, interval `1d`), `ingest technical`, and
  `ingest fundamentals`, each scoped by `--market {india|us}`.
- **FR-010**: The pipeline MUST treat `india` and `us` symmetrically via a market →
  table-set lookup (no divergent code paths), and reject unsupported markets.
- **FR-011**: All time-series writes MUST be idempotent upserts on the natural key
  (`(Ticker, BarDate)`, `(Ticker, AsOfDate)`,
  `(Ticker, AsOfDate, PeriodType, PeriodEndDate)`); re-runs update in place.
- **FR-011a**: Daily-bar ingestion MUST store at most a rolling 1-year window. Bars
  older than `today - DAILY_LOOKBACK_DAYS` (default 365) MUST NOT be staged and MUST be
  pruned on every run, so storage never exceeds the configured window regardless of how
  many times ingestion runs. `DAILY_LOOKBACK_PERIOD` (yfinance fetch window) defaults to
  `1y` to align with this cap.
- **FR-012**: The pipeline MUST fetch from yfinance with retries + exponential backoff
  and throttling, carrying over the worker's prior settings — batched fetches
  (`YFINANCE_BATCH_SIZE`, default 50), an inter-batch delay (`YFINANCE_BATCH_DELAY`,
  default 4.0s), bounded retries (`YFINANCE_MAX_RETRIES`, default 3), and inter-request
  (and, where applicable, inter-batch/inter-sector) sleeps to respect rate limits. It
  MUST skip-and-count tickers that persistently fail and NEVER abort the whole run on a
  single-ticker error.
- **FR-013**: The pipeline MUST ingest the market benchmark (`^NSEI` / `^GSPC`) as a
  ticker so its bars are available locally to consumers.
- **FR-014**: The pipeline MUST perform DML only (INSERT/UPDATE/MERGE/SELECT) — it
  MUST NOT manage schema. The schema is owned exclusively by the dacpac.
- **FR-015**: NaN/inf MUST be persisted as `NULL`; unavailable fields MUST be `NULL`,
  not sentinels.

### Key Entities *(include if feature involves data)*

- **Ticker (master)** — `{Indian|US}Tickers`: one row per symbol; `Ticker` (PK),
  `Exchange`, `Active`, `IsFno`, `BarsAvailable`, `CreatedAt`, `UpdatedAt`.
- **Ticker technical (daily snapshot)** — `{Indian|US}TickerTechnical`: keyed
  `(Ticker, AsOfDate)`; close/day%/OHLC, 52w high + distance, market cap, RS family
  (`Rs`, `Rs1d/1w/1m/3m/6m`, `RsType`, `RsDate`), scanner hits, `UpdatedAt`.
- **Analyst snapshot** — `{Indian|US}AnalystSnapshot`: keyed `(Ticker, AsOfDate)`;
  consensus rating, analyst count, current/next quarter & year EPS, `UpdatedAt`.
- **EPS forecasts** — `{Indian|US}EpsForecasts`: keyed
  `(Ticker, AsOfDate, PeriodType, PeriodEndDate)`; consensus/high/low EPS, estimate
  count, revisions up/down, `UpdatedAt`. `PeriodType ∈ {Q, Y}`.
- **Daily bars** — `{Indian|US}Bars1D`: keyed `(Ticker, BarDate)`; OHLCV + `AdjClose`.

Authoritative DDL: see `contracts/schema.md`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All 10 tables publish cleanly via the dacpac with the exact columns,
  types, PKs, FKs, defaults, indexes, and CHECK constraints in `contracts/schema.md`;
  India tickers are `NVARCHAR(30)`, US are `NVARCHAR(20)`.
- **SC-002**: After a full ingest for a market, every active ticker (and the
  benchmark) has daily bars, and re-running the ingest produces zero duplicate rows
  on any base table (upsert invariant holds).
- **SC-003**: The Stage 2 / sector-rotation worker can run a complete week end-to-end
  reading bars/benchmark/market-cap from these tables with **zero** live yfinance
  calls (validated against `specs/002-stage2-analysis-worker/`).
- **SC-004**: A single-ticker fetch failure never aborts a run; the ticker is skipped
  and counted, and the run still completes.
- **SC-005**: The pipeline performs no schema changes (DML only) and treats India and
  US identically via a market → table-set map.

## Assumptions

- The existing stock catalog (`{Indian|US}Stocks` / `{Indian|US}Sectors`) is seeded
  and is the source of the symbol universe used to populate the ticker master; the
  ticker master is joined to the catalog by `Symbol`.
- yfinance remains the upstream market-data source (`.NS` suffix for India; benchmark
  `^NSEI` / `^GSPC`).
- The new tables live alongside the existing schema in the same SQL Server database
  and dacpac; they do not replace `{Indian|US}StockFundamentals` (which the worker may
  continue to write market caps to during transition).
- The ingestion CLI follows the existing worker's Python toolchain (Python 3.12,
  pandas, yfinance, pyodbc) rather than introducing PostgreSQL/Alembic/SQLAlchemy. The
  yfinance retry/throttle settings (`YFINANCE_BATCH_SIZE`, `YFINANCE_BATCH_DELAY`,
  `YFINANCE_MAX_RETRIES`) carry over from the worker's `config.py`, which retires them.
