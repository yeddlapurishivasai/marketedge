<#
.SYNOPSIS
    Deploys the MarketEdge database schema (dacpac) and optionally seeds data.

.PARAMETER SqlServer
    Azure SQL server name (e.g., market-edge-dr-sql-server-01.database.windows.net)

.PARAMETER DatabaseName
    Target database name. Default: MarketEdge

.PARAMETER SqlUser
    SQL admin username.

.PARAMETER SqlPassword
    SQL admin password (prompted securely if not provided).

.PARAMETER SeedData
    If specified, runs SeedData.sql after schema deployment.

.PARAMETER Configuration
    Build configuration. Default: Release

.EXAMPLE
    .\deploy-database.ps1 -SqlServer "market-edge-dr-sql-server-01.database.windows.net" -SqlUser sqladmin -SeedData
#>

param(
    [string]$SqlServer = "market-edge-dr-sql-server-01.database.windows.net",

    [string]$DatabaseName = "MarketEdge",

    [string]$SqlUser = "sqladmin",

    [SecureString]$SqlPassword,

    [switch]$SeedData,

    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$DbProject = Join-Path $RepoRoot "src\MarketEdge.Database"
$DacpacPath = Join-Path $DbProject "bin\$Configuration\MarketEdge.Database.dacpac"
$SeedScript = Join-Path $DbProject "Scripts\SeedData.sql"

# Prompt for password if not provided
if (-not $SqlPassword) {
    $SqlPassword = Read-Host -Prompt "Enter SQL password for $SqlUser" -AsSecureString
}
$PlainPassword = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($SqlPassword)
)

# Determine if target is Azure SQL (needs SqlAzureV12 provider)
$isAzure = $SqlServer -match "\.database\.windows\.net"

# Build dacpac
Write-Host "Building database project..." -ForegroundColor Cyan
$buildArgs = @("-c", $Configuration)
if ($isAzure) {
    $buildArgs += "/p:DSP=Microsoft.Data.Tools.Schema.Sql.SqlAzureV12DatabaseSchemaProvider"
    Write-Host "  Target: Azure SQL Database" -ForegroundColor Yellow
} else {
    Write-Host "  Target: SQL Server (on-premises)" -ForegroundColor Yellow
}
Push-Location $DbProject
dotnet build @buildArgs
if ($LASTEXITCODE -ne 0) { throw "Database project build failed" }
Pop-Location

# Find sqlpackage
$sqlpackage = Get-Command sqlpackage -ErrorAction SilentlyContinue
if (-not $sqlpackage) {
    $sqlpackage = Join-Path $env:USERPROFILE ".dotnet\tools\sqlpackage.exe"
    if (-not (Test-Path $sqlpackage)) {
        throw "sqlpackage not found. Install with: dotnet tool install -g microsoft.sqlpackage"
    }
}

# Deploy dacpac
Write-Host "`nPublishing dacpac to $SqlServer/$DatabaseName..." -ForegroundColor Cyan
& $sqlpackage /Action:Publish `
    /SourceFile:$DacpacPath `
    /TargetServerName:$SqlServer `
    /TargetDatabaseName:$DatabaseName `
    /TargetUser:$SqlUser `
    /TargetPassword:$PlainPassword `
    /TargetTrustServerCertificate:True

if ($LASTEXITCODE -ne 0) { throw "dacpac deployment failed" }
Write-Host "Schema deployed successfully." -ForegroundColor Green

# Seed data
if ($SeedData) {
    Write-Host "`nSeeding data..." -ForegroundColor Cyan

    $sqlcmd = Get-Command sqlcmd -ErrorAction SilentlyContinue
    if (-not $sqlcmd) {
        $sqlcmd = "C:\Program Files\Microsoft SQL Server\Client SDK\ODBC\180\Tools\Binn\SQLCMD.EXE"
        if (-not (Test-Path $sqlcmd)) {
            throw "sqlcmd not found. Install SQL Server command line tools."
        }
    }

    & $sqlcmd -S $SqlServer -d $DatabaseName -U $SqlUser -P $PlainPassword -i $SeedScript -C
    if ($LASTEXITCODE -ne 0) { throw "Seed data failed" }
    Write-Host "Seed data loaded successfully." -ForegroundColor Green
}

Write-Host "`nDatabase deployment complete!" -ForegroundColor Green
