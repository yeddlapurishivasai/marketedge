# Implementation Plan: Fundamentals Ingestion & Observability

**Branch**: `006-fundamentals` | **Date**: 2026-06-25 | **Spec**: `./spec.md`

**Input**: Feature specification from `specs/006-fundamentals/spec.md`

## Summary

Promote fundamentals ingestion (analyst consensus, EPS forecasts, valuation metrics)
from the best-effort P3 story in `005` into a durable, idempotent, resilient feature,
and establish the **project-wide OpenTelemetry file-logging standard**: all
components (API, Worker, Ingestion) emit logs through OpenTelemetry to files at an
OS-specific location (`/var/log/marketedge/` on the Linux server,
`C:\ProgramData\MarketEdge\logs\` on local Windows), rotated daily with **7-day**
retention. No new database schema is introduced (the fundamentals tables come from the
`005` dacpac contract).

## Technical Context

**Language/Version**: Python 3.12 (Ingestion CLI, Worker); .NET 8 (API).

**Primary Dependencies**: existing pandas / yfinance / pyodbc toolchain; **new**:
OpenTelemetry — Python (`opentelemetry-api`, `opentelemetry-sdk`) bridged to stdlib
`logging`; .NET (`OpenTelemetry`, `OpenTelemetry.Extensions.Logging`). A rotating file
handler / sink provides the mandatory file output.

**Storage**: SQL Server 2022, schema owned by the `005` dacpac (no new tables).
Fundamentals tables: `{Indian|US}AnalystSnapshot`, `{Indian|US}EpsForecasts`;
valuation fields on `{Indian|US}TickerTechnical` / `{Indian|US}StockFundamentals`.

**Observability sink**: log **files** under an OS-specific directory (telemetry
contract `./contracts/telemetry.md`); daily rotation, 7-day retention; OpenTelemetry
is the logging API for all components.

**Testing**: a fundamentals round-trip (seed → ingest a small universe → assert
analyst/EPS rows + idempotency on re-run) and logging tests (directory resolution per
OS, retention pruning to 7 files, secrets redaction).

**Target Platform**: Linux server (deployed) / Windows (local dev); on-demand /
scheduled Python CLI plus the always-on Worker and API.

**Project Type**: Backend data pipeline + cross-cutting observability. No UI.

**Constraints**: DML only (Principle I — no schema management from code); India/US
symmetric via market→table-set map; idempotent upserts on natural keys; NaN/inf → NULL;
single-ticker failures never abort a run; logging never crashes a component; no secrets
in logs.

**Scale/Scope**: India ≈2,285 tickers / US ≈6,368 tickers. Fundamentals are
best-effort per ticker; logging applies to every component process.

## Constitution Check

*GATE: must hold before and after design.*

- **I. Schema owned by SQL project**: PASS — no new tables; fundamentals write only to
  tables defined by the `005` dacpac; ingestion is DML only.
- **II. EF Core query-only (API)**: PASS — the API change is observability only
  (OpenTelemetry logging provider); no schema or EF model changes.
- **III. Worker owns heavy fetching; DB-mediated**: PASS — fundamentals fetching lives
  in the ingestion pipeline / worker toolchain; consumers read rows from SQL. No new
  API↔worker coupling.
- **IV. Week-keyed, idempotent, append-only**: PASS (analogous) — fundamentals upsert
  on their natural keys; re-runs never duplicate; `AsOfDate` preserves revisions.
- **V. REST/seed conventions**: N/A — no new HTTP surface; the universe is sourced
  from SQL (`{Indian|US}Stocks`).

**New dependency note (OpenTelemetry)**: Adding OpenTelemetry as the logging pipeline
is an observability addition, not a constitutional deviation — it manages no schema,
introduces no API↔worker RPC, and keeps India/US code paths uniform. The file sink is
the system of record; OTLP export is optional.

No violations → Complexity Tracking not required.

## Project Structure

### Documentation (this feature)

```text
specs/006-fundamentals/
├── plan.md                  # This file
├── spec.md                  # Feature specification (fundamentals + observability)
└── contracts/
    └── telemetry.md         # OpenTelemetry file-logging contract (paths, format, rotation, retention)
```

### Source Code (repository root)

```text
src/MarketEdge.Ingestion/
├── observability.py         # NEW: configure_logging(service_name, market) — OTel + rotating file sink
├── cli.py                   # CHANGED: call configure_logging at startup; richer fundamentals command + run summary
├── fetch.py                 # CHANGED: harden fundamentals fetch (analyst/EPS/valuation), defensive normalization
└── db.py                    # (reuse) upsert_analyst_snapshot / upsert_eps_forecast / valuation upsert

src/MarketEdge.Worker/
├── observability.py         # NEW (or shared): same OTel file-logging setup
└── app.py / worker.py       # CHANGED: route logging through OpenTelemetry at startup

src/MarketEdge.Api/
├── Program.cs               # CHANGED: AddMarketEdgeLogging() — OpenTelemetry logging provider + file sink
└── (Observability/MarketEdgeLogging.cs)  # NEW: centralized .NET logging setup
```

**Structure Decision**: Fundamentals ingestion extends the existing
`src/MarketEdge.Ingestion` package (no new schema; reuses the `005` upsert helpers).
Observability is a thin, centralized setup helper per language
(`observability.py` for Python, an `AddMarketEdgeLogging` extension for .NET), all
honoring the single telemetry contract so every component logs identically.

## Phasing

1. **Observability foundation (P1)** — implement the OS-specific log directory
   resolution, OpenTelemetry LoggerProvider + rotating file sink, 7-day retention, and
   the central setup helpers; wire each component's startup to call them.
2. **Fundamentals ingestion (P1)** — harden analyst consensus + EPS forecast fetching
   and upserts (durable, idempotent), with per-ticker resilience and a logged run
   summary.
3. **Valuation fundamentals (P2)** — add market-cap/valuation metric upserts.
4. **Validation** — round-trip + idempotency tests; logging tests (per-OS path,
   retention pruning, redaction).

## Complexity Tracking

No constitutional violations — section intentionally empty.
