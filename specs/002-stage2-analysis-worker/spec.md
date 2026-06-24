# Feature Specification: Stage 2 Analysis — Worker (Backtracked)

**Feature Branch**: `002-stage2-analysis-worker`

**Created**: 2026-06-24

**Status**: Backtracked (documents existing behavior)

**Input**: Reverse-engineered from the existing Python worker
(`src/MarketEdge.Worker`): `worker.py`, `stage_analysis.py`, `db.py`, `app.py`,
`config.py`, `test_e2e_worker.py`, plus the shared schema in
`src/MarketEdge.Database`.

> **Scope note (UI EXPLICITLY EXCLUDED)**: This specification covers ONLY the
> Python worker: queue consumption, market-data fetching, the Stage 2 detection
> algorithm, result persistence, and the week-level post-processing. The React SPA
> and any visual presentation are **out of scope**. The API is specified separately
> (`specs/001-stage2-analysis-api/`); here it is referenced only as the producer of
> queue messages and the consumer of the rows the worker writes.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Consume a queued job and run weekly Stage 2 analysis (Priority: P1)

The worker continuously listens to the Azure Storage Queue. On a message it decodes
the job, resolves the target week, fetches weekly price + benchmark + market-cap
data from yfinance for the selected stock universe, computes Stage 2 metrics per
stock, and upserts one result row per `(WeekNumber, Symbol)`.

**Why this priority**: This is the worker's entire reason to exist — turning a
queued job into persisted analysis results.

**Independent Test**: Place a base64 JSON job message
(`{market, runId, weekNumber, ...}`) on the queue with a seeded DB; the worker
processes it, writes rows to `IndianStageAnalysisResults`/`USStageAnalysisResults`,
and marks the `JobRuns` row `completed` with progress 100.
`test_e2e_worker.py` exercises this pipeline for a single sector.

**Acceptance Scenarios**:

1. **Given** a queue message, **When** received, **Then** the worker decodes
   base64 JSON (falling back to plain JSON), extracts `market`, `runId`, optional
   filters, and resolves `weekNumber` (from the message or `JobRuns`).
2. **Given** the run is already `completed`/`cancelled`, **When** processing
   starts, **Then** the worker skips it (idempotent) without re-analyzing.
3. **Given** the target week is fully in the past, **When** fetching prices,
   **Then** all fetches are bounded to that week's close (point-in-time); for the
   current/ongoing week, fetches run live through today.
4. **Given** stocks for the market, **When** analysis runs per stock, **Then** each
   analyzed stock yields a result dict that is immediately upserted by
   `(WeekNumber, Symbol)`, and `JobRuns.Progress`/`Metrics` update after each
   sector.
5. **Given** processing completes, **When** done, **Then** the run is marked
   `completed` (progress 100) with final metrics including result/classification
   counts.

---

### User Story 2 - Compute the Stage 2 signal and per-stock metrics (Priority: P1)

For each stock with sufficient weekly history, the worker computes moving averages,
relative strength vs. the market benchmark, momentum, rotation quadrant, and
accumulation/distribution, and decides whether the stock is in Stage 2.

**Why this priority**: The correctness of the Stage 2 signal and metrics is the
core value of the product.

**Independent Test**: Call `calculate_stage2(stock_frame, benchmark_frame)` with
≥30 weekly bars and assert the returned dict's fields and the `is_stage2` boolean
follow the documented rules (`contracts/algorithm.md`).

**Acceptance Scenarios**:

1. **Given** fewer than 30 weekly bars, **When** `calculate_stage2` is called,
   **Then** it returns `None` (insufficient data → stock skipped).
2. **Given** ≥30 bars, **Then** `MA10` and `MA30` are the latest 10- and 30-week
   rolling means of weekly Close.
3. **Given** the RS line `Close/Benchmark`, **When** ≥52 aligned points exist,
   **Then** `RSScore = ((latest_rs / SMA52(rs)) - 1) * 100` (Mansfield-style);
   `RS1w/RS2w/RS3w` are the RS-line values 1/2/3 weeks ago and
   `RSDelta1w/2w/3w` are their percent changes to now.
