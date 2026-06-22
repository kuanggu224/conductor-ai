<#
Start the Conductor platform console.

Usage:
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1

Options:
  -BackendPort 8001            Use a different backend API port.
  -FrontendPort 4177           Use a different frontend static server port.
  -Open                        Open the demo URL after both servers are ready.
  -SkipCheck                   Skip frontend demo preflight checks before starting.
  -PreflightSmokePort 4179     Use a different temporary preflight smoke port.
  -PidFile <path>              Use a custom PID file path.
#>

param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 4176,
    [switch]$Open,
    [switch]$SkipCheck,
    [int]$PreflightSmokePort = 4178,
    [string]$PidFile = ""
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
Set-Location -LiteralPath $RepoRoot

if (-not $PidFile) {
    $PidFile = Join-Path $RepoRoot ".conductor\runtime\platform.pid.json"
}
$PidFile = [System.IO.Path]::GetFullPath($PidFile)
$RuntimeDir = Split-Path -Parent $PidFile
$LogDir = Join-Path $RuntimeDir "logs"

function Test-PortListening {
    param([Parameter(Mandatory = $true)][int]$Port)
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Test-RecordedProcessRunning {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    try {
        $payload = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    } catch {
        return $false
    }
    foreach ($name in @("backend", "frontend")) {
        $entry = $payload.PSObject.Properties[$name].Value
        if (-not $entry) { continue }
        $pidValue = [int]($entry.pid)
        if ($pidValue -gt 0 -and (Get-Process -Id $pidValue -ErrorAction SilentlyContinue)) {
            return $true
        }
    }
    return $false
}

function Wait-Port {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$Label,
        [int]$TimeoutSeconds = 20
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-PortListening -Port $Port) { return }
        Start-Sleep -Milliseconds 250
    }
    throw "$Label did not start listening on port $Port within $TimeoutSeconds seconds."
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python is required to start the Conductor platform."
}

if (Test-RecordedProcessRunning -Path $PidFile) {
    throw "Conductor platform appears to be running. Stop it first with scripts\stop.ps1."
}

if (Test-Path -LiteralPath $PidFile) {
    Remove-Item -LiteralPath $PidFile -Force
}

if (Test-PortListening -Port $BackendPort) {
    throw "Backend port $BackendPort is already in use."
}
if (Test-PortListening -Port $FrontendPort) {
    throw "Frontend port $FrontendPort is already in use."
}

if (-not $SkipCheck) {
    & (Join-Path $PSScriptRoot "demo-check.ps1") -StaticSmoke -StaticSmokePort $PreflightSmokePort
}

New-Item -ItemType Directory -Force -Path $RuntimeDir, $LogDir | Out-Null

$backendOut = Join-Path $LogDir "backend.out.log"
$backendErr = Join-Path $LogDir "backend.err.log"
$frontendOut = Join-Path $LogDir "frontend.out.log"
$frontendErr = Join-Path $LogDir "frontend.err.log"
Remove-Item -LiteralPath $backendOut, $backendErr, $frontendOut, $frontendErr -Force -ErrorAction SilentlyContinue

$backend = Start-Process `
    -FilePath "python" `
    -ArgumentList @("-m", "uvicorn", "app.board:app", "--host", "127.0.0.1", "--port", "$BackendPort") `
    -WorkingDirectory $RepoRoot `
    -RedirectStandardOutput $backendOut `
    -RedirectStandardError $backendErr `
    -WindowStyle Hidden `
    -PassThru

$frontend = Start-Process `
    -FilePath "python" `
    -ArgumentList @("-m", "http.server", "$FrontendPort", "-d", "frontend") `
    -WorkingDirectory $RepoRoot `
    -RedirectStandardOutput $frontendOut `
    -RedirectStandardError $frontendErr `
    -WindowStyle Hidden `
    -PassThru

try {
    Wait-Port -Port $BackendPort -Label "Backend API"
    Wait-Port -Port $FrontendPort -Label "Frontend"
} catch {
    foreach ($process in @($backend, $frontend)) {
        if ($process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
    throw
}

$demoUrl = "http://127.0.0.1:$FrontendPort/?demo=1"
$liveUrl = "http://127.0.0.1:$FrontendPort/#settings"
$apiUrl = "http://127.0.0.1:$BackendPort"

$payload = [ordered]@{
    started_at = (Get-Date).ToString("o")
    repo_root = "$RepoRoot"
    backend = [ordered]@{
        pid = $backend.Id
        port = $BackendPort
        url = $apiUrl
        stdout = $backendOut
        stderr = $backendErr
    }
    frontend = [ordered]@{
        pid = $frontend.Id
        port = $FrontendPort
        demo_url = $demoUrl
        live_url = $liveUrl
        stdout = $frontendOut
        stderr = $frontendErr
    }
}
$payload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $PidFile -Encoding UTF8

Write-Host ""
Write-Host "Conductor platform started."
Write-Host "  Demo UI: $demoUrl"
Write-Host "  Live UI: $liveUrl"
Write-Host "  API:     $apiUrl"
Write-Host "  PID:     $PidFile"
Write-Host ""
Write-Host "Stop with:"
Write-Host "  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop.ps1"
Write-Host ""

if ($Open) {
    Start-Process $demoUrl
}
