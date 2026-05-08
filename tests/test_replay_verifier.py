"""Read-only run manifest verifier tests."""

from __future__ import annotations

import json
from pathlib import Path

from app.verify_manifest import main as verify_manifest_main
from conductor.config.cli import CLISelectionConfig
from conductor.config.execution import RunProfile
from conductor.controller.engine import ConductorEngine
from conductor.replay_verifier import verify_manifest


def _write_manifest(tmp_path: Path, overrides: dict[str, object] | None = None) -> Path:
    project_root = tmp_path / "project"
    report_path = project_root / ".conductor" / "reports" / "project-1.md"
    log_path = project_root / ".conductor" / "logs" / "project-1.jsonl"
    artifact_path = project_root / ".conductor" / "artifacts" / "artifact-1.md"
    prompt_path = project_root / ".conductor" / "task_prompts" / "task-1.md"
    manifest_path = project_root / ".conductor" / "manifests" / "project-1.manifest.json"
    for path in (report_path, log_path, artifact_path, prompt_path, manifest_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok", encoding="utf-8")

    payload: dict[str, object] = {
        "schema_version": "1.28",
        "run_id": "project-1:run",
        "project_id": "project-1",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "completed_at": "2026-01-01T00:00:00+00:00",
        "run_profile": "mock",
        "project_root": str(project_root),
        "status": "completed",
        "final_status": "completed",
        "current_stage": "testing",
        "working_directory": str(project_root),
        "selected_cli_names": [],
        "role_cli_bindings": {},
        "summary": {
            "final_status": "completed",
            "workitem_count": 1,
            "execution_count": 1,
            "artifact_count": 1,
            "artifact_file_count": 1,
            "task_prompt_file_count": 1,
            "cli_run_count": 0,
            "llm_run_count": 0,
            "collaboration_run_count": 0,
            "retry_history_count": 0,
            "changed_file_count": 0,
            "changed_files": [],
        },
        "resume_cursor": {
            "project_id": "project-1",
            "project_status": "completed",
            "current_stage": "testing",
            "next_action": "complete",
            "terminal": True,
            "blocked": False,
            "blockers": [],
            "next_pending_workitem_ids": [],
            "running_workitem_ids": [],
            "retryable_failed_workitem_ids": [],
            "terminal_failed_workitem_ids": [],
            "completed_workitem_ids": ["workitem-1"],
            "last_execution_workitem_id": "workitem-1",
            "last_event": "completed",
        },
        "run_environment": {},
        "platform_diagnostics": {},
        "agents": [],
        "executions": [
            {
                "workitem_id": "workitem-1",
                "agent_id": "agent-1",
                "status": "success",
                "artifact_ids": ["artifact-1"],
                "artifact_files": [str(artifact_path)],
            }
        ],
        "cli_runs": [],
        "llm_runs": [],
        "collaboration_runs": [],
        "retry_history": [],
        "requirement_evaluations": [],
        "requirement_coverage_results": [],
        "scope_contract_results": [],
        "workitems": [
            {
                "id": "workitem-1",
                "stage": "testing",
                "kind": "acceptance_check",
                "status": "done",
            }
        ],
        "task_assignments": [
            {
                "id": "assignment-1",
                "workitem_id": "workitem-1",
                "role": "tester",
                "status": "completed",
            }
        ],
        "artifacts": [
            {
                "id": "artifact-1",
                "project_id": "project-1",
                "workitem_id": "workitem-1",
                "title": "Report",
                "kind": "test_report",
                "agent_id": "agent-1",
                "path": str(artifact_path),
            }
        ],
        "artifact_files": [str(artifact_path)],
        "task_prompt_files": [str(prompt_path)],
        "files": {
            "log": str(log_path),
            "report": str(report_path),
            "manifest": str(manifest_path),
            "artifacts": [str(artifact_path)],
            "task_prompts": [str(prompt_path)],
            "preflight_gate": "",
        },
        "log_path": str(log_path),
        "report_path": str(report_path),
    }
    if overrides:
        payload.update(overrides)
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def test_manifest_verifier_accepts_consistent_manifest(tmp_path) -> None:
    manifest_path = _write_manifest(tmp_path)

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert result.errors == []
    assert result.project_id == "project-1"
    assert result.schema_version == "1.28"


def test_manifest_verifier_accepts_engine_generated_manifest(tmp_path) -> None:
    project_root = tmp_path / "project"
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        cli_selection_config=CLISelectionConfig(),
        run_profile=RunProfile.MOCK,
    )
    state = engine.create_project("验证 manifest 自检器", project_root=str(project_root))
    state = engine.step_project(state.project.id)
    report_path = engine.write_project_report(state.project.id)
    manifest_path = engine.write_run_manifest(state.project.id, report_path)

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert result.errors == []


