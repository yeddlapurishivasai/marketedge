# MarketEdge

A stock market management application for cataloging Indian (NSE) and US (NASDAQ/NYSE/AMEX) stocks organized by sectors.

## Repository Structure

```
src/
├── MarketEdge.Api/          # .NET 8 Web API + React SPA
└── MarketEdge.Database/     # SQL Server Database Project (dacpac)
```

## Projects

### src/MarketEdge.Api

- **Framework**: .NET 8 Web API
- **ORM**: Entity Framework Core (query-only — no migrations, no database schema management)
- **Frontend**: React 19 + TypeScript + Vite (in `clientapp/` subfolder)
- **UI Library**: Lucide React (icons), custom CSS with dark/light mode support
- **Routing**: react-router-dom v7
- **Architecture**: Controllers → Services → EF Core DbContext
- **API Pattern**: REST at `/api/{india|us}/sectors` and `/api/{india|us}/stocks`
- **Connection String**: configured in `appsettings.json`, uses Windows Auth + TrustServerCertificate

Key files:
- `Program.cs` — app setup, EF Core registration, SPA proxy config
- `Data/MarketEdgeDbContext.cs` — DbContext with DbSets (IndianSectors, IndianStocks, USSectors, USStocks)
- `Services/SectorService.cs` — CRUD for sectors using EF Core LINQ
- `Services/StockService.cs` — search/CRUD/move for stocks using EF Core LINQ
- `Controllers/SectorsController.cs` — REST endpoints for sectors
- `Controllers/StocksController.cs` — REST endpoints for stocks
- `clientapp/src/App.tsx` — full React SPA (all pages and components)
- `clientapp/src/api.ts` — typed API client
- `clientapp/src/styles.css` — CSS with CSS variables for light/dark themes

### src/MarketEdge.Database

- **Type**: SDK-style SQL Server Database Project
- **SDK**: Microsoft.Build.Sql v0.2.0-preview
- **Target**: Sql160 (SQL Server 2022)
- **Purpose**: Schema management via dacpac (build → publish)
- **Tables**: IndianSectors, IndianStocks, USSectors, USStocks
- **Seed Data**: `Scripts/SeedData.sql` (excluded from build, run manually after publish)

Key files:
- `MarketEdge.Database.sqlproj` — project file
- `Tables/*.sql` — table definitions (CREATE TABLE with indexes/FKs)
- `Scripts/SeedData.sql` — INSERT statements for all sectors and stocks

## Important Constraints

1. **No EF Core Migrations** — database schema is managed exclusively by the SQL project dacpac. Never add EF migrations.
2. **EF Core is query-only** — used for reading/writing data in the API, not for schema changes.
3. **Seed data lives in SQL** — `Scripts/SeedData.sql` is the source of truth for initial data, not JSON files.
4. **Connection string** uses `Trusted_Connection=True;TrustServerCertificate=True;` for local SQL Server with Windows Auth.
5. **Vite dev server** proxies `/api` requests to the .NET API (configured in `clientapp/vite.config.ts`).
6. **Port 5173** may be in use on the dev machine; Vite will auto-select 5174.

## Running Locally

```bash
# Start API (from src/MarketEdge.Api/)
dotnet run --urls "http://localhost:5062"

# Start UI dev server (from src/MarketEdge.Api/clientapp/)
npm run dev
```

## Database Setup

```bash
# Build dacpac
dotnet build src/MarketEdge.Database/

# Publish to local SQL Server
sqlpackage /Action:Publish /SourceFile:src/MarketEdge.Database/bin/Debug/MarketEdge.Database.dacpac /TargetServerName:localhost /TargetDatabaseName:MarketEdge /p:TrustServerCertificate=True

# Seed data
sqlcmd -S localhost -d MarketEdge -C -i src/MarketEdge.Database/Scripts/SeedData.sql
```

## Data Summary

- **Indian Market**: 123 sectors, 2,285 stocks (sourced from yfinance industry classifications)
- **US Market**: 153 sectors, 6,368 stocks (sourced from NASDAQ Stock Screener API)
