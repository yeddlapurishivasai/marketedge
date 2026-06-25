# Feature 008 — Stock Lookup

## Summary
A **Stock Lookup** page lets an operator search a single symbol (or company name) in a market and
inspect everything the ingestion pipeline knows about it on one screen: a TradingView-style
candlestick chart with EMA overlays and volume, the latest technical/relative-strength metrics, a
properties grid, analyst consensus + quarterly/yearly EPS forecast tables, and **upside scenarios**
(EPS-implied and analyst price-target based). A **Refresh Analyst
Data** action re-ingests fundamentals for that one symbol on demand.

This is a read-only consumer of data produced by Feature 005 (Data Ingestion) and Feature 006
(Fundamentals). It adds no new tables; it does rely on the analyst **price-target** columns
(`TargetLowPrice`, `TargetMeanPrice`, `TargetHighPrice`) added to `{Market}AnalystSnapshot`.

## User stories

### US-1 — Look up a symbol
As an operator, I type a symbol (e.g. `MSFT`) or company name, pick a market (US / India), and click
**Search**. The page resolves the symbol and renders its header (symbol, company name, broad sector ·
industry), the price chart, metrics, properties, and analyst tables.

### US-2 — Inspect the chart
As an operator, I can toggle the chart between **Daily** and **Weekly** candles and overlay any of
**EMA 10 / 20 / 50 / 200**. The chart shows OHLC candles with a volume histogram beneath, scoped to
the rolling 1-year window held in the bars table.

### US-3 — Read metrics, properties and analyst data
As an operator, I see metric cards (Close, Day %, RS, RS 1D/1W/1M/3M/6M, Consensus Rating, Current &
Next Quarter EPS, Current & Next Year EPS), a properties grid (Exchange, Market Cap, 52W High, From
52W High, Open, High, Low, Options, Active, RS Type, RS Date, Bars Available, Scanner Hits, Last
Scanner Hit), and Analyst Snapshot + Quarterly/Yearly EPS forecast tables.

### US-5 — See upside scenarios
As an operator, I see **upside scenarios** for the symbol:
- **EPS-implied upside** — the implied price move if the P/E stays constant and EPS moves from
  trailing to forward (next-quarter and next-year), surfaced as **best / base / bull** cases.
- **Analyst price-target upside** — `(target/close − 1)` for the analyst **low / mean / high**
  12-month targets (`TargetLowPrice` / `TargetMeanPrice` / `TargetHighPrice`).
- **AI scenario** — a placeholder for a future AI-assisted upside/downside (rendered as
  "coming soon" until the AI flow lands).

### US-4 — Refresh analyst data for one symbol
As an operator, I click **Refresh Analyst Data** to re-run fundamentals ingestion for the current
symbol only. The page reflects the refreshed `As Of` date when it completes.

## Functional requirements

- **FR-001** A symbol search endpoint returns up to 20 candidates matching the query on `Symbol`
  (prefix-preferred) or `CompanyName` (contains), per market.
- **FR-002** A detail endpoint returns, for a symbol: ticker row (Exchange, Active, IsFno,
  BarsAvailable), the latest `TickerTechnical` row, the latest `AnalystSnapshot` row (including the
  `TargetLowPrice` / `TargetMeanPrice` / `TargetHighPrice` price targets), all current
  `EpsForecasts` rows split into quarterly (`Q`) and yearly (`Y`) ordered by `PeriodEndDate`, plus
  CompanyName / BroadSector / SectorName from the catalog. Missing optional sections are returned as
  null/empty rather than failing.
- **FR-003** A bars endpoint returns OHLCV for the symbol. `timeframe=daily` returns raw `Bars1D`;
  `timeframe=weekly` aggregates daily bars into ISO-week candles (open=first, high=max, low=min,
  close=last, volume=sum) server-side. Bars are ordered ascending by date.
- **FR-004** A refresh-stock endpoint re-ingests every pipeline step
  (`ingest bars` → `ingest technical` → `ingest fundamentals`) scoped to the one symbol and then
  recomputes that symbol's score, running out-of-process as a `stock_refresh` JobRun on the worker.
  Only one refresh per (market, symbol) may be in flight; a duplicate request returns the in-flight run.
- **FR-005** EMA values (10/20/50/200) are computed client-side from the returned bars and drawn as
  line overlays; the user may toggle each independently. Default overlay is EMA 20.
- **FR-006** Currency/number formatting is market-aware (₹ vs $; Indian Cr/Lakh-Cr vs B/T market cap),
  reusing existing `format.ts` helpers.
- **FR-007** All endpoints validate `market ∈ {india, us}` and return 400 otherwise, and 404 when the
  symbol does not exist in the market's ticker master.
- **FR-008** Upside scenarios are derived from already-returned data: EPS-implied upside assumes a
  constant P/E and applies the forward/trailing EPS ratio (next-quarter and next-year), surfaced as
  best/base/bull cases; analyst-target upside is `(target/close − 1)` for low/mean/high targets. The
  AI scenario is a placeholder until the AI scoring flow lands.

## Non-functional / constraints

- **NFR-001** Schema is managed only by the SQL project (no EF migrations); new EF entities are
  query-only mappings to existing tables.
- **NFR-002** India and US are symmetric; every endpoint works for both via the `{market}` route.
- **NFR-003** The chart library is TradingView **lightweight-charts** (keeps the TV attribution mark);
  it is added as a `clientapp` dependency.
- **NFR-004** Logging uses the existing OpenTelemetry NDJSON file sinks; the refresh run reuses the
  ingestion CLI's logging.

## API surface

| Method | Route | Purpose |
| ------ | ----- | ------- |
| GET | `/api/{market}/lookup/search?q=` | Symbol/company candidates (max 20) |
| GET | `/api/{market}/lookup/{symbol}` | Full symbol detail |
| GET | `/api/{market}/lookup/{symbol}/bars?timeframe=daily\|weekly` | OHLCV bars |
| POST | `/api/{market}/lookup/{symbol}/refresh-stock` | Re-ingest all steps for one symbol, then rescore it |

## Out of scope
- Editing any ticker/technical/analyst data from this page (read-only except the refresh action).
- Intraday data, options chains, or news.
- Adding new persisted tables (the analyst price-target columns on `{Market}AnalystSnapshot` are the
  only schema addition this feature relies on; they are managed by the SQL project, not here).
