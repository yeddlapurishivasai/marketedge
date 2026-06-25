# Feature 012 — Stock Scoring & Paper Trades

## Summary

A **scoring and paper-trade** subsystem that turns scanner output and ingested
fundamentals into (a) a ranked, explainable conviction score per stock and (b) a simulated
("paper") trade blotter that opens, manages, and closes positions so scanner/pattern quality
can be measured against realised outcomes.

Both run inside the scanner runner (Feature 011) on the daily **pre-close scan**, reusing the
same per-symbol series cache. Scores are written to `{Market}StockScores`; trades to
`{Market}Trades`; adaptive pattern weights to `ScoringWeights`. No new external data is
fetched — scoring and trades consume data already ingested (Features 005/006) plus the day's
scanner hits.

Every stock is evaluated under **two profiles**:

- **Swing** — technical precedence (pattern + trend + RS), short holding horizon.
- **Positional** — fundamentals weighted ~50% alongside technicals, longer horizon.

## Scope

### Scoring

- For each symbol the engine computes a **0..100 conviction score** and a **side**
  (`long` / `short` / `none`) for each profile, using a Wilson lower-bound of the weighted
  pass-rate across component checks (so thin evidence is penalised, matching the worker's
  `z = 1.28`).
- Component checks (trend, RS, pattern/scanner hits, fundamentals, freshness, etc.) and their
  per-check pass/weight breakdown are persisted as `ComponentsJson` for explainability.
- Fundamentals are decayed by recency: `FundFreshnessDecay = 0.5^(DaysSinceEarnings/30)`, so
  stale fundamentals contribute less to the positional score.
- **Adaptive pattern weights** live in `ScoringWeights` (per market, category, component key).
  Pattern weights self-adjust from each scanner's realised paper-trade win/loss record; an
  operator may **pin** a weight (manual override) which freezes auto-adaptation for that row.

### Paper-trade engine

- The engine considers the day's **flagged symbols** (those hit by any scanner) as breakout
  candidates and opens a paper trade when a volume-confirmed break of support/resistance
  triggers, separately for each `TradeType ∈ {swing, positional}`.
- Fixed notional per position: **India ₹100,000**, **US $1,000** (`Qty` derived from entry
  price). Each trade records its entry scanner, the full set of flagged scanners
  (`FlaggedScannersJson` + `ScannerHitCount`), and a **confidence score + rationale**.
- Open positions are managed each run: trailing stop (`CurrentStop` / `StopBasis`), optional
  move-to-break-even (`MovedToBe`), running `LastPrice`, `PnLPct`/`PnLAmount`, and
  `MfePct`/`MaePct`. A position closes with an `ExitReason` (e.g. stop hit), stamping
  `ExitAt`/`ExitPrice` and flipping `Status` to `closed`.
- **Stop model differs by book.** *Swing*: a fixed 6%-below-entry stop that, once price
  reaches +1R, jumps to break-even and thereafter trails the 10-period SMA. *Positional*:
  the initial stop is the **further of the 20-EMA and a 10%-below-entry floor** (so every
  positional trade starts with ≥10% of room); it only tightens to — and then trails — the
  20-EMA once the EMA has risen **above the entry** (the trade is in profit), guaranteeing a
  positional stop is never tighter than swing's when both break out hugging the EMA.

### Pre-close gating (trades generated on the pre-close scan only)

- The paper-trade engine runs **only on the pre-close scan** — the daily all-scanner run
  (`scannerName = null`). Single-scanner runs, ad-hoc/intraday triggers, and local test runs
  still evaluate scanners and **score** the universe but **do not** open or mutate trades; the
  runner logs that the trade engine was skipped.
- This keeps the blotter a faithful reflection of the once-daily pre-close stance and prevents
  intraday or local experimentation from polluting it.

### Clean slate (no backfill)

- Paper trades start from an empty blotter. There is **no historical backfill** — the engine
  never synthesises trades for past scanner hits; positions exist only from genuine pre-close
  triggers going forward.

### PnL & trade views

The UI presents trades under the **Swing / Positional** selector (each view is scoped to the
selected profile), with three sub-views:

- **Positions** — active and closed trades for the profile, with summary stats.
- **P&L by period** — a period picker (1D / 1W / 1M / 3M / 6M / Custom range) showing:
  - **Realized PnL** — trades **closed** (by `ExitAt`) within the selected window, with win/
    loss counts, win rate, and average realised %.
  - **Unrealized PnL** — a live snapshot of **all currently-open** positions' PnL. This is
    **period-independent** (it reflects open positions *now*, not the window).
- **Day** — a date picker listing that day's **entries** (`EntryAt` on the date) and **exits**
  (`ExitAt` on the date), each as a trade table.

