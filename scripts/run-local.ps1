<#
.SYNOPSIS
    Runs the full MarketEdge stack locally (idempotently).

.DESCRIPTION
    Starts every moving piece of the app on this machine:

        * Azurite          - the Azure Storage emulator (queue backend)
        * SQL Server       - verified only (assumed already installed as a service)
        * MarketEdge.Api   - .NET 8 Web API            (http://localhost:5063)
        * clientapp        - React + Vite dev server    (http://localhost:5173)
        * MarketEdge.Worker- Flask queue listener        (http://localhost:8000)

    The script is IDEMPOTENT: on every run it first stops any instance it (or a
    previous run) started, frees the well-known ports, then starts fresh. Run it
    as many times as you like and you always end up with exactly one of each
    service running.

.PARAMETER Stop
    Stop everything and exit (do not start anything back up).

.PARAMETER PublishDb
    Build the dacpac and publish it to the local SQL Server before starting.

.PARAMETER SkipInstall
    Skip dependency setup (npm install / worker venv). Faster on repeat runs.

.EXAMPLE
    .\scripts\run-local.ps1
    .\scripts\run-local.ps1 -PublishDb
    .\scripts\run-local.ps1 -Stop
#>
[CmdletBinding()]
param(
    [switch] $Stop,
    [switch] $PublishDb,
    [switch] $SkipInstall
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# --------------------------------------------------------------------------- #
# Paths & configuration
# --------------------------------------------------------------------------- #
$RepoRoot  = Split-Path -Parent $PSScriptRoot
$StateDir  = Join-Path $RepoRoot '.local'
$LogDir    = Join-Path $StateDir 'logs'
$PidDir    = Join-Path $StateDir 'pids'
$AzuriteWs = Join-Path $StateDir 'azurite'
$VenvDir   = Join-Path $StateDir 'worker-venv'

$ApiProject  = Join-Path $RepoRoot 'src\MarketEdge.Api\MarketEdge.Api.csproj'
$ApiDir      = Join-Path $RepoRoot 'src\MarketEdge.Api'
$ClientAppDir= Join-Path $RepoRoot 'src\MarketEdge.Api\clientapp'
$WorkerDir   = Join-Path $RepoRoot 'src\MarketEdge.Worker'
$DbProject   = Join-Path $RepoRoot 'src\MarketEdge.Database\MarketEdge.Database.sqlproj'

$ApiPort     = 5063
$VitePort    = 5173
$WorkerPort  = 8000
$BlobPort    = 10000
$QueuePort   = 10001
$TablePort   = 10002

# Every port the script owns; cleared on each (re)start to kill stragglers.
$OwnedPorts  = @($ApiPort, $VitePort, $WorkerPort, $BlobPort, $QueuePort, $TablePort)

$SqlConnTarget   = @{ Server = 'localhost'; Database = 'MarketEdge' }

# --------------------------------------------------------------------------- #
# Logging helpers
# --------------------------------------------------------------------------- #
function Write-Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Info($m) { Write-Host "    $m" -ForegroundColor Gray }
function Write-Ok($m)   { Write-Host "    OK  $m" -ForegroundColor Green }
function Write-Warn2($m){ Write-Host "    !!  $m" -ForegroundColor Yellow }
function Write-Err2($m) { Write-Host "    XX  $m" -ForegroundColor Red }

# --------------------------------------------------------------------------- #
# Process / port helpers
# --------------------------------------------------------------------------- #
function Stop-Tree([int] $ProcessId) {
    # Recursively stop children first, then the process itself.
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue)
    foreach ($c in $children) { Stop-Tree -ProcessId ([int]$c.ProcessId) }
    try { Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue } catch {}
}

function Stop-ByPidFile([string] $Name) {
    $pidFile = Join-Path $PidDir "$Name.pid"
    if (Test-Path $pidFile) {
        $procId = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($procId -and (Get-Process -Id $procId -ErrorAction SilentlyContinue)) {
            Write-Info "Stopping $Name (pid $procId)"
            Stop-Tree -ProcessId ([int]$procId)
        }
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    }
}

function Clear-Port([int] $Port) {
    $conns = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    foreach ($conn in $conns) {
        $procId = [int]$conn.OwningProcess
        if ($procId -le 4) { continue }   # skip System / Idle
        $pname = (Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName
        Write-Info "Freeing port $Port (pid $procId $pname)"
        Stop-Tree -ProcessId $procId
    }
}

function Test-PortListening([int] $Port) {
    [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Wait-Port([int] $Port, [int] $TimeoutSec = 30, [string] $Name = 'service') {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-PortListening $Port) { return $true }
        Start-Sleep -Milliseconds 500
    }
    Write-Warn2 "$Name did not start listening on port $Port within ${TimeoutSec}s (check logs)."
    return $false
}

function Wait-Http([string] $Url, [int] $TimeoutSec = 60, [string] $Name = 'service') {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
            if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { return $true }
        } catch {}
        Start-Sleep -Milliseconds 750
    }
    Write-Warn2 "$Name did not respond at $Url within ${TimeoutSec}s (check logs)."
    return $false
}

function Start-Background {
    param(
        [string] $Name,
        [string] $Command,        # full command line, run through cmd.exe
        [string] $WorkingDir,
        [hashtable] $EnvVars = @{}
    )
    $log = Join-Path $LogDir "$Name.log"
    Remove-Item $log -Force -ErrorAction SilentlyContinue

    # Build a single cmd.exe line: set env vars, then run with merged stdout/stderr.
    $prefix = ''
    foreach ($k in $EnvVars.Keys) { $prefix += "set `"$k=$($EnvVars[$k])`" && " }
    $full = "$prefix$Command > `"$log`" 2>&1"

    $proc = Start-Process -FilePath $env:ComSpec -ArgumentList '/c', $full `
        -WorkingDirectory $WorkingDir -WindowStyle Hidden -PassThru
    $proc.Id | Out-File (Join-Path $PidDir "$Name.pid") -Encoding ascii
    Write-Ok "Started $Name (pid $($proc.Id)) -> $log"
}

function Stop-All {
    Write-Step 'Stopping any existing instances'
    foreach ($svc in 'worker', 'vite', 'api', 'azurite') { Stop-ByPidFile $svc }
    foreach ($p in $OwnedPorts) { Clear-Port $p }
    Write-Ok 'All known services stopped and ports freed.'
}

# --------------------------------------------------------------------------- #
# Dependency setup
# --------------------------------------------------------------------------- #
function Initialize-Dirs {
    foreach ($d in $StateDir, $LogDir, $PidDir, $AzuriteWs) {
        if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
    }
}

function Assert-Tools {
    Write-Step 'Checking required tools'
    $required = @{
        dotnet  = 'dotnet'
        node    = 'node'
        npm     = 'npm'
        python  = 'python'
        azurite = 'azurite'
    }
    $missing = @()
    foreach ($k in $required.Keys) {
        if (Get-Command $required[$k] -ErrorAction SilentlyContinue) { Write-Ok "$k found" }
        else { $missing += $required[$k]; Write-Err2 "$k NOT FOUND" }
    }
    if ($missing.Count -gt 0) {
        throw "Missing required tools: $($missing -join ', '). Install them and re-run. (azurite: 'npm install -g azurite')"
    }
}

function Initialize-NodeModules {
    if ($SkipInstall) { return }
    if (-not (Test-Path (Join-Path $ClientAppDir 'node_modules'))) {
        Write-Step 'Installing clientapp npm dependencies (first run)'
        Push-Location $ClientAppDir
        try { & npm install | Out-Host } finally { Pop-Location }
        Write-Ok 'npm install complete'
    } else {
        Write-Info 'clientapp node_modules present (skipping npm install)'
    }
}

function Initialize-WorkerVenv {
    $venvPython = Join-Path $VenvDir 'Scripts\python.exe'
    if (-not (Test-Path $venvPython)) {
        Write-Step 'Creating worker Python virtual environment (first run)'
        & python -m venv $VenvDir
    }
    if (-not $SkipInstall) {
        $marker = Join-Path $VenvDir '.requirements.installed'
        $req    = Join-Path $WorkerDir 'requirements.txt'
        $reqHash= (Get-FileHash $req -Algorithm MD5).Hash
        $have   = if (Test-Path $marker) { Get-Content $marker -Raw } else { '' }
        if ($have.Trim() -ne $reqHash) {
            Write-Step 'Installing worker Python dependencies'
            & $venvPython -m pip install --upgrade pip | Out-Host
            & $venvPython -m pip install -r $req | Out-Host
            $reqHash | Out-File $marker -Encoding ascii
            Write-Ok 'Worker dependencies installed'
        } else {
            Write-Info 'Worker dependencies up to date (skipping pip install)'
        }
    }
    return $venvPython
}

function Test-Sql {
    Write-Step 'Verifying SQL Server connectivity'
    try {
        $out = & sqlcmd -S $SqlConnTarget.Server -d $SqlConnTarget.Database -C -h -1 -W -Q 'SET NOCOUNT ON; SELECT 1' 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "SQL Server reachable (localhost / $($SqlConnTarget.Database))"
            return $true
        }
        Write-Warn2 "SQL Server check failed: $out"
    } catch {
        Write-Warn2 "SQL Server not reachable: $($_.Exception.Message)"
    }
    Write-Warn2 'API/worker DB calls will fail until SQL Server + the MarketEdge database are available.'
    return $false
}

function Publish-Database {
    if (-not (Get-Command sqlpackage -ErrorAction SilentlyContinue)) {
        Write-Warn2 'sqlpackage not found; skipping -PublishDb. Install with: dotnet tool install -g microsoft.sqlpackage'
        return
    }
    Write-Step 'Building dacpac'
    & dotnet build $DbProject -v q | Out-Host
    $dacpac = Join-Path $RepoRoot 'src\MarketEdge.Database\bin\Debug\MarketEdge.Database.dacpac'
    Write-Step 'Publishing dacpac to localhost\MarketEdge'
    & sqlpackage /Action:Publish /SourceFile:$dacpac /TargetServerName:localhost `
        /TargetDatabaseName:MarketEdge /p:TrustServerCertificate=True | Out-Host
    Write-Ok 'Database published'
}

# --------------------------------------------------------------------------- #
# Service starters
# --------------------------------------------------------------------------- #
function Start-Azurite {
    Write-Step 'Starting Azurite (storage emulator)'
    Start-Background -Name 'azurite' -WorkingDir $StateDir -Command (
        "azurite --silent --skipApiVersionCheck " +
        "--location `"$AzuriteWs`" " +
        "--blobHost 127.0.0.1 --blobPort $BlobPort " +
        "--queueHost 127.0.0.1 --queuePort $QueuePort " +
        "--tableHost 127.0.0.1 --tablePort $TablePort"
    )
    Wait-Port $QueuePort 30 'Azurite queue' | Out-Null
}

function Start-Api {
    Write-Step 'Starting MarketEdge.Api'
    Start-Background -Name 'api' -WorkingDir $ApiDir -EnvVars @{ ASPNETCORE_ENVIRONMENT = 'Development' } -Command (
        "dotnet run --project `"$ApiProject`" --urls http://localhost:$ApiPort"
    )
    Wait-Http "http://localhost:$ApiPort/swagger/index.html" 90 'API' | Out-Null
}

function Start-Vite {
    Write-Step 'Starting clientapp (Vite dev server)'
    Start-Background -Name 'vite' -WorkingDir $ClientAppDir -Command (
        "npm run dev -- --port $VitePort --strictPort"
    )
    Wait-Port $VitePort 60 'Vite' | Out-Null
}

function Start-Worker([string] $VenvPython) {
    Write-Step 'Starting MarketEdge.Worker'
    Start-Background -Name 'worker' -WorkingDir $WorkerDir -EnvVars @{
        AZURE_STORAGE_CONNECTION_STRING = 'UseDevelopmentStorage=true'
    } -Command "`"$VenvPython`" app.py"
    Wait-Http "http://localhost:$WorkerPort/health" 60 'Worker' | Out-Null
}

# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
Initialize-Dirs

if ($Stop) {
    Stop-All
    Write-Host "`nStopped. Run without -Stop to start again." -ForegroundColor Cyan
    return
}

Assert-Tools

# Idempotency: always stop anything already running before starting fresh.
Stop-All

if ($PublishDb) { Publish-Database }
Test-Sql | Out-Null

Initialize-NodeModules
$workerPython = Initialize-WorkerVenv

Start-Azurite
Start-Api
Start-Vite
Start-Worker -VenvPython $workerPython

Write-Host "`n========================================================" -ForegroundColor Green
Write-Host " MarketEdge is running locally" -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Green
Write-Host (" UI (Vite)   : http://localhost:{0}" -f $VitePort)
Write-Host (" API         : http://localhost:{0}  (swagger: /swagger)" -f $ApiPort)
Write-Host (" Worker      : http://localhost:{0}/health" -f $WorkerPort)
Write-Host (" Azurite     : blob {0} / queue {1} / table {2}" -f $BlobPort, $QueuePort, $TablePort)
Write-Host ''
Write-Host (" Logs        : {0}" -f $LogDir)
Write-Host  " Stop all    : .\scripts\run-local.ps1 -Stop"
Write-Host  " Restart     : .\scripts\run-local.ps1  (idempotent)"
Write-Host ''
