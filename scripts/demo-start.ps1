<#
Start the frontend offline demo server.

Usage:
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-start.ps1

Options:
  -Port 4177      Use a different static server port.
  -Open           Open the demo URL in the default browser after preflight.
  -SkipCheck      Skip demo preflight checks before starting the server.
#>

param(
    [int]$Port = 4176,
    [switch]$Open,
    [switch]$SkipCheck
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
Set-Location -LiteralPath $RepoRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python is required to start the frontend demo server."
}

if (-not $SkipCheck) {
    & (Join-Path $PSScriptRoot "demo-check.ps1")
}

if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use. Stop the existing process or start with -Port <another-port>."
}

$Url = "http://127.0.0.1:$Port/?demo=1"

Write-Host ""
Write-Host "Conductor demo URL:"
Write-Host "  $Url"
Write-Host ""
if ($Open) {
    Start-Process $Url
}
Write-Host "Press Ctrl+C to stop the static server."
Write-Host ""

python -m http.server $Port -d frontend