4. **Given** weekly closes, **Then** `ROC1w/2w/3w` are 1/2/3-week rates of change
   and `MomentumScore = 0.4*ROC1w + 0.3*ROC2w + 0.3*ROC3w` (only when all present).
5. **Given** `RSScore` and `RSDelta2w`, **Then** `Quadrant` is
   `leading | weakening | lagging | improving` per their signs.
6. **Given** the last 10 weekly bars, **Then** accumulation volume (Close>Open) and
   distribution volume (Close<Open) yield `ADRatio = acc/(acc+dist)` (0.5 if no
   volume) and `ADClassification` = `accumulating` (>0.6) / `distributing` (<0.4) /
   `neutral`.
7. **Given** all signals, **Then** `is_stage2` is true iff: latest Close > MA30,
   the 30-week SMA is rising (now > value 5 weeks ago), Close > MA10, MA10 > MA30,
   and `RSScore` is present and > 0.

---

### User Story 3 - Classify, rank, and count weeks across the full week (Priority: P1)

After all stocks are analyzed, the worker post-processes the entire week's snapshot:
classifies each stock vs. prior history, computes percentile RS ranks, and counts
consecutive Stage 2 weeks.

**Why this priority**: These week-level fields (new/continuing/reentry/removed,
RS rank, weeks-in-stage2) are what downstream consumers rank and filter on; they
must be computed over the whole week, not a single run.

**Independent Test**: With multiple weeks of data, verify `classify_stocks`,
`compute_rs_ranks`, and `get_consecutive_stage2_weeks` produce the documented
values and that the worker writes them back across the week's rows.

**Acceptance Scenarios**:

1. **Given** current/previous/ever Stage 2 sets, **When** classifying, **Then**:
   current ∧ previous → `continuing`; current ∧ never-before → `new`; current ∧
   was-before-but-not-previous → `reentry`; previous ∧ not-current → `removed`.
2. **Given** the week's `RSScore` values, **When** ranking, **Then** each scored
   stock gets a 0–99 percentile `RSRank` (single scored stock → `99`); unscored
   stocks get `None`.
3. **Given** prior weeks, **Then** `WeeksInStage2` for a current Stage 2 stock is
   the count of consecutive prior Stage 2 weeks + 1; non-Stage 2 stocks get 0.
4. **Given** post-processing reads the FULL week snapshot via `get_week_results`,
   **Then** symbols processed in an earlier run for the same week are included.

---

### User Story 4 - Resilience: retries, throttling, cancellation, timeout (Priority: P2)

The worker tolerates flaky market data and long runtimes, honors user cancellation,
and never deadlocks the queue.

**Independent Test**: Cancel a run mid-flight (set `JobRuns.Status=cancelled`) and
confirm the worker stops gracefully at the next sector boundary; simulate fetch
failures and confirm retries with backoff.

**Acceptance Scenarios**:

1. **Given** a yfinance fetch failure, **When** fetching prices/market caps,
   **Then** the worker retries up to `YFINANCE_MAX_RETRIES` with exponential
   backoff and skips the symbol/batch if still failing (no crash).
2. **Given** throttling settings, **Then** the worker sleeps between stocks and
   (longer) between sectors to respect rate limits.
3. **Given** the run is cancelled in the DB, **When** the next sector/cancellation
   check runs, **Then** processing stops gracefully and the listener returns to
   idle without marking failure.
4. **Given** elapsed time exceeds `MAX_RUN_TIMEOUT`, **Then** the run raises a
   timeout and is marked `failed`.
5. **Given** any unhandled error, **Then** the run is marked `failed` with the
   error message, and the queue message handling continues (listener keeps running,
   message visibility/retry preserved).

---

### User Story 5 - Retry-failed and test-sample runs (Priority: P3)

The worker supports re-processing only the symbols missing a result row for the
week, and restricting to the local test-sample universe for fast runs.

