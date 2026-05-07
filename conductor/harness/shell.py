"""ShellHarness 实现。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from threading import Thread
from time import perf_counter

from conductor.harness.base import BaseHarness
from conductor.harness.models import HarnessRequest, HarnessResult
from conductor.io.encoding import utf8_subprocess_environment


class ShellHarness(BaseHarness):
    """基于 subprocess 的最小 shell harness。"""

    name = "shell"

    def run(self, request: HarnessRequest) -> HarnessResult:
        """执行受控 shell 命令并返回结构化结果。"""
        started = perf_counter()
        environment = utf8_subprocess_environment(request.environment)
        before_snapshot = (
            self._snapshot_workspace(Path(request.workspace_root or request.working_directory))
            if request.track_workspace_changes
            else {}
        )
        process = subprocess.Popen(
            request.command,
            cwd=request.working_directory,
            env=environment,
            stdin=subprocess.PIPE if request.stdin_text is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            bufsize=1,
        )
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        def read_stream(channel: str, stream, target: list[str]) -> None:
            for line in iter(stream.readline, ""):
                target.append(line)
                if request.stream_callback is not None:
                    request.stream_callback(channel, line)
            stream.close()

        stdout_thread = Thread(target=read_stream, args=("stdout", process.stdout, stdout_lines), daemon=True)
        stderr_thread = Thread(target=read_stream, args=("stderr", process.stderr, stderr_lines), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        if process.stdin is not None:
            process.stdin.write(request.stdin_text or "")
            process.stdin.close()
        timed_out = False
        try:
            exit_code = process.wait(timeout=request.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._terminate_process_tree(process)
            exit_code = process.wait()
            if request.stream_callback is not None:
                request.stream_callback("stderr", f"[timeout] command exceeded {request.timeout_seconds} seconds\n")
        stdout_thread.join(timeout=1.0)
        stderr_thread.join(timeout=1.0)
        duration_ms = int((perf_counter() - started) * 1000)
        after_snapshot = (
            self._snapshot_workspace(Path(request.workspace_root or request.working_directory))
            if request.track_workspace_changes
            else {}
        )
        return HarnessResult(
            success=exit_code == 0 and not timed_out,
            exit_code=exit_code,
            stdout="".join(stdout_lines),
            stderr="".join(stderr_lines),
            duration_ms=duration_ms,
            changed_files=self._collect_changed_files(before_snapshot, after_snapshot),
            timed_out=timed_out,
            failure_reason=self._failure_reason(exit_code, timed_out),
        )

    def _terminate_process_tree(self, process: subprocess.Popen) -> None:
        """Terminate the process and its children, which matters for CLI wrappers on Windows."""
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return
        process.kill()

    def _snapshot_workspace(self, workspace_root: Path) -> dict[str, tuple[int, int]]:
        """Capture a lightweight workspace snapshot for change tracking."""
        ignored_dirs = {
            ".git",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            "node_modules",
        }
        snapshot: dict[str, tuple[int, int]] = {}
        if not workspace_root.exists():
            return snapshot
        for path in workspace_root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in ignored_dirs for part in path.parts):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            snapshot[str(path.relative_to(workspace_root))] = (stat.st_mtime_ns, stat.st_size)
        return snapshot

    def _collect_changed_files(
        self,
        before_snapshot: dict[str, tuple[int, int]],
        after_snapshot: dict[str, tuple[int, int]],
    ) -> list[str]:
        """Diff two snapshots and return changed file paths."""
        if not before_snapshot and not after_snapshot:
            return []
        changed: list[str] = []
        all_paths = set(before_snapshot) | set(after_snapshot)
        for path in sorted(all_paths):
            if before_snapshot.get(path) != after_snapshot.get(path):
                changed.append(path)
        return changed

    def _failure_reason(self, exit_code: int, timed_out: bool) -> str:
        """Return a stable failure category for callers and logs."""
        if timed_out:
            return "timeout"
        if exit_code != 0:
            return "exit_code"
        return ""
