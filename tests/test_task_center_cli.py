"""Task Center CLI tests."""

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.task_center import main
from conductor.controller.engine import ConductorEngine
from conductor.domain.models import (
    AgentActivation,
    Artifact,
    Execution,
    ExecutionStatus,
    HumanControlAction,
    HumanControlActionType,
    Project,
    ProjectStatus,
    SharedProjectState,
    TaskAssignment,
    TaskAssignmentStatus,
    WorkItem,
    WorkItemStatus,
)
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
            "--lease-seconds",
            "60",
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
    assert claim_payload["task"]["lease_seconds"] == 60
    assert claim_payload["task"]["lease_expires_at"]
    assert claim_payload["task"]["lease_expired"] is False
    assert claim_payload["task"]["returned_at"] == ""
    assert claim_payload["task"]["transition_history"][-1]["action"] == "claim"
    assert claim_payload["task"]["workitem"]["status"] == "running"
    assert claim_payload["task"]["workitem"]["owner_agent"] == "agent-external"
    return_commands = claim_payload["task"]["return_commands"]
    assert return_commands["complete"].startswith(f'python -m app.task_center complete "{assignment_id}"')
    assert f'--project-root "{project_root}"' in return_commands["complete"]
    assert '--agent-id "agent-external"' in return_commands["complete"]
    assert f'--claim-token "{claim_payload["task"]["claim_token"]}"' in return_commands["complete"]
    assert return_commands["complete_with_output_file"].endswith('--output-file "result.md"')
    assert return_commands["heartbeat"].startswith(f'python -m app.task_center heartbeat "{assignment_id}"')
    assert return_commands["fail"].startswith(f'python -m app.task_center fail "{assignment_id}"')
    assert return_commands["fail_with_output_file"].endswith('--output-file "result.md"')
    assert return_commands["release"].startswith(f'python -m app.task_center release "{assignment_id}"')

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
            "--lease-seconds",
            "120",
        ]
    )
    heartbeat_payload = json.loads(capsys.readouterr().out)

    assert heartbeat_code == 0
    assert heartbeat_payload["task"]["status"] == "claimed"
    assert heartbeat_payload["task"]["last_heartbeat_at"]
    assert heartbeat_payload["task"]["heartbeat_age_seconds"] is not None
    assert heartbeat_payload["task"]["lease_seconds"] == 120
    assert heartbeat_payload["task"]["lease_expires_at"] != claim_payload["task"]["lease_expires_at"]

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
    assert [item["action"] for item in complete_payload["task"]["transition_history"]] == [
        "claim",
        "heartbeat",
        "return",
    ]
    assert complete_payload["task"]["return_commands"] == {}
    assert complete_payload["task"]["workitem"]["status"] == "done"

    reloaded = FileStateStore(project_root / ".conductor" / "state").get_state(state.project.id)
    assignment = reloaded.task_assignments[0]
    assert assignment.status == TaskAssignmentStatus.COMPLETED
    assert assignment.assigned_agent_id == "agent-external"
    assert assignment.result_summary == "completed by external worker"
    assert assignment.transition_history[-1]["action"] == "return"
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


def test_task_center_cli_audit_reports_findings_and_can_fail(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    state = SharedProjectState(
        project=Project(id="project-audit", goal="Build a local tool", project_root=str(project_root)),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-completed", description="Completed", stage="development", status=WorkItemStatus.DONE),
            WorkItem(id="workitem-failed", description="Failed", stage="development", status=WorkItemStatus.FAILED),
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-completed",
                workitem_id="workitem-completed",
                role="backend_engineer",
                status=TaskAssignmentStatus.COMPLETED,
                output_artifact_ids=["artifact-missing-output"],
            ),
            TaskAssignment(
                id="assignment-failed",
                workitem_id="workitem-failed",
                role="backend_engineer",
                status=TaskAssignmentStatus.FAILED,
            ),
        ],
    )
    state_store.save_state(state)

    code = main(["audit", "--project-root", str(project_root)])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["passed"] is False
    assert payload["finding_count"] >= 2
    assert {finding["code"] for finding in payload["findings"]} >= {
        "failed_without_reason",
        "missing_output_artifact",
    }

    failing_code = main(["audit", "--project-root", str(project_root), "--fail-on-findings"])
    failing_payload = json.loads(capsys.readouterr().out)

    assert failing_code == 3
    assert failing_payload["passed"] is False


def test_task_center_cli_claim_batch_enforces_limit(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    state = SharedProjectState(
        project=Project(id="project-batch", goal="Build a local tool", project_root=str(project_root)),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-a", description="Task A", stage="development"),
            WorkItem(id="workitem-b", description="Task B", stage="development"),
            WorkItem(id="workitem-c", description="Task C", stage="development"),
        ],
        task_assignments=[
            TaskAssignment(id="assignment-a", workitem_id="workitem-a", role="backend_engineer"),
            TaskAssignment(id="assignment-b", workitem_id="workitem-b", role="backend_engineer"),
            TaskAssignment(id="assignment-c", workitem_id="workitem-c", role="frontend_engineer"),
        ],
    )
    state_store.save_state(state)

    code = main(
        [
            "claim-batch",
            "--project-root",
            str(project_root),
            "--agent-id",
            "agent-batch",
            "--role",
            "backend_engineer",
            "--limit",
            "2",
            "--max-limit",
            "2",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["claimed_count"] == 2
    assert [task["id"] for task in payload["tasks"]] == ["assignment-a", "assignment-b"]
    assert all(task["transition_history"][-1]["action"] == "claim" for task in payload["tasks"])

    rejected = main(
        [
            "claim-batch",
            "--project-root",
            str(project_root),
            "--agent-id",
            "agent-batch",
            "--limit",
            "3",
            "--max-limit",
            "2",
        ]
    )
    captured = capsys.readouterr()

    assert rejected == 2
    assert "exceeds configured maximum" in captured.err


def test_task_center_cli_requires_claim_token_for_guarded_return(tmp_path, capsys) -> None:
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
            "agent-owner",
            "--result-summary",
            "done",
        ]
    )
    captured = capsys.readouterr()

    assert code == 2
    assert "requires claim token guard" in captured.err


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
    assert "Delivery Contract" in payload["execution_brief"]
    assert payload["delivery_contract"]["stage"] == state.workitems[0].stage
    assert payload["delivery_contract"]["role"] == assignment.role
    assert artifact.id in payload["delivery_contract"]["required_input_artifact_ids"]
    assert "frozen_requirement_spec" in payload["delivery_contract"]["required_input_kinds"]
    assert payload["delivery_contract"]["expected_outputs"]
    assert "Frozen requirement coverage" in payload["delivery_contract"]["verification_focus"]
    assert "Frozen Requirement Baseline" in payload["execution_brief"]
    assert payload["frozen_requirement_baseline"]["id"] == artifact.id
    assert payload["input_artifacts"]
    assert "content" in payload["input_artifacts"][0]
    assert "parent_artifact_id" in payload["input_artifacts"][0]
    assert "derived_from" in payload["input_artifacts"][0]
    assert "review_of" in payload["input_artifacts"][0]


