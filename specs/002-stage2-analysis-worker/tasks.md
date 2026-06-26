---
description: "Validation/documentation tasks for the backtracked Stage 2 Analysis Worker spec"
---

# Tasks: Stage 2 Analysis — Worker (Backtracked)

**Input**: Design documents from `specs/002-stage2-analysis-worker/`

**Prerequisites**: `plan.md`, `spec.md`, `contracts/algorithm.md`

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (independent files/areas)
- **[Story]**: Which user story (US1–US5) the task validates

## Phase 1: Setup & Baseline

- [x] T001 Confirm the worker imports/run cleanly: `python -c "import app"` (or run
  `app.py`) against a configured environment, and confirm `GET /health` returns
  healthy and `GET /status` returns the listener status (FR-001).
- [x] T002 [P] Confirm `test_e2e_worker.py` is the baseline pipeline test and review
  its single-sector flow (connect → get_stocks → fetch → calculate_stage2 →
  save_single_result).
- [x] T003 [P] Confirm `config.py` values match `contracts/algorithm.md`
  (queue name, delays, retries, timeouts, SQL driver).

## Phase 2: Job Lifecycle & Orchestration (US1)

- [x] T004 [US1] Validate `process_message`: base64/plain JSON decode, field
  extraction, idempotent skip of completed/cancelled runs, and `weekNumber`
  resolution from `JobRuns` (FR-003/FR-004).
- [x] T005 [US1] Validate point-in-time logic: `week_exclusive_end` + the
  past-vs-current-week branch bound fetches correctly; benchmark fetched once with
  the same window (FR-005, FR-007).
- [x] T006 [US1] Validate the per-sector / per-stock loop in `worker.py`: market-cap
  fetch + persist, market-cap filter, price fetch, analyze, immediate upsert,
  per-sector progress (capped at 90) and metrics updates (FR-006, FR-010, FR-012).

## Phase 3: Stage 2 Algorithm (US2)

- [x] T007 [US2] Validate MA10/MA30 and `sma30_rising` against
  `contracts/algorithm.md` and `stage_analysis.calculate_stage2`.
- [x] T008 [P] [US2] Validate RS: `rs_line = Close/Benchmark`, SMA52-based
  `rs_score`, `rs_1w/2w/3w` and `rs_delta_1w/2w/3w`, including the ≥52 and ≥5
  length guards (FR-008).
- [x] T009 [P] [US2] Validate momentum (`roc_1w/2w/3w`, weighted
  `momentum_score`) and `quadrant` sign table (FR-008).
- [x] T010 [P] [US2] Validate accumulation/distribution over the last 10 bars
  (`ad_ratio`, thresholds → `ad_classification`) (FR-008).
- [x] T011 [US2] Validate the exact `is_stage2` boolean expression and the
  `< 30 bars → None` skip (FR-008/FR-009, SC-001).

## Phase 4: Week-Level Post-Processing (US3)

- [x] T012 [US3] Validate `classify_stocks` truth table
  (continuing/new/reentry/removed) against previous/ever Stage 2 sets from `db.py`
  (FR-011).
- [x] T013 [P] [US3] Validate `compute_rs_ranks` percentile mapping (0–99, single
  → 99, unscored → None) (FR-011).
- [x] T014 [P] [US3] Validate `get_consecutive_stage2_weeks` streak counting and
  the persisted `WeeksInStage2 = prior + 1` for Stage 2 stocks (FR-011).
- [x] T015 [US3] Confirm post-processing reads the FULL week snapshot
  (`get_week_results`) so earlier-run symbols for the same week are included
  (SC-003).

## Phase 5: Resilience & Retry Modes (US4, US5)

- [x] T016 [P] [US4] Validate retry/backoff in `_fetch_single_price_data`,
  `_fetch_single_market_cap`, `fetch_price_data`, `fetch_benchmark_data`, and
  inter-stock/inter-sector throttling (FR-007).
