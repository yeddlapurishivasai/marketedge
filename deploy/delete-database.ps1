<#
.SYNOPSIS
    Deletes the MarketEdge database from an Azure SQL server.

.DESCRIPTION
    Destructive. Permanently removes the target database (all schema + data).
    Defaults target the 'dr' environment in resource group market-edge-dr-rg-01.
    After deleting, re-run deploy-database.ps1 to recreate schema (and -SeedData to seed).

.PARAMETER ResourceGroup
    Azure resource group name. Default: market-edge-dr-rg-01

.PARAMETER SqlServerName
    Azure SQL server short name (NOT the FQDN). Default: market-edge-dr-sql-server-01

.PARAMETER DatabaseName
    Database to delete. Default: MarketEdge

.PARAMETER Subscription
    Azure subscription id. Default: 5bfc6247-f6ee-4d64-8651-711aac18f8f0

.PARAMETER Force
    Skip the interactive confirmation prompt.

.EXAMPLE
    .\delete-database.ps1

.EXAMPLE
    .\delete-database.ps1 -DatabaseName MarketEdge -Force
#>

param(
    [string]$ResourceGroup = "market-edge-dr-rg-01",
    [string]$SqlServerName = "market-edge-dr-sql-server-01",
    [string]$DatabaseName  = "MarketEdge",
    [string]$Subscription  = "5bfc6247-f6ee-4d64-8651-711aac18f8f0",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

# Ensure Azure CLI is available
$az = Get-Command az -ErrorAction SilentlyContinue
if (-not $az) { throw "Azure CLI (az) not found. Install it and run 'az login'." }

Write-Host "=== Delete Azure SQL Database ===" -ForegroundColor Magenta
Write-Host "  Subscription : $Subscription"
Write-Host "  ResourceGroup: $ResourceGroup"
Write-Host "  SQL Server   : $SqlServerName"
Write-Host "  Database     : $DatabaseName" -ForegroundColor Yellow
Write-Host ""

# Verify the database exists before attempting deletion
Write-Host "Checking database exists..." -ForegroundColor Cyan
$exists = az sql db show `
    --subscription $Subscription `
    --resource-group $ResourceGroup `
    --server $SqlServerName `
    --name $DatabaseName `
    --query "name" -o tsv 2>$null

if (-not $exists) {
    Write-Host "Database '$DatabaseName' not found on '$SqlServerName' — nothing to delete." -ForegroundColor Yellow
    return
}

# Confirmation
if (-not $Force) {
    Write-Host "WARNING: This permanently deletes '$DatabaseName' and ALL its data." -ForegroundColor Red
    $answer = Read-Host "Type the database name '$DatabaseName' to confirm"
    if ($answer -ne $DatabaseName) {
        Write-Host "Confirmation did not match. Aborted." -ForegroundColor Yellow
        return
    }
}

Write-Host "`nDeleting database '$DatabaseName'..." -ForegroundColor Cyan
az sql db delete `
    --subscription $Subscription `
    --resource-group $ResourceGroup `
    --server $SqlServerName `
    --name $DatabaseName `
    --yes

if ($LASTEXITCODE -ne 0) { throw "Failed to delete database '$DatabaseName'." }

Write-Host "`nDatabase '$DatabaseName' deleted." -ForegroundColor Green
Write-Host "Recreate it with: .\deploy-database.ps1 -SeedData" -ForegroundColor Green
