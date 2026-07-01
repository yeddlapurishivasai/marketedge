# Feature 010 — Stage 2 analysis reads prices & market cap from the DB (not yfinance)

## Summary
Stage 2 analysis currently re-downloads **every** stock's weekly prices and market cap from
yfinance on each run, with per-stock network throttling. This makes a full-universe run take
many minutes to hours. All of that data is already ingested into the database
(`{Market}Bars1D` daily OHLCV and `{Market}TickerTechnical.MarketCap`).

This feature repoints Stage 2's per-stock price and market-cap reads at the **database**, removing
the per-stock yfinance calls and the throttling sleeps entirely. A full run drops from
minutes/hours to seconds and becomes deterministic (no network flakiness).

## Motivation
- The ingestion pipeline (`/admin` → Ingest Bars) already persists 1 year of daily bars and
  fundamentals for the evaluated universe. Re-fetching the same data live from yfinance for every
  stock is redundant and slow.
- Previous runs blew out to **hours** because each stock did two throttled yfinance calls
  (market cap via `fast_info`, then weekly `download`), each followed by a `time.sleep`, plus
  exponential-backoff retries on transient yfinance failures.
- The RS-rating step (feature 009) already reads only from `{Market}Bars1D`. Stage 2 should use
  the same ingested data for its own price inputs.

## Current behaviour (before)
Per stock, inside `run_stage2_analysis` (worker.py):
1. `_fetch_single_market_cap(symbol)` → `yf.Ticker(...).fast_info` → `time.sleep(stock_delay)`.
2. `_fetch_single_price_data(symbol, end_date)` → `yf.download(interval="1wk", period="2y")` →
   `time.sleep(stock_delay)`.
3. `calculate_stage2(price_frame, benchmark_frame)`.
Plus a `time.sleep(sector_delay)` between sectors. Benchmark fetched once per run from yfinance.

## New behaviour (after)
Per stock:
1. `mc = load_market_cap_from_db(conn, market, symbol)` — latest non-null
   `{Market}TickerTechnical.MarketCap`. **No network, no sleep.**
2. `price_frame = load_weekly_price_from_db(conn, market, symbol, end_date=as_of_end)` — reads
   daily bars from `{Market}Bars1D` (optionally bounded `BarDate < as_of_end` for point-in-time),
   then **resamples daily → weekly** (`W-FRI`: Open=first, High=max, Low=min, Close=last,
   Volume=sum) so the existing weekly Weinstein/Mansfield math in `calculate_stage2` is unchanged.
3. `calculate_stage2(price_frame, benchmark_frame)` — unchanged.
No per-stock or per-sector sleeps remain in the DB path.

### Benchmark
The market index (`^NSEI` / `^GSPC`) is **not** part of the ingested stock universe, so it is the
only remaining yfinance call — **one download per run** (not per stock), via
`fetch_benchmark_weekly_from_daily`, which fetches the index **daily** and resamples with the same
`W-FRI` rule. Using the identical weekly rule for stock and benchmark is required so their indices
align (an inner-join on mismatched week labels would otherwise yield an empty RS line). Ingesting
the benchmark into its own table to remove this last call is a possible follow-up.

## Functional requirements
- **FR-001** Stage 2 per-stock price input is read from `{Market}Bars1D` and resampled to weekly;
  no per-stock yfinance price download occurs.
- **FR-002** Stage 2 per-stock market cap is read from `{Market}TickerTechnical.MarketCap`; no
  per-stock yfinance `fast_info` call occurs.
- **FR-003** All per-stock and per-sector throttling sleeps are removed from the DB path.
- **FR-004** Point-in-time runs still bound prices to the target week (`BarDate < as_of_end`).
- **FR-005** Stocks with no ingested bars (or < 30 weekly bars after resampling) are skipped, the
  same as the previous "insufficient data" path. Stage 2 therefore depends on ingestion having run
  for the evaluated universe.
- **FR-006** The benchmark is fetched once per run (daily, resampled weekly with the same rule).
- **FR-007** The standalone yfinance helpers (`_fetch_single_*`, `fetch_benchmark_data`) remain for
  the live e2e test; only the production run path switches to the DB.

## Data dependency note
Because Stage 2 now reads ingested data, the **Ingest Bars** step must have run for the target
universe before analysis. To guarantee this for the scheduled weekend run, the Stage 2 job now
**self-refreshes the analyzed universe's daily bars** (a full `ingest bars` over the same
sectors/limit/test-sample scope) before the RS-ratings step and the per-stock loop. This runs
only for a live/current-week run — a point-in-time historical run reads already-ingested bars
as-is. The refresh is best-effort: a failure logs a warning and the run continues on existing
bars. Without it, only the *stage2* universe stays fresh (the weekday pre-close scan refreshes
just that set), so non-stage2 stocks would be analysed on stale bars and a name that just
entered Stage 2 could never be discovered. Un-ingested stocks are still silently skipped
(insufficient data).

## Out of scope
- Ingesting the benchmark index into the DB (keeps one yfinance call per run).
- Changing the RS / quadrant / classification formulas (feature 009 / 002 own those).
