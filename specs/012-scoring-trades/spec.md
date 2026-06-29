# Feature 012 — Breakouts (simulated trade blotter)

## Summary

A **breakout** subsystem that turns scanner output and ingested fundamentals into a simulated
("paper") **breakout blotter** that opens, manages, and closes positions so scanner quality
can be measured against realised outcomes.

It runs inside the scanner runner (Feature 011) on the daily **pre-close scan**, reusing the
same per-symbol series cache. Breakouts are written to `{Market}Breakouts`; scanner-mix
weights to `ScoringWeights`. No new external data is fetched — the engine consumes data
already ingested (Features 005/006) plus the day's scanner hits.

> **Removed:** the earlier standing **per-stock conviction score** (`{Market}StockScores`,
> `/scores` endpoints, the worker score-universe pass, and the adaptive *pattern weights*) has
> been removed. Conviction is now expressed **only at the moment a breakout opens**, as that
> breakout's confidence score + rationale, using the values in effect at entry time.

Every breakout is evaluated under **two profiles**:

- **Swing** — technical precedence (pattern + trend + RS), short holding horizon.
- **Positional** — fundamentals weighted ~50% alongside technicals, longer horizon.

## Scope

### Confidence at breakout time

- A breakout's conviction is computed **once, when the position opens**, and stored on the
  breakout row as `ConfidenceScore` + `ConfidenceRationaleJson`. There is no standing
  per-stock score table.
- The confidence blends three components: a **setup score** (scanner reliability of the
  flagging scanners), the relevant **fundamental long/short score**, and the **breakout
  volume** strength. Multiple flagging scanners are combined with a noisy-OR.

### Scanner reliability prior (equal start, self-improving)

- Each scanner starts from an **equal Beta-smoothed prior** and improves as breakouts
  accumulate: `reliability = (wins + 2) / (total + 4)`, so an unproven scanner starts at
  **0.5** and converges to its realised win-rate as evidence grows (`wins`/`total` come from
  closed breakouts). This replaces the old per-pattern adaptive weights.

### Breakout engine

- The engine considers the day's **flagged symbols** (those hit by any scanner) as breakout
  candidates and opens a breakout when a volume-confirmed break of support/resistance
  triggers, separately for each `TradeType ∈ {swing, positional}`.
- Fixed notional per position: **India ₹100,000**, **US $1,000** (`Qty` derived from entry
  price). Each breakout records its entry scanner, the full set of flagged scanners
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

### Pre-close gating (breakouts generated on the pre-close scan only)

- The breakout engine runs **only on the pre-close scan** — the daily all-scanner run
  (`scannerName = null`). Single-scanner runs, ad-hoc/intraday triggers, and local test runs
  still evaluate scanners but **do not** open or mutate breakouts; the runner logs that the
  breakout engine was skipped.
- This keeps the blotter a faithful reflection of the once-daily pre-close stance and prevents
  intraday or local experimentation from polluting it.

### Clean slate (no backfill)

- Breakouts start from an empty blotter. There is **no historical backfill** — the engine
  never synthesises breakouts for past scanner hits; positions exist only from genuine
  pre-close triggers going forward.

### PnL & breakout views

The UI presents breakouts under the **Swing / Positional** selector (each view is scoped to
the selected profile), with three sub-views:

- **Positions** — active and closed breakouts for the profile, with summary stats.
- **P&L by period** — a period picker (1D / 1W / 1M / 3M / 6M / Custom range) showing:
  - **Realized PnL** — breakouts **closed** (by `ExitAt`) within the selected window, with
    win/loss counts, win rate, and average realised %.
  - **Unrealized PnL** — a live snapshot of **all currently-open** positions' PnL. This is
    **period-independent** (it reflects open positions *now*, not the window).
- **Day** — a date picker listing that day's **entries** (`EntryAt` on the date) and **exits**
  (`ExitAt` on the date), each as a breakout table.

## Functional requirements

- **FR-001** A breakout's conviction is computed and stamped **at open time** onto the breakout
  row (`ConfidenceScore` + `ConfidenceRationaleJson`), blending scanner setup reliability, the
  relevant fundamental long/short score, and breakout volume. No standing per-stock score is
  persisted.
- **FR-002** The breakout engine runs **only** when the scan is the pre-close all-scanner run
  (`scannerName = null`); for any single/named-scanner run it is skipped (logged).
- **FR-003** Breakouts are never backfilled; the blotter only accrues positions from live
  pre-close triggers.
- **FR-004** A breakout row carries: `Ticker, CompanyName, TradeType, Direction, Status,
  EntryScanner, FlaggedScannersJson, ScannerHitCount, EntryAt, EntryPrice, Qty, InitialStop,
  CurrentStop, StopBasis, RiskPerShare, MovedToBe, LastPrice, PnLPct, PnLAmount, MfePct,
  MaePct, ExitAt, ExitPrice, ExitReason, ConfidenceScore, ConfidenceRationaleJson`.
- **FR-005** Scanner reliability is a Beta-smoothed prior `(wins + 2)/(total + 4)` updated from
  realised breakout outcomes; unproven scanners start at 0.5. `ScoringWeights` retains only the
  scanner-mix weights (per market, category, component key); an operator may **pin** a weight.
- **FR-006** The PnL endpoint returns, for a `[from, to)` window and optional `tradeType`:
  realised metrics over breakouts **closed** in the window (count, wins, losses, win rate, sum
  PnL amount, avg PnL %) **and** an unrealised snapshot (open count + summed PnL amount) over
  **all** currently-open positions, independent of the window.
- **FR-007** The day endpoint returns, for a date and optional `tradeType`, the breakouts that
  **entered** that day and the breakouts that **exited** that day, as two lists.
- **FR-008** All breakout endpoints validate `market ∈ {india, us}` (400 otherwise); an
  empty/`all` `tradeType` means no profile filter.

## API surface

| Method | Route | Purpose |
| ------ | ----- | ------- |
| GET | `/api/{market}/breakouts?status=&tradeType=` | Breakout blotter (filterable) |
| GET | `/api/{market}/breakouts/stats` | Active/closed/win-rate + open/realised PnL totals |
| GET | `/api/{market}/breakouts/pnl?from=&to=&tradeType=` | Realised (closed-in-window) + unrealised (open-now) PnL |
| GET | `/api/{market}/breakouts/day?date=&tradeType=` | That day's entries and exits |
| GET | `/api/{market}/scanners/performance` | Per-scanner reliability from realised breakouts |
| GET | `/api/{market}/scoring/weights` | Scanner-mix weights |
| PUT | `/api/{market}/scoring/weights/{id}` | Edit/pin a weight |

## Out of scope / follow-ups

- Real capital / broker execution — this is a paper blotter only.
- Breakouts cannot be distinguished as prod vs local at the worker; a genuine **local**
  pre-close run will still create local breakouts.

## Tables

```
IndianBreakouts / USBreakouts
  Id (PK), Ticker (FK -> {Market}Tickers), CompanyName, TradeType (swing|positional),
  Direction (long|short), Status (active|closed), EntryScanner, FlaggedScannersJson,
  ScannerHitCount, EntryAt, EntryPrice, Qty, InitialStop, CurrentStop, StopBasis,
  RiskPerShare, MovedToBe, LastPrice, PnLPct, PnLAmount, MfePct, MaePct,
  ExitAt, ExitPrice, ExitReason, ConfidenceScore, ConfidenceRationaleJson, CreatedAt, UpdatedAt

ScoringWeights
  Id (PK), Market, Category, ComponentKey, Weight, SeedWeight, Wins, Losses,
  ManualOverride, UpdatedAt
```
