<#
.SYNOPSIS
    Full deployment: database schema + seed, API, and Worker to Azure.

.PARAMETER ResourceGroup
    Azure resource group name.

.PARAMETER SqlServer
    Azure SQL server FQDN.

.PARAMETER SqlUser
    SQL admin username.

.PARAMETER SqlPassword
    SQL admin password (prompted securely if not provided).

.PARAMETER StorageAccountName
    Azure Storage account name.

.PARAMETER ApiAppName
    API App Service name. Default: market-edge-dr-api-01

.PARAMETER WorkerAppName
    Worker App Service name. Default: market-edge-dr-worker-01

.PARAMETER SeedData
    If specified, seeds the database after schema deployment.

.PARAMETER SkipDatabase
    Skip database deployment.

.PARAMETER SkipApi
    Skip API deployment.

.PARAMETER SkipWorker
    Skip Worker deployment.

.EXAMPLE
    .\deploy-all.ps1 `
        -ResourceGroup "market-edge-dr-rg-01" `
        -SqlServer "market-edge-dr-sql-server-01.database.windows.net" `
        -SqlUser sqladmin `
        -StorageAccountName marketedgedrsa01 `
        -SeedData
#>

param(
    [string]$ResourceGroup = "market-edge-dr-rg-01",

    [string]$SqlServer = "market-edge-dr-sql-server-01.database.windows.net",

    [string]$SqlUser = "sqladmin",

    [SecureString]$SqlPassword,

    [string]$StorageAccountName = "marketedgedrsa01",

    [string]$ApiAppName = "market-edge-dr-api-01",
    [string]$WorkerAppName = "market-edge-dr-worker-01",

    [switch]$SeedData,
    [switch]$SkipDatabase,
    [switch]$SkipApi,
    [switch]$SkipWorker
)

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot

# Prompt for password once
if (-not $SqlPassword) {
    $SqlPassword = Read-Host -Prompt "Enter SQL password for $SqlUser" -AsSecureString
}

Write-Host "=== MarketEdge Full Deployment ===" -ForegroundColor Magenta
Write-Host ""

# 1. Database
if (-not $SkipDatabase) {
    Write-Host "--- Step 1: Database ---" -ForegroundColor Cyan
    $dbArgs = @{
        SqlServer     = $SqlServer
        SqlUser       = $SqlUser
        SqlPassword   = $SqlPassword
    }
    if ($SeedData) { $dbArgs.SeedData = $true }
    & "$ScriptDir\deploy-database.ps1" @dbArgs
    Write-Host ""
}

# 2. Configure app settings
Write-Host "--- Step 2: Configure App Settings ---" -ForegroundColor Cyan
& "$ScriptDir\configure-apps.ps1" `
    -ResourceGroup $ResourceGroup `
    -ApiAppName $ApiAppName `
    -WorkerAppName $WorkerAppName `
    -SqlServer $SqlServer `
    -SqlUser $SqlUser `
    -SqlPassword $SqlPassword `
    -StorageAccountName $StorageAccountName
Write-Host ""

# 3. Deploy API
if (-not $SkipApi) {
    Write-Host "--- Step 3: Deploy API ---" -ForegroundColor Cyan
    & "$ScriptDir\deploy-api.ps1" `
        -ResourceGroup $ResourceGroup `
        -AppName $ApiAppName
    Write-Host ""
}

# 4. Deploy Worker
if (-not $SkipWorker) {
    Write-Host "--- Step 4: Deploy Worker ---" -ForegroundColor Cyan
    & "$ScriptDir\deploy-worker.ps1" `
        -ResourceGroup $ResourceGroup `
        -AppName $WorkerAppName
    Write-Host ""
}

Write-Host "=== Deployment Complete ===" -ForegroundColor Magenta
Write-Host ""
Write-Host "API:    https://$ApiAppName.azurewebsites.net" -ForegroundColor Green
Write-Host "Worker: https://$WorkerAppName.azurewebsites.net" -ForegroundColor Green
