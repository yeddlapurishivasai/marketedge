# API Contracts: Stage 2 Analysis (Backtracked)

Source of truth: `src/MarketEdge.Api/Controllers/JobsController.cs`,
`Services/JobService.cs`, `Models/AnalysisDtos.cs`. `{market}` ∈ `india | us`.
All payloads are JSON. **UI is out of scope** — these are the raw HTTP contracts.

## Endpoints

### Generic job runs

| Method | Route | Query / Body | Success | Errors |
|--------|-------|--------------|---------|--------|
| GET | `/api/jobs` | `market?`, `jobType?`, `page=1`, `pageSize=20` | `200` `JobRunDto[]` (newest first, paged) | — |
| GET | `/api/jobs/{id}` | — | `200` `JobRunDto` | `404` not found |
| POST | `/api/jobs/{id}/cancel` | — | `200` `{ cancelled: true }` | `404` not found or already terminal |

### Stage 2 analysis

| Method | Route | Query / Body | Success | Errors |
|--------|-------|--------------|---------|--------|
| POST | `/api/{market}/analysis/trigger` | body `TriggerAnalysisRequest?` | `200` `{ runId: int }` | `400` bad market / bad or future `weekNumber` |
| GET | `/api/{market}/analysis/summary` | — | `200` `Stage2SummaryDto` | `400` bad market; `404` no completed run |
| GET | `/api/{market}/analysis/runs/{runId}/stocks` | `classification?`, `sectorId?` | `200` `StageAnalysisResultDto[]` | — (empty list if run unknown) |
| GET | `/api/{market}/analysis/runs/{runId}/sector-rotation` | — | `200` `SectorRotationDto[]` | — |
| GET | `/api/{market}/analysis/history` | `maxRuns=10` | `200` `Stage2HistoryDto[]` | `400` bad market |
| GET | `/api/{market}/analysis/rotation-history` | `maxRuns=12` | `200` `SectorRotationHistoryDto[]` | `400` bad market |

## Request: TriggerAnalysisRequest

```json
{
  "minMarketCap": 0,
  "maxMarketCap": 0,
  "sectorIds": [1, 2, 3],
  "limit": 100,
  "testSampleOnly": false,
  "weekNumber": "2026-W25",
  "retryFailedOnly": false,
  "force": false
}
```

- All fields optional; body itself may be omitted.
- `weekNumber` must match `^\d{4}-W\d{2}$` and not be in the future.
- `force` is deprecated and ignored.

## Queue message (API → worker)

Base64-encoded UTF-8 JSON placed on the `stage-analysis-jobs` queue:

```json
{
  "market": "india",
  "runId": 123,
  "weekNumber": "2026-W25",
  "triggeredBy": "manual",
  "minMarketCap": null,
  "maxMarketCap": null,
  "sectorIds": null,
  "limit": null,
  "testSampleOnly": null,
  "retryFailedOnly": null,
  "timestamp": "2026-06-24T00:00:00Z"
}
```

## Response DTOs

### JobRunDto
`id, jobType, market, weekNumber, status, progress, parameters (obj?),
metrics (obj?), errorMessage?, startedAt?, completedAt?, createdAt,
durationSeconds?` — `durationSeconds` set only when both `startedAt` and
`completedAt` exist.

### StageAnalysisResultDto
`id, runId, symbol, companyName, sectorId, sectorName, closePrice?, ma10?, ma30?,
marketCap?, isStage2, classification?, weeksInStage2?, rsScore?, rsRank?, rs1w?,
rs2w?, rs3w?, rsDelta1w?, rsDelta2w?, rsDelta3w?, momentumScore?, roc1w?, roc2w?,
roc3w?, quadrant?, adRatio?, adClassification?`.

### Stage2SummaryDto
`totalStocks, stage2Count, newAdditions, reEntries, continuing, removed,
bySector: SectorStage2CountDto[], top25: StageAnalysisResultDto[]`.

### SectorStage2CountDto
`sectorName, stage2Count, totalCount`.

### SectorRotationDto
`sectorName, sectorId, avgRSScore, avgRSDelta2w, quadrant, stockCount,
accumulatingCount, distributingCount`.

### Stage2HistoryDto
`runId, runDate, totalStage2, bySector: SectorStage2CountDto[]`. Note: in the
history projection only `stage2Count` is populated on each `bySector` entry;
`totalCount` is left `0` (it is filled only in the point-in-time summary).

### SectorRotationHistoryDto
`runId, runDate, sectors: SectorRotationDto[]`.

## Read-model rules (as implemented)

- **Summary / stocks / rotation** key off the run's `WeekNumber` and read ALL rows
  for that week (full upserted snapshot), not only rows stamped with that `runId`.
- **Stocks filter**: no `classification` → `IsStage2=true`; `classification=removed`
  → `Classification=removed`; otherwise `IsStage2=true AND Classification=<value>`.
  Optional `sectorId` narrows further. Ordering: `RSScore` desc, then
  `MomentumScore` desc — on the raw nullable values (no null coalescing), so
  null-RS rows fall to provider-default position here, unlike the summary `top25`
  which coalesces nulls to `0`.
- **Quadrant** (sector aggregate): `(avgRSScore>0, avgRSDelta2w>0)` →
  `leading | weakening | lagging | improving`.
- **History / rotation-history** collapse the `JobRuns` audit log to the latest
  completed run per `WeekNumber`, then order ascending by run date.
