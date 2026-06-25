<#
.SYNOPSIS
    Deploys the MarketEdge Python worker to Azure App Service.

    The package includes the full worker tree (recursively, so the scanners/ subpackage
    ships) plus the MarketEdge.Ingestion project bundled under ingestion/, which the worker
    now runs out-of-process for data ingestion.

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
$IngestionDir = Join-Path $RepoRoot "src\MarketEdge.Ingestion"
$ZipPath = Join-Path $env:TEMP "marketedge-worker.zip"
$StageDir = Join-Path $env:TEMP "marketedge-worker-stage"

# Stage the worker tree (recursively) plus the bundled ingestion project, so the worker
# ships its scanners/ subpackage AND the ingestion CLI it now runs out-of-process.
Write-Host "Creating worker deployment package..." -ForegroundColor Cyan
if (Test-Path $ZipPath) { Remove-Item $ZipPath }
if (Test-Path $StageDir) { Remove-Item $StageDir -Recurse -Force }
New-Item -ItemType Directory -Path $StageDir | Out-Null

$excludeDirs  = @('__pycache__', '.venv', 'venv', '.pytest_cache')
$excludeFiles = @('.env', '.env.example')

function Copy-PySource {
    param([string]$Source, [string]$Dest)
    New-Item -ItemType Directory -Path $Dest -Force | Out-Null
    Get-ChildItem -Path $Source -Recurse -File | Where-Object {
        $_.Extension -ne '.pyc' -and
        $_.Name -notin $excludeFiles -and
        ($_.FullName.Split([IO.Path]::DirectorySeparatorChar) | Where-Object { $_ -in $excludeDirs }).Count -eq 0
    } | ForEach-Object {
        $rel = $_.FullName.Substring($Source.Length).TrimStart('\', '/')
        $target = Join-Path $Dest $rel
        $targetParent = Split-Path -Parent $target
        if (-not (Test-Path $targetParent)) { New-Item -ItemType Directory -Path $targetParent -Force | Out-Null }
        Copy-Item -Path $_.FullName -Destination $target -Force
    }
}

# Worker files at the package root, ingestion bundled under ingestion/ (matches
# ingestion_runner._resolve_cli() which looks for ./ingestion/cli.py).
Copy-PySource -Source $WorkerDir -Dest $StageDir
Copy-PySource -Source $IngestionDir -Dest (Join-Path $StageDir 'ingestion')

Compress-Archive -Path (Join-Path $StageDir '*') -DestinationPath $ZipPath -Force
Remove-Item $StageDir -Recurse -Force

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
