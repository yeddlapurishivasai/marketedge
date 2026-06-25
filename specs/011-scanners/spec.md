# Feature 011: Technical Stock Scanners

## Summary

Add a **technical stock scanner** subsystem that screens the ingested daily-bar universe for
technical setups, modelled on the reference scanner spec supplied by the user (a Java/ta4j
implementation). Scanners run entirely off data we already ingest — `{Market}Bars1D`
(daily OHLCV) and `{Market}TickerTechnical` (market cap + IBD RS rating) — plus a
just-before-scan refresh of *today's* bar, so they can run any time during market hours.

These are **technical** scanners; results are stored in `{Market}TechnicalScannerResults`.
Fundamental scanners (a separate effort) will get their own result tables, keeping the two
families cleanly isolated.

Each scanner is idempotent per `(ScannerName, Market, ScanDate)`: re-running on the same
day replaces that day's rows. Results are kept per day so the UI can show previous days'
results via a date dropdown.

## Scope

### Scanner families (13 — `EPISODIC_PIVOT` deferred)

Each family has an India (NSE) and a US variant unless noted. Names follow the reference
spec (`US_*` / `NSE_*`).

1. `SETUP` — constructive MA-trend setups, range expansion, strong close.
2. `WEEKLY_SETUP` — weekly constructive setup (NSE in reference; we implement both markets, completed-week resample).
3. `CONTRACTION` — sharp range / inside-bar contraction in an uptrend.
4. `EXTREME_CONTRACTION` — tiny day-over-day move inside a configurable band, above EMA50/200, RS>=70.
5. `BULL_SNORT` — high RVOL gap-up, close>open, above SMA200 (US) / large-cap + turnover (NSE).
6. `HIGH_TIGHT_FLAG` — >=80% run over ~40 bars, near 52w high, liquidity/cap filters.
7. `LOW_TIGHT_FLAG` — 60-80% run over ~40 bars, otherwise same as high-tight-flag.
8. `POCKET_PIVOT` — pocket-pivot near MA support, EMA50/150/200 stack, 52-week window.
9. `SHORT_CANDIDATES` (`NSE_CSS`) — F&O sideways/slack short watch (NSE only; needs `IsFno`).
10. `WEEKEND_SCAN` — very large-cap closing above prior close (US also requires >EMA200).
11. `HIGHEST_VOLUME` — highest-volume day in last 250 bars on an up day (NSE adds reversal filters).
12. `SHOWING_STRENGTH` — very large-cap close above prior close.
13. `DOUBLERS` — 1M/3M/6M >=100% gain vs a calendar-lookback base close (3 sub-windows each market).

**Deferred:** `EPISODIC_PIVOT` (needs EDGAR 8-K filings + earnings-text parsing + analyst
EPS estimates — a separate external-API subsystem). The UI shows it as a disabled
"coming soon" section.

### Universe

- **Default: `stage2`** — the current week's Stage-2 symbols from `{Market}StageAnalysisResults`
  (`IsStage2 = 1`, latest week).
- **Option: `all`** — the full active catalog universe (`{Market}Stocks`), like the Stage-2
  sample/universe toggle.

The price-refresh step and the scan both honour the selected universe.

### Pre-close scan

A single **Pre-Close Scan** action runs the price-refresh **once** then executes **all**
scanners for the market against the chosen universe, writing each scanner's rows
idempotently for today's `ScanDate`.

The pre-close scan also refreshes the `{Market}TickerTechnical` snapshot (prices, 52-week
levels, market cap) for the scanned symbols by reusing the ingestion `technical` step, so
scoring and Stock Lookup reflect today's data. This is best-effort and never aborts the scan.

### Nightly fundamentals refresh (stage2)

- An admin toggle enables a per-market nightly fundamentals schedule.
- A background service in the API checks every minute and **enqueues a fundamentals-only
  refresh once per exchange-local calendar day**, after the configured `HourLocal` (default
  20:00, i.e. after the close), weekdays only.
- The worker resolves the **stage2 universe** itself and runs only the `fundamentals`
  ingestion step (analyst snapshots, EPS forecasts, earnings fundamentals, market cap);
  bars and the technical snapshot are left to the pre-close scan.