- [x] T017 [P] [US4] Validate cooperative cancellation (`_check_cancelled` per
  sector) → graceful stop leaving `cancelled`, and `MAX_RUN_TIMEOUT` → `failed`
  (FR-013, SC-004).
- [x] T018 [P] [US4] Validate the failure path: unhandled exception → run marked
  `failed` with message; listener keeps running and reconnects on Azure errors
  (FR-002, FR-012).
- [x] T019 [P] [US5] Validate `retryFailedOnly` (exclude completed-for-week symbols)
  and `testSampleOnly` (`IsTestSample=1`), plus `sectorIds`/`limit` filtering in
  `get_stocks` (FR-006).

## Phase 6: Persistence & Data Model Sync

- [x] T020 [P] Validate `save_single_result` MERGE keys on `(WeekNumber, Symbol)`,
  stamps latest `RunId`, and `_clean` maps NaN/inf → NULL (FR-010, SC-002).
- [x] T021 [P] Diff the worker result dict + MERGE columns against
  `src/MarketEdge.Database/Tables/StageAnalysisResults.sql` /
  `USStageAnalysisResults.sql` (column names, nullability, CHECK enums for
  Classification/Quadrant/ADClassification).

## Phase 7: End-to-End Verification (optional, requires environment)

- [x] T022 Run `python test_e2e_worker.py --market india` against a seeded local DB
  with internet access; confirm rows are written and the documented fields are
  populated for at least one Stage 2 and one non-Stage 2 stock (SC-001..SC-002).

## Phase 8: Migrate to Ingested Base Data (US1) — base-data source change

> Implements the data-source change in `spec.md` / `contracts/algorithm.md`: replace
> live yfinance fetching with reads from the ingested tables (depends on
> `specs/005-data-ingestion/`). Validation tasks above documented the prior
> fetch-based behavior; these tasks change it.

- [ ] T023 [US1] Add `db.py` readers for base data: `get_daily_bars(market, symbol,
  end_exclusive=None)` and `get_benchmark_daily_bars(market, end_exclusive=None)`
  selecting from `{Indian|US}Bars1D`, and `get_market_cap(market, symbol, as_of)`
  selecting the latest `{Indian|US}TickerTechnical` row at/before the week end
  (FR-005, FR-007).
- [ ] T024 [US1] In `stage_analysis.py`, replace the yfinance fetch functions with a
  daily→weekly resample (W-FRI) of the rows from T023, trimmed to the last 60 weekly
  bars, preserving point-in-time bounding via `week_exclusive_end` (FR-005, FR-007).
- [ ] T025 [US1] Remove the per-stock `fast_info` market-cap fetch and source market
  cap from `get_market_cap`; keep the `min/max` cap filter behavior and the optional
  mirror into `{Indian|US}StockFundamentals` (FR-010).
- [ ] T026 [US4] Update resilience: drop yfinance retry/backoff + rate-limit
  throttling; on missing/insufficient ingested bars skip-and-count the symbol; retry
  only transient SQL read errors (updated US4 scenarios 1–2).
- [ ] T027 Retire `YFINANCE_*` settings from `config.py` and remove `yfinance` from
  the worker's dependencies; confirm `import app` still succeeds (FR-001).
- [ ] T028 Update `test_e2e_worker.py` to seed a small ingested-bars fixture (incl.
  the benchmark) and run the pipeline with **zero** network calls; assert at least one
  Stage 2 and one non-Stage 2 stock are produced (SC-001..SC-003 of `005`).



- T001 precedes all validation tasks.
- Phase 3 (algorithm) before Phase 4 (post-processing depends on per-stock fields).
- T022 (E2E) depends on Phases 2–6.
- Phase 8 (T023–T028) depends on `specs/005-data-ingestion/` (base-data tables
  populated) and supersedes the fetch behavior validated in T005/T006/T016/T022.

## Out of Scope (do NOT create tasks for)

- The React SPA under `src/MarketEdge.Api/clientapp/`.
- Any new schema, DDL, or EF migrations.
- New analysis features beyond documenting the current algorithm.
