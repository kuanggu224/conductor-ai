"""End-to-end coverage for the offline API mock delivery profile."""

from __future__ import annotations

import json
from pathlib import Path

from app import run_project


def test_api_mock_run_profile_delivers_ready_manifest(tmp_path, capsys) -> None:
    exit_code = run_project.main(
        [
            "--project-root",
            str(tmp_path),
            "--project-name",
            "todo-api-mock",
            "--requirement",
            (
                "Build a backend REST API for todo items. It must support creating items, "
                "listing items, filtering active and completed items, keyword query, updating items, "
                "deleting items, and a stats endpoint. Use JSON response payloads and HTTP status codes."
            ),
            "--run-profile",
            "api_mock",
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
    assert payload["run_profile"] == "api_mock"
    assert payload["manifest_verification"]["passed"] is True
    assert payload["task_center_audit"]["passed"] is True
    assert payload["audit_bundle_verification"]["passed"] is True

    assert (project_root / "app.py").is_file()
    assert (project_root / "pytest.ini").is_file()
    assert (project_root / "tests" / "test_api_contract.py").is_file()

    summary = manifest["summary"]
    assert summary["delivery_readiness_status"] == "ready"
    assert summary["delivery_readiness_score"] >= 90
    assert summary["scope_contract_status"] == "pass"
    assert summary["scope_contract_violation_count"] == 0
    assert summary["validation_failure_count"] == 0
    assert "app.py" in summary["changed_files"]
    assert "tests/test_api_contract.py" in summary["changed_files"]

    implementation_execution = next(
        execution for execution in manifest["executions"] if execution["source_backend"] == "api_mock_delivery"
    )
    assert implementation_execution["validation_success"] is True
    assert implementation_execution["validation_exit_code"] == 0
    assert "POST /api/items -> status_code=201 response payload=" in implementation_execution["cli_stdout_tail"]

    tester_executions = [
        execution
        for execution in manifest["executions"]
        if execution["agent_id"] == "agent-tester" and execution.get("validation_command")
    ]
    assert tester_executions
    assert all(execution["validation_success"] is True for execution in tester_executions)
