# Feature 009 — Workflow steps: RS Rating from ingested data

## Summary
Stage 2 analysis is being decomposed into discrete, independently-runnable **workflow steps** so
they can be reused ("pick and choose") across the system rather than being locked inside the
monolithic stage-2 run. This feature introduces the first such reusable step:

**Compute RS Ratings** — derive IBD-style relative-strength ratings (1–99) for every symbol from the
**already-ingested daily bars** (`{Market}Bars1D`) and persist them onto the latest
`{Market}TickerTechnical` row (`Rs`, `Rs1d`, `Rs1w`, `Rs1m`, `Rs3m`, `Rs6m`, `RsType`, `RsDate`).

The step is callable on its own (worker HTTP endpoint) and is also invoked automatically **during a
Stage 2 analysis run**. It does not call yfinance — it reads only ingested data, so it is fast and
deterministic.

## Motivation
- `{Market}TickerTechnical.Rs*` columns are left `NULL` by ingestion; the Stock Lookup page shows
  blank RS values.
- Stage 2 already computes a *Mansfield* `rs_score` per stock and a within-run `RSRank`, but that is
  computed live from yfinance and stored only on the stage-analysis results — it is not the same as
  the persisted, lookup-facing RS rating, and it isn't reusable elsewhere.
- We want analysis broken into composable steps; RS-from-ingested-data is the first extracted step.

## Definitions
- **Base RS score (IBD-style)**: a weighted trailing price-performance score over four 63-day
  quarters, most-recent quarter double-weighted: `raw = (2*Q1 + Q2 + Q3 + Q4) / 5`, where `Qk` is
  the simple return of quarter `k` ending at the as-of bar. Weights are **renormalised over
  whatever quarters have enough history** — the ingested window is ~1 year, so older snapshots
  naturally use fewer quarters.
- **RS Rating**: the percentile rank (1–99) of the base RS score across the evaluated universe at a
  given as-of point. Higher = stronger.
- **`Rs*` columns are the same rating snapshotted back through time** (an RS history), **not**
  different return horizons:
  - `Rs` — rating as of the latest bar
  - `Rs1d` — 1 trading day ago · `Rs1w` — 5 · `Rs1m` — 21 · `Rs3m` — 63 · `Rs6m` — 126 days ago

  Each snapshot recomputes the base RS score as-of that bar and re-ranks the universe at that
  point, so the values drift smoothly (e.g. 93 / 93 / 96 / 78 / 84) rather than scattering like
  raw single-horizon returns.

## User stories

### US-1 — Persisted RS from ingested data
As the system, after bars are ingested I can compute RS ratings for the whole market from the stored
bars and write them to `{Market}TickerTechnical`, so the Stock Lookup page shows real RS values.

### US-2 — Reusable step
As an operator/automation, I can run the RS step on its own (independent of a full Stage 2 run) via a
worker endpoint, scoped to the full universe or the test sample.

### US-3 — Runs inside Stage 2
As an operator, when I trigger a Stage 2 analysis run, the RS step runs as part of it so RS ratings
are refreshed alongside the stage-2 results.

## Functional requirements
- **FR-001** A pure step `compute_rs_ratings(conn, market, test_sample_only=False, symbols=None)`
  reads `{Market}Bars1D`, computes the IBD-style base RS score at six as-of points
  (now, 1d/1w/1m/3m/6m ago), percentile-ranks each (1–99) across the evaluated universe, and
  upserts the ratings onto each symbol's **latest** `{Market}TickerTechnical` row (insert a row at
  the bars as-of date if none exists). It returns a summary `{market, evaluated, updated, as_of}`.
- **FR-002** Quarter weights are renormalised over whatever quarters have data; a snapshot with not
  even one full quarter of prior history gets `NULL` for that column. Under the ~1-year bar cap,
  older snapshots (`Rs3m`, `Rs6m`) use fewer quarters than `Rs`.
- **FR-003** `RsType` is set to `'Full'` and `RsDate` to the as-of (max bar) date.
- **FR-004** The step makes **no network calls** — ingested bars only.
- **FR-005** Stage 2 `process_message` invokes the step once per run, over the run's universe scope
  (respecting `test_sample_only`), before the per-stock loop; a failure in the step is logged and
  does not abort the Stage 2 run.
- **FR-006** A worker endpoint `POST /steps/compute-rs` accepts `{ "market", "testSampleOnly"? }`,
  runs the step synchronously, and returns the summary. Invalid market → 400.
- **FR-007** The step is idempotent: re-running it overwrites the same latest-row RS columns.

## Non-functional / constraints
- **NFR-001** No schema changes; the `Rs*` columns already exist (SQL project owns schema).
- **NFR-002** India/US symmetric.
- **NFR-003** Logging via the existing OpenTelemetry sinks.

## Out of scope
- Replacing the Stage 2 Mansfield `rs_score` / `RSRank` logic (kept as-is).
- Moving the rest of Stage 2's yfinance price fetch onto ingested bars.
- A general step-orchestration engine / UI for arbitrary step ordering (future work; this feature
  just makes the RS step independently callable and wires it into Stage 2).

## Workflow steps inventory (current system)
The discrete steps that exist (or are added here) and can be reasoned about independently:

**Data Ingestion (`MarketEdge.Ingestion`)**
1. Ingest Bars — seed ticker universe + daily OHLCV (rolling 1 year) → `{Market}Bars1D`, `{Market}Tickers`.
2. Ingest Technical — latest snapshot (close, day %, open/high/low, 52w high, market cap) → `{Market}TickerTechnical`.
3. Ingest Fundamentals — analyst snapshot + EPS forecasts + market cap → `{Market}AnalystSnapshot`, `{Market}EpsForecasts`.

**Analysis (`MarketEdge.Worker`)**
4. **Compute RS Ratings (NEW)** — RS 1–99 from ingested bars → `{Market}TickerTechnical.Rs*`.
5. Fetch benchmark + per-stock price (yfinance, point-in-time aware).
6. Calculate Stage 2 — MA10/MA30 trend, Mansfield `rs_score`, momentum/ROC, accumulation/distribution.
7. Classify stocks — new / continuing / reentry / removed.
8. Compute RS Ranks — within-week percentile of `rs_score` → `RSRank`.
9. Compute Weeks-in-Stage-2 — consecutive-week streak.
10. Sector rotation aggregation — sector-level rotation history (served by the API).
