# Algorithm & Data Contracts: Stage 2 Worker (Backtracked)

Source of truth: `src/MarketEdge.Worker/stage_analysis.py`, `worker.py`, `db.py`.
This documents the EXISTING computation exactly. **UI is out of scope.**

> **Base-data change**: price/benchmark inputs are now **read from the ingested
> SQL Server tables** (`{Indian|US}Bars1D`, `{Indian|US}TickerTechnical`) instead of
> fetched live from yfinance — see `specs/005-data-ingestion/`. The formulas below are
> unchanged; only the source of the input frames moved from the network to SQL.

## Inputs

- **Weekly price frame** per stock: read daily OHLCV from `{Indian|US}Bars1D` and
  resample to weekly (W-FRI), bounded to the week-exclusive end for point-in-time
  (past weeks) or all bars through today (current week), trimmed to the last 60
  non-empty weekly bars. Columns are already normalized at ingestion time.
- **Benchmark frame**: weekly Close derived from the ingested daily bars of `^NSEI`
  (India) or `^GSPC` (US), read once per run (same windowing/point-in-time rules).
- **Market cap**: read from the latest `{Indian|US}TickerTechnical` row at/before the
  week end (used for `min/max` cap filtering); no live `fast_info` call.
- **Minimum history**: `calculate_stage2` returns `None` if `< 30` weekly bars.

## Per-stock metrics (`calculate_stage2`)

Let `close` = weekly Close series (sorted ascending), `open` = Open (falls back to
Close), `volume` = Volume (0 if absent).

| Field | Formula |
|-------|---------|
| `close_price` | `close[-1]` |
| `ma10` | last value of `close.rolling(10).mean()` (None if NaN) |
| `ma30` | last value of `close.rolling(30).mean()` (None if NaN) |
| `sma30_rising` | `ma30[-1] > ma30[-5]` (needs ≥5 non-NaN MA30 values) |

### Relative strength (Mansfield-style)

- Align `close` and `benchmark_close` on a shared index (inner join, dedup index).
- `rs_line = aligned_close / aligned_benchmark`.
- If `len(rs_line) >= 52`: `rs_line_sma52 = rs_line.rolling(52).mean()`, and
  `rs_score = ((rs_line[-1] / rs_line_sma52[-1]) - 1) * 100` (None if sma52 is
  NaN/0).
- `rs_1w = rs_line[-2]`, `rs_2w = rs_line[-3]`, `rs_3w = rs_line[-4]` (raw RS-line
  values N weeks ago, guarded by length).
- `rs_delta_Nw = ((rs_line[-1] / rs_Nw) - 1) * 100` for N ∈ {1,2,3} (None if the
  past value is 0).
- Requires `len(aligned_close) >= 5` for any RS output.

### Momentum (rate of change)

- `roc_1w = (close[-1]/close[-2] - 1) * 100`
- `roc_2w = (close[-1]/close[-3] - 1) * 100`
- `roc_3w = (close[-1]/close[-4] - 1) * 100`
- `momentum_score = 0.4*roc_1w + 0.3*roc_2w + 0.3*roc_3w` (only if all three set).

### Rotation quadrant (from RSScore X, RSDelta2w Y)

| `rs_score` | `rs_delta_2w` | `quadrant` |
|------------|---------------|------------|
| > 0 | > 0 | `leading` |
| > 0 | ≤ 0 | `weakening` |
| ≤ 0 | ≤ 0 | `lagging` |
| ≤ 0 | > 0 | `improving` |

(Only set when both `rs_score` and `rs_delta_2w` are present.)

### Accumulation / distribution (last 10 weekly bars)

- `acc_vol` = Σ volume where Close > Open; `dist_vol` = Σ volume where Close < Open.
- `ad_ratio = acc_vol / (acc_vol + dist_vol)`, or `0.5` if no up/down volume.
- `ad_classification` = `accumulating` (`ad_ratio > 0.6`) / `distributing`
  (`ad_ratio < 0.4`) / `neutral` otherwise.

