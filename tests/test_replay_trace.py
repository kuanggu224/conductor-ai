"""Read-only manifest replay trace tests."""

from __future__ import annotations

import json
from pathlib import Path

from app.replay_manifest import main as replay_manifest_main
from conductor.replay_trace import build_manifest_replay_trace


def _write_manifest(tmp_path: Path, overrides: dict[str, object] | None = None) -> Path:
    project_root = tmp_path / "project"
    manifest_path = project_root / ".conductor" / "manifests" / "project-1.manifest.json"
    report_path = project_root / ".conductor" / "reports" / "project-1.md"
    log_path = project_root / ".conductor" / "logs" / "project-1.jsonl"
    for path in (manifest_path, report_path, log_path):
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
            "artifact_count": 0,
            "artifact_file_count": 0,
            "task_prompt_file_count": 0,
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
                "agent_id": "agent-tester",
                "status": "success",
                "source_backend": "mock",
                "artifact_ids": [],
                "artifact_files": [],
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
                "owner_agent": "agent-tester",
            }
        ],
        "task_assignments": [
            {
                "id": "assignment-1",
                "workitem_id": "workitem-1",
                "role": "tester",
                "status": "completed",
                "assigned_agent_id": "agent-tester",
                "claim_token": "secret-token",
                "claim_reason": "acceptance check",
                "claimable": False,
                "stale_claimed": False,
                "prompt_file": "prompt.md",
            }
        ],
        "artifacts": [],
        "artifact_files": [],
        "task_prompt_files": [],
        "files": {
            "log": str(log_path),
            "report": str(report_path),
            "manifest": str(manifest_path),
            "artifacts": [],
            "task_prompts": [],
            "preflight_gate": "",
        },
        "log_path": str(log_path),
        "report_path": str(report_path),
    }
    if overrides:
        payload.update(overrides)
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def test_replay_trace_builds_deterministic_events(tmp_path) -> None:
    manifest_path = _write_manifest(tmp_path)

    trace = build_manifest_replay_trace(manifest_path)

    assert trace.passed is True
    assert [event.event_type for event in trace.events] == [
        "project",
        "workitem",
        "task_assignment",
        "execution",
        "terminal",
    ]
    assert trace.events[1].workitem_id == "workitem-1"
    assert trace.events[2].metadata["assignment_id"] == "assignment-1"
    assert "claim_token" not in trace.events[2].metadata
    assert trace.events[3].agent_id == "agent-tester"
    assert trace.events[4].metadata["next_action"] == "complete"


def test_replay_trace_refuses_invalid_manifest(tmp_path) -> None:
    manifest_path = _write_manifest(tmp_path, {"project_id": ""})

    trace = build_manifest_replay_trace(manifest_path)

    assert trace.passed is False
    assert trace.events == []
    assert any("project_id must be non-empty" in error for error in trace.verification.errors)


def test_replay_manifest_cli_outputs_json(tmp_path, capsys) -> None:
    manifest_path = _write_manifest(tmp_path)

    exit_code = replay_manifest_main([str(manifest_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["passed"] is True
    assert payload["event_count"] == 5


def test_replay_manifest_cli_outputs_markdown(tmp_path, capsys) -> None:
    manifest_path = _write_manifest(tmp_path)

    exit_code = replay_manifest_main([str(manifest_path), "--format", "markdown"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "# Replay Trace: project-1" in captured.out
    assert "WorkItem workitem-1 reached done" in captured.out


def test_replay_manifest_cli_writes_output_file(tmp_path, capsys) -> None:
    manifest_path = _write_manifest(tmp_path)
    output_path = tmp_path / "trace" / "project-1.replay.md"

    exit_code = replay_manifest_main([str(manifest_path), "--format", "markdown", "--output", str(output_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["output_path"] == str(output_path.resolve())
    assert output_path.exists()
    assert "# Replay Trace: project-1" in output_path.read_text(encoding="utf-8")
