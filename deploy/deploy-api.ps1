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
    [Parameter(Mandatory)]
    [string]$ResourceGroup,

    [Parameter(Mandatory)]
    [string]$AppName,

    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ApiProject = Join-Path $RepoRoot "src\MarketEdge.Api"
$PublishDir = Join-Path $ApiProject "publish"
$ZipPath = Join-Path $env:TEMP "marketedge-api.zip"

# Build and publish
Write-Host "Building and publishing .NET API..." -ForegroundColor Cyan
Push-Location $ApiProject
dotnet publish -c $Configuration -o $PublishDir
if ($LASTEXITCODE -ne 0) { throw "dotnet publish failed" }
Pop-Location

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
