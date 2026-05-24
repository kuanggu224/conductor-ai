"""End-to-end coverage for the offline API SQLite delivery profile."""

from __future__ import annotations

import json
from pathlib import Path

from app import run_project


def test_api_sqlite_run_profile_delivers_ready_manifest(tmp_path, capsys) -> None:
    exit_code = run_project.main(
        [
            "--project-root",
            str(tmp_path),
            "--project-name",
            "todo-api-sqlite",
            "--requirement",
            (
                "Build a backend REST API for todo items using SQLite database persistence. "
                "It must support creating items, listing items, filtering active and completed items, "
                "keyword query, updating items, deleting items, and a stats endpoint. "
                "Use JSON response payloads and HTTP status codes, and verify database persistence."
            ),
            "--run-profile",
            "api_sqlite",
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
    assert payload["run_profile"] == "api_sqlite"
    assert payload["manifest_verification"]["passed"] is True
    assert payload["task_center_audit"]["passed"] is True
    assert payload["audit_bundle_verification"]["passed"] is True

    assert (project_root / "app.py").is_file()
    assert (project_root / "pytest.ini").is_file()
    assert (project_root / "tests" / "test_api_contract.py").is_file()
    assert (project_root / "items.db").is_file()

    summary = manifest["summary"]
    assert summary["delivery_readiness_status"] == "ready"
    assert summary["delivery_readiness_score"] >= 90
    assert summary["scope_contract_status"] == "pass"
    assert summary["scope_contract_violation_count"] == 0
    assert summary["validation_failure_count"] == 0
    assert "app.py" in summary["changed_files"]
    assert "tests/test_api_contract.py" in summary["changed_files"]

    implementation_execution = next(
        execution for execution in manifest["executions"] if execution["source_backend"] == "api_sqlite_delivery"
    )
    assert implementation_execution["validation_success"] is True
    assert implementation_execution["validation_exit_code"] == 0
    assert "POST /api/items -> status_code=201 response payload=" in implementation_execution["cli_stdout_tail"]
    assert "SQLite persistence verified -> database=items.db row_count=2" in implementation_execution["cli_stdout_tail"]

    coverage_results = manifest["requirement_coverage_results"]
    assert coverage_results
    assert all(result["passed"] is True for result in coverage_results)
    assert any("db_persistence" in result["required_rules"] for result in coverage_results)
