"""Task Center CLI tests."""

import json

from app.task_center import main
from conductor.controller.engine import ConductorEngine
from conductor.domain.models import TaskAssignmentStatus
from conductor.state.file_store import FileStateStore


def test_task_center_cli_lists_claims_and_completes_persisted_assignment(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    state = engine.create_project(requirement="Build a local reading list", project_root=str(project_root))
    assignment_id = state.task_assignments[0].id

    list_code = main(["list", "--project-root", str(project_root)])
    list_payload = json.loads(capsys.readouterr().out)

    assert list_code == 0
    assert list_payload["project_id"] == state.project.id
    assert list_payload["total"] == 1
    assert list_payload["tasks"][0]["status"] == "queued"

    claim_code = main(
        [
            "claim",
            assignment_id,
            "--project-root",
            str(project_root),
            "--agent-id",
            "agent-external",
            "--claim-reason",
            "external worker",
        ]
    )
    claim_payload = json.loads(capsys.readouterr().out)

    assert claim_code == 0
    assert claim_payload["task"]["status"] == "claimed"
    assert claim_payload["task"]["assigned_agent_id"] == "agent-external"

    complete_code = main(
        [
            "complete",
            assignment_id,
            "--project-root",
            str(project_root),
            "--result-summary",
            "completed by external worker",
            "--output-artifact-id",
            "artifact-external",
        ]
    )
    complete_payload = json.loads(capsys.readouterr().out)

    assert complete_code == 0
    assert complete_payload["task"]["status"] == "completed"
    assert complete_payload["task"]["output_artifact_ids"] == ["artifact-external"]

    reloaded = FileStateStore(project_root / ".conductor" / "state").get_state(state.project.id)
    assignment = reloaded.task_assignments[0]
    assert assignment.status == TaskAssignmentStatus.COMPLETED
    assert assignment.assigned_agent_id == "agent-external"
    assert assignment.result_summary == "completed by external worker"
    assert any("TaskCenterCLI" in event for event in reloaded.recent_events)


def test_task_center_cli_rejects_return_before_claim(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    state = engine.create_project(requirement="Build a local reading list", project_root=str(project_root))
    assignment_id = state.task_assignments[0].id

    code = main(
        [
            "complete",
            assignment_id,
            "--project-root",
            str(project_root),
            "--result-summary",
            "done",
        ]
    )
    captured = capsys.readouterr()

    assert code == 2
    assert "not claimed" in captured.err
