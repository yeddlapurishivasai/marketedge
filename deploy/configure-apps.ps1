<#
.SYNOPSIS
    Configures Azure App Service settings for MarketEdge API and Worker.

.PARAMETER ResourceGroup
    Azure resource group name.

.PARAMETER ApiAppName
    API App Service name.

.PARAMETER WorkerAppName
    Worker App Service name.

.PARAMETER SqlServer
    Azure SQL server FQDN (e.g., market-edge-dr-sql-server-01.database.windows.net)

.PARAMETER SqlUser
    SQL admin username.

.PARAMETER SqlPassword
    SQL admin password (prompted securely if not provided).

.PARAMETER StorageAccountName
    Azure Storage account name for queue messaging.

.EXAMPLE
    .\configure-apps.ps1 `
        -ResourceGroup "market-edge-dr-rg-01" `
        -ApiAppName "market-edge-dr-api-01" `
        -WorkerAppName "market-edge-dr-worker-01" `
        -SqlServer "market-edge-dr-sql-server-01.database.windows.net" `
        -SqlUser sqladmin `
        -StorageAccountName marketedgedrsa01
#>

param(
    [string]$ResourceGroup = "market-edge-dr-rg-01",

    [string]$ApiAppName = "market-edge-dr-api-01",

    [string]$WorkerAppName = "market-edge-dr-worker-01",

    [string]$SqlServer = "market-edge-dr-sql-server-01.database.windows.net",

    [string]$SqlUser = "sqladmin",

    [SecureString]$SqlPassword,

    [string]$StorageAccountName = "marketedgedrsa01"
)

$ErrorActionPreference = "Stop"

# Prompt for password if not provided
if (-not $SqlPassword) {
    $SqlPassword = Read-Host -Prompt "Enter SQL password for $SqlUser" -AsSecureString
}
$PlainPassword = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($SqlPassword)
)

# Get storage connection string
Write-Host "Retrieving storage connection string..." -ForegroundColor Cyan
$StorageConn = az storage account show-connection-string `
    --name $StorageAccountName `
    --resource-group $ResourceGroup `
    --query connectionString -o tsv

if (-not $StorageConn) { throw "Failed to retrieve storage connection string" }

# --- Configure API App Service ---
Write-Host "`nConfiguring API ($ApiAppName)..." -ForegroundColor Cyan

$apiSqlConn = "Server=$SqlServer;Database=MarketEdge;User Id=$SqlUser;Password=$PlainPassword;TrustServerCertificate=True;Encrypt=True;"

az webapp config connection-string set `
    --resource-group $ResourceGroup `
    --name $ApiAppName `
    --connection-string-type SQLAzure `
    --settings MarketEdge="$apiSqlConn" `
    --output none

az webapp config appsettings set `
    --resource-group $ResourceGroup `
    --name $ApiAppName `
    --settings `
        AzureStorage__ConnectionString="$StorageConn" `
        AzureStorage__QueueName="stage-analysis-jobs" `
    --output none

Write-Host "  API configured." -ForegroundColor Green

# --- Configure Worker App Service ---
Write-Host "`nConfiguring Worker ($WorkerAppName)..." -ForegroundColor Cyan

$workerSqlConn = "Driver={ODBC Driver 18 for SQL Server};Server=$SqlServer;Database=MarketEdge;Uid=$SqlUser;Pwd=$PlainPassword;Encrypt=yes;TrustServerCertificate=yes;"

az webapp config appsettings set `
    --resource-group $ResourceGroup `
    --name $WorkerAppName `
    --settings `
        AZURE_STORAGE_CONNECTION_STRING="$StorageConn" `
        QUEUE_NAME="stage-analysis-jobs" `
        SQL_CONNECTION_STRING="$workerSqlConn" `
        YFINANCE_BATCH_SIZE="50" `
        YFINANCE_BATCH_DELAY="4.0" `
        YFINANCE_MAX_RETRIES="3" `
        AZURE_LOG_LEVEL="ERROR" `
        SCM_DO_BUILD_DURING_DEPLOYMENT="true" `
    --output none

az webapp config set `
    --resource-group $ResourceGroup `
    --name $WorkerAppName `
    --startup-file "gunicorn --bind=0.0.0.0:8000 --workers=1 --threads=4 app:app" `
    --output none

Write-Host "  Worker configured." -ForegroundColor Green

Write-Host "`nAll app settings configured!" -ForegroundColor Green