def test_manifest_verifier_rejects_bad_summary_and_cursor_reference(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
                "workitem_count": 2,
                "execution_count": 1,
                "artifact_count": 1,
                "artifact_file_count": 1,
                "task_prompt_file_count": 1,
            },
            "resume_cursor": {
                "project_id": "project-1",
                "project_status": "completed",
                "current_stage": "testing",
                "next_action": "complete",
                "terminal": True,
                "completed_workitem_ids": ["missing-workitem"],
                "last_execution_workitem_id": "workitem-1",
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "summary.workitem_count=2 does not match len(workitems)=1" in result.errors
    assert any("missing-workitem" in error for error in result.errors)


def test_manifest_verifier_rejects_unknown_task_assignment_dependencies(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "task_assignments": [
                {
                    "id": "assignment-1",
                    "workitem_id": "workitem-1",
                    "role": "tester",
                    "status": "queued",
                    "dependencies": ["missing-workitem"],
                }
            ]
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "task assignment assignment-1 dependency references unknown WorkItem: missing-workitem" in result.errors


def test_manifest_verifier_rejects_unknown_task_assignment_input_artifacts(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "task_assignments": [
                {
                    "id": "assignment-1",
                    "workitem_id": "workitem-1",
                    "role": "tester",
                    "status": "queued",
                    "input_artifact_ids": ["missing-artifact"],
                }
            ]
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "task assignment assignment-1 input_artifact_ids references unknown Artifact: missing-artifact" in result.errors


def test_manifest_verifier_warns_for_unindexed_task_assignment_output_artifacts(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "task_assignments": [
                {
                    "id": "assignment-1",
                    "workitem_id": "workitem-1",
                    "role": "tester",
                    "status": "completed",
                    "output_artifact_ids": ["artifact-external"],
                }
            ]
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert (
        "task assignment assignment-1 output_artifact_ids is not indexed in artifacts: artifact-external"
        in result.warnings
    )


def test_manifest_verifier_rejects_unknown_workitem_input_artifacts(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "workitems": [
                {
                    "id": "workitem-1",
                    "stage": "testing",
                    "kind": "acceptance_check",
                    "status": "done",
                    "input_artifact_ids": ["missing-artifact"],
                }
            ]
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "workitem workitem-1 input_artifact_ids references unknown Artifact: missing-artifact" in result.errors


def test_manifest_verifier_warns_for_unindexed_workitem_output_artifacts(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "workitems": [
                {
                    "id": "workitem-1",
                    "stage": "testing",
                    "kind": "acceptance_check",
                    "status": "done",
                    "output_artifact_ids": ["artifact-external"],
                }
            ]
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "workitem workitem-1 output_artifact_ids is not indexed in artifacts: artifact-external" in result.warnings


def test_manifest_verifier_warns_for_unresolved_artifact_lineage(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "artifacts": [
                {
                    "id": "artifact-1",
                    "project_id": "project-1",
                    "workitem_id": "workitem-1",
                    "title": "Report",
                    "kind": "test_report",
                    "agent_id": "agent-1",
                    "path": str(tmp_path / "project" / ".conductor" / "artifacts" / "artifact-1.md"),
                    "derived_from": ["missing-input-artifact"],
                }
            ]
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "artifact artifact-1 has unresolved derived_from: missing-input-artifact" in result.warnings


def test_manifest_verifier_warns_for_self_referential_artifact_lineage(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "artifacts": [
                {
                    "id": "artifact-1",
                    "project_id": "project-1",
                    "workitem_id": "workitem-1",
                    "title": "Report",
                    "kind": "test_report",
                    "agent_id": "agent-1",
                    "path": str(tmp_path / "project" / ".conductor" / "artifacts" / "artifact-1.md"),
                    "parent_artifact_id": "artifact-1",
                    "review_of": "artifact-1",
                    "derived_from": ["artifact-1"],
                }
            ]
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "artifact artifact-1 has self-referential parent_artifact_id" in result.warnings
    assert "artifact artifact-1 has self-referential review_of" in result.warnings
    assert "artifact artifact-1 has self-referential derived_from" in result.warnings


def test_manifest_verifier_warns_for_malformed_artifact_lineage_type(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "artifacts": [
                {
                    "id": "artifact-1",
                    "project_id": "project-1",
                    "workitem_id": "workitem-1",
                    "title": "Report",
                    "kind": "test_report",
                    "agent_id": "agent-1",
                    "path": str(tmp_path / "project" / ".conductor" / "artifacts" / "artifact-1.md"),
                    "derived_from": "artifact-input",
                }
            ]
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "artifact artifact-1 derived_from must be a list" in result.warnings


def test_verify_manifest_cli_can_fail_on_unresolved_artifact_lineage_warning(tmp_path, capsys) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "artifacts": [
                {
                    "id": "artifact-1",
                    "project_id": "project-1",
                    "workitem_id": "workitem-1",
                    "title": "Report",
                    "kind": "test_report",
                    "agent_id": "agent-1",
                    "path": str(tmp_path / "project" / ".conductor" / "artifacts" / "artifact-1.md"),
                    "derived_from": ["missing-input-artifact"],
                }
            ]
        },
    )

    exit_code = verify_manifest_main([str(manifest_path), "--fail-on-warnings"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["passed"] is True
    assert "artifact artifact-1 has unresolved derived_from: missing-input-artifact" in payload["warnings"]


def test_manifest_verifier_reports_missing_files_as_warnings(tmp_path) -> None:
    manifest_path = _write_manifest(tmp_path, {"artifact_files": [str(tmp_path / "missing.md")]})

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert any("artifact_files entry does not exist" in warning for warning in result.warnings)


def test_manifest_verifier_warns_for_non_current_schema_version(tmp_path) -> None:
    manifest_path = _write_manifest(tmp_path, {"schema_version": "1.0"})

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "manifest schema_version 1.0 differs from current 1.28" in result.warnings


def test_manifest_verifier_rejects_api_key_fields(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "platform_diagnostics": {
                "cloud": {
                    "api_key": "sk-leaked-provider-key-12345",
                }
            }
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "manifest contains sensitive field: platform_diagnostics.cloud.api_key" in result.errors


def test_manifest_verifier_rejects_bearer_token_values(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "run_environment": {
                "authorization_header": "Bearer leaked-provider-token-12345",
            }
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert any("run_environment.authorization_header" in error for error in result.errors)


def test_manifest_verifier_allows_task_assignment_claim_token(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "task_assignments": [
                {
                    "id": "assignment-1",
                    "workitem_id": "workitem-1",
                    "role": "tester",
                    "status": "claimed",
                    "claim_token": "safe-existing-claim-token",
                }
            ]
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert not any("claim_token" in error for error in result.errors)


def test_verify_manifest_cli_exits_zero_for_valid_manifest(tmp_path, capsys) -> None:
    manifest_path = _write_manifest(tmp_path)

    exit_code = verify_manifest_main([str(manifest_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"passed": true' in captured.out


def test_verify_manifest_cli_writes_output_file(tmp_path, capsys) -> None:
    manifest_path = _write_manifest(tmp_path)
    output_path = tmp_path / "audit" / "manifest-verification.json"

    exit_code = verify_manifest_main([str(manifest_path), "--output", str(output_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["output_path"] == str(output_path.resolve())
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["project_id"] == "project-1"


def test_verify_manifest_cli_can_fail_on_warnings(tmp_path, capsys) -> None:
    manifest_path = _write_manifest(tmp_path, {"artifact_files": [str(tmp_path / "missing.md")]})

    exit_code = verify_manifest_main([str(manifest_path), "--fail-on-warnings"])
    captured = capsys.readouterr()

    assert exit_code == 2
    payload = json.loads(captured.out)
    assert payload["passed"] is True
    assert payload["warning_count"] > 0


def test_verify_manifest_cli_output_summary_respects_fail_on_warnings(tmp_path, capsys) -> None:
    manifest_path = _write_manifest(tmp_path, {"artifact_files": [str(tmp_path / "missing.md")]})
    output_path = tmp_path / "audit" / "manifest-verification.json"

    exit_code = verify_manifest_main([str(manifest_path), "--fail-on-warnings", "--output", str(output_path)])
    captured = capsys.readouterr()

    assert exit_code == 2
    summary = json.loads(captured.out)
    assert summary["ok"] is False
    assert summary["warning_count"] > 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["passed"] is True


def test_verify_manifest_cli_exits_two_for_invalid_manifest(tmp_path, capsys) -> None:
    manifest_path = _write_manifest(tmp_path, {"project_id": ""})

    exit_code = verify_manifest_main([str(manifest_path)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "project_id must be non-empty" in captured.out
