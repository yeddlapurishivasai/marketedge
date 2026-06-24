# Feature Specification: Sector & Stock Catalog — API (Backtracked)

**Feature Branch**: `004-catalog-api`
**Created**: 2026-06-24
**Status**: Backtracked (documents existing behavior)

**Input**: Reverse-engineered from the existing .NET 8 Web API
(`SectorsController`, `StocksController`, `SectorService`, `StockService`,
`Models/Dtos.cs`, `Models/Entities.cs`).

## Overview

The catalog API is the CRUD surface that lets a client browse and maintain the
sector taxonomy and the stock universe for each market (`india`, `us`). It is the
backend the React SPA's Sectors and Stocks screens (`003-marketedge-spa`, US2/US3)
call, and it supplies the sectors and stocks that the Stage 2 analysis
(`001`/`002`) scores. This spec documents the **existing** REST endpoints,
request/response contracts, and behavior — it does not propose new functionality.

The two markets are symmetric: every operation selects the Indian or US tables by
the `{market}` route segment and is otherwise identical (Constitution: India/US
symmetry).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Browse and search the stock universe (Priority: P1)

A client lists stocks for a market, optionally filtered by a free-text query and/or
a sector, and pages through the results.

**Why this priority**: Reading the catalog is the most-used operation and the
foundation for every other catalog action and for analysis.

**Acceptance Scenarios**:

1. **Given** market `india`, **When** `GET /api/india/stocks` is called, **Then** a
   `PagedResult<StockDto>` is returned with `Items` ordered by `CompanyName`,
   `TotalCount`, `Page`, and `PageSize` (defaults `page=1`, `pageSize=50`).
2. **Given** a `q` query, **When** provided, **Then** results are limited to stocks
   whose `Symbol` or `CompanyName` contains `q`.
3. **Given** a `sectorId`, **When** provided, **Then** results are limited to that
   sector; `q` and `sectorId` combine (AND).
4. **Given** an unknown `{market}`, **When** any catalog endpoint is called,
   **Then** the API returns `400` ("Market must be 'india' or 'us'").

### User Story 2 - Maintain the sector taxonomy (Priority: P2)

A client lists sectors with their stock counts and creates, renames, or deletes
sectors.

**Why this priority**: Sectors are the grouping every stock and analysis rolls up
to; they must be editable but safe to delete.

**Acceptance Scenarios**:

1. **Given** market `us`, **When** `GET /api/us/sectors`, **Then** a list of
   `SectorDto` (`Id`, `SectorName`, `StockCount`) ordered by `SectorName` is
   returned.
2. **Given** `testSampleOnly=true`, **When** listing sectors, **Then** only sectors
   that contain at least one `IsTestSample` stock are returned and `StockCount`
   counts only test-sample stocks.
3. **Given** `POST /api/{market}/sectors` with a `SectorName`, **Then** the sector
   is created (`StockCount=0`) and `201 Created` is returned with a `Location` to
   `GET /api/{market}/sectors/{id}`.
4. **Given** `PUT /api/{market}/sectors/{id}` with a name, **Then** the sector is
   renamed and `204` is returned; a missing id returns `404`.
