<#
.SYNOPSIS
    Builds and deploys the MarketEdge .NET API to Azure App Service.

.PARAMETER ResourceGroup
    Azure resource group name.

.PARAMETER AppName
    Azure App Service name for the API.

.PARAMETER Configuration
    Build configuration. Default: Release

.EXAMPLE
    .\deploy-api.ps1 -ResourceGroup "market-edge-dr-rg-01" -AppName "market-edge-dr-api-01"
#>

param(
    [string]$ResourceGroup = "market-edge-dr-rg-01",

    [string]$AppName = "market-edge-dr-api-01",

    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ApiProject = Join-Path $RepoRoot "src\MarketEdge.Api"
$deploymentId = [Guid]::NewGuid().ToString("N")
$PublishDir = Join-Path $env:TEMP "marketedge-api-publish-$deploymentId"
$ZipPath = Join-Path $env:TEMP "marketedge-api-$deploymentId.zip"

# Build and publish
Write-Host "Building and publishing .NET API..." -ForegroundColor Cyan
Push-Location $ApiProject
try {
    if (-not (Test-Path (Join-Path $ApiProject "clientapp\node_modules"))) {
        Push-Location (Join-Path $ApiProject "clientapp")
        try {
            npm ci
            if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
        } finally {
            Pop-Location
        }
    }
    dotnet publish -c $Configuration -o $PublishDir /p:SkipNpmInstall=true
    if ($LASTEXITCODE -ne 0) { throw "dotnet publish failed" }
} finally {
    Pop-Location
}

# Create zip
Write-Host "Creating deployment package..." -ForegroundColor Cyan
if (Test-Path $ZipPath) { Remove-Item $ZipPath }
Compress-Archive -Path "$PublishDir\*" -DestinationPath $ZipPath -Force

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
Remove-Item $PublishDir -Recurse -ErrorAction SilentlyContinue

Write-Host "`nAPI deployed to https://$AppName.azurewebsites.net" -ForegroundColor Green
