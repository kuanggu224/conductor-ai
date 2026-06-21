"""Smoke tests for Windows demo helper scripts."""

from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path

import pytest


def test_demo_powershell_scripts_parse() -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is not available")

    repo_root = Path(__file__).resolve().parents[1]
    scripts = [
        repo_root / "scripts" / "demo-check.ps1",
        repo_root / "scripts" / "demo-start.ps1",
    ]
    command = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "foreach ($file in @(",
            *[f"  '{str(path)}'" for path in scripts],
            ")) {",
            "  $errors = $null",
            "  [System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw -LiteralPath $file), [ref]$errors) | Out-Null",
            "  if ($errors -and $errors.Count -gt 0) { throw ($errors | Out-String) }",
            "}",
        ]
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=repo_root,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_demo_check_static_smoke_covers_frontend_assets() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = (repo_root / "scripts" / "demo-check.ps1").read_text(encoding="utf-8")

    for asset in [
        "favicon.svg",
        "src/styles.css",
        "src/main.js",
        "src/demo.js",
        "src/api.js",
        "src/utils.js",
    ]:
        assert f'"{asset}"' in script

    assert "./src/styles.css" in script
    assert "./src/main.js" in script
    assert "Demo asset $path returned HTTP" in script


def test_demo_check_static_smoke_runs() -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    node = shutil.which("node")
    python = shutil.which("python")
    if powershell is None:
        pytest.skip("PowerShell is not available")
    if node is None:
        pytest.skip("node is not available")
    if python is None:
        pytest.skip("python is not available")

    repo_root = Path(__file__).resolve().parents[1]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(repo_root / "scripts" / "demo-check.ps1"),
            "-StaticSmoke",
            "-StaticSmokePort",
            str(port),
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "Demo preflight passed." in result.stdout
    assert "demo-start.ps1 -Open" in result.stdout
    assert "http://127.0.0.1:4176/?demo=1" in result.stdout
