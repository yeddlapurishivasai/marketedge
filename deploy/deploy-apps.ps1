<#
.SYNOPSIS
    Publishes the MarketEdge API (including the React UI) and the Python Worker to Azure.

.DESCRIPTION
    Deploys application code only (no database). The API publish builds the React
    frontend (clientapp) and bundles it into wwwroot, so this covers API + UI + Worker.
    Defaults target the 'dr' environment in resource group market-edge-dr-rg-01.

    App settings / connection strings are NOT changed here. Use -Configure (and supply
    -SqlUser / SQL password + storage account) to also (re)apply settings via
    configure-apps.ps1 — only needed on first deploy or when settings change.

.PARAMETER ResourceGroup
    Azure resource group name. Default: market-edge-dr-rg-01

.PARAMETER ApiAppName
    API App Service name. Default: market-edge-dr-api-01

.PARAMETER WorkerAppName
    Worker App Service name. Default: market-edge-dr-worker-01

.PARAMETER Configuration
    Build configuration. Default: Release

.PARAMETER SkipApi
    Skip the API (+UI) deployment.

.PARAMETER SkipWorker
    Skip the Worker deployment.

.PARAMETER Configure
    Also (re)apply app settings via configure-apps.ps1 before deploying.

.PARAMETER SqlServer
    Azure SQL server FQDN (used only with -Configure).
    Default: market-edge-dr-sql-server-01.database.windows.net

.PARAMETER SqlUser
    SQL admin username (used only with -Configure). Default: sqladmin

.PARAMETER SqlPassword
    SQL admin password (used only with -Configure; prompted securely if omitted).

.PARAMETER StorageAccountName
    Azure Storage account name (used only with -Configure). Default: marketedgedrsa01

.EXAMPLE
    # Publish API (UI) + Worker code with all dr defaults
    .\deploy-apps.ps1

.EXAMPLE
    # Publish only the worker
    .\deploy-apps.ps1 -SkipApi

.EXAMPLE
    # First-time deploy: apply settings, then publish both
    .\deploy-apps.ps1 -Configure -SqlUser sqladmin
#>

param(
    [string]$ResourceGroup  = "market-edge-dr-rg-01",
    [string]$ApiAppName     = "market-edge-dr-api-01",
    [string]$WorkerAppName  = "market-edge-dr-worker-01",
    [string]$Configuration  = "Release",

    [switch]$SkipApi,
    [switch]$SkipWorker,

    [switch]$Configure,
    [string]$SqlServer          = "market-edge-dr-sql-server-01.database.windows.net",
    [string]$SqlUser            = "sqladmin",
    [SecureString]$SqlPassword,
    [string]$StorageAccountName = "marketedgedrsa01"
)

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot

Write-Host "=== MarketEdge App Deployment (API + UI + Worker) ===" -ForegroundColor Magenta
Write-Host ""

# Optional: (re)apply app settings first
if ($Configure) {
    Write-Host "--- Configure App Settings ---" -ForegroundColor Cyan
    if (-not $SqlPassword) {
        $SqlPassword = Read-Host -Prompt "Enter SQL password for $SqlUser" -AsSecureString
    }
    & "$ScriptDir\configure-apps.ps1" `
        -ResourceGroup $ResourceGroup `
        -ApiAppName $ApiAppName `
        -WorkerAppName $WorkerAppName `
        -SqlServer $SqlServer `
        -SqlUser $SqlUser `
        -SqlPassword $SqlPassword `
        -StorageAccountName $StorageAccountName
    Write-Host ""
}

# API (+ UI)
if (-not $SkipApi) {
    Write-Host "--- Deploy API (+ React UI) ---" -ForegroundColor Cyan
    & "$ScriptDir\deploy-api.ps1" `
        -ResourceGroup $ResourceGroup `
        -AppName $ApiAppName `
        -Configuration $Configuration
    Write-Host ""
}

# Worker
if (-not $SkipWorker) {
    Write-Host "--- Deploy Worker ---" -ForegroundColor Cyan
    & "$ScriptDir\deploy-worker.ps1" `
        -ResourceGroup $ResourceGroup `
        -AppName $WorkerAppName
    Write-Host ""
}

Write-Host "=== App Deployment Complete ===" -ForegroundColor Magenta
Write-Host "API/UI: https://$ApiAppName.azurewebsites.net" -ForegroundColor Green
Write-Host "Worker: https://$WorkerAppName.azurewebsites.net" -ForegroundColor Green
