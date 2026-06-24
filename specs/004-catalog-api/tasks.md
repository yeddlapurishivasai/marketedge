---
description: "Validation/documentation tasks for the backtracked Sector & Stock Catalog API spec"
---

# Tasks: Sector & Stock Catalog — API (Backtracked)

**Input**: Design documents from `specs/004-catalog-api/`

**Prerequisites**: `plan.md`, `spec.md`, `contracts/api.md`

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (independent files/areas)
- **[Story]**: Which user story (US1–US3) the task validates

## Phase 1: Setup & Baseline

- [x] T001 Establish baseline: `dotnet build src/MarketEdge.Api/` succeeds (no API
  unit-test project exists; build is the baseline).
- [x] T002 [P] Confirm `MarketEdgeDbContext` exposes the DbSets used here
  (`IndianSectors`, `IndianStocks`, `USSectors`, `USStocks`, and the fundamentals
  sets) and that no EF migration files exist anywhere in the repo (Constitution II).

## Phase 2: Stock Browse & Search (US1)

- [x] T003 [US1] Validate `GET /api/{market}/stocks` against
  `StockService.SearchStocksAsync`: `q` matches `Symbol` OR `CompanyName`
  (Contains), `sectorId` filters, results order by `CompanyName`, and
  `PagedResult` carries `TotalCount/Page/PageSize` (defaults 1/50).
- [x] T004 [P] [US1] Validate `GET /api/{market}/stocks/{id}` hydration:
  `SectorName` resolved and `MarketCap` from optional `Fundamentals` (null when
  absent); `404` when missing.
- [x] T005 [P] [US1] Validate the `IsValidMarket` guard returns `400` for any market
  other than `india`/`us` on every catalog endpoint.

## Phase 3: Sector Taxonomy (US2)

- [x] T006 [US2] Validate `GET /api/{market}/sectors` ordering by `SectorName` and
  the `testSampleOnly` branch (filter to sectors with test-sample stocks; count only
  those) in `SectorService.GetSectorsAsync`.
- [x] T007 [P] [US2] Validate sector create/rename: `POST` returns `201` + `Location`
  to `GET /{id}`; `PUT` returns `204` or `404`.
- [x] T008 [US2] Validate `DELETE /api/{market}/sectors/{id}` guard:
  `400` when any stock references the sector or it is missing; `204` when empty
  (`SectorService.DeleteSectorAsync`).

## Phase 4: Stock Maintenance & Bulk Move (US3)

- [x] T009 [US3] Validate `POST /api/{market}/stocks` create + re-hydration and
  `PUT /api/{market}/stocks/{id}` partial update (only non-null fields of
  `UpdateStockRequest` applied), incl. `404` for missing.
- [x] T010 [P] [US3] Validate `DELETE /api/{market}/stocks/{id}` (`204`/`404`).
- [x] T011 [US3] Validate `POST /api/{market}/stocks/move`: single set-based
  `ExecuteUpdate` setting `SectorId=TargetSectorId` for `StockIds`, returning
  `{ moved: <count> }`.

## Phase 5: Contract Sync

- [x] T012 [P] Confirm `contracts/api.md` DTO shapes match `Models/Dtos.cs`
  (`SectorDto`, `StockDto`, `CreateSectorRequest`, `CreateStockRequest`,
  `UpdateStockRequest`, `MoveStocksRequest`, `PagedResult<T>`).
- [x] T013 [P] Confirm every sector/stock function in `src/api.ts`
  (`003-marketedge-spa`) maps to exactly one endpoint here, resolving SC-002 of 003.
- [x] T014 [P] Confirm india/us code paths differ only by table family (Constitution
  India/US symmetry) in both services.

## Phase 6: End-to-End Verification (optional, requires environment)

- [x] T015 With the API running against a seeded DB, exercise: list/search stocks,
  create→rename→delete a sector (incl. the non-empty-delete `400`), create→update→
  move→delete stocks, and confirm responses match `contracts/api.md` (SC-001..SC-004).

## Dependencies

- T001 precedes all validation tasks.
- T015 (E2E) depends on Phases 2–5 and a running API + seeded DB.

## Out of Scope (do NOT create tasks for)

- Stage 2 analysis orchestration/read-models (`001-stage2-analysis-api`).
- The worker and algorithm (`002-stage2-analysis-worker`).
- The React SPA presentation (`003-marketedge-spa`).
- Fundamentals ingestion/refresh (catalog only reads `MarketCap`).
