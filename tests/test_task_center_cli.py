"""Task Center CLI tests."""

import json
from dataclasses import replace

from app.task_center import main
from conductor.controller.engine import ConductorEngine
from conductor.domain.models import Artifact, TaskAssignmentStatus, WorkItemStatus
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
    assert list_payload["summary"]["total"] == 1
    assert list_payload["summary"]["claimable"] == 1
    assert list_payload["summary"]["blocked_by_dependencies"] == 0
    assert list_payload["tasks"][0]["status"] == "queued"
    assert list_payload["tasks"][0]["claimable"] is True
    assert list_payload["tasks"][0]["unmet_dependency_ids"] == []

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
    assert claim_payload["summary"]["claimed"] == 1
    assert claim_payload["summary"]["claimable"] == 0
    assert claim_payload["task"]["status"] == "claimed"
    assert claim_payload["task"]["assigned_agent_id"] == "agent-external"
    assert claim_payload["task"]["claimed_at"]
    assert claim_payload["task"]["returned_at"] == ""
    assert claim_payload["task"]["workitem"]["status"] == "running"
    assert claim_payload["task"]["workitem"]["owner_agent"] == "agent-external"

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
    assert complete_payload["summary"]["completed"] == 1
    assert complete_payload["summary"]["claimable"] == 0
    assert complete_payload["task"]["status"] == "completed"
    assert complete_payload["task"]["output_artifact_ids"] == ["artifact-external"]
    assert complete_payload["task"]["returned_at"]
    assert complete_payload["task"]["workitem"]["status"] == "done"

    reloaded = FileStateStore(project_root / ".conductor" / "state").get_state(state.project.id)
    assignment = reloaded.task_assignments[0]
    assert assignment.status == TaskAssignmentStatus.COMPLETED
    assert assignment.assigned_agent_id == "agent-external"
    assert assignment.result_summary == "completed by external worker"
    assert reloaded.workitems[0].status == WorkItemStatus.DONE
    assert reloaded.workitems[0].owner_agent == "agent-external"
    assert any("TaskCenterCLI" in event for event in reloaded.recent_events)


