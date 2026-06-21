<#
Run the frontend demo preflight checks.

Usage:
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-check.ps1

Optional:
  -FullBackendChecks  Also run the API delivery regression subset used by the demo path.
  -StaticSmoke        Start a temporary static server and verify demo HTML and frontend assets.
  -StaticSmokePort    Port used by the temporary static smoke server.
#>

param(
    [switch]$FullBackendChecks,
    [switch]$StaticSmoke,
    [int]$StaticSmokePort = 4178
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
Set-Location -LiteralPath $RepoRoot

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command
    )

    Write-Host "==> $Label"
    & $Command
}

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js is required for frontend demo checks."
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python is required for frontend demo checks."
}

Invoke-Step "Checking frontend JavaScript syntax" {
    node --check frontend\src\demo.js
    node --check frontend\src\main.js
    node --check frontend\src\api.js
    node --check frontend\src\utils.js
}

Invoke-Step "Running frontend demo smoke test" {
    python -m pytest `
        tests\test_frontend_demo.py `
        tests\test_frontend_demo_mode_guards.py `
        tests\test_demo_docs.py `
        -q
}

if ($FullBackendChecks) {
    Invoke-Step "Running demo API regression subset" {
        python -m pytest `
            tests\test_board_api.py `
            tests\test_runner_flow.py `
            tests\test_api_mock_delivery_e2e.py `
            tests\test_api_sqlite_delivery_e2e.py `
            -q
    }
}

if ($StaticSmoke) {
    Invoke-Step "Running frontend static HTTP smoke test" {
        if (Get-NetTCPConnection -LocalPort $StaticSmokePort -State Listen -ErrorAction SilentlyContinue) {
            throw "Port $StaticSmokePort is already in use. Retry with -StaticSmokePort <another-port>."
        }

        $stdoutPath = Join-Path $env:TEMP "conductor-demo-http-$StaticSmokePort.out.log"
        $stderrPath = Join-Path $env:TEMP "conductor-demo-http-$StaticSmokePort.err.log"
        Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
        $server = $null
        try {
            $server = Start-Process `
                -FilePath "python" `
                -ArgumentList @("-m", "http.server", "$StaticSmokePort", "-d", "frontend") `
                -WorkingDirectory $RepoRoot `
                -RedirectStandardOutput $stdoutPath `
                -RedirectStandardError $stderrPath `
                -WindowStyle Hidden `
                -PassThru

            $deadline = (Get-Date).AddSeconds(10)
            do {
                Start-Sleep -Milliseconds 200
                $listening = Get-NetTCPConnection -LocalPort $StaticSmokePort -State Listen -ErrorAction SilentlyContinue
                if ($server.HasExited) {
                    $stderr = if (Test-Path -LiteralPath $stderrPath) { Get-Content -LiteralPath $stderrPath -Raw } else { "" }
                    throw "Static smoke server exited early. $stderr"
                }
            } while (-not $listening -and (Get-Date) -lt $deadline)

            if (-not $listening) {
                throw "Static smoke server did not listen on port $StaticSmokePort."
            }

            $baseUrl = "http://127.0.0.1:$StaticSmokePort"
            $index = Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/?demo=1"
            if ($index.StatusCode -ne 200) {
                throw "Demo index returned HTTP $($index.StatusCode)."
            }
            if (-not $index.Content.Contains("favicon.svg")) {
                throw "Demo index does not reference favicon.svg."
            }
            if (-not $index.Content.Contains("./src/styles.css")) {
                throw "Demo index does not reference src/styles.css."
            }
            if (-not $index.Content.Contains("./src/main.js")) {
                throw "Demo index does not reference src/main.js."
            }

            $assetPaths = @(
                "favicon.svg",
                "src/styles.css",
                "src/main.js",
                "src/demo.js",
                "src/api.js",
                "src/utils.js"
            )
            foreach ($path in $assetPaths) {
                $asset = Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/$path"
                if ($asset.StatusCode -ne 200) {
                    throw "Demo asset $path returned HTTP $($asset.StatusCode)."
                }
            }
        }
        finally {
            if ($server -and -not $server.HasExited) {
                Stop-Process -Id $server.Id -Force
            }
        }
    }
}

Write-Host "Demo preflight passed."
Write-Host "Next:"
Write-Host "  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-start.ps1 -Open"
Write-Host "  http://127.0.0.1:4176/?demo=1"