### Stage 2 decision

```
is_stage2 = (ma30 is not None) and (ma10 is not None)
            and close[-1] > ma30
            and sma30_rising
            and close[-1] > ma10
            and ma10 > ma30
            and rs_score is not None and rs_score > 0
```

## Week-level post-processing

### Classification (`classify_stocks`)

Inputs: `current` (Stage 2 this week), `previous` (Stage 2 in the most recent prior
week, `get_previous_stage2_symbols`), `ever` (any prior week,
`get_ever_stage2_symbols`).

- `symbol ∈ current ∧ symbol ∈ previous` → `continuing`
- `symbol ∈ current ∧ symbol ∉ ever` → `new`
- `symbol ∈ current ∧ symbol ∈ ever ∧ symbol ∉ previous` → `reentry`
- `symbol ∈ previous ∧ symbol ∉ current` → `removed`

### RS rank (`compute_rs_ranks`)

- Over stocks with non-null `rs_score`: percentile rank
  `round((rank(method="average") - 1) / (n - 1) * 99)` → integer `0..99`.
- Single scored stock → `99`. Unscored stocks → `rs_rank = None`.

### Weeks in Stage 2 (`get_consecutive_stage2_weeks`)

- For each current Stage 2 symbol, count consecutive prior weeks (newest→oldest)
  where it was Stage 2, stopping at the first non-Stage 2 week it appears in.
- Persisted value = `consecutive_prior + 1` for Stage 2 stocks; `0` otherwise.

All three are computed over the FULL week snapshot read back via
`get_week_results`, then written across the week's rows.

## Persistence (`db.py`)

- **Upsert** `save_single_result`: `MERGE` on `(WeekNumber, Symbol)` into
  `IndianStageAnalysisResults` / `USStageAnalysisResults`; updates stamp the latest
  `RunId`. NaN/inf → `NULL` via `_clean`.
- **Market cap** `update_market_cap`: `MERGE` on `StockId` into
  `{Indian|US}StockFundamentals`.
- **Status** `update_job_status`: updates `JobRuns` (`Status`, `Progress`,
  `Metrics` JSON, `ErrorMessage`, `StartedAt`, `CompletedAt`).
- Helpers: `get_week_number`, `get_completed_symbols_for_week`, `get_week_results`,
  `get_previous_stage2_symbols`, `get_ever_stage2_symbols`,
  `get_consecutive_stage2_weeks`.

## Market mapping

| market | stocks / sectors / results | benchmark | ingested base-data tables | fundamentals |
|--------|----------------------------|-----------|---------------------------|--------------|
| india | IndianStocks / IndianSectors / IndianStageAnalysisResults | `^NSEI` | IndianBars1D / IndianTickerTechnical | IndianStockFundamentals |
| us | USStocks / USSectors / USStageAnalysisResults | `^GSPC` | USBars1D / USTickerTechnical | USStockFundamentals |

(The `.NS` suffix for India and the benchmark symbols are applied by the ingestion
pipeline, `specs/005-data-ingestion/`, when populating the base-data tables.)

## Configuration (`config.py`)

`QUEUE_NAME=stage-analysis-jobs`, `QUEUE_POLL_INTERVAL=10s`,
`MAX_RUN_TIMEOUT=14400s`, `QUEUE_VISIBILITY_TIMEOUT=18000s`. SQL via ODBC Driver 17,
Windows Auth + `TrustServerCertificate`. The former `YFINANCE_*` fetch settings are
retired from the worker now that base data is read from SQL; yfinance fetching config
lives in the ingestion pipeline instead.

## Worker HTTP surface (`app.py`)

| Method | Route | Returns |
|--------|-------|---------|
| GET | `/health` | `{ status: "healthy", service: "MarketEdge.Worker" }` |
| GET | `/status` | background listener status dict |
