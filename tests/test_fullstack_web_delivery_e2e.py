"""End-to-end coverage for the offline full-stack web delivery profile."""

from __future__ import annotations

import json
from pathlib import Path

from app import run_project


def test_fullstack_web_run_profile_delivers_ready_manifest(tmp_path, capsys) -> None:
    exit_code = run_project.main(
        [
            "--project-root",
            str(tmp_path),
            "--project-name",
            "todo-fullstack-web",
            "--requirement",
            (
                "Build a fullstack web app for todo items with a browser frontend and backend REST API. "
                "The page must let users create items through a form, list items from the API, filter items, "
                "delete items, and show API-backed stats. Use JSON response payloads, HTTP status codes, "
                "and browser integration evidence proving the frontend calls the backend API."
            ),
            "--run-profile",
            "fullstack_web",
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
    assert payload["run_profile"] == "fullstack_web"
    assert payload["manifest_verification"]["passed"] is True
    assert payload["task_center_audit"]["passed"] is True
    assert payload["audit_bundle_verification"]["passed"] is True

    assert (project_root / "app.py").is_file()
    assert (project_root / "index.html").is_file()
    assert (project_root / "static" / "app.js").is_file()
    assert (project_root / "static" / "style.css").is_file()
    assert (project_root / "pytest.ini").is_file()
    assert (project_root / "tests" / "test_fullstack_contract.py").is_file()

    summary = manifest["summary"]
    assert summary["delivery_readiness_status"] == "ready"
    assert summary["delivery_readiness_score"] >= 90
    assert summary["scope_contract_status"] == "pass"
    assert summary["scope_contract_violation_count"] == 0
    assert summary["validation_failure_count"] == 0
    assert "app.py" in summary["changed_files"]
    assert "index.html" in summary["changed_files"]
    assert "static/app.js" in summary["changed_files"]
    assert "tests/test_fullstack_contract.py" in summary["changed_files"]

    implementation_execution = next(
        execution for execution in manifest["executions"] if execution["source_backend"] == "fullstack_web_delivery"
    )
    assert implementation_execution["validation_success"] is True
    assert implementation_execution["validation_exit_code"] == 0
    assert "GET / -> status_code=200 response payload=html" in implementation_execution["cli_stdout_tail"]
    assert "Browser form interaction updated visible state: Write backend" in implementation_execution["cli_stdout_tail"]
    assert "GET /api/items/stats -> status_code=200 response payload=" in implementation_execution["cli_stdout_tail"]
    assert "Fullstack frontend API integration verified" in implementation_execution["cli_stdout_tail"]

    tester_executions = [execution for execution in manifest["executions"] if "tester" in execution["agent_id"]]
    assert tester_executions
    assert all(execution["validation_success"] is True for execution in tester_executions)

    coverage_results = manifest["requirement_coverage_results"]
    assert coverage_results
    assert all(result["passed"] is True for result in coverage_results)
    required_rules = {rule for result in coverage_results for rule in result["required_rules"]}
    assert "api_behavior" in required_rules
    assert "fullstack_integration" in required_rules
