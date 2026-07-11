# Feature 013: Market Regime

## Summary

Add a **market regime** signal for the two markets (India/NSE and US) that answers one
question before an operator reviews scanner results: *is the broad market supportive enough
to take breakout/setup risk aggressively, selectively, defensively, or not at all?* It is a
**context signal**, not a trade-execution signal, and never replaces scanner-specific entry
criteria.

The regime is derived from **two independent signals**, computed **separately per market**
(US and NSE universes never mix):

1. **Benchmark condition** — a fast read of the primary index's trend + volume state,
   labelled by the priority rules in [§ Benchmark condition](#benchmark-condition-signal).
2. **Breadth composite** — a participation health score: how much of the exchange universe
   is above key moving averages, plus benchmark performance and volatility, scored by the
   rules in [§ Breadth composite](#breadth-composite).

The two are then combined into an **effective regime + trading posture**
([§ Regime interpretation](#regime-interpretation)). Both are surfaced in the UI with the
benchmark symbol actually used, an as-of date, and an explicit unavailable/stale state.

This feature is modelled on the user-supplied *Market Regime Specification v1.0*, reconciled
to MarketEdge's existing names and the project constitution. Where the source spec references
subsystems that do not exist in this codebase (CANSLIM rows, industry ranks), those
"leadership context" inputs are **out of scope**; the regime is computed from the two core
signals only. Regime-driven scanner **filtering/re-prioritization** is likewise deferred — the
signal is *exposed for consumption*, but no scanner results are hidden or reordered by regime
(the source spec itself requires any such filter to be explicit, configurable, and visible).

## Reconciled naming (source spec → this codebase)

| Source spec name            | This codebase                                                        |
| --------------------------- | -------------------------------------------------------------------- |
| `MarketConditionService`    | worker `compute_condition` (§3.1), persisted into the regime snapshot |
| `MarketBreadthService`      | worker `compute_breadth` (§3.2), persisted into the regime snapshot   |
| `market_breadth_snapshots`  | `IndianRegimeSnapshots` / `USRegimeSnapshots` (parallel tables; full computed regime) |
| (implied regime combiner)   | worker `combine_regime` (§4); API `MarketRegimeService` + `RegimeController` (`/api/{market}/regime`) is a thin reader |
| benchmark bars (unstored)   | `IndianBenchmarkBars1D` / `USBenchmarkBars1D` (index + volatility)   |
| nightly regime compute      | worker job type `market_regime` + `market_regime_runner.py`          |

## Covered markets

| Market | Key     | Primary benchmark | Volatility proxy |
| ------ | ------- | ----------------- | ---------------- |
| US     | `us`    | `^GSPC`           | `^VIX`           |
| India  | `india` | `^NSEI`           | `^INDIAVIX`      |

`^GSPC` / `^NSEI` reuse the existing `BENCHMARKS` map in `MarketEdge.Ingestion/fetch.py`.
Volatility symbols are new. US/NSE breadth, RS, scanners, and regime scores MUST never mix
the two universes (Constitution: India/US symmetry, selected by table family per market key).

## Scope

### In scope

- Persisting benchmark index + volatility daily bars per market (new small ingestion).
- A nightly, on-demand `market_regime` worker job that refreshes those bars and computes the
  **entire regime** — benchmark condition (§3.1), breadth composite (§3.2), and combined
  effective regime (§4) — persisting one fully-rendered snapshot per as-of date.
- A thin API that reads the latest persisted regime snapshot into a DTO (no business logic;
  only the read-time `stale` flag is derived API-side) and orchestrates refresh/schedule.
- REST endpoints under `/api/{market}/regime`.
- SPA: a regime card on the market menu, a dedicated `/:market/regime` page, and a
  display-only regime banner on the Scanners page.
- Freshness/staleness handling and unavailable states.
- Unit tests (worker: participation + benchmark context math, condition labels, breadth
  score bands, and the regime combination matrix).

### Out of scope / deferred

- Regime-driven scanner filtering or re-prioritization (source spec §7). The GET endpoint
  exposes the regime so scanner UIs can *display or consume* it; nothing is hidden/reordered.
- CANSLIM / industry-rank "leadership context" inputs (source spec §4–§6) — not in codebase.
- A global, single-label header badge. Market-specific screens show their own regime; a
  compact global badge may be added later.
- Any regime-driven changes to the Stage-2 or scoring pipelines.

## Data model (SQL project — no EF migrations)

All schema lives as `.sql` files under `src/MarketEdge.Database/Tables/` and ships via the
dacpac (Constitution I). New EF entities are query-only mappings (Constitution II).

### `Indian`/`US``BenchmarkBars1D`

Daily OHLCV for **index-class** symbols (the benchmark and its volatility proxy) — kept out
of `{Market}Bars1D` because those rows are FK-bound to the stock universe (`{Market}Tickers`)
and breadth counts *stocks*. Parallel tables per market, matching `{Market}Bars1D` shape.

```
[Symbol]   NVARCHAR(30)  NOT NULL,   -- e.g. '^GSPC', '^VIX', '^NSEI', '^INDIAVIX'
[BarDate]  DATE          NOT NULL,
[Open]     DECIMAL(18,4) NULL,
[High]     DECIMAL(18,4) NULL,
[Low]      DECIMAL(18,4) NULL,
[Close]    DECIMAL(18,4) NULL,
[Volume]   BIGINT        NULL,
[AdjClose] DECIMAL(18,4) NULL,
PRIMARY KEY CLUSTERED ([Symbol], [BarDate]),
INDEX IX_..._BarDate NONCLUSTERED ([BarDate])
```

### `Indian`/`US``RegimeSnapshots`

One row per as-of date per market, holding the **fully-computed regime** the worker renders:
the effective regime + posture (§4), the benchmark condition (§3.1), the breadth composite
(§3.2) with its per-signal breakdown (`SignalsJson`), plus the raw participation/context facts
for transparency. The API reads this row directly — all thresholds and rules live in the worker.

```
[AsOfDate]                 DATE          NOT NULL,   -- PK; latest available market data date
[ConditionAsOfDate]        DATE          NULL,
[BreadthAsOfDate]          DATE          NULL,
[BenchmarkSymbol]          NVARCHAR(30)  NULL,
[VolatilitySymbol]         NVARCHAR(30)  NULL,
[EvaluatedCount]           INT           NOT NULL DEFAULT 0,  -- valid stocks with enough history
-- effective regime (§4)
[Regime]                   NVARCHAR(30)  NOT NULL,   -- RiskOn | SelectiveRiskOn | Caution | RiskOff | Mixed | Unavailable
[RegimeLabel]              NVARCHAR(60)  NOT NULL,
[RegimeTone]               NVARCHAR(10)  NOT NULL,
[Posture]                  NVARCHAR(400) NULL,
[Available]                BIT           NOT NULL DEFAULT 0,
-- benchmark condition (§3.1)
[ConditionLabel]           NVARCHAR(30)  NOT NULL,
[ConditionTone]            NVARCHAR(10)  NOT NULL,
[ConditionExplanation]     NVARCHAR(400) NULL,
[ConditionAvailable]       BIT           NOT NULL DEFAULT 0,
[ConditionClose]           DECIMAL(18,4) NULL,
[ConditionSma20]           DECIMAL(18,4) NULL,
[ConditionSma50]           DECIMAL(18,4) NULL,
[ConditionSma200]          DECIMAL(18,4) NULL,
[ConditionCloseVsSma20Pct] DECIMAL(10,4) NULL,
[ConditionCloseVsSma50Pct] DECIMAL(10,4) NULL,
[ConditionCloseVsSma200Pct] DECIMAL(10,4) NULL,
[ConditionVolumeVsAvgPct]  DECIMAL(10,4) NULL,
-- breadth composite (§3.2)
[BreadthLabel]             NVARCHAR(30)  NOT NULL,   -- Bullish | Positive | Neutral | Negative | Bearish | Unavailable
[BreadthTone]              NVARCHAR(10)  NOT NULL,
[BreadthScore]             INT           NULL,       -- 0..100 (percent of positive among available)
[BreadthPositiveSignals]   INT           NOT NULL DEFAULT 0,
[BreadthAvailableSignals]  INT           NOT NULL DEFAULT 0,
[BreadthAvailable]         BIT           NOT NULL DEFAULT 0,
[SignalsJson]              NVARCHAR(MAX) NULL,        -- per-signal breakdown for the UI
-- raw participation facts (percent 0..100, NULL when uncomputable)
[PctAboveSma10]            DECIMAL(6,2)  NULL,
[PctAboveSma20]            DECIMAL(6,2)  NULL,
[PctAboveSma50]            DECIMAL(6,2)  NULL,
[PctAboveSma200]           DECIMAL(6,2)  NULL,
[PctSma20AboveSma50]       DECIMAL(6,2)  NULL,
[PctSma50AboveSma200]      DECIMAL(6,2)  NULL,
-- benchmark / volatility context
[BenchmarkYtdPct]          DECIMAL(10,4) NULL,
[Benchmark1wPct]           DECIMAL(10,4) NULL,
[Benchmark1mPct]           DECIMAL(10,4) NULL,
[Benchmark1yPct]           DECIMAL(10,4) NULL,
[BenchmarkPctFrom52wHigh]  DECIMAL(10,4) NULL,        -- distance from 52w high (negative below)
[VolatilityClose]          DECIMAL(10,4) NULL,
[CreatedAt]                DATETIME2     NOT NULL DEFAULT GETUTCDATE(),
PRIMARY KEY CLUSTERED ([AsOfDate])
```

### `RegimeSchedules` (market-keyed singleton)

Mirrors `Stage2Schedules`/`FundamentalsSchedules` (single row per market, CHECK on Market),
driving the nightly post-close refresh.

```
[Market]         NVARCHAR(10) NOT NULL PRIMARY KEY,
[Enabled]        BIT          NOT NULL DEFAULT 1,
[HourLocal]      INT          NOT NULL DEFAULT 20,   -- exchange-local hour to refresh
[LastEnqueuedAt] DATETIME2    NULL,
[UpdatedAt]      DATETIME2    NOT NULL DEFAULT GETUTCDATE(),
CHECK ([Market] IN ('india','us'))
```

## <a name="benchmark-condition-signal"></a>Benchmark condition signal (§3.1)

Computed in the worker (`compute_condition`) from the stored benchmark index bars (loaded from
`{Market}BenchmarkBars1D`). For each market, resolve the first
benchmark with enough history (candidate order US: `^GSPC`; NSE: `^NSEI`). Build a series as of
the latest stored bar and compute Close, SMA20, SMA50, SMA200, current volume, 50-day average
volume, and the close/volume distances. The resulting label + facts are persisted into the
regime snapshot; the API just reads them back.

When the job runs **during market hours** (NSE 09:15–15:30 IST, NYSE 09:30–16:00 ET, weekdays —
see the worker's `market_hours.py`), stage 1 overlays the **live intraday** last price of the
benchmark and volatility indices onto today's provisional bar before persisting, so the condition
and benchmark-returns context reflect the live index level. Breadth continues to use the latest
stored universe closes (§3.2). Today's partial intraday volume is left untouched so the
volume-confirmation rule never treats an in-progress session as a full day. Snapshots produced
this way set `IsIntraday = 1`; the nightly EOD run overwrites the row with final closes and
`IsIntraday = 0`.

Label in **priority order** (first match wins):

| Label        | Rule                                                                                   | Tone   |
| ------------ | -------------------------------------------------------------------------------------- | ------ |
| `Pessimistic`| Close ≥ 10% below SMA200                                                                | red    |
| `Bearish`    | Close < SMA50                                                                           | red    |
| `Cautious`   | Closed below SMA20 for two sessions, or has not volume-confirmed a recovery from recent caution | yellow |
| `Euphoric`   | Close > 10% above SMA20                                                                 | green  |
| `Uptrend`    | Close > SMA20 and (not recently cautious OR volume > 50-day average)                    | green  |
| `Neutral`    | No stronger rule active                                                                 | grey   |

If no benchmark resolves, the condition is **unavailable** (rendered neutral/grey, not
inferred from breadth).

## <a name="breadth-composite"></a>Breadth composite (§3.2)

The **worker** iterates each active stock in the market's universe, builds a recent series
from `{Market}Bars1D`, and counts participation. The same worker job (`compute_breadth`) then
turns those facts plus the benchmark returns/volatility into the composite score/label and
persists it into the regime snapshot. A `NULL` signal is excluded from the denominator (§8)
rather than counted as negative.

Positive-signal thresholds:

| Signal                     | Positive when |
| -------------------------- | ------------- |
| Percent above SMA10        | > 60%         |
| Percent above SMA20        | > 60%         |
| Percent above SMA50        | > 60%         |
| Percent above SMA200       | > 60%         |
| Percent SMA20 above SMA50  | > 60%         |
| Percent SMA50 above SMA200 | > 60%         |
| Benchmark YTD return       | > 5%          |
| Benchmark 1-week return     | > 1%          |
| Benchmark 1-month return    | > 3%          |
| Benchmark 1-year return     | > 15%         |
| Benchmark distance from 52w high | > -5%   |
| Volatility close           | < 15          |

**Score** = percentage of positive signals among **available** signals (a `NULL` signal is
excluded from the denominator — e.g. missing volatility drops that signal, per §8, rather
than failing the snapshot). **Label**:

| Score       | Label      |
| ----------- | ---------- |
| > 80        | `Bullish`  |
| > 60, ≤ 80  | `Positive` |
| > 40, ≤ 60  | `Neutral`  |
| > 20, ≤ 40  | `Negative` |
| ≤ 20        | `Bearish`  |

## <a name="regime-interpretation"></a>Regime interpretation (§4)

The worker (`combine_regime`) combines the condition label and breadth label into an
**effective regime** and **posture** string, persisted into the regime snapshot:

| Effective regime    | Benchmark condition                     | Breadth composite            |
| ------------------- | --------------------------------------- | ---------------------------- |
| `RiskOn`            | Uptrend or constructive Euphoric        | Positive or Bullish          |
| `SelectiveRiskOn`   | Uptrend, Neutral, or improving Cautious | Neutral or better            |
| `Caution`           | Cautious or Bearish                     | Neutral, Negative            |
| `RiskOff`           | Bearish or Pessimistic                  | Negative or Bearish          |
| `Mixed`             | condition and breadth disagree materially | contradictory combination  |

`Euphoric` is **not** automatically bearish — in a breadth-confirmed uptrend it stays
risk-on; the posture text notes extension/pullback risk. When either component is unavailable
the regime is reported as `Unavailable` (never inferred from the single present signal).

## Data freshness (§8)

Every regime response carries: `market`, `asOfDate`, `benchmarkSymbol`, condition label,
breadth score + label, an `available` flag, an `isIntraday` flag, and a `stale` flag/warning
when benchmark, volatility, or breadth data is missing or older than the latest trading date.
Missing one component degrades only that component (condition unavailable ⇒ breadth still shown,
and vice versa); it never fabricates a bullish/bearish label.

`isIntraday` is `true` when the snapshot was computed during market hours with a live index
price overlaid (see §3.1). Intraday, the effective `asOfDate` is the **freshest** component date
(`max` of the condition and breadth as-of dates), so a live condition (today) can lead an
EOD-only breadth date (yesterday); that lag is surfaced through the existing `stale` reason. The
SPA renders a pulsing **LIVE** badge whenever `isIntraday` is set.

## Worker (Constitution III — heavy compute off the API)

New job type `market_regime` dispatched in `worker.py::process_message`, handled by
`market_regime_runner.run_market_regime_job(payload)` with a `StageTracker` roadmap:

1. `benchmark` — fetch + upsert benchmark + volatility daily bars (~2y window) into
   `{Market}BenchmarkBars1D` (reuses the ingestion fetch helpers). During market hours it also
   fetches each index's live last price and overlays it onto today's provisional bar (§3.1).
2. `breadth` — compute the **whole regime**: load the active universe's `{Market}Bars1D` and
   the persisted benchmark bars, then compute participation, benchmark returns/52w-high
   distance/volatility, the benchmark condition (§3.1), the breadth composite (§3.2), and the
   combined effective regime (§4).
3. `persist` — upsert the `{Market}RegimeSnapshots` row for the as-of date (idempotent per
   `AsOfDate`), including the per-signal `SignalsJson` breakdown.

The job reads only ingested stock bars for participation (no per-stock network calls); the
only network calls are the 1–2 index downloads in stage 1 (plus their live-price lookups when
the market is open).

## API surface

- `GET /api/{market}/regime` → the combined regime payload (condition + breadth + effective
  regime + freshness). `{market}` validated to `india`/`us` (400 otherwise).
- `POST /api/{market}/regime/refresh` → enqueue a `market_regime` job (JobRuns row + queue
  message), returns `{ runId }`. Idempotent against an in-flight regime job for the market.
- `GET /api/{market}/regime/schedule` / `PUT .../schedule` → view/update the nightly schedule
  (mirrors the scanner schedule endpoints).

`MarketRegimeService` is a **thin reader**: `GET` maps the latest persisted regime snapshot
row to the DTO (deserializing `SignalsJson`) and derives only the read-time `stale` flag; it
contains no thresholds, labels, or combination rules. `MarketRegimeScheduleService` (a hosted
`BackgroundService`, mirroring `ScannerScheduleService`) enqueues the nightly post-close
refresh (with dedupe + in-flight guards).

## UI (§10)

- **Market menu (`/:market`)**: a compact regime card — effective regime, condition label +
  one-line explanation, breadth score + label, as-of date, and a clear unavailable/stale
  badge.
- **Dedicated `/:market/regime` page**: full detail — condition (with benchmark symbol used
  and the SMA distances), breadth score/label with the core participation metrics, benchmark
  returns + volatility, as-of date, and a manual "Refresh" trigger.
- **Scanners page**: a display-only regime banner (consumes `GET /api/{market}/regime`),
  showing the effective regime so the operator sees context without any results being hidden
  or reordered.

## Constitution Check

- **I. Schema owned by SQL project** — all four tables are new `.sql` files under
  `Tables/`; no EF migrations. ✅
- **II. EF query-only** — new entities map existing tables via `[Table]`; Controllers →
  Services → DbContext; DTOs cross the boundary. ✅
- **III. Worker owns heavy analysis** — the worker computes the *entire* regime (breadth over
  thousands of stocks, the benchmark condition, and the combination) via the queue + shared DB
  and persists a fully-rendered snapshot; the API only reads that snapshot and derives the
  read-time staleness flag. ✅
- **IV. Jobs idempotent** — the regime snapshot upserts by `AsOfDate`; at most one in-flight
  `market_regime` job per market. (This job is *date-keyed*, not week-keyed — an accepted,
  documented deviation consistent with the daily scanner job, which is also not week-keyed.)
- **V. REST conventions** — market-scoped under `/api/{market}/...`, `{market}` validated,
  reference symbols selected by table family per market key. ✅

## Acceptance criteria (§11)

1. US and NSE regime signals are computed independently (separate universes/benchmarks).
2. US uses US benchmark + US stock universe only; NSE uses NSE benchmark + NSE universe only.
3. Benchmark condition labels follow the §3.1 priority rules.
4. Breadth composite labels follow the §3.2 score bands.
5. Scanner pages can display/consume the market regime without mixing exchanges.
6. Missing data produces an unavailable/stale state instead of a misleading label.
7. Any future scanner filtering based on regime is explicit, visible, and configurable
   (none is added in this feature).
