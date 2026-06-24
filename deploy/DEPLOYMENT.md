# MarketEdge — Azure Deployment Guide

## Prerequisites

- [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) (`az login`)
- [.NET 8 SDK](https://dotnet.microsoft.com/download/dotnet/8.0)
- [sqlpackage](https://learn.microsoft.com/en-us/sql/tools/sqlpackage/sqlpackage-download) (`dotnet tool install -g microsoft.sqlpackage`)
- [sqlcmd](https://learn.microsoft.com/en-us/sql/tools/sqlcmd/sqlcmd-utility) (for seeding data)
- PowerShell 7+

## Azure Resources

| Resource | Naming Convention | Example |
|----------|-------------------|---------|
| Resource Group | `market-edge-{env}-rg-01` | `market-edge-dr-rg-01` |
| SQL Server | `market-edge-{env}-sql-server-01` | `market-edge-dr-sql-server-01` |
| SQL Database | `MarketEdge` | `MarketEdge` |
| Storage Account | `marketedge{env}sa01` | `marketedgedrsa01` |
| Storage Queue | `stage-analysis-jobs` | `stage-analysis-jobs` |
| App Service Plan | `market-edge-{env}-asp-01` | `market-edge-dr-asp-01` |
| API App Service | `market-edge-{env}-api-01` | `market-edge-dr-api-01` |
| Worker App Service | `market-edge-{env}-worker-01` | `market-edge-dr-worker-01` |

## Quick Start — Full Deployment

```powershell
# Login and select the correct subscription
az login

# Run the full deployment (prompts for SQL password securely)
.\deploy\deploy-all.ps1 `
    -ResourceGroup "market-edge-dr-rg-01" `
    -SqlServer "market-edge-dr-sql-server-01.database.windows.net" `
    -SqlUser sqladmin `
    -StorageAccountName marketedgedrsa01 `
    -SeedData
```

This runs all steps: database schema → app settings → API deploy → worker deploy.

## Individual Scripts

### 1. Database (Schema + Seed)

```powershell
.\deploy\deploy-database.ps1 `
    -SqlServer "market-edge-dr-sql-server-01.database.windows.net" `
    -SqlUser sqladmin `
    -SeedData
```

- Builds the dacpac (auto-detects Azure SQL vs on-premises target)
- Publishes schema via `sqlpackage`
- Optionally seeds data from `Scripts/SeedData.sql`

### 2. API Deployment

```powershell
.\deploy\deploy-api.ps1 `
    -ResourceGroup "market-edge-dr-rg-01" `
    -AppName "market-edge-dr-api-01"
```

- Runs `dotnet publish` (builds React frontend too)
- Zips and deploys via `az webapp deployment source config-zip`

### 3. Worker Deployment

```powershell
.\deploy\deploy-worker.ps1 `
    -ResourceGroup "market-edge-dr-rg-01" `
    -AppName "market-edge-dr-worker-01"
```

- Packages Python source files (excludes `.env`, `__pycache__`)
- Deploys via zip; Azure installs `requirements.txt` automatically (`SCM_DO_BUILD_DURING_DEPLOYMENT=true`)

### 4. Configure App Settings

```powershell
.\deploy\configure-apps.ps1 `
    -ResourceGroup "market-edge-dr-rg-01" `
    -ApiAppName "market-edge-dr-api-01" `
    -WorkerAppName "market-edge-dr-worker-01" `
    -SqlServer "market-edge-dr-sql-server-01.database.windows.net" `
    -SqlUser sqladmin `
    -StorageAccountName marketedgedrsa01
```

Sets connection strings and environment variables for both apps.

## Skipping Steps

Use flags on `deploy-all.ps1` to skip steps:

```powershell
# Redeploy only the API (skip DB + worker)
.\deploy\deploy-all.ps1 ... -SkipDatabase -SkipWorker

# Redeploy only the worker
.\deploy\deploy-all.ps1 ... -SkipDatabase -SkipApi
```

## App Settings Reference

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

## Infrastructure Setup (One-Time)

If creating resources from scratch:

```powershell
$RG = "market-edge-dr-rg-01"
$LOCATION = "centralus"

# Resource Group
az group create --name $RG --location $LOCATION

# SQL Server (replace <password> with your chosen password)
az sql server create --resource-group $RG --name market-edge-dr-sql-server-01 `
    --admin-user sqladmin --admin-password <password> --location $LOCATION

# Enable public access + firewall
az sql server update --resource-group $RG --name market-edge-dr-sql-server-01 `
    --enable-public-network true
az sql server firewall-rule create --resource-group $RG `
    --server market-edge-dr-sql-server-01 --name AllowAzureServices `
    --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0

# SQL Database
az sql db create --resource-group $RG --server market-edge-dr-sql-server-01 `
    --name MarketEdge --service-objective S0

# Storage Account
az storage account create --name marketedgedrsa01 --resource-group $RG `
    --location $LOCATION --sku Standard_LRS --kind StorageV2

# Storage Queue
$CONN = az storage account show-connection-string --name marketedgedrsa01 `
    --resource-group $RG --query connectionString -o tsv
az storage queue create --name stage-analysis-jobs --connection-string $CONN

# App Service Plan (Linux, B1)
az appservice plan create --resource-group $RG --name market-edge-dr-asp-01 `
    --location $LOCATION --sku B1 --is-linux

# API App Service (.NET 8)
az webapp create --resource-group $RG --plan market-edge-dr-asp-01 `
    --name market-edge-dr-api-01 --runtime "DOTNETCORE:8.0"

# Worker App Service (Python 3.12)
az webapp create --resource-group $RG --plan market-edge-dr-asp-01 `
    --name market-edge-dr-worker-01 --runtime "PYTHON:3.12"
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| dacpac fails with "cannot publish to Azure SQL" | Script auto-detects and uses `SqlAzureV12` provider |
| "Deny Public Network Access" on SQL | Run `az sql server update --enable-public-network true` |
| Worker not processing queue messages | Check `AZURE_STORAGE_CONNECTION_STRING` and `QUEUE_NAME` in app settings |
| yfinance throttling errors | Increase `YFINANCE_BATCH_DELAY` (default 4s) |
| Logs | `az webapp log tail --resource-group <rg> --name <app>` |
