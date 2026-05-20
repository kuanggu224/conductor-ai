"""End-to-end coverage for the offline static web delivery profile."""

from __future__ import annotations

import json
from pathlib import Path

from app import run_project


def test_static_web_run_profile_delivers_ready_manifest(tmp_path, capsys) -> None:
    exit_code = run_project.main(
        [
            "--project-root",
            str(tmp_path),
            "--project-name",
            "flashcard-static-web",
            "--requirement",
            (
                "Build a browser-only flashcard study tracker. Users can add flashcards "
                "with question, answer, topic, and status; filter by topic and status; "
                "persist data after refresh using localStorage; delete cards; export CSV; "
                "no backend, no login, no payments."
            ),
            "--run-profile",
            "static_web",
            "--max-steps",
            "30",
            "--static-requirement-review",
            "--write-audit-bundle",
            "--replay-trace-format",
            "markdown",
        ]
    )

    output = capsys.readouterr().out
    payload = json.loads(output)
    project_root = Path(payload["project_root"])
    manifest_path = Path(payload["manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert payload["status"] == "completed"
    assert payload["run_profile"] == "static_web"
    assert payload["manifest_verification"]["passed"] is True
    assert payload["task_center_audit"]["passed"] is True
    assert payload["audit_bundle_verification"]["passed"] is True

    assert (project_root / "index.html").is_file()
    assert (project_root / "static" / "app.js").is_file()
    assert (project_root / "static" / "style.css").is_file()

    summary = manifest["summary"]
    assert summary["delivery_readiness_status"] == "ready"
    assert summary["delivery_readiness_score"] >= 90
    assert summary["scope_contract_status"] == "pass"
    assert summary["scope_contract_violation_count"] == 0
    assert summary["validation_failure_count"] == 0
    assert "index.html" in summary["changed_files"]
    assert "static/app.js" in summary["changed_files"]
    assert "static/style.css" in summary["changed_files"]

    implementation_execution = next(
        execution for execution in manifest["executions"] if execution["source_backend"] == "static_web_delivery"
    )
    assert implementation_execution["validation_success"] is True
    assert implementation_execution["validation_exit_code"] == 0

    tester_executions = [
        execution
        for execution in manifest["executions"]
        if execution["agent_id"] == "agent-tester" and execution.get("validation_command")
    ]
    assert tester_executions
    assert all(execution["validation_success"] is True for execution in tester_executions)
