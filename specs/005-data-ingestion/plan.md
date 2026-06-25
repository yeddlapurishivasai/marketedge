# Implementation Plan: Market Data Ingestion (Base Data)

**Branch**: `005-data-ingestion` | **Date**: 2026-06-25 | **Spec**: `./spec.md`

**Input**: Feature specification from `specs/005-data-ingestion/spec.md`

## Summary

Add a local **base-data layer** to MarketEdge: 10 new SQL Server tables (per-market
`Indian*`/`US*` pairs for Tickers, TickerTechnical, AnalystSnapshot, EpsForecasts,
Bars1D) in the `src/MarketEdge.Database` dacpac, plus a Python ingestion CLI
that fetches prices, technicals, and fundamentals from yfinance and upserts them by
natural key. This makes Stage 2 / sector-rotation analysis run against local data
instead of live yfinance (consumer-side change tracked in
`specs/002-stage2-analysis-worker/`).

## Technical Context

**Language/Version**: Python 3.12 (ingestion CLI); SQL (dacpac DDL)

**Primary Dependencies**: pandas, yfinance, pyodbc (reuse the existing worker
toolchain — NO new PostgreSQL/Alembic/SQLAlchemy stack); `typer`/`argparse` for the
CLI.

**Storage**: SQL Server 2022, schema owned by `src/MarketEdge.Database` dacpac. New
tables: `{Indian|US}Tickers`, `{Indian|US}TickerTechnical`,
`{Indian|US}AnalystSnapshot`, `{Indian|US}EpsForecasts`, `{Indian|US}Bars1D`.
Authoritative DDL in `contracts/schema.md`.

**Testing**: A bars round-trip test (seed tickers → ingest a small universe → assert
row counts + idempotency on re-run) mirroring the worker's `test_e2e_worker.py` style.

**Target Platform**: On-demand / scheduled Python CLI; SQL Server backend.

**Project Type**: Backend data pipeline + database schema. No UI.

**Performance Goals**: Correctness and idempotency over speed; throttled yfinance
access with retries/backoff to respect rate limits.

**Constraints**: DML only (NO schema management from code/EF/pyodbc — Principle I);
India/US symmetric via a market→table-set map; idempotent upserts on natural keys;
NaN/inf persisted as NULL; UTC audit timestamps; currency implicit by market.

**Scale/Scope**: India ≈2,285 tickers / 123 sectors; US ≈6,368 tickers / 153 sectors.
Daily bars history per ticker + benchmark.

## Constitution Check

*GATE: must hold before and after design.*

- **I. Schema owned by SQL project**: PASS — all 10 tables are `.sql` files in the
  dacpac; the pipeline runs DML only. The PostgreSQL/Alembic/SQLAlchemy approach from
  the original proposal is explicitly rejected in favor of dacpac.
- **II. EF Core query-only (API)**: N/A — ingestion is a Python CLI using pyodbc; it
  performs no schema management and the API is unchanged.
- **III. Worker owns heavy fetching; DB-mediated**: PASS — heavy market-data fetching
  moves into this pipeline; downstream consumers read the resulting rows from SQL.
  No direct RPC introduced.
- **IV. Week-keyed, idempotent, append-only**: PASS (analogous) — every base table
  upserts on its natural key; re-runs never duplicate.
- **V. REST/seed conventions**: N/A (no new HTTP surface). Reference symbol universe
  is sourced from SQL (`{Indian|US}Stocks`), not JSON/code.

No violations → Complexity Tracking not required.

## Project Structure

### Documentation (this feature)

```text
specs/005-data-ingestion/
├── plan.md              # This file
├── spec.md              # Feature specification
└── contracts/
    └── schema.md        # Authoritative SQL Server DDL for the 10 tables
```

### Source Code (repository root)

```text
src/MarketEdge.Database/Tables/        # NEW dacpac table files (schema source of truth)
├── IndianTickers.sql        / USTickers.sql
├── IndianTickerTechnical.sql/ USTickerTechnical.sql
├── IndianAnalystSnapshot.sql/ USAnalystSnapshot.sql
├── IndianEpsForecasts.sql   / USEpsForecasts.sql
└── IndianBars1D.sql         / USBars1D.sql

src/MarketEdge.Ingestion/   # NEW Python ingestion CLI (mirrors worker toolchain)
├── cli.py                  # seed tickers | ingest bars | ingest technical | ingest fundamentals
├── fetch.py                # yfinance fetching (retries, backoff, throttle, column flatten)
├── ingest_db.py            # pyodbc MERGE upserts by natural key; market→table-set map
├── config.py               # env-driven configuration
└── test_ingest.py          # seed → ingest → idempotency round-trip test
```

**Structure Decision**: Schema lives in the existing dacpac (one `.sql` file per
table, per convention). The ingestion pipeline is a new sibling Python package that
reuses the worker's pandas/yfinance/pyodbc stack rather than introducing a new
database technology.

## Complexity Tracking

No constitutional violations — section intentionally empty.