**Independent Test**: With some symbols already persisted for the week, run with
`retryFailedOnly=true` and confirm only the missing symbols are processed; run with
`testSampleOnly=true` and confirm only `IsTestSample=1` stocks are selected.

**Acceptance Scenarios**:

1. **Given** `retryFailedOnly=true`, **When** selecting the universe, **Then**
   symbols already having a row for the week (`get_completed_symbols_for_week`) are
   excluded; if none remain, the worker proceeds to post-processing without error.
2. **Given** `testSampleOnly=true`, **Then** `get_stocks` filters to
   `st.IsTestSample = 1`.
3. **Given** `sectorIds` / `limit`, **Then** the universe is restricted to those
   sectors / capped by `TOP`.

### Edge Cases

- yfinance returns `MultiIndex` columns even for single tickers; the worker
  flattens/deduplicates columns before computing metrics.
- NaN/inf metric values are converted to `NULL` before persistence
  (SQL compatibility).
- A symbol with <30 bars or an analysis exception is skipped (counted in
  `skipped_stocks`) rather than failing the run.
- Market-cap filter: stocks with unknown market cap are skipped when any
  `min/max` cap filter is active.
- The benchmark is fetched once per run (`^NSEI` for India, `^GSPC` for US);
  failure to fetch the benchmark fails the run.
- Health/status: the Flask `/health` and `/status` endpoints reflect the
  background listener's state without performing analysis.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The worker MUST run a Flask app (`app.py`) exposing `GET /health`
  and `GET /status`, and MUST start a daemon background thread running
  `start_queue_listener`.
- **FR-002**: The listener MUST poll the `stage-analysis-jobs` Azure Storage Queue
  with a visibility timeout exceeding `MAX_RUN_TIMEOUT`, process one message at a
  time, delete the message on success, and reconnect/retry on Azure or unexpected
  errors without terminating.
- **FR-003**: `process_message` MUST decode base64 JSON (fallback plain JSON),
  extract `market`, `runId`, and optional `minMarketCap`, `maxMarketCap`,
  `sectorIds`, `limit`, `testSampleOnly`, `retryFailedOnly`, `weekNumber`.
- **FR-004**: The worker MUST be idempotent: skip runs already `completed`/
  `cancelled`; resolve `weekNumber` from `JobRuns` when absent and error if none.
- **FR-005**: For a fully-past week the worker MUST bound all price/benchmark
  fetches to the week's exclusive end (point-in-time); for the current week it MUST
  fetch live through today.
- **FR-006**: The worker MUST select the stock universe via `get_stocks` honoring
  `sectorIds`, `limit`, `testSampleOnly`, and (in `retryFailedOnly` mode) exclude
  symbols already persisted for the week.
- **FR-007**: The worker MUST fetch weekly OHLCV from yfinance (`1wk`, ~2y / 760d
  window, last 60 bars), the market benchmark once (`^NSEI`/`^GSPC`), and per-stock
  market cap via `fast_info`, with retries + exponential backoff and inter-stock /
  inter-sector throttling.
- **FR-008**: `calculate_stage2` MUST return `None` for <30 weekly bars; otherwise
  compute `ClosePrice, MA10, MA30, RSScore, RS1w–RS3w, RSDelta1w–3w, MomentumScore,
  ROC1w–3w, Quadrant, ADRatio, ADClassification, is_stage2` per the algorithm in
  `contracts/algorithm.md`.
- **FR-009**: `is_stage2` MUST be true iff Close>MA30 ∧ 30wk SMA rising ∧ Close>MA10
  ∧ MA10>MA30 ∧ RSScore present ∧ RSScore>0.
- **FR-010**: Each analyzed stock MUST be upserted immediately into the market's
  results table keyed by `(WeekNumber, Symbol)` via a MERGE, stamping the latest
  `RunId`; NaN/inf MUST be stored as `NULL`. Freshly fetched market caps MUST be
  upserted into the market's fundamentals table.
