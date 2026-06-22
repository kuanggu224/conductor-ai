<#
Stop the Conductor platform console started by scripts\start.ps1.

Usage:
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop.ps1

Options:
  -PidFile <path>    Use a custom PID file path.
  -KeepPidFile       Keep the PID file after stopping processes.
#>

param(
    [string]$PidFile = "",
    [switch]$KeepPidFile
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
Set-Location -LiteralPath $RepoRoot

if (-not $PidFile) {
    $PidFile = Join-Path $RepoRoot ".conductor\runtime\platform.pid.json"
}
$PidFile = [System.IO.Path]::GetFullPath($PidFile)

if (-not (Test-Path -LiteralPath $PidFile)) {
    Write-Host "Conductor platform PID file was not found:"
    Write-Host "  $PidFile"
    Write-Host "Nothing to stop."
    return
}

try {
    $payload = Get-Content -Raw -LiteralPath $PidFile | ConvertFrom-Json
} catch {
    throw "Could not read PID file $PidFile`: $($_.Exception.Message)"
}

$stopped = 0
foreach ($name in @("frontend", "backend")) {
    $entry = $payload.PSObject.Properties[$name].Value
    if (-not $entry) { continue }
    $pidValue = [int]$entry.pid
    if ($pidValue -le 0) { continue }
    $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    if (-not $process) {
        Write-Host "$name process $pidValue is not running."
        continue
    }
    Write-Host "Stopping $name process $pidValue..."
    Stop-Process -Id $pidValue -Force
    $stopped += 1
}

if (-not $KeepPidFile) {
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

Write-Host "Conductor platform stopped. Processes stopped: $stopped."
