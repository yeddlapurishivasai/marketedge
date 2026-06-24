# Implementation Plan: Sector & Stock Catalog — API (Backtracked)

**Branch**: `004-catalog-api` | **Date**: 2026-06-24 | **Spec**: `./spec.md`

**Input**: Feature specification from `specs/004-catalog-api/spec.md`

## Summary

Document and verify the existing sector/stock CRUD surface of `MarketEdge.Api`:
list/search/get/create/update/delete sectors and stocks plus bulk move, for both
`india` and `us`. The technical approach is to trace controller → service → EF Core
(query-only) → SQL Server, capture the HTTP/DTO contracts, and assert the documented
behavior matches `SectorsController`, `StocksController`, `SectorService`,
`StockService`, and `Models/Dtos.cs`.

## Technical Context

**Language/Version**: C# / .NET 8

**Primary Dependencies**: ASP.NET Core Web API, EF Core (query-only,
`MarketEdgeDbContext`)

**Storage**: SQL Server 2022 (schema owned by `src/MarketEdge.Database` dacpac;
tables `IndianSectors`, `IndianStocks`, `USSectors`, `USStocks`,
`IndianStockFundamentals`, `USStockFundamentals`)

**Testing**: Manual/HTTP verification against a seeded local DB; no dedicated API
unit-test project exists in-repo (baseline = `dotnet build`)

**Target Platform**: Windows/IIS-or-Kestrel server hosting the API + SPA proxy

**Project Type**: Web service (backend API). Frontend SPA documented separately in
`003-marketedge-spa`.

**Performance Goals**: Simple LINQ reads/writes; paged list (`pageSize=50`) and a
set-based bulk move (`ExecuteUpdate`); no special targets.

**Constraints**: EF Core query-only (NO migrations); DTO-only responses; market
endpoints symmetric for `india`/`us`.

**Scale/Scope**: India ≈123 sectors / 2,285 stocks; US ≈153 sectors / 6,368 stocks.

## Constitution Check

*GATE: must hold for the documented behavior.*

- **I. Schema owned by SQL project**: PASS — catalog adds no migrations; entities
  map to dacpac-owned tables.
- **II. EF Core query-only**: PASS — services use LINQ reads/writes and
  `ExecuteUpdate`; responses are DTOs, never entities.
- **III. Worker decoupled via queue + DB**: N/A — the catalog has no worker
  interaction.
- **IV. Week-keyed, idempotent, append-only**: N/A — catalog is mutable reference
  data, not week-keyed analysis output.
- **V. REST conventions**: PASS — `/api/{india|us}/sectors`, `/api/{india|us}/stocks`
  with `400`/`404`/`201`/`204` semantics and a `Location` header on create.

No violations → Complexity Tracking not required.

## Project Structure

### Documentation (this feature)

```text
specs/004-catalog-api/
├── plan.md              # This file
├── spec.md              # Backtracked specification
├── tasks.md             # Validation/documentation tasks
└── contracts/
    └── api.md           # HTTP contracts + DTO shapes
```

### Source Code (repository root)

```text
src/MarketEdge.Api/
├── Controllers/
│   ├── SectorsController.cs       # /api/{market}/sectors
│   └── StocksController.cs        # /api/{market}/stocks (+ /move)
├── Services/
│   ├── SectorService.cs           # Sector CRUD (EF Core, query-only)
│   └── StockService.cs            # Stock search/CRUD/move (EF Core, query-only)
├── Models/
│   ├── Dtos.cs                    # SectorDto, StockDto, requests, PagedResult<T>
│   └── Entities.cs                # *Sector, *Stock, *StockFundamentals
└── Data/
    └── MarketEdgeDbContext.cs     # DbSets for both markets
```

## Approach (documenting & validating existing behavior)

1. Trace each route in the two controllers to its service method and the EF query.
2. Record request/response DTO shapes and status codes in `contracts/api.md`.
3. Confirm market symmetry: india/us paths differ only by table family.
4. Confirm query-only EF usage and absence of migrations (Constitution I/II).
5. Cross-check the SPA's sector/stock `api.ts` client (`003`) against these
   endpoints so SC-002 of `003` resolves to this spec.

## Complexity Tracking

No constitutional deviations; table intentionally omitted.