## Functional requirements

- **FR-001** During the pre-close scan, the engine scores every symbol in the scanned universe
  for both profiles and upserts one row per ticker into `{Market}StockScores` (PK `Ticker`),
  including `SwingScore/SwingSide`, `PositionalScore/PositionalSide`, the bull/bear tallies,
  freshness inputs, `ScannerHits`, and `ComponentsJson`.
- **FR-002** The paper-trade engine runs **only** when the scan is the pre-close all-scanner
  run (`scannerName = null`); for any single/named-scanner run it is skipped (logged) while
  scoring still runs.
- **FR-003** Trades are never backfilled; the blotter only accrues positions from live
  pre-close triggers.
- **FR-004** A trade row carries: `Ticker, CompanyName, TradeType, Direction, Status,
  EntryScanner, FlaggedScannersJson, ScannerHitCount, EntryAt, EntryPrice, Qty, InitialStop,
  CurrentStop, StopBasis, RiskPerShare, MovedToBe, LastPrice, PnLPct, PnLAmount, MfePct,
  MaePct, ExitAt, ExitPrice, ExitReason, ConfidenceScore, ConfidenceRationaleJson`.
- **FR-005** Adaptive `ScoringWeights` update from realised paper-trade outcomes; a manual
  override pins a weight (clamped 0..1) and stops auto-adaptation for that row.
- **FR-006** The PnL endpoint returns, for a `[from, to)` window and optional `tradeType`:
  realised metrics over trades **closed** in the window (count, wins, losses, win rate, sum
  PnL amount, avg PnL %) **and** an unrealised snapshot (open count + summed PnL amount) over
  **all** currently-open positions, independent of the window.
- **FR-007** The day endpoint returns, for a date and optional `tradeType`, the trades that
  **entered** that day and the trades that **exited** that day, as two lists.
- **FR-008** All trade/score endpoints validate `market ∈ {india, us}` (400 otherwise); an
  empty/`all` `tradeType` means no profile filter.

## API surface

| Method | Route | Purpose |
| ------ | ----- | ------- |
| GET | `/api/{market}/scores?profile=swing\|positional&side=&take=` | Ranked scores for a profile |
| GET | `/api/{market}/scores/{ticker}` | Single stock's score |
| GET | `/api/{market}/trades?status=&tradeType=` | Trade blotter (filterable) |
| GET | `/api/{market}/trades/stats` | Active/closed/win-rate + open/realised PnL totals |
| GET | `/api/{market}/trades/pnl?from=&to=&tradeType=` | Realised (closed-in-window) + unrealised (open-now) PnL |
| GET | `/api/{market}/trades/day?date=&tradeType=` | That day's entries and exits |
| GET | `/api/{market}/scanners/performance` | Per-scanner reliability from realised trades |
| GET | `/api/{market}/scoring/weights` | Confidence weights |
| PUT | `/api/{market}/scoring/weights/{id}` | Edit/pin a weight |

## Out of scope / follow-ups

- Real capital / broker execution — this is a paper blotter only.
- The `Ai*` score columns (`AiUpsidePct`, `AiDownsidePct`, `AiRationale`) are a placeholder
  for a future AI-assisted scoring flow; the deterministic Wilson scores are authoritative
  today.
- Trades cannot be distinguished as prod vs local at the worker; a genuine **local** pre-close
  run will still create local trades.

## Tables

```
IndianStockScores / USStockScores
  Ticker (PK), AsOfDate,
  UpsideEpsPct, UpsideAnalystPct, TargetPrice,
  AiUpsidePct, AiDownsidePct, AiRationale,
  SwingScore, SwingSide, SwingBull, SwingBear,
  PositionalScore, PositionalSide, PositionalBull, PositionalBear,
  FundFreshnessDecay, DaysSinceEarnings, ScannerHits, IsFno, ComponentsJson, ScoredAt

IndianTrades / USTrades
  Id (PK), Ticker (FK -> {Market}Tickers), CompanyName, TradeType (swing|positional),
  Direction (long|short), Status (active|closed), EntryScanner, FlaggedScannersJson,
  ScannerHitCount, EntryAt, EntryPrice, Qty, InitialStop, CurrentStop, StopBasis,
  RiskPerShare, MovedToBe, LastPrice, PnLPct, PnLAmount, MfePct, MaePct,
  ExitAt, ExitPrice, ExitReason, ConfidenceScore, ConfidenceRationaleJson, CreatedAt, UpdatedAt

ScoringWeights
  Id (PK), Market, Category, ComponentKey, Weight, SeedWeight, Wins, Losses,
  ManualOverride, UpdatedAt
```
