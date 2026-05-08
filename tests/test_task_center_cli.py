"""Task Center CLI tests."""

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

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
    assert claim_payload["task"]["claim_token"]
    assert claim_payload["task"]["claimed_at"]
    assert claim_payload["task"]["last_heartbeat_at"]
    assert claim_payload["task"]["returned_at"] == ""
    assert claim_payload["task"]["workitem"]["status"] == "running"
    assert claim_payload["task"]["workitem"]["owner_agent"] == "agent-external"

    heartbeat_code = main(
        [
            "heartbeat",
            assignment_id,
            "--project-root",
            str(project_root),
            "--agent-id",
            "agent-external",
            "--claim-token",
            claim_payload["task"]["claim_token"],
        ]
    )
    heartbeat_payload = json.loads(capsys.readouterr().out)

    assert heartbeat_code == 0
    assert heartbeat_payload["task"]["status"] == "claimed"
    assert heartbeat_payload["task"]["last_heartbeat_at"]
    assert heartbeat_payload["task"]["heartbeat_age_seconds"] is not None

    complete_code = main(
        [
            "complete",
            assignment_id,
            "--project-root",
            str(project_root),
            "--agent-id",
            "agent-external",
            "--claim-token",
            heartbeat_payload["task"]["claim_token"],
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


def test_task_center_cli_rejects_complete_for_stale_claim_token(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    state = engine.create_project(requirement="Build a local reading list", project_root=str(project_root))
    assignment_id = state.task_assignments[0].id
    claim_code = main(["claim", assignment_id, "--project-root", str(project_root), "--agent-id", "agent-owner"])
    claim_payload = json.loads(capsys.readouterr().out)
    assert claim_code == 0

    code = main(
        [
            "complete",
            assignment_id,
            "--project-root",
            str(project_root),
            "--agent-id",
            "agent-owner",
            "--claim-token",
            f"stale-{claim_payload['task']['claim_token']}",
            "--result-summary",
            "done",
        ]
    )
    captured = capsys.readouterr()

    assert code == 2
    assert "claim token does not match" in captured.err


def test_task_center_cli_rejects_complete_for_wrong_agent(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    state = engine.create_project(requirement="Build a local reading list", project_root=str(project_root))
    assignment_id = state.task_assignments[0].id
    assert main(["claim", assignment_id, "--project-root", str(project_root), "--agent-id", "agent-owner"]) == 0
    capsys.readouterr()

    code = main(
        [
            "complete",
            assignment_id,
            "--project-root",
            str(project_root),
            "--agent-id",
            "agent-other",
            "--result-summary",
            "done",
        ]
    )
    captured = capsys.readouterr()

    assert code == 2
    assert "claimed by another agent" in captured.err


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
    assert "Frozen Requirement Baseline" in payload["execution_brief"]
    assert payload["frozen_requirement_baseline"]["id"] == artifact.id
    assert payload["input_artifacts"]
    assert "content" in payload["input_artifacts"][0]
    assert "parent_artifact_id" in payload["input_artifacts"][0]
    assert "derived_from" in payload["input_artifacts"][0]
    assert "review_of" in payload["input_artifacts"][0]


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
    assert "## Frozen Requirement Baseline" in output
    assert "controlling contract" in output
    assert "## Input Artifacts" in output
    assert "artifact-context" in output
    assert "Acceptance: add book, persist refresh, export CSV." in output
    assert "- Parent Artifact ID:" in output
    assert "- Review Of:" in output
    assert "- Derived From:" in output
    assert "## CLI Return Commands" in output
    assert f'python -m app.task_center complete "{assignment.id}"' in output
    assert "--claim-token" in output
    assert f'python -m app.task_center heartbeat "{assignment.id}"' in output
    assert f'python -m app.task_center release "{assignment.id}"' in output
    assert f'--project-root "{project_root}"' in output
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


def test_task_center_cli_claim_next_can_print_context_markdown(tmp_path, capsys) -> None:
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
            "agent-markdown-worker",
            "--with-context",
            "--context-format",
            "markdown",
        ]
    )
    output = capsys.readouterr().out

    assert code == 0
    assert output.startswith("# Task Assignment Context")
    assert "- Status: claimed" in output
    assert "- Assigned Agent: agent-markdown-worker" in output
    assert "- Claim Token:" in output
    assert "artifact-context" in output
    assert "Acceptance: add book, persist refresh, export CSV." in output

    reloaded = FileStateStore(project_root / ".conductor" / "state").get_state(state.project.id)
    assert reloaded.task_assignments[0].status == TaskAssignmentStatus.CLAIMED
    assert reloaded.task_assignments[0].assigned_agent_id == "agent-markdown-worker"


def test_task_center_cli_claim_next_can_write_prompt_file(tmp_path, capsys) -> None:
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
            "agent-file-worker",
            "--prompt-file",
            ".conductor/task_center/prompts/next-task.md",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    prompt_file = project_root / ".conductor" / "task_center" / "prompts" / "next-task.md"
    prompt_content = prompt_file.read_text(encoding="utf-8")

    assert code == 0
    assert payload["task"]["status"] == "claimed"
    assert payload["prompt_file"] == str(prompt_file.resolve())
    assert "context" not in payload
    assert "# Task Assignment Context" in prompt_content
    assert "artifact-context" in prompt_content
    assert "Acceptance: add book, persist refresh, export CSV." in prompt_content
    assert "## CLI Return Commands" in prompt_content
    assert f'python -m app.task_center complete "{assignment.id}"' in prompt_content
    assert f'python -m app.task_center fail "{assignment.id}"' in prompt_content
    assert f'python -m app.task_center release "{assignment.id}"' in prompt_content

    reloaded = FileStateStore(project_root / ".conductor" / "state").get_state(state.project.id)
    assert reloaded.task_assignments[0].status == TaskAssignmentStatus.CLAIMED
    assert reloaded.task_assignments[0].assigned_agent_id == "agent-file-worker"
    assert reloaded.task_assignments[0].prompt_file == str(prompt_file.resolve())


def test_task_center_cli_rejects_prompt_file_outside_project_root(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    state = engine.create_project(requirement="Build a local reading list", project_root=str(project_root))

    code = main(
        [
            "claim-next",
            "--project-root",
            str(project_root),
            "--agent-id",
            "agent-file-worker",
            "--prompt-file",
            "..\\escaped-task.md",
        ]
    )
    captured = capsys.readouterr()

    assert code == 2
    assert "Prompt file must be inside project root" in captured.err
    assert not (tmp_path / "escaped-task.md").exists()

    reloaded = FileStateStore(project_root / ".conductor" / "state").get_state(state.project.id)
    assert reloaded.task_assignments[0].status == TaskAssignmentStatus.QUEUED
    assert reloaded.task_assignments[0].prompt_file == ""


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


def test_task_center_cli_release_requeues_claimed_assignment(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    state = engine.create_project(requirement="Build a local reading list", project_root=str(project_root))
    assignment_id = state.task_assignments[0].id

    claim_code = main(
        [
            "claim",
            assignment_id,
            "--project-root",
            str(project_root),
            "--agent-id",
            "agent-external",
        ]
    )
    capsys.readouterr()
    release_code = main(
        [
            "release",
            assignment_id,
            "--project-root",
            str(project_root),
            "--release-reason",
            "worker interrupted",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert claim_code == 0
    assert release_code == 0
    assert payload["summary"]["queued"] == 1
    assert payload["summary"]["claimable"] == 1
    assert payload["task"]["status"] == "queued"
    assert payload["task"]["assigned_agent_id"] == ""
    assert payload["task"]["claim_reason"] == "worker interrupted"
    assert payload["task"]["workitem"]["status"] == "pending"

    reloaded = FileStateStore(project_root / ".conductor" / "state").get_state(state.project.id)
    assert reloaded.task_assignments[0].status == TaskAssignmentStatus.QUEUED
    assert reloaded.workitems[0].status == WorkItemStatus.PENDING


def test_task_center_cli_lists_stale_claimed_assignments(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    state = engine.create_project(requirement="Build a local reading list", project_root=str(project_root))
    claim_code = main(
        [
            "claim-next",
            "--project-root",
            str(project_root),
            "--agent-id",
            "agent-external",
        ]
    )
    capsys.readouterr()
    claimed_state = FileStateStore(project_root / ".conductor" / "state").get_state(state.project.id)
    claimed_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    assignment = replace(claimed_state.task_assignments[0], claimed_at=claimed_at, last_heartbeat_at=claimed_at)
    FileStateStore(project_root / ".conductor" / "state").upsert_task_assignment(state.project.id, assignment)

    list_code = main(
        [
            "list",
            "--project-root",
            str(project_root),
            "--stale-only",
            "--stale-after-seconds",
            "1",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert claim_code == 0
    assert list_code == 0
    assert payload["stale_only"] is True
    assert payload["summary"]["stale_claimed"] == 1
    assert payload["total"] == 1
    assert payload["tasks"][0]["stale_claimed"] is True
    assert payload["tasks"][0]["claimed_age_seconds"] is not None


def test_task_center_cli_release_stale_requeues_stale_claimed_assignments(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    state = engine.create_project(requirement="Build a local reading list", project_root=str(project_root))
    claim_code = main(
        [
            "claim-next",
            "--project-root",
            str(project_root),
            "--agent-id",
            "agent-external",
        ]
    )
    capsys.readouterr()
    claimed_state = FileStateStore(project_root / ".conductor" / "state").get_state(state.project.id)
    claimed_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    assignment = replace(claimed_state.task_assignments[0], claimed_at=claimed_at, last_heartbeat_at=claimed_at)
    FileStateStore(project_root / ".conductor" / "state").upsert_task_assignment(state.project.id, assignment)

    release_code = main(
        [
            "release-stale",
            "--project-root",
            str(project_root),
            "--stale-after-seconds",
            "1",
            "--release-reason",
            "stale cleanup",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert claim_code == 0
    assert release_code == 0
    assert payload["released_count"] == 1
    assert payload["summary"]["queued"] == 1
    assert payload["summary"]["claimable"] == 1
    assert payload["summary"]["stale_claimed"] == 0
    assert payload["tasks"][0]["status"] == "queued"
    assert payload["tasks"][0]["claim_reason"] == "stale cleanup"

    reloaded = FileStateStore(project_root / ".conductor" / "state").get_state(state.project.id)
    assert reloaded.task_assignments[0].status == TaskAssignmentStatus.QUEUED
    assert reloaded.workitems[0].status == WorkItemStatus.PENDING


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