def test_task_center_cli_prints_summary_only(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    state = engine.create_project(requirement="Build a local reading list", project_root=str(project_root))

    code = main(["summary", "--project-root", str(project_root)])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["project_id"] == state.project.id
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["claimable"] == 1
    assert "tasks" not in payload


def test_task_center_cli_prints_assignment_context_with_input_artifacts(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    state = engine.create_project(requirement="Build a local reading list with CSV export", project_root=str(project_root))
    artifact = engine.artifact_store.save_markdown(
        Artifact(
            id="artifact-context",
            project_id=state.project.id,
            workitem_id="workitem-upstream",
            agent_id="agent-designer",
            kind="frozen_requirement_spec",
            title="Frozen Requirement",
            content="Acceptance: add book, persist refresh, export CSV.",
        ),
        project_root=state.project.project_root,
    )
    state = state_store.add_artifact(state.project.id, artifact)
    assignment = replace(state.task_assignments[0], input_artifact_ids=[artifact.id])
    state = state_store.upsert_task_assignment(state.project.id, assignment)

    code = main(["context", assignment.id, "--project-root", str(project_root)])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["project_id"] == state.project.id
    assert payload["assignment"]["id"] == assignment.id
    assert payload["assignment"]["input_artifact_ids"]
    assert payload["workitem"]["id"] == assignment.workitem_id
    assert "Return Protocol" in payload["execution_brief"]
    assert payload["input_artifacts"]
    assert "content" in payload["input_artifacts"][0]


def test_task_center_cli_prints_assignment_context_as_markdown(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    state = engine.create_project(requirement="Build a local reading list with CSV export", project_root=str(project_root))
    artifact = engine.artifact_store.save_markdown(
        Artifact(
            id="artifact-context",
            project_id=state.project.id,
            workitem_id="workitem-upstream",
            agent_id="agent-designer",
            kind="frozen_requirement_spec",
            title="Frozen Requirement",
            content="Acceptance: add book, persist refresh, export CSV.",
        ),
        project_root=state.project.project_root,
    )
    state = state_store.add_artifact(state.project.id, artifact)
    assignment = replace(state.task_assignments[0], input_artifact_ids=[artifact.id])
    state_store.upsert_task_assignment(state.project.id, assignment)

    code = main(["context", assignment.id, "--project-root", str(project_root), "--format", "markdown"])
    output = capsys.readouterr().out

    assert code == 0
    assert "# Task Assignment Context" in output
    assert "## WorkItem" in output
    assert "## Input Artifacts" in output
    assert "artifact-context" in output
    assert "Acceptance: add book, persist refresh, export CSV." in output
    assert "## Return Protocol" in output


def test_task_center_cli_claim_next_can_include_context(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    state = engine.create_project(requirement="Build a local reading list with CSV export", project_root=str(project_root))
    artifact = engine.artifact_store.save_markdown(
        Artifact(
            id="artifact-context",
            project_id=state.project.id,
            workitem_id="workitem-upstream",
            agent_id="agent-designer",
            kind="frozen_requirement_spec",
            title="Frozen Requirement",
            content="Acceptance: add book, persist refresh, export CSV.",
        ),
        project_root=state.project.project_root,
    )
    state = state_store.add_artifact(state.project.id, artifact)
    assignment = replace(state.task_assignments[0], input_artifact_ids=[artifact.id])
    state_store.upsert_task_assignment(state.project.id, assignment)

    code = main(
        [
            "claim-next",
            "--project-root",
            str(project_root),
            "--agent-id",
            "agent-context-worker",
            "--with-context",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["task"]["status"] == "claimed"
    assert payload["context"]["assignment"]["id"] == assignment.id
    assert "Return Protocol" in payload["context"]["execution_brief"]
    assert payload["context"]["input_artifacts"][0]["id"] == artifact.id
    assert "content" in payload["context"]["input_artifacts"][0]


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


def test_task_center_cli_claim_next_selects_available_role_task(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    state = engine.create_project(requirement="Build a local reading list", project_root=str(project_root))
    role = state.task_assignments[0].role

    code = main(
        [
            "claim-next",
            "--project-root",
            str(project_root),
            "--role",
            role,
            "--agent-id",
            "agent-role-worker",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["summary"]["claimed"] == 1
    assert payload["summary"]["claimable"] == 0
    assert payload["task"]["role"] == role
    assert payload["task"]["status"] == "claimed"
    assert payload["task"]["assigned_agent_id"] == "agent-role-worker"
    assert payload["task"]["workitem"]["status"] == "running"


def test_task_center_cli_complete_can_create_output_artifact_from_file(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    output_file = tmp_path / "result.md"
    output_file.write_text("# Worker Result\n\nImplemented by external worker.", encoding="utf-8")
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    state = engine.create_project(requirement="Build a local reading list", project_root=str(project_root))
    assignment_id = state.task_assignments[0].id
    assert main(["claim", assignment_id, "--project-root", str(project_root), "--agent-id", "agent-external"]) == 0
    capsys.readouterr()

    code = main(
        [
            "complete",
            assignment_id,
            "--project-root",
            str(project_root),
            "--result-summary",
            "done",
            "--output-file",
            str(output_file),
            "--output-artifact-kind",
            "implementation_report",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    artifact_id = payload["task"]["output_artifact_ids"][0]
    reloaded = FileStateStore(project_root / ".conductor" / "state").get_state(state.project.id)
    artifact = next(item for item in reloaded.artifacts if item.id == artifact_id)
    assert artifact.kind == "implementation_report"
    assert artifact.source_backend == "task_center/external"
    assert "Implemented by external worker" in artifact.content


def test_task_center_cli_rejects_empty_output_artifact_file(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    output_file = tmp_path / "empty.md"
    output_file.write_text("   ", encoding="utf-8")
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    state = engine.create_project(requirement="Build a local reading list", project_root=str(project_root))
    assignment_id = state.task_assignments[0].id
    assert main(["claim", assignment_id, "--project-root", str(project_root), "--agent-id", "agent-external"]) == 0
    capsys.readouterr()

    code = main(
        [
            "complete",
            assignment_id,
            "--project-root",
            str(project_root),
            "--output-file",
            str(output_file),
        ]
    )
    captured = capsys.readouterr()

    assert code == 2
    assert "cannot be empty" in captured.err


def test_task_center_cli_claim_next_returns_error_when_no_role_task(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    engine.create_project(requirement="Build a local reading list", project_root=str(project_root))

    code = main(
        [
            "claim-next",
            "--project-root",
            str(project_root),
            "--role",
            "missing_role",
            "--agent-id",
            "agent-role-worker",
        ]
    )
    captured = capsys.readouterr()

    assert code == 2
    assert "No queued task assignment available for role missing_role" in captured.err
