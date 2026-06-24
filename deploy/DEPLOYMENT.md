# MarketEdge — Azure Deployment Guide

All scripts live in `deploy/` and default to the **`dr`** environment, so you can run
most of them with **no arguments**. Override any parameter to target a different env.

## Target resources (defaults)

| Resource | Value |
|----------|-------|
| Subscription | `5bfc6247-f6ee-4d64-8651-711aac18f8f0` (Visual Studio Enterprise with MSDN) |
| Resource Group | `market-edge-dr-rg-01` |
| SQL Server | `market-edge-dr-sql-server-01` (FQDN `…database.windows.net`) |
| SQL Database | `MarketEdge` |
| SQL Admin User | `sqladmin` |
| Storage Account | `marketedgedrsa01` |
| Storage Queue | `stage-analysis-jobs` |
| App Service Plan | `market-edge-dr-asp-01` |
| API App Service | `market-edge-dr-api-01` (also serves the React UI) |
| Worker App Service | `market-edge-dr-worker-01` |

## Prerequisites

- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) — `az login`
- [.NET 8 SDK](https://dotnet.microsoft.com/download/dotnet/8.0)
- [sqlpackage](https://learn.microsoft.com/sql/tools/sqlpackage/sqlpackage-download) — `dotnet tool install -g microsoft.sqlpackage`
- [sqlcmd](https://learn.microsoft.com/sql/tools/sqlcmd/sqlcmd-utility) (only for `-SeedData`)
- Node.js (the API publish builds the React app)
- PowerShell 7+

```powershell
az login
az account set --subscription 5bfc6247-f6ee-4d64-8651-711aac18f8f0
```

---

## Scripts

### 1. Delete the database — `delete-database.ps1`

Permanently deletes the Azure SQL database (schema + data). Verifies the DB exists and
prompts for confirmation (type the database name); pass `-Force` to skip the prompt.

```powershell
# Delete MarketEdge on the dr server (interactive confirm)
.\deploy\delete-database.ps1

# Non-interactive
.\deploy\delete-database.ps1 -Force

# Different target
.\deploy\delete-database.ps1 `
    -ResourceGroup market-edge-dr-rg-01 `
    -SqlServerName market-edge-dr-sql-server-01 `
    -DatabaseName MarketEdge
```

> Note: the server also has a `market-edge-dr-db-01` database. The app uses `MarketEdge`
> (the default). Pass `-DatabaseName market-edge-dr-db-01` only if you intend to delete that one.

### 2. Publish the dacpac — `deploy-database.ps1`

Builds the SQL project (auto-selects the Azure SQL provider) and publishes the schema via
`sqlpackage`. Add `-SeedData` to also run `Scripts/SeedData.sql`. Prompts for the SQL password.

```powershell
# Schema only
.\deploy\deploy-database.ps1

# Schema + seed data
.\deploy\deploy-database.ps1 -SeedData
```

Typical "fresh database" flow:

```powershell
.\deploy\delete-database.ps1 -Force
.\deploy\deploy-database.ps1 -SeedData
```

> The Azure SQL **database resource** must exist for `sqlpackage` to publish into it. If you
> deleted it, recreate the empty DB first (see *Infrastructure setup*) before publishing.

### 3. Publish API + UI + Worker — `deploy-apps.ps1`

Publishes application code only (no database). The API publish builds the React frontend and
bundles it into `wwwroot`, so this single script covers **API + UI + Worker**.

```powershell
# Publish API (with UI) and Worker
.\deploy\deploy-apps.ps1

# Only the worker / only the API
.\deploy\deploy-apps.ps1 -SkipApi
.\deploy\deploy-apps.ps1 -SkipWorker

# First-time deploy: also (re)apply app settings, then publish
.\deploy\deploy-apps.ps1 -Configure -SqlUser sqladmin
```

App settings/connection strings are **not** changed unless you pass `-Configure`
(only needed on first deploy or when settings change).

---

## Other scripts

| Script | Purpose |
|--------|---------|
| `deploy-api.ps1` | Build + publish only the .NET API (incl. React UI). |
| `deploy-worker.ps1` | Package + publish only the Python worker. |
| `configure-apps.ps1` | Set connection strings + app settings for API and Worker. |
| `deploy-all.ps1` | End-to-end: database → settings → API → Worker (use `-Skip*` flags). |

```powershell
# Everything in one shot (prompts for SQL password)
.\deploy\deploy-all.ps1 -SeedData
```

---

## App settings reference

### API (.NET)

| Setting | Description |
|---------|-------------|
| `ConnectionStrings:MarketEdge` | SQL connection string (set as SQLAzure connection string) |
| `AzureStorage__ConnectionString` | Azure Storage connection string |
| `AzureStorage__QueueName` | Queue name (`stage-analysis-jobs`) |

### Worker (Python)

| Setting | Description | Default |
|---------|-------------|---------|
| `SQL_CONNECTION_STRING` | ODBC connection string for SQL Server | — |
| `AZURE_STORAGE_CONNECTION_STRING` | Azure Storage connection string | — |
| `QUEUE_NAME` | Queue name | `stage-analysis-jobs` |
| `YFINANCE_BATCH_SIZE` | Symbols per yfinance batch download | `50` |
| `YFINANCE_BATCH_DELAY` | Seconds between batches | `4.0` |
| `YFINANCE_MAX_RETRIES` | Max retries per batch | `3` |
| `AZURE_LOG_LEVEL` | Azure SDK log level | `ERROR` |

---

## Infrastructure setup (one-time)

If creating resources from scratch (or recreating an empty DB after delete):

```powershell
$RG = "market-edge-dr-rg-01"
$LOCATION = "centralus"

# Resource Group
az group create --name $RG --location $LOCATION

# SQL Server (replace <password>)
az sql server create --resource-group $RG --name market-edge-dr-sql-server-01 `
    --admin-user sqladmin --admin-password <password> --location $LOCATION

# Public access + firewall (allow Azure services)
az sql server update --resource-group $RG --name market-edge-dr-sql-server-01 `
    --enable-public-network true
az sql server firewall-rule create --resource-group $RG `
    --server market-edge-dr-sql-server-01 --name AllowAzureServices `
    --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0

# SQL Database (empty; dacpac publishes schema into it)
az sql db create --resource-group $RG --server market-edge-dr-sql-server-01 `
    --name MarketEdge --service-objective S0

# Storage Account + Queue
az storage account create --name marketedgedrsa01 --resource-group $RG `
    --location $LOCATION --sku Standard_LRS --kind StorageV2
$CONN = az storage account show-connection-string --name marketedgedrsa01 `
    --resource-group $RG --query connectionString -o tsv
az storage queue create --name stage-analysis-jobs --connection-string $CONN

# App Service Plan (Linux, B1)
az appservice plan create --resource-group $RG --name market-edge-dr-asp-01 `
    --location $LOCATION --sku B1 --is-linux

# API + Worker App Services
az webapp create --resource-group $RG --plan market-edge-dr-asp-01 `
    --name market-edge-dr-api-01 --runtime "DOTNETCORE:8.0"
az webapp create --resource-group $RG --plan market-edge-dr-asp-01 `
    --name market-edge-dr-worker-01 --runtime "PYTHON:3.12"
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| dacpac fails publishing to Azure SQL | Script auto-detects and uses the `SqlAzureV12` provider |
| "Deny Public Network Access" on SQL | `az sql server update --enable-public-network true` |
| Worker not processing queue messages | Check `AZURE_STORAGE_CONNECTION_STRING` and `QUEUE_NAME` |
| yfinance throttling errors | Increase `YFINANCE_BATCH_DELAY` (default 4s) |
| Wrong subscription | `az account set --subscription 5bfc6247-f6ee-4d64-8651-711aac18f8f0` |
| Tail logs | `az webapp log tail --resource-group market-edge-dr-rg-01 --name <app>` |
