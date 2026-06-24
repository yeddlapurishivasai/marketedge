<#
.SYNOPSIS
    Deploys the MarketEdge Python worker to Azure App Service.

.PARAMETER ResourceGroup
    Azure resource group name.

.PARAMETER AppName
    Azure App Service name for the worker.

.EXAMPLE
    .\deploy-worker.ps1 -ResourceGroup "market-edge-dr-rg-01" -AppName "market-edge-dr-worker-01"
#>

param(
    [string]$ResourceGroup = "market-edge-dr-rg-01",

    [string]$AppName = "market-edge-dr-worker-01"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$WorkerDir = Join-Path $RepoRoot "src\MarketEdge.Worker"
$ZipPath = Join-Path $env:TEMP "marketedge-worker.zip"

# Create zip (exclude __pycache__, .env, .venv)
Write-Host "Creating worker deployment package..." -ForegroundColor Cyan
if (Test-Path $ZipPath) { Remove-Item $ZipPath }

$filesToInclude = Get-ChildItem -Path $WorkerDir -File | Where-Object {
    $_.Name -notin @('.env', '.env.example') -and
    $_.Extension -ne '.pyc'
}

Compress-Archive -Path $filesToInclude.FullName -DestinationPath $ZipPath -Force

# Deploy to Azure
Write-Host "Deploying to $AppName..." -ForegroundColor Cyan
az webapp deployment source config-zip `
    --resource-group $ResourceGroup `
    --name $AppName `
    --src $ZipPath `
    --timeout 600

if ($LASTEXITCODE -ne 0) { throw "Azure deployment failed" }

# Cleanup
Remove-Item $ZipPath -ErrorAction SilentlyContinue

Write-Host "`nWorker deployed to https://$AppName.azurewebsites.net" -ForegroundColor Green