5. **Given** `DELETE /api/{market}/sectors/{id}`, **When** the sector still has
   stocks (or does not exist), **Then** `400` is returned ("Sector has stocks or
   does not exist. Move stocks before deleting."); an empty, existing sector is
   deleted and `204` is returned.

### User Story 3 - Maintain stocks and re-home them in bulk (Priority: P2)

A client creates a stock, edits its mutable fields, deletes it, or moves many
stocks to a different sector at once.

**Why this priority**: Curating the universe (and fixing mis-classified stocks) is
the core editorial workflow behind both the SPA and analysis inputs.

**Acceptance Scenarios**:

1. **Given** `POST /api/{market}/stocks` with `Symbol`, `CompanyName`, `SectorId`,
   optional `BroadSector`, `IsFno`, **Then** the stock is created and `201 Created`
   is returned with the hydrated `StockDto` (including resolved `SectorName` and
   `MarketCap` when fundamentals exist).
2. **Given** `PUT /api/{market}/stocks/{id}` with an `UpdateStockRequest`, **Then**
   only the supplied (non-null) fields among `CompanyName`, `SectorId`,
   `BroadSector`, `IsFno`, `IsTestSample` are changed and `204` is returned; a
   missing id returns `404`.
3. **Given** `DELETE /api/{market}/stocks/{id}`, **Then** the stock is deleted and
   `204` is returned; a missing id returns `404`.
4. **Given** `POST /api/{market}/stocks/move` with `StockIds` and a
   `TargetSectorId`, **Then** every matching stock's `SectorId` is set in a single
   set-based update and `{ moved: <count> }` is returned.

### Edge Cases

- `StockDto.MarketCap` is `null` when a stock has no `Fundamentals` row; otherwise
  it is the fundamentals `MarketCap`.
- `GET /stocks` paging is applied after ordering by `CompanyName`; `TotalCount`
  reflects the filtered set, not the page.
- `MoveStocks` is a bulk `ExecuteUpdate` (no entity loading); it returns the count
  of rows affected and silently affects only ids that exist for the market.
- Sector delete guards on **any** stock referencing the sector; the stocks must be
  moved first (US2 AC5).
- All write endpoints assume valid `SectorId`/foreign keys; referential integrity
  is enforced by the database, not re-validated in the service.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Every catalog endpoint MUST be routed under `/api/{market}/...` and
  MUST reject any `{market}` other than `india` or `us` with `400`.
- **FR-002**: `GET /api/{market}/sectors` MUST return `SectorDto` (`Id`,
  `SectorName`, `StockCount`) ordered by `SectorName`, and MUST honor
  `testSampleOnly` by filtering to sectors with test-sample stocks and counting only
  those stocks.
- **FR-003**: `GET /api/{market}/sectors/{id}` MUST return the single `SectorDto`
  with full `StockCount`, or `404` when absent.
- **FR-004**: `POST /api/{market}/sectors` MUST create a sector from
  `CreateSectorRequest` and return `201` with a `Location` header.
- **FR-005**: `PUT /api/{market}/sectors/{id}` MUST rename an existing sector
  (`204`) or return `404`.
- **FR-006**: `DELETE /api/{market}/sectors/{id}` MUST refuse deletion with `400`
  when the sector has any stocks or does not exist, otherwise delete and return
  `204`.
- **FR-007**: `GET /api/{market}/stocks` MUST support `q`, `sectorId`, `page`
  (default 1), `pageSize` (default 50) and return a `PagedResult<StockDto>` ordered
  by `CompanyName`, where `q` matches `Symbol` or `CompanyName` (Contains) and
  combines with `sectorId`.
- **FR-008**: `GET /api/{market}/stocks/{id}` MUST return the hydrated `StockDto`
  (resolved `SectorName`, `MarketCap` from fundamentals when present) or `404`.
- **FR-009**: `POST /api/{market}/stocks` MUST create a stock from
  `CreateStockRequest` and return `201` with the re-hydrated `StockDto`.
- **FR-010**: `PUT /api/{market}/stocks/{id}` MUST apply only the non-null fields of
  `UpdateStockRequest` (`CompanyName`, `SectorId`, `BroadSector`, `IsFno`,
  `IsTestSample`) and return `204`, or `404` when absent.
- **FR-011**: `DELETE /api/{market}/stocks/{id}` MUST delete the stock (`204`) or
  return `404`.
- **FR-012**: `POST /api/{market}/stocks/move` MUST set `SectorId =
  TargetSectorId` for all `StockIds` in a single set-based update and return
  `{ moved: <count> }`.
- **FR-013**: All persistence MUST use query-only EF Core against the market's
  tables; NO EF migrations and no schema management from code (Constitution I/II).

### Key Entities

- **SectorDto**: `Id`, `SectorName`, `StockCount` (full or test-sample count).
- **StockDto**: `Id`, `Symbol`, `CompanyName`, `SectorId`, `SectorName?`,
  `BroadSector?`, `MarketCap?` (decimal), `IsFno`, `IsTestSample`.
- **CreateSectorRequest**: `SectorName`.
- **CreateStockRequest**: `Symbol`, `CompanyName`, `SectorId`, `BroadSector?`,
  `IsFno`.
- **UpdateStockRequest**: nullable `CompanyName`, `SectorId`, `BroadSector`,
  `IsFno`, `IsTestSample` (only non-null fields applied).
- **MoveStocksRequest**: `StockIds[]`, `TargetSectorId`.
- **PagedResult<T>**: `Items[]`, `TotalCount`, `Page`, `PageSize`.
- **Entities** (query-only): `IndianSector`/`USSector`, `IndianStock`/`USStock`,
  `IndianStockFundamentals`/`USStockFundamentals` (1:1 optional, supplies
  `MarketCap`).

## Success Criteria *(mandatory)*

- **SC-001**: Every route, verb, and status code documented here matches
  `SectorsController.cs` and `StocksController.cs` exactly.
- **SC-002**: Every `api.ts` sector/stock client function in `003-marketedge-spa`
  maps to one endpoint in this spec.
- **SC-003**: `testSampleOnly` sector filtering and counting behave as specified for
  both markets.
- **SC-004**: Sector delete is blocked while stocks reference it, and bulk move
  re-homes the selected stocks and reports the moved count.
- **SC-005**: No EF migration exists; all catalog persistence is query-only against
  dacpac-owned tables.

## Assumptions

- The four catalog tables and the two fundamentals tables are owned by the SQL
  dacpac project (`001`/`002` and the constitution describe schema ownership).
- The SPA (`003-marketedge-spa`) is the primary consumer; this spec is the backend
  contract that SC-002 of `003` refers to.
- Authentication/authorization is out of scope (none is enforced in the
  controllers as written).

## Out of Scope

- Stage 2 analysis orchestration and read-models (`001-stage2-analysis-api`).
- The worker and the algorithm (`002-stage2-analysis-worker`).
- The React SPA presentation (`003-marketedge-spa`).
- Fundamentals ingestion/refresh (only `MarketCap` is read here, never written).
