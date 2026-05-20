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


def test_manifest_verifier_checks_pre_run_maintenance_report(tmp_path) -> None:
    project_root = tmp_path / "project"
    report_path = project_root / ".conductor" / "maintenance" / "pre-run.json"
    latest_path = project_root / ".conductor" / "maintenance" / "latest.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "project_id": "project-1",
                "status": "clean",
                "released_count": 1,
                "expired_lease_released_count": 1,
                "stale_released_count": 0,
                "audit": {"finding_count": 0, "error_count": 0, "warning_count": 0},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    latest_path.write_text(
        json.dumps(
            {
                "project_id": "project-1",
                "status": "clean",
                "released_count": 1,
                "finding_count": 0,
                "error_count": 0,
                "warning_count": 0,
                "report_path": str(report_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    manifest_path = _write_manifest(
        tmp_path,
        {
            "pre_run_maintenance": {
                "project_id": "project-1",
                "status": "clean",
                "released_count": 1,
                "expired_lease_released_count": 1,
                "stale_released_count": 0,
                "report_path": str(report_path),
                "latest_path": str(latest_path),
                "audit": {"finding_count": 0, "error_count": 0, "warning_count": 0},
            }
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert not any("pre_run_maintenance.report_path" in warning for warning in result.warnings)
    assert not any("pre_run_maintenance.latest_path" in warning for warning in result.warnings)


def test_manifest_verifier_warns_for_missing_pre_run_maintenance_report(tmp_path) -> None:
    missing_path = tmp_path / "project" / ".conductor" / "maintenance" / "missing.json"
    manifest_path = _write_manifest(
        tmp_path,
        {
            "pre_run_maintenance": {
                "project_id": "project-1",
                "status": "clean",
                "report_path": str(missing_path),
            }
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert f"pre_run_maintenance.report_path does not exist: {missing_path}" in result.warnings


def test_manifest_verifier_warns_for_missing_pre_run_maintenance_latest(tmp_path) -> None:
    project_root = tmp_path / "project"
    report_path = project_root / ".conductor" / "maintenance" / "pre-run.json"
    latest_path = project_root / ".conductor" / "maintenance" / "latest-missing.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "project_id": "project-1",
                "status": "clean",
                "released_count": 0,
                "expired_lease_released_count": 0,
                "stale_released_count": 0,
                "audit": {"finding_count": 0, "error_count": 0, "warning_count": 0},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    manifest_path = _write_manifest(
        tmp_path,
        {
            "pre_run_maintenance": {
                "project_id": "project-1",
                "status": "clean",
                "released_count": 0,
                "report_path": str(report_path),
                "latest_path": str(latest_path),
                "audit": {"finding_count": 0, "error_count": 0, "warning_count": 0},
            }
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert f"pre_run_maintenance.latest_path does not exist: {latest_path}" in result.warnings


def test_manifest_verifier_rejects_pre_run_maintenance_latest_mismatch(tmp_path) -> None:
    project_root = tmp_path / "project"
    report_path = project_root / ".conductor" / "maintenance" / "pre-run.json"
    latest_path = project_root / ".conductor" / "maintenance" / "latest.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "project_id": "project-1",
                "status": "clean",
                "released_count": 1,
                "expired_lease_released_count": 1,
                "stale_released_count": 0,
                "audit": {"finding_count": 0, "error_count": 0, "warning_count": 0},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    latest_path.write_text(
        json.dumps(
            {
                "project_id": "project-other",
                "status": "needs_attention",
                "released_count": 0,
                "finding_count": 2,
                "error_count": 1,
                "warning_count": 1,
                "report_path": "wrong.json",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    manifest_path = _write_manifest(
        tmp_path,
        {
            "pre_run_maintenance": {
                "project_id": "project-1",
                "status": "clean",
                "released_count": 1,
                "report_path": str(report_path),
                "latest_path": str(latest_path),
                "audit": {"finding_count": 0, "error_count": 0, "warning_count": 0},
            }
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "pre_run_maintenance.latest.project_id='project-other' does not match manifest project_id='project-1'" in result.errors
    assert "pre_run_maintenance.latest.status='needs_attention' does not match manifest status='clean'" in result.errors
    assert "pre_run_maintenance.latest.finding_count=2 does not match report audit finding_count=0" in result.errors


def test_manifest_verifier_rejects_pre_run_maintenance_report_mismatch(tmp_path) -> None:
    project_root = tmp_path / "project"
    report_path = project_root / ".conductor" / "maintenance" / "pre-run.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "project_id": "project-1",
                "status": "needs_attention",
                "released_count": 0,
                "expired_lease_released_count": 0,
                "stale_released_count": 0,
                "audit": {"finding_count": 2, "error_count": 1, "warning_count": 1},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    manifest_path = _write_manifest(
        tmp_path,
        {
            "pre_run_maintenance": {
                "project_id": "project-1",
                "status": "clean",
                "released_count": 1,
                "expired_lease_released_count": 1,
                "stale_released_count": 0,
                "report_path": str(report_path),
                "audit": {"finding_count": 0, "error_count": 0, "warning_count": 0},
            }
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "pre_run_maintenance.status='clean' does not match report status='needs_attention'" in result.errors
    assert "pre_run_maintenance.released_count=1 does not match report released_count=0" in result.errors
    assert "pre_run_maintenance.audit.finding_count=0 does not match report audit finding_count=2" in result.errors


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


def test_manifest_verifier_rejects_status_final_status_mismatch(tmp_path) -> None:
    manifest_path = _write_manifest(tmp_path, {"status": "in_progress", "final_status": "completed"})

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "manifest.status does not match manifest.final_status" in result.errors


def test_manifest_verifier_rejects_summary_final_status_mismatch(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
                "final_status": "blocked",
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
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "summary.final_status does not match manifest.final_status" in result.errors


def test_manifest_verifier_rejects_bad_summary_workitem_status_counts(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
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
                "workitem_status_counts": {"pending": 1},
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert any("summary.workitem_status_counts" in error for error in result.errors)


def test_manifest_verifier_rejects_bad_summary_execution_status_counts(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
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
                "execution_status_counts": {"failed": 1},
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert any("summary.execution_status_counts" in error for error in result.errors)


def test_manifest_verifier_rejects_bad_summary_agent_count(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
                "final_status": "completed",
                "workitem_count": 1,
                "execution_count": 1,
                "artifact_count": 1,
                "agent_count": 2,
                "artifact_file_count": 1,
                "task_prompt_file_count": 1,
                "cli_run_count": 0,
                "llm_run_count": 0,
                "collaboration_run_count": 0,
                "retry_history_count": 0,
                "changed_file_count": 0,
                "changed_files": [],
            },
            "agents": [
                {
                    "agent_id": "agent-1",
                    "role": "tester",
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "summary.agent_count=2 does not match len(agents)=1" in result.errors


def test_manifest_verifier_rejects_bad_control_plane_summary_counts(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
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
                "agent_team_plan_count": 2,
                "tl_decision_count": 0,
                "human_control_action_count": 0,
                "task_center_audit_finding_count": 3,
                "task_center_audit_error_count": 0,
                "task_center_audit_warning_count": 2,
                "changed_file_count": 0,
                "changed_files": [],
            },
            "agent_team_plans": [
                {
                    "id": "team-plan-1",
                    "project_id": "project-1",
                    "stage": "development",
                }
            ],
            "tl_decisions": [
                {
                    "id": "tl-decision-1",
                    "project_id": "project-1",
                    "stage": "testing",
                    "action": "request_human_approval",
                }
            ],
            "human_control_actions": [
                {
                    "id": "human-action-1",
                    "project_id": "project-1",
                    "action": "request_approval",
                    "actor": "tl_agent",
                    "reason": "high risk",
                    "stage": "testing",
                }
            ],
            "task_center_audit": [
                {
                    "code": "write_scope_conflict",
                    "severity": "error",
                    "assignment_id": "assignment-1",
                    "workitem_id": "workitem-1",
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "summary.agent_team_plan_count=2 does not match len(agent_team_plans)=1" in result.errors
    assert "summary.tl_decision_count=0 does not match len(tl_decisions)=1" in result.errors
    assert "summary.human_control_action_count=0 does not match len(human_control_actions)=1" in result.errors
    assert "summary.task_center_audit_finding_count=3 does not match len(task_center_audit)=1" in result.errors
    assert "summary.task_center_audit_error_count=0 does not match task_center_audit=1" in result.errors
    assert "summary.task_center_audit_warning_count=2 does not match task_center_audit=0" in result.errors


def test_manifest_verifier_rejects_bad_task_center_audit_links(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
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
                "task_center_audit_finding_count": 3,
                "task_center_audit_error_count": 2,
                "task_center_audit_warning_count": 1,
                "changed_file_count": 0,
                "changed_files": [],
            },
            "task_center_audit": [
                {
                    "code": "stale_claimed",
                    "severity": "warning",
                    "assignment_id": "missing-assignment",
                    "workitem_id": "workitem-1",
                },
                {
                    "code": "lease_expired",
                    "severity": "error",
                    "assignment_id": "assignment-1",
                    "workitem_id": "missing-workitem",
                },
                {
                    "code": "claimed_missing_token",
                    "severity": "fatal",
                    "assignment_id": "assignment-1",
                    "workitem_id": "workitem-other",
                    "related_assignment_ids": ["missing-related-assignment"],
                },
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "task_center_audit[0] references unknown TaskAssignment: missing-assignment" in result.errors
    assert "task_center_audit[1] references unknown WorkItem: missing-workitem" in result.errors
    assert (
        "task_center_audit[1] workitem_id=missing-workitem does not match "
        "TaskAssignment assignment-1 workitem_id=workitem-1"
    ) in result.errors
    assert "task_center_audit[2].severity must be error or warning" in result.errors
    assert (
        "task_center_audit[2].related_assignment_ids references unknown TaskAssignment: "
        "missing-related-assignment"
    ) in result.errors


def test_manifest_verifier_rejects_bad_control_plane_links(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "agent_team_plans": [
                {
                    "id": "team-plan-foreign",
                    "project_id": "project-other",
                    "stage": "development",
                    "reasons": [],
                    "agent_specs": [],
                }
            ],
            "tl_decisions": [
                {
                    "id": "tl-decision-foreign",
                    "project_id": "project-other",
                    "stage": "testing",
                    "action": "request_human_approval",
                    "recommendations": [],
                }
            ],
            "human_control_actions": [
                {
                    "id": "human-action-foreign",
                    "project_id": "project-other",
                    "action": "request_approval",
                    "actor": "tl_agent",
                    "reason": "high risk",
                    "stage": "testing",
                    "workitem_id": "missing-workitem",
                    "payload": {"controller_action": "advance_stage"},
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "agent_team_plans[0].project_id does not match manifest.project_id: team-plan-foreign" in result.errors
    assert "tl_decisions[0].project_id does not match manifest.project_id: tl-decision-foreign" in result.errors
    assert "human_control_actions[0].project_id does not match manifest.project_id: human-action-foreign" in result.errors
    assert "human_control_actions[0] references unknown WorkItem: missing-workitem" in result.errors


def test_manifest_verifier_rejects_inconsistent_agent_team_plan_specs(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "agents": [
                {"agent_id": "agent-frontend-a"},
                {"agent_id": "agent-frontend-b"},
                {"agent_id": "agent-frontend-c"},
            ],
            "agent_team_plans": [
                {
                    "id": "team-plan-1",
                    "project_id": "project-1",
                    "stage": "development",
                    "reasons": [],
                    "agent_specs": [
                        {
                            "role": "frontend_engineer",
                            "agent_id": "agent-frontend-a",
                            "instance_id": "layout",
                            "stage": "development",
                            "collaboration_mode": "parallel_development",
                            "parallel_safe": True,
                            "write_scope": [],
                        },
                        {
                            "role": "frontend_engineer",
                            "agent_id": "agent-frontend-a",
                            "instance_id": "state",
                            "stage": "testing",
                            "collaboration_mode": "parallel_development",
                            "parallel_safe": True,
                            "write_scope": ["src/state.ts"],
                        },
                        {
                            "role": "frontend_engineer",
                            "agent_id": "",
                            "instance_id": "empty",
                            "stage": "development",
                            "collaboration_mode": "sequential_review",
                            "parallel_safe": False,
                            "write_scope": [],
                        },
                        {
                            "role": "frontend_engineer",
                            "agent_id": "agent-frontend-c",
                            "instance_id": "overlap",
                            "stage": "development",
                            "collaboration_mode": "parallel_development",
                            "parallel_safe": True,
                            "write_scope": ["SRC/STATE.ts"],
                        },
                    ],
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "agent_team_plans[0].agent_specs[0] parallel_development requires write_scope" in result.errors
    assert "agent_team_plans[0] contains duplicate agent_spec agent_id: agent-frontend-a" in result.errors
    assert "agent_team_plans[0].agent_specs[1].stage=testing does not match plan stage=development" in result.errors
    assert "agent_team_plans[0].agent_specs[2].agent_id must be non-empty" in result.errors
    assert (
        "agent_team_plans[0].agent_specs[3] parallel_development write_scope overlaps "
        "with agent-frontend-a: SRC/STATE.ts"
    ) in result.errors


def test_manifest_verifier_warns_for_unpaired_tl_human_gate(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "tl_decisions": [
                {
                    "id": "tl-needs-human",
                    "project_id": "project-1",
                    "stage": "testing",
                    "action": "escalate_project",
                    "risk_level": "high",
                    "summary": "TL requires human approval",
                    "recommendations": ["Inspect blocker"],
                    "human_action_required": True,
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert (
        "tl_decisions[0] human_action_required has no matching human_control_actions gate: "
        "action=escalate_project, stage=testing"
    ) in result.warnings


def test_manifest_verifier_warns_for_human_gate_without_tl_decision(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "human_control_actions": [
                {
                    "id": "human-gate",
                    "project_id": "project-1",
                    "action": "request_approval",
                    "actor": "tl_agent",
                    "reason": "needs approval",
                    "stage": "testing",
                    "payload": {"controller_action": "escalate_project", "stage": "testing"},
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert (
        "human_control_actions[0] controller gate has no matching tl_decisions entry: "
        "action=escalate_project, stage=testing"
    ) in result.warnings


def test_manifest_verifier_accepts_matched_tl_human_gate(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "tl_decisions": [
                {
                    "id": "tl-needs-human",
                    "project_id": "project-1",
                    "stage": "testing",
                    "action": "escalate_project",
                    "risk_level": "high",
                    "summary": "TL requires human approval",
                    "recommendations": ["Inspect blocker"],
                    "human_action_required": True,
                }
            ],
            "human_control_actions": [
                {
                    "id": "human-gate",
                    "project_id": "project-1",
                    "action": "request_approval",
                    "actor": "tl_agent",
                    "reason": "needs approval",
                    "stage": "testing",
                    "payload": {"controller_action": "escalate_project", "stage": "testing"},
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert not any("human_action_required has no matching" in warning for warning in result.warnings)
    assert not any("controller gate has no matching" in warning for warning in result.warnings)


def test_manifest_verifier_rejects_bad_summary_failed_workitem_ids(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
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
                "failed_workitem_ids": ["workitem-1"],
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "summary.failed_workitem_ids=['workitem-1'] does not match failed WorkItems=[]" in result.errors


def test_manifest_verifier_rejects_bad_summary_failure_counts(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "status": "in_progress",
            "final_status": "in_progress",
            "summary": {
                "final_status": "in_progress",
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
                "failed_workitem_ids": ["workitem-1"],
                "retryable_failure_count": 0,
                "non_retryable_failure_count": 1,
            },
            "workitems": [
                {
                    "id": "workitem-1",
                    "stage": "testing",
                    "kind": "acceptance_check",
                    "status": "failed",
                    "retryable": True,
                }
            ],
            "resume_cursor": {
                "project_id": "project-1",
                "project_status": "in_progress",
                "current_stage": "testing",
                "next_action": "retry_workitem",
                "terminal": False,
                "blocked": False,
                "retryable_failed_workitem_ids": ["workitem-1"],
                "completed_workitem_ids": [],
                "last_execution_workitem_id": "workitem-1",
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "summary.retryable_failure_count=0 does not match failed WorkItems=1" in result.errors
    assert "summary.non_retryable_failure_count=1 does not match failed WorkItems=0" in result.errors


def test_manifest_verifier_rejects_bad_summary_retry_attempt_count(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
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
                "retry_history_count": 1,
                "retry_attempt_count": 3,
                "changed_file_count": 0,
                "changed_files": [],
            },
            "retry_history": [
                {
                    "workitem_id": "workitem-1",
                    "retry_count": 1,
                    "max_retries": 2,
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "summary.retry_attempt_count=3 does not match retry_history total=1" in result.errors


def test_manifest_verifier_rejects_bad_task_center_summary_counts(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
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
                "task_center_summary": {
                    "total": 2,
                    "claimable": 1,
                    "completed": 0,
                },
            },
            "task_assignments": [
                {
                    "id": "assignment-1",
                    "workitem_id": "workitem-1",
                    "role": "tester",
                    "status": "completed",
                    "claimable": False,
                    "unmet_dependency_ids": [],
                    "stale_claimed": False,
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "summary.task_center_summary.total=2 does not match task_assignments=1" in result.errors
    assert "summary.task_center_summary.claimable=1 does not match task_assignments=0" in result.errors
    assert "summary.task_center_summary.completed=0 does not match task_assignments=1" in result.errors


def test_manifest_verifier_rejects_bad_task_center_summary_stale_and_blocked_counts(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "status": "in_progress",
            "final_status": "in_progress",
            "summary": {
                "final_status": "in_progress",
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
                "task_center_summary": {
                    "blocked_by_dependencies": 0,
                    "stale_claimed": 0,
                    "queued": 1,
                },
            },
            "workitems": [
                {
                    "id": "workitem-1",
                    "stage": "testing",
                    "kind": "acceptance_check",
                    "status": "pending",
                }
            ],
            "resume_cursor": {
                "project_id": "project-1",
                "project_status": "in_progress",
                "current_stage": "testing",
                "next_action": "execute_workitem",
                "terminal": False,
                "blocked": False,
                "next_pending_workitem_ids": ["workitem-1"],
                "completed_workitem_ids": [],
                "last_execution_workitem_id": "workitem-1",
            },
            "task_assignments": [
                {
                    "id": "assignment-1",
                    "workitem_id": "workitem-1",
                    "role": "tester",
                    "status": "queued",
                    "claimable": False,
                    "unmet_dependency_ids": ["workitem-upstream"],
                    "stale_claimed": True,
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "summary.task_center_summary.blocked_by_dependencies=0 does not match task_assignments=1" in result.errors
    assert "summary.task_center_summary.stale_claimed=0 does not match task_assignments=1" in result.errors


def test_manifest_verifier_rejects_bad_task_center_lease_and_write_scope_counts(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "status": "in_progress",
            "final_status": "in_progress",
            "summary": {
                "final_status": "in_progress",
                "workitem_count": 2,
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
                "task_center_summary": {
                    "blocked_by_write_scope": 0,
                    "lease_expired": 0,
                    "queued": 1,
                    "claimed": 1,
                },
            },
            "workitems": [
                {
                    "id": "workitem-1",
                    "stage": "development",
                    "kind": "ui_implementation",
                    "status": "pending",
                },
                {
                    "id": "workitem-2",
                    "stage": "development",
                    "kind": "ui_implementation",
                    "status": "running",
                },
            ],
            "resume_cursor": {
                "project_id": "project-1",
                "project_status": "in_progress",
                "current_stage": "development",
                "next_action": "inspect_running",
                "terminal": False,
                "blocked": False,
                "next_pending_workitem_ids": ["workitem-1"],
                "running_workitem_ids": ["workitem-2"],
                "completed_workitem_ids": [],
                "last_execution_workitem_id": "workitem-1",
            },
            "task_assignments": [
                {
                    "id": "assignment-1",
                    "workitem_id": "workitem-1",
                    "role": "frontend_engineer",
                    "status": "queued",
                    "claimable": False,
                    "unmet_dependency_ids": [],
                    "write_scope_conflict_assignment_ids": ["assignment-2"],
                    "stale_claimed": False,
                    "lease_expired": False,
                },
                {
                    "id": "assignment-2",
                    "workitem_id": "workitem-2",
                    "role": "frontend_engineer",
                    "status": "claimed",
                    "assigned_agent_id": "agent-layout",
                    "claimable": False,
                    "unmet_dependency_ids": [],
                    "write_scope_conflict_assignment_ids": [],
                    "stale_claimed": False,
                    "lease_expired": True,
                },
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "summary.task_center_summary.blocked_by_write_scope=0 does not match task_assignments=1" in result.errors
    assert "summary.task_center_summary.lease_expired=0 does not match task_assignments=1" in result.errors


def test_manifest_verifier_rejects_unknown_write_scope_conflict_assignment(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "status": "in_progress",
            "final_status": "in_progress",
            "summary": {
                "final_status": "in_progress",
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
            "task_assignments": [
                {
                    "id": "assignment-1",
                    "workitem_id": "workitem-1",
                    "role": "frontend_engineer",
                    "status": "queued",
                    "claimable": False,
                    "unmet_dependency_ids": [],
                    "write_scope_conflict_assignment_ids": ["missing-assignment"],
                    "stale_claimed": False,
                    "lease_expired": False,
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert (
        "task assignment assignment-1 write_scope_conflict_assignment_ids references unknown TaskAssignment: "
        "missing-assignment"
    ) in result.errors


def test_manifest_verifier_rejects_inconsistent_task_assignment_transition_history(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "task_assignments": [
                {
                    "id": "assignment-1",
                    "workitem_id": "workitem-1",
                    "role": "tester",
                    "status": "completed",
                    "returned_at": "2026-01-01T00:10:00+00:00",
                    "transition_history": [
                        {
                            "at": "2026-01-01T00:00:00+00:00",
                            "action": "claim",
                            "status": "claimed",
                            "agent_id": "agent-1",
                        }
                    ],
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "task assignment assignment-1 status completed has no return transition" in result.errors
    assert (
        "task assignment assignment-1 status completed has incompatible latest transition: claim"
    ) in result.errors


def test_manifest_verifier_rejects_bad_return_transition_timestamp(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "task_assignments": [
                {
                    "id": "assignment-1",
                    "workitem_id": "workitem-1",
                    "role": "tester",
                    "status": "completed",
                    "returned_at": "2026-01-01T00:10:00+00:00",
                    "transition_history": [
                        {
                            "at": "2026-01-01T00:00:00+00:00",
                            "action": "claim",
                            "status": "claimed",
                            "agent_id": "agent-1",
                        },
                        {
                            "at": "2026-01-01T00:09:00+00:00",
                            "action": "return",
                            "status": "completed",
                            "agent_id": "agent-1",
                        },
                    ],
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "task assignment assignment-1 returned_at does not match latest return transition" in result.errors


def test_manifest_verifier_rejects_bad_summary_blocked_count(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
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
                "blocked_count": 2,
                "blocked_reasons": ["manual approval required"],
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "summary.blocked_count=2 does not match len(summary.blocked_reasons)=1" in result.errors


def test_manifest_verifier_rejects_summary_blockers_mismatch_cursor_blockers(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "status": "blocked",
            "final_status": "blocked",
            "current_stage": "requirement",
            "summary": {
                "final_status": "blocked",
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
                "blocked_count": 1,
                "blocked_reasons": ["manual approval required"],
            },
            "workitems": [
                {
                    "id": "workitem-1",
                    "stage": "testing",
                    "kind": "acceptance_check",
                    "status": "failed",
                }
            ],
            "resume_cursor": {
                "project_id": "project-1",
                "project_status": "blocked",
                "current_stage": "testing",
                "next_action": "blocked",
                "terminal": True,
                "blocked": True,
                "blockers": ["retry limit reached"],
                "terminal_failed_workitem_ids": ["workitem-1"],
                "completed_workitem_ids": [],
                "last_execution_workitem_id": "workitem-1",
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert (
        "summary.blocked_reasons=['manual approval required'] "
        "does not match resume_cursor.blockers=['retry limit reached']"
    ) in result.errors


def test_manifest_verifier_rejects_bad_summary_validation_failure_count(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
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
                "validation_failure_count": 0,
            },
            "executions": [
                {
                    "workitem_id": "workitem-1",
                    "agent_id": "agent-1",
                    "status": "success",
                    "artifact_ids": ["artifact-1"],
                    "artifact_files": [],
                    "validation_success": False,
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "summary.validation_failure_count=0 does not match failed validations=1" in result.errors


def test_manifest_verifier_rejects_bad_requirement_quality_score(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
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
                "requirement_quality_score": 42,
            },
            "requirement_evaluations": [
                {
                    "workitem_id": "workitem-1",
                    "kind": "requirement_spec",
                    "score": 88,
                },
                {
                    "workitem_id": "workitem-2",
                    "kind": "requirement_spec",
                    "score": 75,
                },
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "summary.requirement_quality_score=42 does not match max(requirement_evaluations.score)=88" in result.errors


def test_manifest_verifier_accepts_requirement_quality_score_without_evaluations(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
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
                "requirement_quality_score": 0,
            },
            "requirement_evaluations": [],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True


def test_manifest_verifier_rejects_bad_design_quality_score(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
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
                "design_quality_score": 20,
            },
            "design_evaluations": [
                {
                    "artifact_id": "artifact-design",
                    "kind": "frozen_design_spec",
                    "score": 84,
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "summary.design_quality_score=20 does not match max(design_evaluations.score)=84" in result.errors


def test_manifest_verifier_rejects_bad_requirement_coverage_status(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
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
                "requirement_coverage_status": "pass",
            },
            "requirement_coverage_results": [
                {
                    "workitem_id": "workitem-1",
                    "artifact_id": "artifact-1",
                    "passed": False,
                    "required_rules": ["add_item", "export_csv"],
                    "covered_rules": ["add_item"],
                    "missing_rules": ["export_csv"],
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert (
        "summary.requirement_coverage_status=pass "
        "does not match requirement coverage results=missing_coverage"
    ) in result.errors


def test_manifest_verifier_accepts_requirement_coverage_status_without_results(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
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
                "requirement_coverage_status": "not_evaluated",
            },
            "requirement_coverage_results": [],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True


def test_manifest_verifier_rejects_bad_delivery_readiness_summary(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "delivery_readiness": {
                "status": "blocked",
                "score": 30,
                "blocking_count": 2,
                "warning_count": 1,
                "checks": [],
            },
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
                "delivery_readiness_status": "ready",
                "delivery_readiness_score": 99,
                "delivery_readiness_blocking_count": 0,
                "delivery_readiness_warning_count": 0,
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "summary.delivery_readiness_status=ready does not match delivery_readiness.status=blocked" in result.errors
    assert "summary.delivery_readiness_score=99 does not match delivery_readiness.score=30" in result.errors
    assert "summary.delivery_readiness_blocking_count=0 does not match delivery_readiness.blocking_count=2" in result.errors
    assert "summary.delivery_readiness_warning_count=0 does not match delivery_readiness.warning_count=1" in result.errors


def test_manifest_verifier_rejects_bad_scope_contract_status(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
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
                "scope_contract_status": "pass",
                "scope_contract_violation_count": 1,
            },
            "scope_contract_results": [
                {
                    "artifact_id": "artifact-1",
                    "passed": False,
                    "rule_ids": ["no-auth"],
                    "violations": [
                        {
                            "rule_id": "no-auth",
                            "excerpt": "login screen",
                        }
                    ],
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "summary.scope_contract_status=pass does not match scope contract results=violation" in result.errors


def test_manifest_verifier_rejects_bad_scope_contract_violation_count(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
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
                "scope_contract_status": "violation",
                "scope_contract_violation_count": 0,
            },
            "scope_contract_results": [
                {
                    "artifact_id": "artifact-1",
                    "passed": False,
                    "rule_ids": ["no-auth"],
                    "violations": [{"rule_id": "no-auth"}],
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "summary.scope_contract_violation_count=0 does not match scope contract violations=1" in result.errors


def test_manifest_verifier_accepts_scope_contract_status_without_results(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
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
                "scope_contract_status": "not_evaluated",
                "scope_contract_violation_count": 0,
            },
            "scope_contract_results": [],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True


def test_manifest_verifier_rejects_llm_context_window_mismatch(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
                "final_status": "completed",
                "workitem_count": 1,
                "execution_count": 1,
                "artifact_count": 1,
                "artifact_file_count": 1,
                "task_prompt_file_count": 1,
                "cli_run_count": 0,
                "llm_run_count": 1,
                "collaboration_run_count": 0,
                "retry_history_count": 0,
                "changed_file_count": 0,
                "changed_files": [],
                "llm_context_windows": [
                    {
                        "backend": "local",
                        "model": "qwen2.5",
                        "context_length": 32768,
                    }
                ],
            },
            "llm_runs": [
                {
                    "mode": "workitem_execution",
                    "workitem_id": "workitem-1",
                    "agent_id": "agent-1",
                    "model": "qwen2.5",
                    "context_length": 8192,
                    "token_usage": {},
                    "output_files": [],
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "llm_runs[0].context_length=8192 does not match summary.llm_context_windows[qwen2.5]=32768" in result.errors


def test_manifest_verifier_rejects_malformed_llm_context_windows(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
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
                "llm_context_windows": [
                    {
                        "backend": "local",
                        "model": "qwen2.5",
                        "context_length": "32768",
                    }
                ],
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "summary.llm_context_windows[0].context_length must be an integer or null" in result.errors


def test_manifest_verifier_rejects_summary_changed_files_mismatch_executions(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
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
                "changed_file_count": 1,
                "changed_files": ["ghost.py"],
            },
            "executions": [
                {
                    "workitem_id": "workitem-1",
                    "agent_id": "agent-1",
                    "status": "success",
                    "artifact_ids": ["artifact-1"],
                    "artifact_files": [],
                    "changed_files": ["app.py"],
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "summary.changed_files=['ghost.py'] does not match execution changed_files=['app.py']" in result.errors


def test_manifest_verifier_accepts_deduped_summary_changed_files(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
                "final_status": "completed",
                "workitem_count": 1,
                "execution_count": 2,
                "artifact_count": 1,
                "artifact_file_count": 1,
                "task_prompt_file_count": 1,
                "cli_run_count": 0,
                "llm_run_count": 0,
                "collaboration_run_count": 0,
                "retry_history_count": 0,
                "changed_file_count": 1,
                "changed_files": ["app.py"],
            },
            "executions": [
                {
                    "workitem_id": "workitem-1",
                    "agent_id": "agent-1",
                    "status": "failed",
                    "artifact_ids": ["artifact-1"],
                    "artifact_files": [],
                    "changed_files": ["app.py"],
                },
                {
                    "workitem_id": "workitem-1",
                    "agent_id": "agent-1",
                    "status": "success",
                    "artifact_ids": ["artifact-1"],
                    "artifact_files": [],
                    "changed_files": ["app.py"],
                },
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True


def test_manifest_verifier_rejects_terminal_status_without_terminal_cursor(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "resume_cursor": {
                "project_id": "project-1",
                "project_status": "completed",
                "current_stage": "testing",
                "next_action": "complete",
                "terminal": False,
                "blocked": False,
                "completed_workitem_ids": ["workitem-1"],
                "last_execution_workitem_id": "workitem-1",
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "terminal manifest must have resume_cursor.terminal=true" in result.errors


def test_manifest_verifier_rejects_blocked_status_without_blocked_cursor(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "status": "blocked",
            "final_status": "blocked",
            "current_stage": "requirement",
            "summary": {
                "final_status": "blocked",
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
                "project_status": "blocked",
                "current_stage": "testing",
                "next_action": "blocked",
                "terminal": True,
                "blocked": False,
                "completed_workitem_ids": ["workitem-1"],
                "last_execution_workitem_id": "workitem-1",
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "blocked manifest must have resume_cursor.blocked=true" in result.errors


def test_manifest_verifier_rejects_bad_active_human_control_cursor(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "status": "in_progress",
            "final_status": "in_progress",
            "current_stage": "testing",
            "summary": {
                "final_status": "in_progress",
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
                "human_control_action_count": 1,
            },
            "human_control_actions": [
                {
                    "id": "human-request",
                    "project_id": "project-1",
                    "action": "request_approval",
                    "actor": "tl_agent",
                    "reason": "high risk",
                    "stage": "testing",
                    "workitem_id": "workitem-1",
                    "payload": {"controller_action": "advance_stage", "stage": "testing"},
                }
            ],
            "resume_cursor": {
                "project_id": "project-1",
                "project_status": "in_progress",
                "current_stage": "testing",
                "next_action": "execute_pending",
                "terminal": False,
                "blocked": False,
                "completed_workitem_ids": ["workitem-1"],
                "last_execution_workitem_id": "workitem-1",
                "active_human_control_action": {
                    "id": "human-stale",
                    "project_id": "project-other",
                    "action": "pause",
                    "actor": "operator",
                    "reason": "stale hold",
                    "stage": "testing",
                    "workitem_id": "missing-workitem",
                    "payload": {},
                },
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert (
        "resume_cursor.active_human_control_action does not match human_control_actions active hold"
    ) in result.errors
    assert "resume_cursor.next_action must be human_hold when active_human_control_action is set" in result.errors
    assert "resume_cursor.active_human_control_action.project_id does not match manifest.project_id" in result.errors
    assert (
        "resume_cursor.active_human_control_action references unknown WorkItem: missing-workitem"
    ) in result.errors


def test_manifest_verifier_rejects_non_blocked_status_with_blocked_cursor(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "resume_cursor": {
                "project_id": "project-1",
                "project_status": "completed",
                "current_stage": "testing",
                "next_action": "complete",
                "terminal": True,
                "blocked": True,
                "completed_workitem_ids": ["workitem-1"],
                "last_execution_workitem_id": "workitem-1",
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "non-blocked manifest cannot have resume_cursor.blocked=true" in result.errors


def test_manifest_verifier_allows_in_progress_human_hold_with_blocked_resume_cursor(tmp_path) -> None:
    human_action = {
        "id": "human-request",
        "project_id": "project-1",
        "action": "request_approval",
        "actor": "tl_agent",
        "reason": "terminal test failure needs operator decision",
        "stage": "testing",
        "workitem_id": "workitem-1",
        "payload": {"controller_action": "escalate_project", "stage": "testing"},
    }
    manifest_path = _write_manifest(
        tmp_path,
        {
            "status": "in_progress",
            "final_status": "in_progress",
            "current_stage": "testing",
            "summary": {
                "final_status": "in_progress",
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
                "human_control_action_count": 1,
            },
            "human_control_actions": [human_action],
            "workitems": [
                {
                    "id": "workitem-1",
                    "stage": "testing",
                    "kind": "acceptance_check",
                    "status": "failed",
                    "blocked_reason": "retry limit reached",
                }
            ],
            "task_assignments": [
                {
                    "id": "assignment-1",
                    "workitem_id": "workitem-1",
                    "role": "tester",
                    "status": "failed",
                    "blocked_reason": "retry limit reached",
                }
            ],
            "executions": [
                {
                    "workitem_id": "workitem-1",
                    "agent_id": "agent-1",
                    "status": "failed",
                    "artifact_ids": ["artifact-1"],
                    "artifact_files": [str(tmp_path / "project" / ".conductor" / "artifacts" / "artifact-1.md")],
                }
            ],
            "resume_cursor": {
                "project_id": "project-1",
                "project_status": "in_progress",
                "current_stage": "testing",
                "next_action": "human_hold",
                "terminal": False,
                "blocked": True,
                "blockers": [],
                "next_pending_workitem_ids": [],
                "running_workitem_ids": [],
                "retryable_failed_workitem_ids": [],
                "terminal_failed_workitem_ids": ["workitem-1"],
                "completed_workitem_ids": [],
                "last_execution_workitem_id": "workitem-1",
                "active_human_control_action": human_action,
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "non-blocked manifest cannot have resume_cursor.blocked=true" not in result.errors


def test_manifest_verifier_rejects_completed_cursor_with_pending_work(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "resume_cursor": {
                "project_id": "project-1",
                "project_status": "completed",
                "current_stage": "testing",
                "next_action": "complete",
                "terminal": True,
                "blocked": False,
                "next_pending_workitem_ids": ["workitem-1"],
                "running_workitem_ids": [],
                "retryable_failed_workitem_ids": [],
                "terminal_failed_workitem_ids": [],
                "completed_workitem_ids": ["workitem-1"],
                "last_execution_workitem_id": "workitem-1",
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "completed manifest cannot have resume_cursor.next_pending_workitem_ids" in result.errors


def test_manifest_verifier_rejects_completed_cursor_with_blockers(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "resume_cursor": {
                "project_id": "project-1",
                "project_status": "completed",
                "current_stage": "testing",
                "next_action": "complete",
                "terminal": True,
                "blocked": False,
                "blockers": ["waiting for external approval"],
                "completed_workitem_ids": ["workitem-1"],
                "last_execution_workitem_id": "workitem-1",
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "completed manifest cannot have resume_cursor.blockers" in result.errors


def test_manifest_verifier_rejects_pending_cursor_with_done_workitem(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "status": "in_progress",
            "final_status": "in_progress",
            "summary": {
                "final_status": "in_progress",
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
                "project_status": "in_progress",
                "current_stage": "testing",
                "next_action": "execute_workitem",
                "terminal": False,
                "blocked": False,
                "next_pending_workitem_ids": ["workitem-1"],
                "running_workitem_ids": [],
                "retryable_failed_workitem_ids": [],
                "terminal_failed_workitem_ids": [],
                "completed_workitem_ids": [],
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert (
        "resume_cursor.next_pending_workitem_ids references WorkItem workitem-1 "
        "with status done, expected pending"
    ) in result.errors


def test_manifest_verifier_rejects_completed_cursor_with_failed_workitem(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "workitems": [
                {
                    "id": "workitem-1",
                    "stage": "testing",
                    "kind": "acceptance_check",
                    "status": "failed",
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert (
        "resume_cursor.completed_workitem_ids references WorkItem workitem-1 "
        "with status failed, expected done"
    ) in result.errors


def test_manifest_verifier_warns_for_queued_assignment_with_done_workitem(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "task_assignments": [
                {
                    "id": "assignment-1",
                    "workitem_id": "workitem-1",
                    "role": "tester",
                    "status": "queued",
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert (
        "task assignment assignment-1 status queued references WorkItem workitem-1 "
        "with status done, expected pending"
    ) in result.warnings


def test_manifest_verifier_warns_for_claimed_assignment_with_pending_workitem(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "status": "in_progress",
            "final_status": "in_progress",
            "summary": {
                "final_status": "in_progress",
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
            "workitems": [
                {
                    "id": "workitem-1",
                    "stage": "testing",
                    "kind": "acceptance_check",
                    "status": "pending",
                }
            ],
            "resume_cursor": {
                "project_id": "project-1",
                "project_status": "in_progress",
                "current_stage": "testing",
                "next_action": "execute_workitem",
                "terminal": False,
                "blocked": False,
                "next_pending_workitem_ids": ["workitem-1"],
                "completed_workitem_ids": [],
                "last_execution_workitem_id": "workitem-1",
            },
            "task_assignments": [
                {
                    "id": "assignment-1",
                    "workitem_id": "workitem-1",
                    "role": "tester",
                    "status": "claimed",
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert (
        "task assignment assignment-1 status claimed references WorkItem workitem-1 "
        "with status pending, expected running"
    ) in result.warnings


def test_manifest_verifier_warns_for_latest_execution_workitem_status_drift(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "status": "in_progress",
            "final_status": "in_progress",
            "summary": {
                "final_status": "in_progress",
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
            "workitems": [
                {
                    "id": "workitem-1",
                    "stage": "testing",
                    "kind": "acceptance_check",
                    "status": "failed",
                }
            ],
            "resume_cursor": {
                "project_id": "project-1",
                "project_status": "in_progress",
                "current_stage": "testing",
                "next_action": "blocked",
                "terminal": False,
                "blocked": False,
                "completed_workitem_ids": [],
                "terminal_failed_workitem_ids": ["workitem-1"],
                "last_execution_workitem_id": "workitem-1",
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert (
        "latest execution for WorkItem workitem-1 has status success, "
        "but WorkItem status is failed, expected done"
    ) in result.warnings


def test_manifest_verifier_allows_successful_execution_failed_by_collaboration_gate(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "status": "blocked",
            "final_status": "blocked",
            "current_stage": "requirement",
            "summary": {
                "final_status": "blocked",
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
            "workitems": [
                {
                    "id": "workitem-1",
                    "stage": "requirement",
                    "kind": "requirement_spec",
                    "status": "failed",
                    "blocked_reason": "failure_type=validation_failed; summary=collaboration collaboration-1 ended with max_rounds_reached",
                }
            ],
            "resume_cursor": {
                "project_id": "project-1",
                "project_status": "blocked",
                "current_stage": "requirement",
                "next_action": "blocked",
                "terminal": True,
                "blocked": True,
                "blockers": ["需求门禁连续返工仍未通过"],
                "completed_workitem_ids": [],
                "terminal_failed_workitem_ids": ["workitem-1"],
                "last_execution_workitem_id": "workitem-1",
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert not any("latest execution for WorkItem workitem-1" in warning for warning in result.warnings)


def test_manifest_verifier_uses_latest_execution_for_status_drift(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
                "final_status": "completed",
                "workitem_count": 1,
                "execution_count": 2,
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
            "executions": [
                {
                    "workitem_id": "workitem-1",
                    "agent_id": "agent-1",
                    "status": "failed",
                    "artifact_ids": ["artifact-1"],
                    "artifact_files": [],
                },
                {
                    "workitem_id": "workitem-1",
                    "agent_id": "agent-1",
                    "status": "success",
                    "artifact_ids": ["artifact-1"],
                    "artifact_files": [],
                },
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert not any("latest execution for WorkItem workitem-1" in warning for warning in result.warnings)


def test_manifest_verifier_accepts_feedback_reclassified_failed_test_workitem(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
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
            "workitems": [
                {
                    "id": "workitem-1",
                    "stage": "testing",
                    "kind": "ui_validation",
                    "status": "done",
                    "blocked_reason": "测试失败已回流到研发返工",
                }
            ],
            "executions": [
                {
                    "workitem_id": "workitem-1",
                    "agent_id": "agent-1",
                    "status": "failed",
                    "artifact_ids": ["artifact-1"],
                    "artifact_files": [],
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert not any("latest execution for WorkItem workitem-1" in warning for warning in result.warnings)


def test_manifest_verifier_checks_cli_config_shape_and_bindings(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "selected_cli_names": ["codex"],
            "role_cli_bindings": {
                "designer": "aspirecode",
                "tester": 123,
                "backend_engineer": None,
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "role_cli_bindings.designer references unselected CLI: aspirecode" in result.warnings
    assert "role_cli_bindings.tester must be a string or null" in result.warnings


def test_manifest_verifier_rejects_malformed_cli_config_types(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "selected_cli_names": "codex",
            "role_cli_bindings": ["designer", "codex"],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "selected_cli_names must be a list" in result.errors
    assert "role_cli_bindings must be an object" in result.errors


def test_manifest_verifier_warns_for_malformed_summary_changed_files(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
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
                "changed_files": "app.py",
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "summary.changed_files must be a list" in result.warnings


def test_manifest_verifier_warns_for_malformed_summary_llm_token_usage(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
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
                "llm_token_usage": {
                    "prompt_tokens": -1,
                    "completion_tokens": "many",
                },
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "summary.llm_token_usage.prompt_tokens must be non-negative" in result.warnings
    assert "summary.llm_token_usage.completion_tokens must be an integer" in result.warnings


def test_manifest_verifier_rejects_summary_llm_token_usage_mismatch(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
                "workitem_count": 1,
                "execution_count": 1,
                "artifact_count": 1,
                "artifact_file_count": 1,
                "task_prompt_file_count": 1,
                "cli_run_count": 0,
                "llm_run_count": 2,
                "collaboration_run_count": 0,
                "retry_history_count": 0,
                "changed_file_count": 0,
                "changed_files": [],
                "llm_token_usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 1,
                },
            },
            "llm_runs": [
                {
                    "token_usage": {
                        "prompt_tokens": 7,
                        "completion_tokens": 1,
                    },
                    "output_files": [],
                },
                {
                    "token_usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 1,
                    },
                    "output_files": [],
                },
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert (
        "summary.llm_token_usage={'prompt_tokens': 10, 'completion_tokens': 1} "
        "does not match llm_runs token_usage={'prompt_tokens': 10, 'completion_tokens': 2}"
    ) in result.errors


def test_manifest_verifier_accepts_matching_summary_llm_token_usage(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
                "workitem_count": 1,
                "execution_count": 1,
                "artifact_count": 1,
                "artifact_file_count": 1,
                "task_prompt_file_count": 1,
                "cli_run_count": 0,
                "llm_run_count": 2,
                "collaboration_run_count": 0,
                "retry_history_count": 0,
                "changed_file_count": 0,
                "changed_files": [],
                "llm_token_usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                },
            },
            "llm_runs": [
                {
                    "token_usage": {
                        "prompt_tokens": 7,
                        "completion_tokens": 1,
                    },
                    "output_files": [],
                },
                {
                    "token_usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 1,
                    },
                    "output_files": [],
                },
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True


def test_manifest_verifier_warns_for_malformed_summary_llm_cost_estimate(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
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
                "llm_cost_estimate": {
                    "estimated_total": -0.1,
                    "model_costs": [
                        {"model": "local", "estimated_cost": "unknown"},
                        {"model": "cloud", "estimated_cost": -1},
                        "bad-record",
                    ],
                },
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "summary.llm_cost_estimate.estimated_total must be non-negative" in result.warnings
    assert "summary.llm_cost_estimate.model_costs[0].estimated_cost must be a number" in result.warnings
    assert "summary.llm_cost_estimate.model_costs[1].estimated_cost must be non-negative" in result.warnings
    assert "summary.llm_cost_estimate.model_costs[2] must be an object" in result.warnings


def test_manifest_verifier_rejects_summary_llm_cost_estimate_total_mismatch(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
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
                "llm_cost_estimate": {
                    "estimated_total": 1.25,
                    "model_costs": [
                        {"model": "local-a", "estimated_cost": 0.4},
                        {"model": "local-b", "estimated_cost": 0.5},
                    ],
                },
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "summary.llm_cost_estimate.estimated_total=1.25 does not match sum(model_costs)=0.9" in result.errors


def test_manifest_verifier_accepts_matching_summary_llm_cost_estimate(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
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
                "llm_cost_estimate": {
                    "estimated_total": 0.9,
                    "model_costs": [
                        {"model": "local-a", "estimated_cost": 0.4},
                        {"model": "local-b", "estimated_cost": 0.5},
                    ],
                },
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True


def test_manifest_verifier_rejects_summary_llm_cost_model_usage_mismatch(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
                "workitem_count": 1,
                "execution_count": 1,
                "artifact_count": 1,
                "artifact_file_count": 1,
                "task_prompt_file_count": 1,
                "cli_run_count": 0,
                "llm_run_count": 2,
                "collaboration_run_count": 0,
                "retry_history_count": 0,
                "changed_file_count": 0,
                "changed_files": [],
                "llm_cost_estimate": {
                    "estimated_total": 0.9,
                    "model_costs": [
                        {
                            "model": "qwen-local",
                            "token_usage": {
                                "prompt_tokens": 8,
                                "completion_tokens": 2,
                            },
                            "estimated_cost": 0.9,
                        }
                    ],
                },
            },
            "llm_runs": [
                {
                    "model": "qwen-local",
                    "token_usage": {
                        "prompt_tokens": 7,
                        "completion_tokens": 1,
                    },
                    "output_files": [],
                },
                {
                    "model": "qwen-local",
                    "token_usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 1,
                    },
                    "output_files": [],
                },
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert (
        "summary.llm_cost_estimate.model_costs[0].token_usage={'prompt_tokens': 8, 'completion_tokens': 2} "
        "does not match llm_runs token_usage for qwen-local={'prompt_tokens': 10, 'completion_tokens': 2}"
    ) in result.errors


def test_manifest_verifier_accepts_summary_llm_cost_model_usage_match(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
                "workitem_count": 1,
                "execution_count": 1,
                "artifact_count": 1,
                "artifact_file_count": 1,
                "task_prompt_file_count": 1,
                "cli_run_count": 0,
                "llm_run_count": 2,
                "collaboration_run_count": 0,
                "retry_history_count": 0,
                "changed_file_count": 0,
                "changed_files": [],
                "llm_cost_estimate": {
                    "estimated_total": 0.9,
                    "model_costs": [
                        {
                            "model": "qwen-local",
                            "token_usage": {
                                "prompt_tokens": 10,
                                "completion_tokens": 2,
                            },
                            "estimated_cost": 0.9,
                        }
                    ],
                },
            },
            "llm_runs": [
                {
                    "model": "qwen-local",
                    "token_usage": {
                        "prompt_tokens": 7,
                        "completion_tokens": 1,
                    },
                    "output_files": [],
                },
                {
                    "model": "qwen-local",
                    "token_usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 1,
                    },
                    "output_files": [],
                },
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True


def test_manifest_verifier_warns_for_malformed_resume_cursor_lists(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "resume_cursor": {
                "project_id": "project-1",
                "project_status": "completed",
                "current_stage": "testing",
                "next_action": "complete",
                "terminal": True,
                "completed_workitem_ids": "workitem-1",
                "next_pending_workitem_ids": "workitem-2",
                "last_execution_workitem_id": "workitem-1",
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "resume_cursor.completed_workitem_ids must be a list" in result.warnings
    assert "resume_cursor.next_pending_workitem_ids must be a list" in result.warnings


def test_manifest_verifier_warns_for_malformed_files_indexes(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "files": {
                "log": str(tmp_path / "project" / ".conductor" / "logs" / "project-1.jsonl"),
                "report": str(tmp_path / "project" / ".conductor" / "reports" / "project-1.md"),
                "manifest": str(tmp_path / "project" / ".conductor" / "manifests" / "project-1.manifest.json"),
                "artifacts": "artifact-1.md",
                "task_prompts": "prompt.md",
                "preflight_gate": "",
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "files artifacts must be a list" in result.warnings
    assert "files task_prompts must be a list" in result.warnings


def test_manifest_verifier_rejects_preflight_gate_summary_mismatch(tmp_path) -> None:
    gate_path = tmp_path / "project" / ".conductor" / "diagnostics" / "run-preflight" / "preflight-gate.json"
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    gate_path.write_text(
        json.dumps(
            {
                "ok": False,
                "preflight_gate": {
                    "errors": ["local server unavailable"],
                    "recommendations": ["Start LM Studio"],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    manifest_path = _write_manifest(
        tmp_path,
        {
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
                "preflight_gate_ok": True,
                "preflight_gate_errors": [],
                "preflight_gate_recommendations": [],
            },
            "files": {
                "log": str(tmp_path / "project" / ".conductor" / "logs" / "project-1.jsonl"),
                "report": str(tmp_path / "project" / ".conductor" / "reports" / "project-1.md"),
                "manifest": str(tmp_path / "project" / ".conductor" / "manifests" / "project-1.manifest.json"),
                "artifacts": [str(tmp_path / "project" / ".conductor" / "artifacts" / "artifact-1.md")],
                "task_prompts": [str(tmp_path / "project" / ".conductor" / "task_prompts" / "task-1.md")],
                "preflight_gate": str(gate_path),
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "summary.preflight_gate_ok=True does not match preflight gate ok=False" in result.errors
    assert (
        "summary.preflight_gate_errors=[] does not match preflight gate errors=['local server unavailable']"
        in result.errors
    )
    assert (
        "summary.preflight_gate_recommendations=[] does not match preflight gate recommendations=['Start LM Studio']"
        in result.errors
    )


def test_manifest_verifier_accepts_matching_preflight_gate_summary(tmp_path) -> None:
    gate_path = tmp_path / "project" / ".conductor" / "diagnostics" / "run-preflight" / "preflight-gate.json"
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    gate_path.write_text(
        json.dumps(
            {
                "ok": False,
                "preflight_gate": {
                    "errors": ["local server unavailable"],
                    "recommendations": ["Start LM Studio"],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    manifest_path = _write_manifest(
        tmp_path,
        {
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
                "preflight_gate_ok": False,
                "preflight_gate_errors": ["local server unavailable"],
                "preflight_gate_recommendations": ["Start LM Studio"],
            },
            "files": {
                "log": str(tmp_path / "project" / ".conductor" / "logs" / "project-1.jsonl"),
                "report": str(tmp_path / "project" / ".conductor" / "reports" / "project-1.md"),
                "manifest": str(tmp_path / "project" / ".conductor" / "manifests" / "project-1.manifest.json"),
                "artifacts": [str(tmp_path / "project" / ".conductor" / "artifacts" / "artifact-1.md")],
                "task_prompts": [str(tmp_path / "project" / ".conductor" / "task_prompts" / "task-1.md")],
                "preflight_gate": str(gate_path),
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True


def test_manifest_verifier_warns_for_files_artifact_index_mismatch(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "files": {
                "log": str(tmp_path / "project" / ".conductor" / "logs" / "project-1.jsonl"),
                "report": str(tmp_path / "project" / ".conductor" / "reports" / "project-1.md"),
                "manifest": str(tmp_path / "project" / ".conductor" / "manifests" / "project-1.manifest.json"),
                "artifacts": [str(tmp_path / "missing-artifact.md")],
                "task_prompts": [str(tmp_path / "missing-prompt.md")],
                "preflight_gate": "",
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert any("files.artifacts entry does not exist" in warning for warning in result.warnings)
    assert any("files.task_prompts entry does not exist" in warning for warning in result.warnings)
    assert any("files.artifacts entry is not indexed in artifact_files" in warning for warning in result.warnings)
    assert any("files.task_prompts entry is not indexed in task_prompt_files" in warning for warning in result.warnings)


def test_manifest_verifier_warns_for_top_level_file_indexes_missing_from_files(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "files": {
                "log": str(tmp_path / "project" / ".conductor" / "logs" / "project-1.jsonl"),
                "report": str(tmp_path / "project" / ".conductor" / "reports" / "project-1.md"),
                "manifest": str(tmp_path / "project" / ".conductor" / "manifests" / "project-1.manifest.json"),
                "artifacts": [],
                "task_prompts": [],
                "preflight_gate": "",
            },
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert any("artifact_files entry is not indexed in files.artifacts" in warning for warning in result.warnings)
    assert any("task_prompt_files entry is not indexed in files.task_prompts" in warning for warning in result.warnings)


def test_manifest_verifier_warns_for_unindexed_assignment_prompt_file(tmp_path) -> None:
    prompt_path = tmp_path / "project" / ".conductor" / "task_prompts" / "missing-assignment-prompt.md"
    manifest_path = _write_manifest(
        tmp_path,
        {
            "task_assignments": [
                {
                    "id": "assignment-1",
                    "workitem_id": "workitem-1",
                    "role": "tester",
                    "status": "completed",
                    "prompt_file": str(prompt_path),
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert f"task assignment prompt_file does not exist: {prompt_path}" in result.warnings
    assert f"task assignment prompt_file is not indexed in task_prompt_files: {prompt_path}" in result.warnings


def test_manifest_verifier_warns_for_unindexed_execution_artifact_file(tmp_path) -> None:
    artifact_path = tmp_path / "project" / ".conductor" / "artifacts" / "missing-execution-artifact.md"
    manifest_path = _write_manifest(
        tmp_path,
        {
            "executions": [
                {
                    "workitem_id": "workitem-1",
                    "agent_id": "agent-1",
                    "status": "success",
                    "artifact_ids": ["artifact-1"],
                    "artifact_files": [str(artifact_path)],
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert f"execution workitem-1 artifact_files entry does not exist: {artifact_path}" in result.warnings
    assert f"execution workitem-1 artifact_files entry is not indexed in artifact_files: {artifact_path}" in result.warnings


def test_manifest_verifier_warns_for_run_output_files(tmp_path) -> None:
    missing_cli_output = tmp_path / "missing-cli-output.md"
    missing_llm_output = tmp_path / "missing-llm-output.md"
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
                "workitem_count": 1,
                "execution_count": 1,
                "artifact_count": 1,
                "artifact_file_count": 1,
                "task_prompt_file_count": 1,
                "cli_run_count": 1,
                "llm_run_count": 1,
                "collaboration_run_count": 0,
                "retry_history_count": 0,
                "changed_file_count": 0,
                "changed_files": [],
            },
            "cli_runs": [
                {
                    "agent_id": "agent-cli",
                    "output_files": "not-a-list",
                }
            ],
            "llm_runs": [
                {
                    "agent_id": "agent-llm",
                    "output_files": [str(missing_llm_output)],
                }
            ],
        },
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["cli_runs"][0]["output_files"] = [str(missing_cli_output)]
    payload["llm_runs"][0]["output_files"] = "not-a-list"
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert f"cli_runs[0].output_files entry does not exist: {missing_cli_output}" in result.warnings
    assert "llm_runs[0] output_files must be a list" in result.warnings


def test_manifest_verifier_warns_for_malformed_llm_run_token_usage(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
                "workitem_count": 1,
                "execution_count": 1,
                "artifact_count": 1,
                "artifact_file_count": 1,
                "task_prompt_file_count": 1,
                "cli_run_count": 0,
                "llm_run_count": 1,
                "collaboration_run_count": 0,
                "retry_history_count": 0,
                "changed_file_count": 0,
                "changed_files": [],
            },
            "llm_runs": [
                {
                    "agent_id": "agent-llm",
                    "token_usage": {
                        "prompt_tokens": "-1",
                        "completion_tokens": {},
                    },
                    "output_files": [],
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "llm_runs[0].token_usage.prompt_tokens must be an integer" in result.warnings
    assert "llm_runs[0].token_usage.completion_tokens must be an integer" in result.warnings


def test_manifest_verifier_warns_when_llm_run_model_is_not_auditable(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
                "workitem_count": 1,
                "execution_count": 1,
                "artifact_count": 1,
                "artifact_file_count": 1,
                "task_prompt_file_count": 1,
                "cli_run_count": 0,
                "llm_run_count": 2,
                "collaboration_run_count": 0,
                "retry_history_count": 0,
                "changed_file_count": 0,
                "changed_files": [],
            },
            "llm_runs": [
                {
                    "source_backend": "llm/cloud",
                    "agent_id": "agent-llm",
                    "model": "cloud",
                    "token_usage": {},
                    "output_files": [],
                },
                {
                    "source_backend": "llm/cloud",
                    "agent_id": "agent-llm",
                    "token_usage": {},
                    "output_files": [],
                },
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "llm_runs[0].model should identify the actual model, not the backend label: cloud" in result.warnings
    assert "llm_runs[1].model is missing; cannot audit actual LLM provider usage" in result.warnings


def test_manifest_verifier_rejects_summary_llm_identity_mismatch(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
                "workitem_count": 1,
                "execution_count": 1,
                "artifact_count": 1,
                "artifact_file_count": 1,
                "task_prompt_file_count": 1,
                "cli_run_count": 0,
                "llm_run_count": 1,
                "llm_models": ["cloud"],
                "llm_source_backends": ["llm/local"],
                "collaboration_run_count": 0,
                "retry_history_count": 0,
                "changed_file_count": 0,
                "changed_files": [],
            },
            "llm_runs": [
                {
                    "source_backend": "llm/cloud",
                    "agent_id": "agent-llm",
                    "model": "jiutian-lan-comv3",
                    "token_usage": {},
                    "output_files": [],
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "summary.llm_models=['cloud'] does not match llm_runs model=['jiutian-lan-comv3']" in result.errors
    assert "summary.llm_source_backends=['llm/local'] does not match llm_runs source_backend=['llm/cloud']" in result.errors


def test_manifest_verifier_rejects_run_unknown_workitem_references(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
                "workitem_count": 1,
                "execution_count": 1,
                "artifact_count": 1,
                "artifact_file_count": 1,
                "task_prompt_file_count": 1,
                "cli_run_count": 1,
                "llm_run_count": 1,
                "collaboration_run_count": 0,
                "retry_history_count": 0,
                "changed_file_count": 0,
                "changed_files": [],
            },
            "agents": [{"agent_id": "agent-1"}],
            "cli_runs": [
                {
                    "workitem_id": "missing-cli-workitem",
                    "agent_id": "agent-1",
                    "output_files": [],
                }
            ],
            "llm_runs": [
                {
                    "workitem_id": "missing-llm-workitem",
                    "agent_id": "agent-1",
                    "token_usage": {},
                    "output_files": [],
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "cli_runs[0] references unknown WorkItem: missing-cli-workitem" in result.errors
    assert "llm_runs[0] references unknown WorkItem: missing-llm-workitem" in result.errors


def test_manifest_verifier_warns_for_run_unknown_agent_and_collaboration(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
                "workitem_count": 1,
                "execution_count": 1,
                "artifact_count": 1,
                "artifact_file_count": 1,
                "task_prompt_file_count": 1,
                "cli_run_count": 0,
                "llm_run_count": 1,
                "collaboration_run_count": 0,
                "retry_history_count": 0,
                "changed_file_count": 0,
                "changed_files": [],
            },
            "agents": [{"agent_id": "agent-1"}],
            "llm_runs": [
                {
                    "workitem_id": "workitem-1",
                    "agent_id": "agent-missing",
                    "collaboration_id": "collaboration-missing",
                    "token_usage": {},
                    "output_files": [],
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "llm_runs[0] references unknown Agent: agent-missing" in result.warnings
    assert "llm_runs[0] references unknown CollaborationRun: collaboration-missing" in result.warnings


def test_manifest_verifier_rejects_unknown_retry_history_workitem(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
                "workitem_count": 1,
                "execution_count": 1,
                "artifact_count": 1,
                "artifact_file_count": 1,
                "task_prompt_file_count": 1,
                "cli_run_count": 0,
                "llm_run_count": 0,
                "collaboration_run_count": 0,
                "retry_history_count": 1,
                "changed_file_count": 0,
                "changed_files": [],
            },
            "retry_history": [
                {
                    "workitem_id": "missing-workitem",
                    "retry_count": 1,
                    "max_retries": 2,
                    "related_events": [],
                    "related_gate_history": [],
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "retry_history[0] references unknown WorkItem: missing-workitem" in result.errors


def test_manifest_verifier_warns_for_malformed_retry_history_fields(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
                "workitem_count": 1,
                "execution_count": 1,
                "artifact_count": 1,
                "artifact_file_count": 1,
                "task_prompt_file_count": 1,
                "cli_run_count": 0,
                "llm_run_count": 0,
                "collaboration_run_count": 0,
                "retry_history_count": 1,
                "changed_file_count": 0,
                "changed_files": [],
            },
            "retry_history": [
                {
                    "workitem_id": "workitem-1",
                    "retry_count": -1,
                    "max_retries": "many",
                    "related_events": "event",
                    "related_gate_history": "testing:retry",
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "retry_history[0].retry_count must be non-negative" in result.warnings
    assert "retry_history[0].max_retries must be an integer" in result.warnings
    assert "retry_history[0] related_events must be a list" in result.warnings
    assert "retry_history[0] related_gate_history must be a list" in result.warnings


def test_manifest_verifier_checks_collaboration_run_links(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
                "workitem_count": 1,
                "execution_count": 1,
                "artifact_count": 1,
                "artifact_file_count": 1,
                "task_prompt_file_count": 1,
                "cli_run_count": 0,
                "llm_run_count": 0,
                "collaboration_run_count": 1,
                "retry_history_count": 0,
                "changed_file_count": 0,
                "changed_files": [],
            },
            "collaboration_runs": [
                {
                    "id": "collaboration-1",
                    "workitem_id": "missing-workitem",
                    "final_artifact_id": "missing-artifact",
                    "reviewer_agent_ids": ["agent-reviewer"],
                    "review_count": 2,
                    "draft_version_count": 2,
                    "reviews": [
                        {
                            "id": "review-1",
                            "agent_id": "agent-reviewer",
                            "decision": "request_changes",
                        }
                    ],
                    "draft_versions": [
                        {
                            "version": 1,
                            "review_ids": ["missing-review"],
                        }
                    ],
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "collaboration_runs[0] references unknown WorkItem: missing-workitem" in result.errors
    assert "collaboration_runs[0] final_artifact_id is not indexed in artifacts: missing-artifact" in result.warnings
    assert "collaboration_runs[0].review_count does not match len(reviews)" in result.warnings
    assert "collaboration_runs[0].draft_version_count does not match len(draft_versions)" in result.warnings
    assert "collaboration_runs[0].draft_versions[0] references unknown review_id: missing-review" in result.warnings


def test_manifest_verifier_warns_for_malformed_collaboration_run_lists(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
                "workitem_count": 1,
                "execution_count": 1,
                "artifact_count": 1,
                "artifact_file_count": 1,
                "task_prompt_file_count": 1,
                "cli_run_count": 0,
                "llm_run_count": 0,
                "collaboration_run_count": 1,
                "retry_history_count": 0,
                "changed_file_count": 0,
                "changed_files": [],
            },
            "collaboration_runs": [
                {
                    "id": "collaboration-1",
                    "workitem_id": "workitem-1",
                    "reviewer_agent_ids": "agent-reviewer",
                    "reviews": "review-1",
                    "draft_versions": [
                        {
                            "version": 1,
                            "review_ids": "review-1",
                        }
                    ],
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "collaboration_runs[0] reviewer_agent_ids must be a list" in result.warnings
    assert "collaboration_runs[0] reviews must be a list" in result.warnings
    assert "collaboration_runs[0].draft_versions[0] review_ids must be a list" in result.warnings


def test_manifest_verifier_warns_for_missing_collaboration_output_paths(tmp_path) -> None:
    missing_review = tmp_path / "missing-review.md"
    missing_draft = tmp_path / "missing-draft.md"
    manifest_path = _write_manifest(
        tmp_path,
        {
            "summary": {
                "workitem_count": 1,
                "execution_count": 1,
                "artifact_count": 1,
                "artifact_file_count": 1,
                "task_prompt_file_count": 1,
                "cli_run_count": 0,
                "llm_run_count": 0,
                "collaboration_run_count": 1,
                "retry_history_count": 0,
                "changed_file_count": 0,
                "changed_files": [],
            },
            "collaboration_runs": [
                {
                    "id": "collaboration-1",
                    "workitem_id": "workitem-1",
                    "review_count": 1,
                    "draft_version_count": 1,
                    "reviews": [
                        {
                            "id": "review-1",
                            "agent_id": "agent-reviewer",
                            "decision": "request_changes",
                            "output_path": str(missing_review),
                        }
                    ],
                    "draft_versions": [
                        {
                            "version": 1,
                            "review_ids": ["review-1"],
                            "output_path": str(missing_draft),
                        }
                    ],
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert f"collaboration_runs[0].reviews[0].output_path does not exist: {missing_review}" in result.warnings
    assert f"collaboration_runs[0].draft_versions[0].output_path does not exist: {missing_draft}" in result.warnings


def test_manifest_verifier_warns_for_unknown_agent_references_when_agents_are_indexed(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "agents": [
                {
                    "agent_id": "agent-known",
                    "role": "tester",
                }
            ],
            "summary": {
                "workitem_count": 1,
                "execution_count": 1,
                "artifact_count": 1,
                "artifact_file_count": 1,
                "task_prompt_file_count": 1,
                "cli_run_count": 0,
                "llm_run_count": 0,
                "collaboration_run_count": 1,
                "retry_history_count": 0,
                "changed_file_count": 0,
                "changed_files": [],
            },
            "task_assignments": [
                {
                    "id": "assignment-1",
                    "workitem_id": "workitem-1",
                    "role": "tester",
                    "status": "completed",
                    "assigned_agent_id": "agent-missing-assignment",
                }
            ],
            "collaboration_runs": [
                {
                    "id": "collaboration-1",
                    "workitem_id": "workitem-1",
                    "lead_agent_id": "agent-missing-lead",
                    "reviewer_agent_ids": ["agent-missing-reviewer"],
                    "review_count": 1,
                    "draft_version_count": 1,
                    "reviews": [
                        {
                            "id": "review-1",
                            "agent_id": "agent-missing-review",
                        }
                    ],
                    "draft_versions": [
                        {
                            "version": 1,
                            "author_agent_id": "agent-missing-author",
                            "review_ids": ["review-1"],
                        }
                    ],
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "execution for workitem-1 references unknown Agent: agent-1" in result.warnings
    assert "artifact artifact-1 references unknown Agent: agent-1" in result.warnings
    assert "task assignment assignment-1 references unknown Agent: agent-missing-assignment" in result.warnings
    assert "collaboration_runs[0].lead_agent_id references unknown Agent: agent-missing-lead" in result.warnings
    assert "collaboration_runs[0].reviewer_agent_ids references unknown Agent: agent-missing-reviewer" in result.warnings
    assert "collaboration_runs[0].reviews[0].agent_id references unknown Agent: agent-missing-review" in result.warnings
    assert (
        "collaboration_runs[0].draft_versions[0].author_agent_id references unknown Agent: agent-missing-author"
        in result.warnings
    )


def test_manifest_verifier_allows_specialized_agent_seat_references(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "agents": [
                {
                    "agent_id": "agent-designer",
                    "role": "designer",
                }
            ],
            "summary": {
                "workitem_count": 1,
                "execution_count": 1,
                "artifact_count": 1,
                "artifact_file_count": 1,
                "task_prompt_file_count": 1,
                "cli_run_count": 0,
                "llm_run_count": 0,
                "collaboration_run_count": 1,
                "retry_history_count": 0,
                "changed_file_count": 0,
                "changed_files": [],
            },
            "executions": [
                {
                    "workitem_id": "workitem-1",
                    "agent_id": "agent-designer:interaction",
                    "status": "success",
                    "artifact_ids": ["artifact-1"],
                    "artifact_files": [],
                }
            ],
            "artifacts": [
                {
                    "id": "artifact-1",
                    "project_id": "project-1",
                    "workitem_id": "workitem-1",
                    "title": "Report",
                    "kind": "test_report",
                    "agent_id": "agent-designer:interaction",
                    "path": str(tmp_path / "project" / ".conductor" / "artifacts" / "artifact-1.md"),
                }
            ],
            "collaboration_runs": [
                {
                    "id": "collaboration-1",
                    "workitem_id": "workitem-1",
                    "lead_agent_id": "agent-designer:interaction",
                    "reviewer_agent_ids": ["agent-designer:information_architecture"],
                    "review_count": 1,
                    "draft_version_count": 1,
                    "reviews": [
                        {
                            "id": "review-1",
                            "agent_id": "agent-designer:information_architecture",
                        }
                    ],
                    "draft_versions": [
                        {
                            "version": 1,
                            "author_agent_id": "agent-designer:interaction",
                            "review_ids": ["review-1"],
                        }
                    ],
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert not any("references unknown Agent" in warning for warning in result.warnings)


def test_manifest_verifier_rejects_malformed_agent_index(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "agents": [
                {
                    "agent_id": "agent-1",
                    "role": "tester",
                },
                {
                    "agent_id": "agent-1",
                    "role": "duplicate",
                },
                {},
                "bad-agent-record",
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "agents contains duplicate agent_id: agent-1" in result.errors
    assert "agents contains a record without agent_id" in result.errors
    assert "agents contains a non-object record" in result.errors


def test_manifest_verifier_warns_for_agent_record_link_breaks(tmp_path) -> None:
    missing_output = tmp_path / "missing-agent-output.md"
    manifest_path = _write_manifest(
        tmp_path,
        {
            "agents": [
                {
                    "agent_id": "agent-1",
                    "role": "tester",
                    "workitem_ids": ["missing-workitem"],
                    "artifact_ids": ["missing-artifact"],
                    "output_files": [str(missing_output)],
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "agents[0] workitem_ids references unknown WorkItem: missing-workitem" in result.warnings
    assert "agents[0] artifact_ids references unknown Artifact: missing-artifact" in result.warnings
    assert f"agents[0].output_files entry does not exist: {missing_output}" in result.warnings


def test_manifest_verifier_warns_for_malformed_agent_record_lists(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "agents": [
                {
                    "agent_id": "agent-1",
                    "role": "tester",
                    "workitem_ids": "workitem-1",
                    "artifact_ids": "artifact-1",
                    "output_files": "artifact-1.md",
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "agents[0] workitem_ids must be a list" in result.warnings
    assert "agents[0] artifact_ids must be a list" in result.warnings
    assert "agents[0] output_files must be a list" in result.warnings


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


def test_manifest_verifier_rejects_unknown_workitem_dependencies(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "workitems": [
                {
                    "id": "workitem-1",
                    "stage": "testing",
                    "kind": "acceptance_check",
                    "status": "done",
                    "dependencies": ["missing-workitem"],
                }
            ]
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "workitem workitem-1 dependency references unknown WorkItem: missing-workitem" in result.errors


def test_manifest_verifier_warns_for_malformed_workitem_relationship_lists(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "workitems": [
                {
                    "id": "workitem-1",
                    "stage": "testing",
                    "kind": "acceptance_check",
                    "status": "done",
                    "dependencies": "workitem-0",
                    "input_artifact_ids": "artifact-input",
                    "output_artifact_ids": "artifact-output",
                    "feedback_from": "workitem-feedback",
                    "testing_feedback": "bad-feedback",
                }
            ]
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "workitem workitem-1 dependencies must be a list" in result.warnings
    assert "workitem workitem-1 input_artifact_ids must be a list" in result.warnings
    assert "workitem workitem-1 output_artifact_ids must be a list" in result.warnings
    assert "workitem workitem-1 feedback_from must be a list" in result.warnings
    assert "workitem workitem-1 testing_feedback must be a list" in result.warnings


def test_manifest_verifier_rejects_unknown_testing_feedback_workitem(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "workitems": [
                {
                    "id": "workitem-1",
                    "stage": "development",
                    "kind": "ui_implementation",
                    "status": "pending",
                    "testing_feedback": [{"workitem_id": "missing-test", "failing_checks": []}],
                }
            ]
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert "workitem workitem-1.testing_feedback[0] references unknown WorkItem: missing-test" in result.errors


def test_manifest_verifier_warns_for_malformed_testing_checklist(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "workitems": [
                {
                    "id": "workitem-1",
                    "stage": "testing",
                    "kind": "acceptance_check",
                    "status": "done",
                    "testing_checklist": [
                        {
                            "rule_id": 123,
                            "label": "CSV export/download",
                            "status": "unknown",
                            "requirement_terms": "CSV",
                            "required_evidence_terms": [],
                        }
                    ],
                }
            ]
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "workitem workitem-1.testing_checklist[0].rule_id must be a string" in result.warnings
    assert "workitem workitem-1.testing_checklist[0].status has unknown value: unknown" in result.warnings
    assert "workitem workitem-1.testing_checklist[0] requirement_terms must be a list" in result.warnings


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


def test_manifest_verifier_warns_for_malformed_task_assignment_relationship_lists(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "task_assignments": [
                {
                    "id": "assignment-1",
                    "workitem_id": "workitem-1",
                    "role": "tester",
                    "status": "completed",
                    "dependencies": "workitem-0",
                    "input_artifact_ids": "artifact-input",
                    "output_artifact_ids": "artifact-output",
                }
            ]
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "task assignment assignment-1 dependencies must be a list" in result.warnings
    assert "task assignment assignment-1 input_artifact_ids must be a list" in result.warnings
    assert "task assignment assignment-1 output_artifact_ids must be a list" in result.warnings


def test_manifest_verifier_warns_for_malformed_execution_artifact_ids(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "executions": [
                {
                    "workitem_id": "workitem-1",
                    "agent_id": "agent-1",
                    "status": "success",
                    "artifact_ids": "artifact-1",
                    "artifact_files": "artifact-1.md",
                    "changed_files": "app.py",
                }
            ]
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is True
    assert "execution for workitem-1 artifact_ids must be a list" in result.warnings
    assert "execution for workitem-1 artifact_files must be a list" in result.warnings
    assert "execution for workitem-1 changed_files must be a list" in result.warnings


def test_manifest_verifier_checks_execution_delivery_contract_and_acceptance_trace(tmp_path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "schema_version": "1.35",
            "executions": [
                {
                    "workitem_id": "workitem-1",
                    "agent_id": "agent-1",
                    "status": "success",
                    "artifact_ids": ["artifact-1"],
                    "artifact_files": [],
                    "changed_files": [],
                    "delivery_contract": {
                        "stage": "testing",
                        "kind": "acceptance_check",
                        "role": "tester",
                        "required_input_artifact_ids": ["missing-artifact"],
                        "required_input_kinds": "frozen_requirement_spec",
                        "expected_outputs": [],
                        "guardrails": [],
                        "verification_focus": [],
                    },
                    "acceptance_trace": [
                        {
                            "criterion": "should pass",
                            "status": "unknown",
                            "evidence": 123,
                        }
                    ],
                }
            ],
        },
    )

    result = verify_manifest(manifest_path)

    assert result.passed is False
    assert (
        "execution for workitem-1.delivery_contract.required_input_artifact_ids "
        "references unknown Artifact: missing-artifact"
    ) in result.errors
    assert "execution for workitem-1.delivery_contract required_input_kinds must be a list" in result.warnings
    assert "execution for workitem-1.acceptance_trace[0].status has unknown value: unknown" in result.warnings
    assert "execution for workitem-1.acceptance_trace[0].evidence must be a string" in result.warnings


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
    assert "manifest schema_version 1.0 differs from current 1.36" in result.warnings


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
