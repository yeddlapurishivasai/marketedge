# API Contracts: Sector & Stock Catalog (Backtracked)

Source: `SectorsController.cs`, `StocksController.cs`, `SectorService.cs`,
`StockService.cs`, `Models/Dtos.cs`, `Models/Entities.cs`. All routes are prefixed
`/api/{market}` where `market ∈ {india, us}`; any other value yields
`400 "Market must be 'india' or 'us'"`.

## Sectors — `/api/{market}/sectors`

| Verb | Path | Query/Body | Success | Errors |
|------|------|-----------|---------|--------|
| GET | `/` | `testSampleOnly=false` | `200 SectorDto[]` (ordered by `SectorName`) | `400` bad market |
| GET | `/{id}` | — | `200 SectorDto` | `400`, `404` |
| POST | `/` | `CreateSectorRequest` | `201 SectorDto` + `Location` → `GET /{id}` | `400` |
| PUT | `/{id}` | `CreateSectorRequest` | `204` | `400`, `404` |
| DELETE | `/{id}` | — | `204` | `400` (has stocks / missing), bad market |

- `testSampleOnly=true` → only sectors containing ≥1 `IsTestSample` stock;
  `StockCount` counts only test-sample stocks.
- DELETE is refused (`400`) when any stock references the sector.

## Stocks — `/api/{market}/stocks`

| Verb | Path | Query/Body | Success | Errors |
|------|------|-----------|---------|--------|
| GET | `/` | `q?`, `sectorId?`, `page=1`, `pageSize=50` | `200 PagedResult<StockDto>` | `400` |
| GET | `/{id}` | — | `200 StockDto` | `400`, `404` |
| POST | `/` | `CreateStockRequest` | `201 StockDto` + `Location` → `GET /{id}` | `400` |
| PUT | `/{id}` | `UpdateStockRequest` | `204` | `400`, `404` |
| DELETE | `/{id}` | — | `204` | `400`, `404` |
| POST | `/move` | `MoveStocksRequest` | `200 { moved: <int> }` | `400` |

- List ordering: `CompanyName` asc; `q` matches `Symbol` OR `CompanyName`
  (Contains); `q` and `sectorId` combine with AND. `TotalCount` is the filtered
  total (pre-paging).
- PUT applies only non-null fields of `UpdateStockRequest`.
- `/move` is a single set-based `ExecuteUpdate` (`SectorId = TargetSectorId`) over
  `StockIds`; returns the affected row count.

## DTOs

### SectorDto
`id: int, sectorName: string, stockCount: int`

### StockDto
`id: int, symbol: string, companyName: string, sectorId: int, sectorName: string?,
broadSector: string?, marketCap: decimal?, isFno: bool, isTestSample: bool`
- `marketCap` is `null` when the stock has no `Fundamentals` row.
- `sectorName` is resolved from the related sector.

### CreateSectorRequest
`sectorName: string`

### CreateStockRequest
`symbol: string, companyName: string, sectorId: int, broadSector: string?,
isFno: bool`

### UpdateStockRequest (all optional; only non-null applied)
`companyName: string?, sectorId: int?, broadSector: string?, isFno: bool?,
isTestSample: bool?`

### MoveStocksRequest
`stockIds: int[], targetSectorId: int`

### PagedResult<T>
`items: T[], totalCount: int, page: int, pageSize: int`

## Persistence rules (as implemented)

- Market selects the table family (`IndianSectors`/`USSectors`,
  `IndianStocks`/`USStocks`) — uniform code path per Constitution India/US symmetry.
- All reads/writes use query-only EF Core; no migrations, schema owned by the dacpac.
- `MarketCap` is read from the optional 1:1 `*StockFundamentals` table; the catalog
  never writes fundamentals.