def test_task_center_cli_auto_includes_frozen_requirement_for_downstream_context(tmp_path, capsys) -> None:
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
            id="artifact-frozen-auto",
            project_id=state.project.id,
            workitem_id="workitem-requirement",
            agent_id="agent-requirement",
            kind="frozen_requirement_spec",
            title="Frozen Requirement",
            content="Acceptance: add book, persist refresh, export CSV.",
        ),
        project_root=state.project.project_root,
    )
    downstream = WorkItem(
        id="workitem-dev-auto",
        description="Implement downstream work",
        stage="development",
        kind="api_implementation",
    )
    assignment = TaskAssignment(
        id="assignment-dev-auto",
        workitem_id=downstream.id,
        role="backend_engineer",
    )
    state = replace(
        state,
        workitems=[*state.workitems, downstream],
        task_assignments=[*state.task_assignments, assignment],
        artifacts=[*state.artifacts, artifact],
    )
    state_store.save_state(state)

    code = main(["context", assignment.id, "--project-root", str(project_root)])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["frozen_requirement_baseline"]["id"] == artifact.id
    assert payload["input_artifacts"][0]["id"] == artifact.id
    assert "Acceptance: add book, persist refresh, export CSV." in payload["input_artifacts"][0]["content"]


def test_task_center_cli_auto_includes_frozen_design_for_development_context(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    state = engine.create_project(requirement="Build a local reading list with CSV export", project_root=str(project_root))
    frozen_requirement = engine.artifact_store.save_markdown(
        Artifact(
            id="artifact-frozen-req-auto",
            project_id=state.project.id,
            workitem_id="workitem-requirement",
            agent_id="agent-requirement",
            kind="frozen_requirement_spec",
            title="Frozen Requirement",
            content="Acceptance: add book, persist refresh, export CSV.",
        ),
        project_root=state.project.project_root,
    )
    frozen_design = engine.artifact_store.save_markdown(
        Artifact(
            id="artifact-frozen-design-auto",
            project_id=state.project.id,
            workitem_id="workitem-design",
            agent_id="agent-designer",
            kind="frozen_design_spec",
            title="Frozen Design",
            content="Implementation baseline: static HTML, localStorage, CSV export button.",
        ),
        project_root=state.project.project_root,
    )
    downstream = WorkItem(
        id="workitem-dev-design-auto",
        description="Implement downstream work",
        stage="development",
        kind="ui_implementation",
    )
    assignment = TaskAssignment(
        id="assignment-dev-design-auto",
        workitem_id=downstream.id,
        role="frontend_engineer",
    )
    state = replace(
        state,
        workitems=[*state.workitems, downstream],
        task_assignments=[*state.task_assignments, assignment],
        artifacts=[*state.artifacts, frozen_requirement, frozen_design],
    )
    state_store.save_state(state)

    code = main(["context", assignment.id, "--project-root", str(project_root)])
    payload = json.loads(capsys.readouterr().out)
    input_ids = [artifact["id"] for artifact in payload["input_artifacts"]]

    assert code == 0
    assert input_ids[:2] == [frozen_requirement.id, frozen_design.id]
    assert payload["frozen_design_baseline"]["id"] == frozen_design.id
    assert "frozen_design_spec" in payload["delivery_contract"]["required_input_kinds"]
    assert "Frozen design coverage" in payload["delivery_contract"]["verification_focus"]
    assert payload["handoff_safety"]["baseline_handoff"]["status"] == "needs_attention"
    assert payload["handoff_safety"]["baseline_handoff"]["requirement_baseline_artifact_ids"] == [
        frozen_requirement.id
    ]
    assert payload["handoff_safety"]["baseline_handoff"]["design_baseline_artifact_ids"] == [frozen_design.id]
    assert payload["handoff_safety"]["baseline_handoff"]["missing_contracts"] == [
        "requirement_baseline",
        "design_baseline",
    ]
    assert (
        "development acceptance criteria do not explicitly preserve the requirement baseline"
        in payload["handoff_safety"]["warnings"]
    )
    assert "Frozen Design Baseline" in payload["execution_brief"]
    assert "Baseline Contract: needs_attention" in payload["execution_brief"]
    assert "Implementation baseline" in payload["input_artifacts"][1]["content"]

    code = main(["context", assignment.id, "--project-root", str(project_root), "--format", "markdown"])
    markdown = capsys.readouterr().out

    assert code == 0
    assert "- Baseline Contract: needs_attention" in markdown
    assert "- Missing Baseline Contracts: requirement_baseline, design_baseline" in markdown


def test_task_center_cli_marks_development_baseline_handoff_covered(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    state = engine.create_project(requirement="Build a local reading list with CSV export", project_root=str(project_root))
    frozen_requirement = engine.artifact_store.save_markdown(
        Artifact(
            id="artifact-frozen-req-covered",
            project_id=state.project.id,
            workitem_id="workitem-requirement",
            agent_id="agent-requirement",
            kind="frozen_requirement_spec",
            title="Frozen Requirement",
            content="Acceptance: add book, persist refresh, export CSV.",
        ),
        project_root=state.project.project_root,
    )
    frozen_design = engine.artifact_store.save_markdown(
        Artifact(
            id="artifact-frozen-design-covered",
            project_id=state.project.id,
            workitem_id="workitem-design",
            agent_id="agent-designer",
            kind="frozen_design_spec",
            title="Frozen Design",
            content="Implementation baseline: static HTML, localStorage, CSV export button.",
        ),
        project_root=state.project.project_root,
    )
    downstream = WorkItem(
        id="workitem-dev-baseline-covered",
        description="Implement downstream work",
        stage="development",
        kind="ui_implementation",
        acceptance_criteria=[
            "\u9075\u5b88\u8f93\u5165\u4ea7\u7269\u4e2d\u7684\u51bb\u7ed3\u9700\u6c42/"
            "\u9700\u6c42\u57fa\u7ebf\u8303\u56f4\u3001\u975e\u76ee\u6807\u548c"
            "\u9a8c\u6536\u6807\u51c6",
            "\u9075\u5b88\u8f93\u5165\u4ea7\u7269\u4e2d\u7684\u51bb\u7ed3\u8bbe\u8ba1/"
            "\u8bbe\u8ba1\u7ea6\u675f\uff0c\u5fc5\u8981\u504f\u79bb\u5fc5\u987b"
            "\u663e\u5f0f\u8bf4\u660e",
        ],
    )
    assignment = TaskAssignment(
        id="assignment-dev-baseline-covered",
        workitem_id=downstream.id,
        role="frontend_engineer",
    )
    state = replace(
        state,
        workitems=[*state.workitems, downstream],
        task_assignments=[*state.task_assignments, assignment],
        artifacts=[*state.artifacts, frozen_requirement, frozen_design],
    )
    state_store.save_state(state)

    code = main(["context", assignment.id, "--project-root", str(project_root)])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["handoff_safety"]["baseline_handoff"]["status"] == "covered"
    assert payload["handoff_safety"]["baseline_handoff"]["missing_contracts"] == []
    assert payload["handoff_safety"]["warnings"] == []


def test_task_center_cli_context_exposes_eligible_dynamic_agents(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    state = engine.create_project(requirement="Build a local reading list UI", project_root=str(project_root))
    downstream = WorkItem(
        id="workitem-dynamic-ui",
        description="Implement UI layout",
        stage="development",
        kind="ui_implementation",
    )
    assignment = TaskAssignment(
        id="assignment-dynamic-ui",
        workitem_id=downstream.id,
        role="frontend_engineer",
    )
    matching_activation = AgentActivation(
        role="frontend_engineer",
        agent_id="agent-frontend-engineer-ui-layout",
        stage="development",
        reason="Frontend work can be split by layout scope.",
        related_workitem_kinds=["ui_implementation"],
        execution_backend="cli",
        preferred_backend="local",
        instance_id="ui_layout",
        scope="HTML/component structure and responsive layout",
        dynamic=True,
        parallel_safe=True,
        write_scope=["frontend layout files", "component markup"],
    )
    non_matching_activation = AgentActivation(
        role="backend_engineer",
        agent_id="agent-backend-engineer-api-contracts",
        stage="development",
        reason="Backend scope.",
        related_workitem_kinds=["api_implementation"],
        instance_id="api_contracts",
        dynamic=True,
    )
    state = replace(
        state,
        workitems=[*state.workitems, downstream],
        task_assignments=[*state.task_assignments, assignment],
        agent_activations=[*state.agent_activations, matching_activation, non_matching_activation],
    )
    state_store.save_state(state)

    code = main(["context", assignment.id, "--project-root", str(project_root)])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["eligible_agent_activations"][0]["agent_id"] == matching_activation.agent_id
    assert payload["eligible_agent_activations"][0]["instance_id"] == "ui_layout"
    assert payload["eligible_agent_activations"][0]["write_scope"] == ["frontend layout files", "component markup"]
    assert payload["eligible_agent_activations"][0]["claimable_for_agent"] is True
    assert payload["eligible_agent_activations"][0]["write_scope_conflict_assignment_ids"] == []
    assert payload["handoff_safety"]["ready_for_handoff"] is True
    assert payload["handoff_safety"]["status"] == "ready"
    assert payload["handoff_safety"]["write_scope_conflict_assignment_ids"] == []
    assert non_matching_activation.agent_id not in json.dumps(payload, ensure_ascii=False)
    assert "Eligible Dynamic Agents" in payload["execution_brief"]
    assert "Handoff Safety" in payload["execution_brief"]

    code = main(["context", assignment.id, "--project-root", str(project_root), "--format", "markdown"])
    markdown = capsys.readouterr().out

    assert code == 0
    assert "### Eligible Dynamic Agents" in markdown
    assert matching_activation.agent_id in markdown
    assert "## Handoff Safety" in markdown
    assert "- Status: ready" in markdown
    assert "- Ready For Handoff: True" in markdown
    assert "parallel_safe=True" in markdown
    assert "claimable_for_agent=True" in markdown
    assert "write_scope_conflicts=None" in markdown

    code = main(["agents-for", assignment.id, "--project-root", str(project_root)])
    agents_payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert agents_payload["assignment_id"] == assignment.id
    assert agents_payload["workitem_id"] == downstream.id
    assert agents_payload["role"] == "frontend_engineer"
    assert agents_payload["kind"] == "ui_implementation"
    assert agents_payload["eligible_count"] == 1
    assert agents_payload["agents"][0]["agent_id"] == matching_activation.agent_id
    assert agents_payload["agents"][0]["parallel_safe"] is True

    code = main(["tasks-for-agent", matching_activation.agent_id, "--project-root", str(project_root), "--claimable-only"])
    tasks_payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert tasks_payload["agent_id"] == matching_activation.agent_id
    assert tasks_payload["activation_count"] == 1
    assert tasks_payload["claimable_only"] is True
    assert tasks_payload["task_count"] == 1
    assert tasks_payload["tasks"][0]["assignment_id"] == assignment.id
    assert tasks_payload["tasks"][0]["workitem_id"] == downstream.id
    assert tasks_payload["tasks"][0]["claimable"] is True
    assert tasks_payload["tasks"][0]["parallel_safe"] is True
    assert tasks_payload["tasks"][0]["write_scope"] == ["frontend layout files", "component markup"]
    assert tasks_payload["tasks"][0]["claim_command"].startswith(
        f'python -m app.task_center claim "{assignment.id}"'
    )
    assert f'--project-root "{project_root}"' in tasks_payload["tasks"][0]["claim_command"]
    assert f'--agent-id "{matching_activation.agent_id}"' in tasks_payload["tasks"][0]["claim_command"]
    assert "--with-context" not in tasks_payload["tasks"][0]["claim_command"]
    assert tasks_payload["tasks"][0]["claim_with_context_command"].endswith("--with-context")

    code = main(
        [
            "claim-for-agent",
            matching_activation.agent_id,
            "--project-root",
            str(project_root),
            "--claim-reason",
            "dynamic agent self claim",
            "--lease-seconds",
            "60",
            "--with-context",
            "--prompt-file",
            ".conductor/task_center/prompts/dynamic-ui.md",
        ]
    )
    claim_payload = json.loads(capsys.readouterr().out)
    prompt_path = project_root / ".conductor" / "task_center" / "prompts" / "dynamic-ui.md"

    assert code == 0
    assert claim_payload["task"]["id"] == assignment.id
    assert claim_payload["task"]["status"] == "claimed"
    assert claim_payload["task"]["assigned_agent_id"] == matching_activation.agent_id
    assert claim_payload["task"]["claim_reason"] == "dynamic agent self claim"
    assert claim_payload["task"]["lease_seconds"] == 60
    assert claim_payload["matched_agent"]["instance_id"] == "ui_layout"
    assert claim_payload["matched_agent"]["write_scope"] == ["frontend layout files", "component markup"]
    assert claim_payload["context"]["assignment"]["id"] == assignment.id
    assert claim_payload["context"]["eligible_agent_activations"][0]["agent_id"] == matching_activation.agent_id
    assert claim_payload["prompt_file"] == str(prompt_path)
    assert claim_payload["task"]["prompt_file"] == str(prompt_path)
    assert prompt_path.exists()
    prompt_content = prompt_path.read_text(encoding="utf-8")
    assert "### Eligible Dynamic Agents" in prompt_content
    assert matching_activation.agent_id in prompt_content
    assert "Return Protocol" in prompt_content

    reloaded = FileStateStore(project_root / ".conductor" / "state").get_state(state.project.id)
    reloaded_assignment = next(item for item in reloaded.task_assignments if item.id == assignment.id)
    assert reloaded_assignment.prompt_file == str(prompt_path)


def test_task_center_cli_context_exposes_write_scope_conflicts_for_dynamic_agents(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    state = engine.create_project(requirement="Build a local reading list UI", project_root=str(project_root))
    claimed_workitem = WorkItem(
        id="workitem-layout-a",
        description="Implement first layout slice",
        stage="development",
        kind="ui_layout_slice",
        status=WorkItemStatus.RUNNING,
    )
    queued_workitem = WorkItem(
        id="workitem-layout-b",
        description="Implement second layout slice",
        stage="development",
        kind="ui_implementation",
    )
    claimed_assignment = TaskAssignment(
        id="assignment-layout-a",
        workitem_id=claimed_workitem.id,
        role="frontend_engineer",
        status=TaskAssignmentStatus.CLAIMED,
        assigned_agent_id="agent-frontend-engineer-layout-a",
    )
    queued_assignment = TaskAssignment(
        id="assignment-layout-b",
        workitem_id=queued_workitem.id,
        role="frontend_engineer",
    )
    claimed_activation = AgentActivation(
        role="frontend_engineer",
        agent_id="agent-frontend-engineer-layout-a",
        stage="development",
        reason="First layout slice.",
        related_workitem_kinds=["ui_layout_slice"],
        instance_id="layout_a",
        dynamic=True,
        parallel_safe=True,
        write_scope=["index.html"],
    )
    queued_activation = AgentActivation(
        role="frontend_engineer",
        agent_id="agent-frontend-engineer-layout-b",
        stage="development",
        reason="Second layout slice.",
        related_workitem_kinds=["ui_implementation"],
        instance_id="layout_b",
        dynamic=True,
        parallel_safe=True,
        write_scope=["index.html"],
    )
    state = replace(
        state,
        current_stage="development",
        workitems=[*state.workitems, claimed_workitem, queued_workitem],
        task_assignments=[*state.task_assignments, claimed_assignment, queued_assignment],
        agent_activations=[*state.agent_activations, claimed_activation, queued_activation],
    )
    state_store.save_state(state)

    code = main(["context", queued_assignment.id, "--project-root", str(project_root)])
    payload = json.loads(capsys.readouterr().out)
    activation = payload["eligible_agent_activations"][0]

    assert code == 0
    assert activation["agent_id"] == queued_activation.agent_id
    assert activation["claimable_for_agent"] is False
    assert activation["write_scope_conflict_assignment_ids"] == [claimed_assignment.id]
    assert payload["handoff_safety"]["ready_for_handoff"] is False
    assert payload["handoff_safety"]["status"] == "blocked"
    assert payload["handoff_safety"]["assignment_claimable"] is False
    assert payload["handoff_safety"]["blocked_agent_count"] == 1
    assert payload["handoff_safety"]["write_scope_conflict_assignment_ids"] == [claimed_assignment.id]
    assert "write scope conflicts with claimed assignments" in payload["handoff_safety"]["warnings"]
    assert claimed_assignment.id in payload["execution_brief"]

    code = main(["context", queued_assignment.id, "--project-root", str(project_root), "--format", "markdown"])
    markdown = capsys.readouterr().out

    assert code == 0
    assert "## Handoff Safety" in markdown
    assert "- Status: blocked" in markdown
    assert "- Ready For Handoff: False" in markdown
    assert f"- Write Scope Conflicts: {claimed_assignment.id}" in markdown
    assert "claimable_for_agent=False" in markdown
    assert f"write_scope_conflicts={claimed_assignment.id}" in markdown

    code = main(["tasks-for-agent", queued_activation.agent_id, "--project-root", str(project_root)])
    tasks_payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert tasks_payload["tasks"][0]["claimable"] is False
    assert tasks_payload["tasks"][0]["write_scope_conflict_assignment_ids"] == [claimed_assignment.id]
    assert tasks_payload["tasks"][0]["claim_command"] == ""
    assert tasks_payload["tasks"][0]["claim_with_context_command"] == ""

    code = main(
        [
            "claim",
            queued_assignment.id,
            "--project-root",
            str(project_root),
            "--agent-id",
            queued_activation.agent_id,
        ]
    )
    error_payload = json.loads(capsys.readouterr().err)

    assert code == 2
    assert error_payload["ok"] is False
    assert error_payload["error_code"] == "write_scope_conflict"
    assert error_payload["status_code"] == 409
    assert error_payload["details"]["write_scope_conflict_assignment_ids"] == [claimed_assignment.id]


def test_task_center_cli_context_marks_rework_feedback_inputs(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    state = engine.create_project(requirement="Build a local reading list with CSV export", project_root=str(project_root))
    failed_artifact = engine.artifact_store.save_markdown(
        Artifact(
            id="artifact-failed-ui-test",
            project_id=state.project.id,
            workitem_id="workitem-failed-ui-test",
            agent_id="agent-tester",
            kind="ui_validation",
            title="Failed UI Validation",
                content=(
                    "Static Web Validation: FAIL\n\n"
                    "Errors:\n"
                    "- Browser form submit did not change visible page state\n"
                    "Requirement coverage missing: add item interaction\n"
                ),
        ),
        project_root=state.project.project_root,
    )
    original_artifact = engine.artifact_store.save_markdown(
        Artifact(
            id="artifact-original-ui",
            project_id=state.project.id,
            workitem_id="workitem-original-ui",
            agent_id="agent-frontend",
            kind="ui_implementation",
            title="Original UI Implementation",
            content="Initial UI implementation details.",
        ),
        project_root=state.project.project_root,
    )
    failed_workitem = WorkItem(
        id="workitem-failed-ui-test",
        description="UI validation failed",
        stage="testing",
        kind="ui_validation",
        status=WorkItemStatus.DONE,
        failure_type="validation_failed",
        failure_summary="Validation exit_code=1",
        result="Static web validation failed.",
        blocked_reason="测试失败已回流到研发返工",
        testing_checklist=[
            {
                "rule_id": "add_item",
                "label": "add item interaction",
                "status": "pending",
                "required_evidence_terms": ["browser form interaction updated visible state"],
            }
        ],
    )
    rework = WorkItem(
        id="workitem-rework-ui",
        description="Fix failed UI validation",
        stage="development",
        kind="ui_implementation",
        feedback_from=["workitem-failed-ui-test"],
        rework_of="workitem-original-ui",
    )
    assignment = TaskAssignment(
        id="assignment-rework-ui",
        workitem_id=rework.id,
        role="frontend_engineer",
        input_artifact_ids=[failed_artifact.id, original_artifact.id],
    )
    state = replace(
        state,
        workitems=[*state.workitems, failed_workitem, rework],
        task_assignments=[*state.task_assignments, assignment],
        artifacts=[*state.artifacts, failed_artifact, original_artifact],
        pending_test_scope=["ui_validation"],
        executions=[
            *state.executions,
            Execution(
                workitem_id=failed_workitem.id,
                agent_id="agent-tester",
                result="UI validation failed",
                status=ExecutionStatus.FAILED,
                validation_command=["python", "-m", "conductor.harness.static_web_cli"],
                validation_exit_code=1,
            ),
        ],
    )
    state_store.save_state(state)

    code = main(["context", assignment.id, "--project-root", str(project_root)])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["rework_context"]["is_rework"] is True
    assert payload["rework_context"]["feedback_from"] == ["workitem-failed-ui-test"]
    assert payload["rework_context"]["rework_of"] == "workitem-original-ui"
    assert payload["rework_context"]["pending_retest_scope"] == ["ui_validation"]
    assert payload["rework_context"]["feedback_artifacts"][0]["id"] == failed_artifact.id
    assert payload["rework_context"]["original_artifacts"][0]["id"] == original_artifact.id
    assert payload["rework_context"]["testing_feedback"][0]["workitem_id"] == failed_workitem.id
    assert payload["rework_context"]["testing_feedback"][0]["validation_command"] == [
        "python",
        "-m",
        "conductor.harness.static_web_cli",
    ]
    assert payload["rework_context"]["testing_feedback"][0]["validation_exit_code"] == "1"
    assert "Browser form submit did not change visible page state" in payload["rework_context"]["testing_feedback"][0]["failing_checks"]
    assert any(
        "检查表单/按钮事件绑定" in action
        for action in payload["rework_context"]["testing_feedback"][0]["suggested_actions"]
    )
    assert "browser form interaction updated visible state" in payload["execution_brief"]
    assert "Rework Context" in payload["execution_brief"]
    assert "Structured Testing Feedback" in payload["execution_brief"]
    assert "Pending Retest Scope: ui_validation" in payload["execution_brief"]
    assert "Validation Command: python, -m, conductor.harness.static_web_cli" in payload["execution_brief"]

    code = main(["context", assignment.id, "--project-root", str(project_root), "--format", "markdown"])
    output = capsys.readouterr().out

    assert code == 0
    assert "## Rework Context" in output
    assert "Feedback From: workitem-failed-ui-test" in output
    assert "Pending Retest Scope: ui_validation" in output
    assert "validation_exit_code=1" in output
    assert "Validation Command: python, -m, conductor.harness.static_web_cli" in output
    assert "artifact-failed-ui-test" in output
    assert "Original Artifacts" in output
    assert "artifact-original-ui" in output
    assert "Structured Testing Feedback" in output
    assert "Browser form submit did not change visible page state" in output


def test_task_center_cli_context_renders_testing_checklist(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    state = engine.create_project(requirement="Build a local reading list with CSV export", project_root=str(project_root))
    testing = WorkItem(
        id="workitem-testing-checklist",
        description="Validate frozen requirement coverage",
        stage="testing",
        kind="acceptance_check",
        testing_checklist=[
            {
                "rule_id": "export_csv",
                "label": "CSV export/download",
                "status": "pending",
                "requirement_terms": ["CSV"],
                "required_evidence_terms": ["browser export/download action triggered"],
            }
        ],
    )
    assignment = TaskAssignment(
        id="assignment-testing-checklist",
        workitem_id=testing.id,
        role="tester",
    )
    state = replace(
        state,
        workitems=[*state.workitems, testing],
        task_assignments=[*state.task_assignments, assignment],
    )
    state_store.save_state(state)

    code = main(["context", assignment.id, "--project-root", str(project_root), "--format", "markdown"])
    output = capsys.readouterr().out

    assert code == 0
    assert "### Testing Checklist" in output
    assert "export_csv | CSV export/download | status=pending" in output
    assert "browser export/download action triggered" in output


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
    assert "## Delivery Contract" in output
    assert "- Expected Outputs:" in output
    assert "- Guardrails:" in output
    assert "- Verification Focus:" in output
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
    assert "- Lease Seconds:" in output
    assert "- Lease Expires At:" in output
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
    assert payload["context"]["assignment"]["return_commands"]["complete_with_output_file"].endswith(
        '--output-file "result.md"'
    )
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
    assert '--agent-id "agent-markdown-worker"' in output
    assert '--agent-id "<agent-id>"' not in output
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
    claim_payload = json.loads(capsys.readouterr().out)
    release_code = main(
        [
            "release",
            assignment_id,
            "--project-root",
            str(project_root),
            "--agent-id",
            "agent-external",
            "--claim-token",
            claim_payload["task"]["claim_token"],
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


def test_task_center_cli_release_expired_leases_requeues_expired_claims(tmp_path, capsys) -> None:
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
            "--lease-seconds",
            "1",
        ]
    )
    capsys.readouterr()
    claimed_state = FileStateStore(project_root / ".conductor" / "state").get_state(state.project.id)
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    assignment = replace(
        claimed_state.task_assignments[0],
        lease_seconds=1,
        lease_expires_at=expired_at,
    )
    FileStateStore(project_root / ".conductor" / "state").upsert_task_assignment(state.project.id, assignment)

    release_code = main(
        [
            "release-expired-leases",
            "--project-root",
            str(project_root),
            "--release-reason",
            "lease cleanup",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert claim_code == 0
    assert release_code == 0
    assert payload["released_count"] == 1
    assert payload["summary"]["queued"] == 1
    assert payload["summary"]["lease_expired"] == 0
    assert payload["tasks"][0]["status"] == "queued"
    assert payload["tasks"][0]["lease_seconds"] == 0
    assert payload["tasks"][0]["lease_expires_at"] == ""
    assert payload["tasks"][0]["claim_reason"] == "lease cleanup"

    reloaded = FileStateStore(project_root / ".conductor" / "state").get_state(state.project.id)
    assert reloaded.task_assignments[0].status == TaskAssignmentStatus.QUEUED
    assert reloaded.task_assignments[0].lease_expires_at == ""
    assert reloaded.workitems[0].status == WorkItemStatus.PENDING


def test_task_center_cli_sweep_releases_expired_and_stale_claims(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    state = SharedProjectState(
        project=Project(id="project-sweep", goal="Build a local tool", project_root=str(project_root)),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-expired", description="Expired", stage="development", status=WorkItemStatus.RUNNING),
            WorkItem(id="workitem-stale", description="Stale", stage="development", status=WorkItemStatus.RUNNING),
            WorkItem(id="workitem-fresh", description="Fresh", stage="development", status=WorkItemStatus.RUNNING),
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-expired",
                workitem_id="workitem-expired",
                role="backend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-expired",
                claimed_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                last_heartbeat_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                lease_seconds=60,
                lease_expires_at=(datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),
            ),
            TaskAssignment(
                id="assignment-stale",
                workitem_id="workitem-stale",
                role="backend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-stale",
                claimed_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                last_heartbeat_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            ),
            TaskAssignment(
                id="assignment-fresh",
                workitem_id="workitem-fresh",
                role="backend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-fresh",
                claimed_at=datetime.now(timezone.utc).isoformat(),
                last_heartbeat_at=datetime.now(timezone.utc).isoformat(),
            ),
        ],
    )
    state_store.save_state(state)

    code = main(
        [
            "sweep",
            "--project-root",
            str(project_root),
            "--stale-after-seconds",
            "3600",
            "--expired-lease-release-reason",
            "lease sweep",
            "--stale-release-reason",
            "stale sweep",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["released_count"] == 2
    assert payload["expired_lease_released_count"] == 1
    assert payload["stale_released_count"] == 1
    assert [task["id"] for task in payload["expired_lease_tasks"]] == ["assignment-expired"]
    assert [task["id"] for task in payload["stale_tasks"]] == ["assignment-stale"]
    assert payload["summary"]["queued"] == 2
    assert payload["summary"]["claimed"] == 1

    reloaded = FileStateStore(project_root / ".conductor" / "state").get_state(state.project.id)
    assignments = {item.id: item for item in reloaded.task_assignments}
    assert assignments["assignment-expired"].claim_reason == "lease sweep"
    assert assignments["assignment-stale"].claim_reason == "stale sweep"
    assert assignments["assignment-fresh"].status == TaskAssignmentStatus.CLAIMED


def test_task_center_cli_sweep_all_scans_every_project_in_state_dir(tmp_path, capsys) -> None:
    project_root = tmp_path / "workspace"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    expired_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    first_state = SharedProjectState(
        project=Project(id="project-expired", goal="Expired lease", project_root=str(project_root)),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-expired", description="Expired", stage="development", status=WorkItemStatus.RUNNING)
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-expired",
                workitem_id="workitem-expired",
                role="backend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-expired",
                claimed_at=old_time,
                last_heartbeat_at=old_time,
                lease_seconds=60,
                lease_expires_at=expired_time,
            )
        ],
    )
    second_state = SharedProjectState(
        project=Project(id="project-stale", goal="Stale claim", project_root=str(project_root)),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-stale", description="Stale", stage="development", status=WorkItemStatus.RUNNING)
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-stale",
                workitem_id="workitem-stale",
                role="backend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-stale",
                claimed_at=old_time,
                last_heartbeat_at=old_time,
                claim_token="stale-token",
            )
        ],
    )
    state_store.save_state(first_state)
    state_store.save_state(second_state)

    code = main(
        [
            "sweep-all",
            "--project-root",
            str(project_root),
            "--stale-after-seconds",
            "3600",
            "--expired-lease-release-reason",
            "lease sweep all",
            "--stale-release-reason",
            "stale sweep all",
            "--output",
            "maintenance/sweep-all.json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    output_path = project_root / "maintenance" / "sweep-all.json"
    report_payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert code == 0
    assert payload["generated_at"]
    assert payload["output_path"] == str(output_path)
    assert payload["project_count"] == 2
    assert payload["released_count"] == 2
    assert payload["expired_lease_released_count"] == 1
    assert payload["stale_released_count"] == 1
    assert report_payload["project_count"] == 2
    assert report_payload["released_count"] == 2
    assert "output_path" not in report_payload
    project_counts = {item["project_id"]: item for item in payload["projects"]}
    assert project_counts["project-expired"]["expired_lease_released_count"] == 1
    assert project_counts["project-stale"]["stale_released_count"] == 1

    reloaded = FileStateStore(project_root / ".conductor" / "state")
    expired = reloaded.get_state("project-expired").task_assignments[0]
    stale = reloaded.get_state("project-stale").task_assignments[0]
    assert expired.status == TaskAssignmentStatus.QUEUED
    assert expired.claim_reason == "lease sweep all"
    assert stale.status == TaskAssignmentStatus.QUEUED
    assert stale.claim_reason == "stale sweep all"


def test_task_center_cli_audit_all_reports_every_project_and_can_fail(tmp_path, capsys) -> None:
    project_root = tmp_path / "workspace"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    clean_state = SharedProjectState(
        project=Project(id="project-clean", goal="Clean project", project_root=str(project_root)),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[WorkItem(id="workitem-clean", description="Clean", stage="development")],
        task_assignments=[
            TaskAssignment(id="assignment-clean", workitem_id="workitem-clean", role="backend_engineer")
        ],
    )
    broken_state = SharedProjectState(
        project=Project(id="project-broken", goal="Broken project", project_root=str(project_root)),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-broken", description="Broken", stage="development", status=WorkItemStatus.DONE)
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-broken",
                workitem_id="workitem-broken",
                role="backend_engineer",
                status=TaskAssignmentStatus.COMPLETED,
                output_artifact_ids=["artifact-missing"],
            )
        ],
    )
    state_store.save_state(clean_state)
    state_store.save_state(broken_state)

    code = main(
        [
            "audit-all",
            "--project-root",
            str(project_root),
            "--fail-on-findings",
            "--output",
            "maintenance/audit-all.json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    output_path = project_root / "maintenance" / "audit-all.json"
    report_payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert code == 3
    assert payload["generated_at"]
    assert payload["output_path"] == str(output_path)
    assert payload["passed"] is False
    assert payload["project_count"] == 2
    assert payload["finding_count"] >= 1
    assert payload["error_count"] >= 1
    assert payload["attention_project_ids"] == ["project-broken"]
    assert payload["finding_code_counts"]["missing_output_artifact"] == 1
    assert "Restore the output artifact records or rerun the worker return step." in payload["recommendations"]
    assert report_payload["project_count"] == 2
    assert report_payload["finding_count"] == payload["finding_count"]
    assert report_payload["attention_project_ids"] == ["project-broken"]
    assert "output_path" not in report_payload
    projects = {item["project_id"]: item for item in payload["projects"]}
    assert projects["project-clean"]["passed"] is True
    assert projects["project-broken"]["passed"] is False
    assert "missing_output_artifact" in {finding["code"] for finding in projects["project-broken"]["findings"]}


def test_task_center_cli_maintenance_sweeps_then_audits_and_writes_report(tmp_path, capsys) -> None:
    project_root = tmp_path / "workspace"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    expired_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    recoverable_state = SharedProjectState(
        project=Project(id="project-recoverable", goal="Recoverable", project_root=str(project_root)),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-expired", description="Expired", stage="development", status=WorkItemStatus.RUNNING)
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-expired",
                workitem_id="workitem-expired",
                role="backend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-expired",
                claimed_at=old_time,
                last_heartbeat_at=old_time,
                lease_seconds=60,
                lease_expires_at=expired_time,
            )
        ],
        pending_test_scope=["ui_validation"],
        human_control_actions=[
            HumanControlAction(
                id="human-action-pause",
                project_id="project-recoverable",
                action=HumanControlActionType.PAUSE,
                actor="operator",
                reason="inspect failed UI validation",
                stage="testing",
                workitem_id="workitem-expired",
                created_at=old_time,
            )
        ],
    )
    broken_state = SharedProjectState(
        project=Project(id="project-broken", goal="Broken", project_root=str(project_root)),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-broken", description="Broken", stage="development", status=WorkItemStatus.DONE)
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-broken",
                workitem_id="workitem-broken",
                role="backend_engineer",
                status=TaskAssignmentStatus.COMPLETED,
                output_artifact_ids=["artifact-missing"],
            )
        ],
    )
    state_store.save_state(recoverable_state)
    state_store.save_state(broken_state)

    code = main(
        [
            "maintenance",
            "--project-root",
            str(project_root),
            "--stale-after-seconds",
            "3600",
            "--fail-on-findings",
            "--output",
            "maintenance/report.json",
            "--latest-output",
            ".conductor/maintenance/latest.json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    output_path = project_root / "maintenance" / "report.json"
    report_payload = json.loads(output_path.read_text(encoding="utf-8"))
    latest_path = project_root / ".conductor" / "maintenance" / "latest.json"
    latest_payload = json.loads(latest_path.read_text(encoding="utf-8"))

    assert code == 3
    assert payload["status"] == "needs_attention"
    assert payload["output_path"] == str(output_path)
    assert payload["latest_output_path"] == str(latest_path)
    assert payload["project_count"] == 2
    assert payload["released_count"] == 1
    assert payload["expired_lease_released_count"] == 1
    assert payload["finding_count"] >= 1
    assert payload["attention_project_ids"] == ["project-broken"]
    assert payload["finding_code_counts"]["missing_output_artifact"] == 1
    assert "Restore the output artifact records or rerun the worker return step." in payload["recommendations"]
    assert payload["pending_retest_project_ids"] == ["project-recoverable"]
    assert payload["pending_retest_scopes"] == {"project-recoverable": ["ui_validation"]}
    assert payload["human_control_project_ids"] == ["project-recoverable"]
    assert payload["active_human_control_actions"][0]["action"] == "pause"
    assert payload["active_human_control_actions"][0]["reason"] == "inspect failed UI validation"
    assert payload["operator_guidance"].startswith("Schedule the maintenance command")
    assert payload["operator_commands"][0].startswith("python -m app.task_center maintenance")
    assert f'--project-root "{project_root}"' in payload["operator_commands"][0]
    assert '--output "maintenance/report.json"' in payload["operator_commands"][0]
    assert '--latest-output ".conductor/maintenance/latest.json"' in payload["operator_commands"][0]
    assert payload["operator_commands"][1].startswith("python -m app.task_center maintenance-status")
    assert "--fail-on-findings" in payload["operator_commands"][1]
    assert payload["operator_commands"][2].startswith("python -m app.human_control status-all")
    assert "--active-only" in payload["operator_commands"][2]
    assert "--fail-on-active" in payload["operator_commands"][2]
    assert '--output ".conductor/human-control/status.json"' in payload["operator_commands"][2]
    assert payload["sweep"]["released_count"] == 1
    assert payload["audit"]["passed"] is False
    assert report_payload["status"] == "needs_attention"
    assert report_payload["attention_project_ids"] == ["project-broken"]
    assert report_payload["pending_retest_project_ids"] == ["project-recoverable"]
    assert report_payload["human_control_project_ids"] == ["project-recoverable"]
    assert report_payload["operator_commands"] == payload["operator_commands"]
    assert "output_path" not in report_payload
    assert latest_payload["ok"] is True
    assert latest_payload["generated_at"] == payload["generated_at"]
    assert latest_payload["status"] == "needs_attention"
    assert latest_payload["project_count"] == 2
    assert latest_payload["released_count"] == 1
    assert latest_payload["finding_count"] == payload["finding_count"]
    assert latest_payload["error_count"] == payload["error_count"]
    assert latest_payload["warning_count"] == payload["warning_count"]
    assert latest_payload["attention_project_ids"] == ["project-broken"]
    assert latest_payload["finding_code_counts"]["missing_output_artifact"] == 1
    assert "Restore the output artifact records or rerun the worker return step." in latest_payload["recommendations"]
    assert latest_payload["pending_retest_project_ids"] == ["project-recoverable"]
    assert latest_payload["pending_retest_scopes"] == {"project-recoverable": ["ui_validation"]}
    assert latest_payload["human_control_project_ids"] == ["project-recoverable"]
    assert latest_payload["active_human_control_actions"][0]["action"] == "pause"
    assert latest_payload["operator_guidance"] == payload["operator_guidance"]
    assert latest_payload["operator_commands"] == payload["operator_commands"]
    assert latest_payload["report_path"] == str(output_path)

    status_code = main(
        [
            "maintenance-status",
            "--project-root",
            str(project_root),
            "--latest",
            ".conductor/maintenance/latest.json",
            "--fail-on-findings",
        ]
    )
    status_payload = json.loads(capsys.readouterr().out)

    assert status_code == 3
    assert status_payload["healthy"] is False
    assert status_payload["reason"] == "findings"
    assert status_payload["status"] == "needs_attention"
    assert status_payload["attention_project_ids"] == ["project-broken"]
    assert status_payload["finding_code_counts"]["missing_output_artifact"] == 1
    assert status_payload["pending_retest_project_ids"] == ["project-recoverable"]
    assert status_payload["pending_retest_scopes"] == {"project-recoverable": ["ui_validation"]}
    assert status_payload["human_control_project_ids"] == ["project-recoverable"]
    assert status_payload["active_human_control_actions"][0]["action"] == "pause"
    assert status_payload["operator_guidance"] == payload["operator_guidance"]
    assert status_payload["operator_commands"] == payload["operator_commands"]
    assert status_payload["report_path"] == str(output_path)

    reloaded = FileStateStore(project_root / ".conductor" / "state").get_state("project-recoverable")
    assert reloaded.task_assignments[0].status == TaskAssignmentStatus.QUEUED
    assert reloaded.workitems[0].status == WorkItemStatus.PENDING


def test_task_center_cli_maintenance_status_reads_clean_latest(tmp_path, capsys) -> None:
    project_root = tmp_path / "workspace"
    latest_path = project_root / ".conductor" / "maintenance" / "latest.json"
    latest_path.parent.mkdir(parents=True)
    latest_path.write_text(
        json.dumps(
            {
                "ok": True,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "status": "clean",
                "project_count": 2,
                "released_count": 0,
                "finding_count": 0,
                "error_count": 0,
                "warning_count": 0,
                "report_path": str(project_root / ".conductor" / "maintenance" / "report.json"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    code = main(["maintenance-status", "--project-root", str(project_root), "--fail-on-findings"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["exists"] is True
    assert payload["healthy"] is True
    assert payload["reason"] == "clean"
    assert payload["stale"] is False
    assert payload["project_count"] == 2
    assert payload["attention_project_ids"] == []
    assert payload["finding_code_counts"] == {}
    assert payload["recommendations"] == []
    assert payload["pending_retest_project_ids"] == []
    assert payload["pending_retest_scopes"] == {}
    assert payload["human_control_project_ids"] == []
    assert payload["active_human_control_actions"] == []
    assert payload["operator_guidance"].startswith("Schedule the maintenance command")
    assert payload["operator_commands"][0].startswith("python -m app.task_center maintenance")
    assert f'--project-root "{project_root}"' in payload["operator_commands"][0]
    assert '--latest-output ".conductor/maintenance/latest.json"' in payload["operator_commands"][0]
    assert payload["operator_commands"][1].startswith("python -m app.task_center maintenance-status")
    assert '--latest ".conductor/maintenance/latest.json"' in payload["operator_commands"][1]
    assert "--fail-on-findings" in payload["operator_commands"][1]
    assert payload["operator_commands"][2].startswith("python -m app.human_control status-all")
    assert "--fail-on-active" in payload["operator_commands"][2]


def test_task_center_cli_maintenance_status_reports_stale_latest(tmp_path, capsys) -> None:
    project_root = tmp_path / "workspace"
    latest_path = project_root / ".conductor" / "maintenance" / "latest.json"
    latest_path.parent.mkdir(parents=True)
    latest_path.write_text(
        json.dumps(
            {
                "ok": True,
                "generated_at": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
                "status": "clean",
                "project_count": 1,
                "released_count": 0,
                "finding_count": 0,
                "error_count": 0,
                "warning_count": 0,
                "report_path": str(project_root / ".conductor" / "maintenance" / "report.json"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    code = main(
        [
            "maintenance-status",
            "--project-root",
            str(project_root),
            "--max-age-seconds",
            "60",
            "--fail-on-findings",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 3
    assert payload["healthy"] is False
    assert payload["stale"] is True
    assert payload["reason"] == "stale"
    assert payload["age_seconds"] >= 60
    assert "--max-age-seconds 60" in payload["operator_commands"][1]


def test_task_center_cli_maintenance_status_reports_missing_latest(tmp_path, capsys) -> None:
    project_root = tmp_path / "workspace"

    code = main(["maintenance-status", "--project-root", str(project_root)])
    payload = json.loads(capsys.readouterr().out)

    assert code == 2
    assert payload["exists"] is False
    assert payload["healthy"] is False
    assert payload["operator_commands"][0].startswith("python -m app.task_center maintenance")
    assert payload["operator_commands"][1].startswith("python -m app.task_center maintenance-status")
    assert payload["operator_commands"][2].startswith("python -m app.human_control status-all")
    assert payload["error"] == "latest maintenance file not found"


def test_task_center_cli_watchdog_runs_maintenance_when_latest_missing(tmp_path, capsys) -> None:
    project_root = tmp_path / "workspace"
    state_store = FileStateStore(project_root / ".conductor" / "state")
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        state_store=state_store,
    )
    engine.create_project(requirement="Build a local reading list", project_root=str(project_root))

    code = main(
        [
            "watchdog",
            "--project-root",
            str(project_root),
            "--max-age-seconds",
            "60",
            "--stale-after-seconds",
            "3600",
            "--output",
            "maintenance/report.json",
            "--latest-output",
            ".conductor/maintenance/latest.json",
            "--fail-on-unhealthy",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    report_path = project_root / "maintenance" / "report.json"
    latest_path = project_root / ".conductor" / "maintenance" / "latest.json"

    assert code == 0
    assert payload["maintenance_ran"] is True
    assert payload["refresh_reason"] == "missing_latest"
    assert payload["before"]["exists"] is False
    assert payload["healthy"] is True
    assert payload["reason"] == "clean"
    assert payload["status"]["healthy"] is True
    assert payload["maintenance"]["project_count"] == 1
    assert payload["maintenance"]["latest_output_path"] == str(latest_path)
    assert report_path.exists()
    assert latest_path.exists()
    assert payload["operator_guidance"].startswith("Run watchdog from a scheduler")
    assert payload["operator_commands"][0].startswith("python -m app.task_center watchdog")
    assert f'--project-root "{project_root}"' in payload["operator_commands"][0]
    assert '--output "maintenance/report.json"' in payload["operator_commands"][0]
    assert '--latest-output ".conductor/maintenance/latest.json"' in payload["operator_commands"][0]
    assert "--max-age-seconds 60" in payload["operator_commands"][0]
    assert "--fail-on-unhealthy" in payload["operator_commands"][0]
    assert payload["operator_commands"][2].startswith("python -m app.human_control status-all")
    assert "--fail-on-active" in payload["operator_commands"][2]


def test_task_center_cli_watchdog_check_only_does_not_write_when_latest_missing(tmp_path, capsys) -> None:
    project_root = tmp_path / "workspace"

    code = main(
        [
            "watchdog",
            "--project-root",
            str(project_root),
            "--check-only",
            "--fail-on-unhealthy",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 3
    assert payload["maintenance_ran"] is False
    assert payload["check_only"] is True
    assert payload["refresh_reason"] == "missing_latest"
    assert payload["healthy"] is False
    assert payload["before"]["exists"] is False
    assert payload["maintenance"] == {}
    assert not (project_root / ".conductor" / "maintenance" / "report.json").exists()
    assert not (project_root / ".conductor" / "maintenance" / "latest.json").exists()


def test_task_center_cli_watchdog_skips_fresh_healthy_latest(tmp_path, capsys) -> None:
    project_root = tmp_path / "workspace"
    latest_path = project_root / ".conductor" / "maintenance" / "latest.json"
    latest_path.parent.mkdir(parents=True)
    latest_path.write_text(
        json.dumps(
            {
                "ok": True,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "status": "clean",
                "project_count": 2,
                "released_count": 0,
                "finding_count": 0,
                "error_count": 0,
                "warning_count": 0,
                "report_path": str(project_root / ".conductor" / "maintenance" / "report.json"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    code = main(["watchdog", "--project-root", str(project_root), "--max-age-seconds", "60"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["maintenance_ran"] is False
    assert payload["refresh_reason"] == "healthy"
    assert payload["healthy"] is True
    assert payload["reason"] == "clean"
    assert payload["status"]["project_count"] == 2
    assert payload["maintenance"] == {}


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
    input_artifact = Artifact(
        id="artifact-input-baseline",
        project_id=state.project.id,
        workitem_id="workitem-original",
        agent_id="agent-upstream",
        kind="design_overview",
        title="Input Baseline",
        content="Input artifact for lineage.",
    )
    state = state_store.add_artifact(state.project.id, input_artifact)
    rework_item = replace(state.workitems[0], rework_of="workitem-original")
    state = replace(state, workitems=[rework_item, *state.workitems[1:]])
    state_store.save_state(state)
    assignment = replace(state.task_assignments[0], input_artifact_ids=[input_artifact.id])
    state_store.upsert_task_assignment(state.project.id, assignment)
    assignment_id = assignment.id
    assert main(["claim", assignment_id, "--project-root", str(project_root), "--agent-id", "agent-external"]) == 0
    claim_payload = json.loads(capsys.readouterr().out)

    code = main(
        [
            "complete",
            assignment_id,
            "--project-root",
            str(project_root),
            "--agent-id",
            "agent-external",
            "--claim-token",
            claim_payload["task"]["claim_token"],
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
    assert artifact.parent_artifact_id == input_artifact.id
    assert artifact.derived_from == [input_artifact.id]
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
    error_payload = json.loads(captured.err)
    assert error_payload["status_code"] == 404
    assert error_payload["error_code"] == "task_center_error"
    assert "No queued task assignment available for role missing_role" in error_payload["error"]
