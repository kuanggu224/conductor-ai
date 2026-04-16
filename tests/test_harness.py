"""Harness 协议测试。"""

import sys

from conductor.harness.models import HarnessRequest
from conductor.harness.shell import ShellHarness


def test_shell_harness_runs_python_command(tmp_path) -> None:
    harness = ShellHarness()
    request = HarnessRequest(
        command=[sys.executable, "-c", "print('harness-ok')"],
        working_directory=str(tmp_path),
        timeout_seconds=10,
        description="smoke",
    )

    result = harness.run(request)

    assert result.success is True
    assert result.exit_code == 0
    assert "harness-ok" in result.stdout
    assert result.stderr == ""
    assert result.duration_ms >= 0


def test_shell_harness_tracks_workspace_changes(tmp_path) -> None:
    harness = ShellHarness()
    request = HarnessRequest(
        command=[sys.executable, "-c", "from pathlib import Path; Path('changed.txt').write_text('ok', encoding='utf-8')"],
        working_directory=str(tmp_path),
        timeout_seconds=10,
        description="change-smoke",
        track_workspace_changes=True,
        workspace_root=str(tmp_path),
    )

    result = harness.run(request)

    assert result.success is True
    assert "changed.txt" in result.changed_files


def test_shell_harness_streams_output_lines(tmp_path) -> None:
    harness = ShellHarness()
    streamed: list[tuple[str, str]] = []
    request = HarnessRequest(
        command=[sys.executable, "-c", "print('line-1'); print('line-2')"],
        working_directory=str(tmp_path),
        timeout_seconds=10,
        description="stream-smoke",
        stream_callback=lambda channel, line: streamed.append((channel, line.strip())),
    )

    result = harness.run(request)

    assert result.success is True
    assert streamed == [("stdout", "line-1"), ("stdout", "line-2")]
