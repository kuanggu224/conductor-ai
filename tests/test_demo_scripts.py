"""Smoke tests for Windows demo helper scripts."""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_is_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def _stop_python_listener(powershell: str, repo_root: Path, port: int) -> None:
    command = (
        f"$connections = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue; "
        "foreach ($connection in $connections) { "
        "$process = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue; "
        "if ($process -and $process.ProcessName -like 'python*') { Stop-Process -Id $process.Id -Force } "
        "}"
    )
    subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=repo_root,
        text=True,
        capture_output=True,
        timeout=20,
    )


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


def test_demo_start_runs_static_smoke_preflight() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = (repo_root / "scripts" / "demo-start.ps1").read_text(encoding="utf-8")

    assert "[int]$PreflightSmokePort = 4178" in script
    assert "-StaticSmoke -StaticSmokePort $PreflightSmokePort" in script
    assert "-SkipCheck" in script


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


def test_demo_start_runs_preflight_then_serves_demo() -> None:
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
    server_port = _free_tcp_port()
    smoke_port = _free_tcp_port()
    while smoke_port == server_port:
        smoke_port = _free_tcp_port()

    process = subprocess.Popen(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(repo_root / "scripts" / "demo-start.ps1"),
            "-Port",
            str(server_port),
            "-PreflightSmokePort",
            str(smoke_port),
        ],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output = ""
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                raise AssertionError(f"demo-start exited before serving:\n{output}")
            if _port_is_listening(server_port):
                break
            time.sleep(0.25)
        else:
            output = process.stdout.read() if process.stdout else ""
            raise AssertionError(f"demo-start did not listen on {server_port}:\n{output}")

        assert _port_is_listening(server_port)
    finally:
        _stop_python_listener(powershell, repo_root, server_port)
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                output, _ = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = process.communicate(timeout=10)

    assert "Demo preflight passed." in output
    assert "Running frontend static HTTP smoke test" in output
    assert "Conductor demo URL:" in output
    assert f"http://127.0.0.1:{server_port}/?demo=1" in output
