"""Harness 协议测试。"""

import sys

from conductor.harness.models import HarnessRequest
from conductor.harness.shell import ShellHarness
from conductor.harness.static_web import StaticWebHarness


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


def test_shell_harness_preserves_chinese_stdout_and_file_output(tmp_path) -> None:
    harness = ShellHarness()
    request = HarnessRequest(
        command=[
            sys.executable,
            "-c",
            (
                "import os, sys; "
                "from pathlib import Path; "
                "print(os.environ.get('PYTHONUTF8')); "
                "print('需求：费用报销审批'); "
                "Path('需求.txt').write_text('状态：已保存中文', encoding='utf-8')"
            ),
        ],
        working_directory=str(tmp_path),
        timeout_seconds=10,
        description="utf8-roundtrip",
        track_workspace_changes=True,
        workspace_root=str(tmp_path),
    )

    result = harness.run(request)

    assert result.success is True
    assert "1" in result.stdout.splitlines()
    assert "需求：费用报销审批" in result.stdout
    assert (tmp_path / "需求.txt").read_text(encoding="utf-8") == "状态：已保存中文"
    assert "需求.txt" in result.changed_files


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


def test_shell_harness_marks_timeout(tmp_path) -> None:
    harness = ShellHarness()
    request = HarnessRequest(
        command=[sys.executable, "-c", "import time; time.sleep(2)"],
        working_directory=str(tmp_path),
        timeout_seconds=0.1,
        description="timeout-smoke",
    )

    result = harness.run(request)

    assert result.success is False
    assert result.timed_out is True
    assert result.failure_reason == "timeout"


def test_static_web_harness_validates_basic_static_app(tmp_path) -> None:
    (tmp_path / "static").mkdir()
    (tmp_path / "index.html").write_text(
        """<!doctype html>
<html>
  <head>
    <title>Expense Tracker</title>
    <link rel="stylesheet" href="static/style.css">
  </head>
  <body>
    <form><input type="number" required><button type="submit">Add</button></form>
    <script src="static/app.js"></script>
  </body>
</html>
""",
        encoding="utf-8",
    )
    (tmp_path / "static" / "style.css").write_text("body { color: #111; }\n", encoding="utf-8")
    (tmp_path / "static" / "app.js").write_text(
        """
document.querySelector('form').addEventListener('submit', event => {
  event.preventDefault();
  const output = document.createElement('p');
  output.textContent = 'Added 12';
  document.body.appendChild(output);
});
""",
        encoding="utf-8",
    )

    result = StaticWebHarness().run(HarnessRequest(command=[], working_directory=str(tmp_path)))

    assert result.success is True
    assert result.exit_code == 0
    assert "Static Web Validation: PASS" in result.stdout
    assert "HTTP serving works" in result.stdout
    assert "JavaScript syntax valid: static/app.js" in result.stdout
    assert "Browser form interaction updated visible state" in result.stdout


def test_static_web_harness_fails_missing_local_asset(tmp_path) -> None:
    (tmp_path / "index.html").write_text(
        """<!doctype html>
<html>
  <head><title>Broken</title></head>
  <body><script src="static/missing.js"></script></body>
</html>
""",
        encoding="utf-8",
    )

    result = StaticWebHarness().run(HarnessRequest(command=[], working_directory=str(tmp_path)))

    assert result.success is False
    assert result.exit_code == 1
    assert result.failure_reason == "static_web_validation_failed"
    assert "Missing script asset: static/missing.js" in result.stdout


def test_static_web_harness_fails_when_form_submit_does_not_change_visible_state(tmp_path) -> None:
    (tmp_path / "static").mkdir()
    (tmp_path / "index.html").write_text(
        """<!doctype html>
<html>
  <head><title>Noop</title></head>
  <body>
    <form><input id="amount" type="number" required><button type="submit">Add</button></form>
    <script src="static/app.js"></script>
  </body>
</html>
""",
        encoding="utf-8",
    )
    (tmp_path / "static" / "app.js").write_text(
        "document.querySelector('form').addEventListener('submit', event => event.preventDefault());\n",
        encoding="utf-8",
    )

    result = StaticWebHarness().run(HarnessRequest(command=[], working_directory=str(tmp_path)))

    assert result.success is False
    assert "Browser form submit did not change visible page state" in result.stdout