- Idempotent: `LastEnqueuedAt` (persisted, compared in the exchange-local timezone) prevents
  a second enqueue the same local day, and it never enqueues while a fundamentals run is
  already queued/running for the market. Job type: `fundamentals`.

### Schedule (auto start/stop during market hours)

- An admin toggle enables an automatic schedule per market.
- A background service in the API checks every minute and **enqueues a pre-close scan
  message every 15 minutes while the market is open**, in the exchange's local timezone:
  - India (NSE): 09:15-15:30 IST (`India Standard Time`), Mon-Fri.
  - US (NYSE/NASDAQ): 09:30-16:00 ET (`Eastern Standard Time`), Mon-Fri.
- Outside market hours nothing is enqueued (auto start/stop). The schedule is idempotent:
  it never enqueues if a pre-close run is already queued/running for the market.

## Data dependencies

- Daily bars must already be ingested (`{Market}Bars1D`). The refresh step appends/updates
  only today's bar; it does not backfill history.
- `{Market}TickerTechnical.MarketCap` provides market cap; `.Rs` provides the RS rating
  shown on result rows. `{Market}Stocks.IsFno` provides `hasOptions` for NSE F&O scanners.

## Functional requirements

- **FR-001** Each scanner reads up to N daily bars (family-specific: 220/250/300/320/520)
  from `{Market}Bars1D` as of the scan date, ascending, and computes an indicator snapshot
  (SMA/EMA/ATR/RSI/turnover/RVOL/PGO/NATR/candle/inside-bar/gap/trend flags) matching the
  reference spec's semantics. Indicator close uses `AdjClose` when present else `Close`.
- **FR-002** Scanner result rows carry: `Symbol, CompanyName, ClosePrice, DayChangePct,
  Volume, RelVolume, RsRating, SectorName, Industry, TriggerDetails(JSON)`.
- **FR-003** Persistence is idempotent: before inserting, delete existing rows for
  `(ScannerName, ScanDate)` in the market's table, then insert the new rows.
- **FR-004** A scan run is tracked as a `JobRun` with `JobType='scanner'`; metrics record
  per-scanner hit counts and the universe used.
- **FR-005** The worker dispatches queue messages with `jobType='scanner'` to the scanner
  runner; existing stage-2 messages are unaffected.
- **FR-006** The price-refresh step fetches today's daily bar for the universe via yfinance
  (throttled, reusing the ingestion fetcher) and upserts into `{Market}Bars1D`.
- **FR-007** API exposes: trigger (scanner name optional -> one or all; universe stage2/all),
  list scanners with latest counts, results for a scanner+date, and the list of available
  scan dates (for the dropdown).
- **FR-008** API exposes schedule get/set per market; a hosted service enforces the 15-min
  market-hours enqueue with auto start/stop.
- **FR-009** UI shows a Scanners page with one section per scanner, each with a previous-day
  date dropdown and a results table; symbols open the existing Stock Lookup modal. Admin
  gets trigger buttons (per-scanner + Pre-Close Scan), the universe toggle, and the schedule
  on/off switch.

## Out of scope / follow-ups

- `EPISODIC_PIVOT` (EDGAR) — deferred placeholder.
- Intraday tick precision: the refresh uses yfinance's latest daily bar (good enough for
  pre-close scanning); true real-time quotes are out of scope.
- Exchange holiday calendars: the schedule uses weekday + market-hours windows only; holiday
  suppression is a follow-up.

## Tables

```
IndianTechnicalScannerResults / USTechnicalScannerResults
  Id, RunId, ScannerName, ScanDate, Symbol, CompanyName, SectorName, Industry,
  ClosePrice, DayChangePct, Volume, RelVolume, RsRating, TriggerDetails(JSON), CreatedAt
  UNIQUE (ScannerName, ScanDate, Symbol)

ScannerSchedules
  Market (PK), Enabled, IntervalMinutes, LastEnqueuedAt, UpdatedAt

FundamentalsSchedules
  Market (PK), Enabled, HourLocal, LastEnqueuedAt, UpdatedAt
```