- **FR-011**: After all stocks, the worker MUST post-process the FULL week snapshot:
  set `Classification` (`new`/`continuing`/`reentry`/`removed`) via
  `classify_stocks` using previous-week and ever-before Stage 2 sets; set `RSRank`
  (0–99 percentile) via `compute_rs_ranks`; set `WeeksInStage2` from consecutive
  prior Stage 2 weeks + 1.
- **FR-012**: The worker MUST update `JobRuns` status/progress/metrics throughout
  (running at start, per-sector progress capped at 90, completed at 100) and MUST
  mark the run `failed` with the error message on unhandled exceptions and stop
  gracefully (returning to idle) on cancellation.
- **FR-013**: The worker MUST honor cooperative cancellation (checked per sector
  and pre-flight) and `MAX_RUN_TIMEOUT`, raising on timeout.
- **FR-014**: The worker MUST treat `india` and `us` symmetrically via
  `MARKET_TABLES`/`FUNDAMENTALS_TABLES` lookups; unsupported markets raise.
- **FR-015**: The worker MUST share state with the API ONLY through SQL Server and
  the queue (no direct API calls), and MUST NOT manage schema (query/DML only).

### Key Entities *(include if feature involves data)*

- **Job message** (queue): `{ market, runId, weekNumber?, minMarketCap?,
  maxMarketCap?, sectorIds?, limit?, testSampleOnly?, retryFailedOnly?,
  triggeredBy, timestamp }`, base64-encoded JSON.
- **Stock (universe row)**: `id, symbol, company_name, sector_id, sector_name`
  from `{Indian|US}Stocks` joined to `{Indian|US}Sectors`, optionally filtered by
  `IsTestSample`.
- **Per-stock analysis result** (dict → `{Indian|US}StageAnalysisResults` row):
  `run_id, week_number, symbol, company_name, sector_id, sector_name, market_cap,
  close_price, ma10, ma30, is_stage2, rs_score, rs_1w/2w/3w, rs_delta_1w/2w/3w,
  momentum_score, roc_1w/2w/3w, quadrant, ad_ratio, ad_classification` plus
  post-processed `classification`, `rs_rank`, `weeks_in_stage2`.
- **JobRun** (`JobRuns`): updated in place — `Status`, `Progress`, `Metrics`
  (JSON: `market`, `totalStocks`, `filteredStocks`, `stage2Count`,
  `processedStocks`, `priceDataCount`, `skippedStocks`, min/max cap, plus
  `resultCount`, `newCount`, `reentryCount`, `continuingCount`, `removedCount`),
  `ErrorMessage`, `StartedAt`, `CompletedAt`.
- **Benchmark series**: weekly Close of `^NSEI` (India) / `^GSPC` (US), fetched
  once per run, optionally point-in-time.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The documented Stage 2 rule and every per-stock metric in
  `contracts/algorithm.md` match `stage_analysis.calculate_stage2` exactly
  (formulas, thresholds, window sizes).
- **SC-002**: For a given week, each `(WeekNumber, Symbol)` has exactly one result
  row regardless of how many runs/retries touched that week (upsert invariant).
- **SC-003**: Classification, RS rank, and weeks-in-stage2 are computed over the
  full week snapshot, so a retry run does not corrupt values for symbols from a
  prior run of the same week.
- **SC-004**: A cancelled run stops at the next sector boundary and leaves the run
  `cancelled`, not `failed`; a timed-out or errored run is marked `failed` with a
  message, and the listener keeps running.
- **SC-005**: The worker performs zero schema changes and no direct API calls
  (DB + queue only).

## Assumptions

- A seeded SQL Server (sectors, stocks, `IsTestSample` flags) and a reachable
  Azure Storage Queue (Azurite locally via `UseDevelopmentStorage=true`) exist.
- yfinance is reachable and its weekly bars/benchmark symbols
  (`.NS` suffix for India, `^NSEI`/`^GSPC`) behave as the code expects.
- ISO week strings use the `YYYY-Www` format produced by the API; the worker's
  `week_exclusive_end` interprets them via `date.fromisocalendar`.
- The React SPA is out of scope; the worker has no UI responsibilities.
