"""Task Center service tests."""

from datetime import datetime, timedelta, timezone

from conductor.domain.models import (
    AgentActivation,
    Artifact,
    Project,
    ProjectStatus,
    SharedProjectState,
    TaskAssignment,
    TaskAssignmentStatus,
    WorkItem,
    WorkItemStatus,
)
from conductor.state.store import InMemoryStateStore
from conductor.state.file_store import FileStateStore
from conductor.task_center.service import TaskCenterError, TaskCenterService


def test_task_center_service_claim_next_skips_unsatisfied_dependencies() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-dependency", description="Dependency", stage="development"),
            WorkItem(
                id="workitem-blocked",
                description="Blocked task",
                stage="development",
                dependencies=["workitem-dependency"],
            ),
            WorkItem(id="workitem-open", description="Open task", stage="development"),
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-blocked",
                workitem_id="workitem-blocked",
                role="backend_engineer",
                dependencies=["workitem-dependency"],
            ),
            TaskAssignment(
                id="assignment-open",
                workitem_id="workitem-open",
                role="backend_engineer",
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)
    blocked_assignment = state.task_assignments[0]

    assert service.claimable(state, blocked_assignment) is False
    assert service.unmet_dependency_ids(state, blocked_assignment) == ["workitem-dependency"]
    assert service.summary(state)["total"] == 2
    assert service.summary(state)["claimable"] == 1
    assert service.summary(state)["blocked_by_dependencies"] == 1

    transition = service.claim_next("project-service", agent_id="agent-backend", role="backend_engineer")

    assert transition.assignment.id == "assignment-open"
    assert transition.assignment.status == TaskAssignmentStatus.CLAIMED
    assert transition.assignment.claim_token
    assert transition.assignment.claimed_at
    assert transition.assignment.returned_at == ""
    assert transition.state.workitems[2].status == WorkItemStatus.RUNNING
    assert transition.state.workitems[2].owner_agent == "agent-backend"
    assert transition.assignment.transition_history[-1]["action"] == "claim"
    assert transition.assignment.transition_history[-1]["agent_id"] == "agent-backend"


def test_task_center_service_claim_batch_enforces_limit_and_audits_claims() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-a", description="Open task A", stage="development"),
            WorkItem(id="workitem-b", description="Open task B", stage="development"),
            WorkItem(id="workitem-c", description="Open task C", stage="development"),
        ],
        task_assignments=[
            TaskAssignment(id="assignment-a", workitem_id="workitem-a", role="backend_engineer"),
            TaskAssignment(id="assignment-b", workitem_id="workitem-b", role="backend_engineer"),
            TaskAssignment(id="assignment-c", workitem_id="workitem-c", role="frontend_engineer"),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store, max_bulk_claim_limit=2)

    transition = service.claim_batch(
        "project-service",
        agent_id="agent-backend",
        role="backend_engineer",
        claim_reason="batch worker",
        limit=2,
    )

    assert [assignment.id for assignment in transition.assignments] == ["assignment-a", "assignment-b"]
    assert all(assignment.status == TaskAssignmentStatus.CLAIMED for assignment in transition.assignments)
    assert all(assignment.transition_history[-1]["action"] == "claim" for assignment in transition.assignments)
    assert all(assignment.transition_history[-1]["reason"] == "batch worker" for assignment in transition.assignments)
    assert service.summary(transition.state)["claimed"] == 2

    try:
        service.claim_batch("project-service", agent_id="agent-backend", limit=3)
    except TaskCenterError as error:
        assert str(error) == "Bulk claim limit 3 exceeds configured maximum 2."
        assert error.status_code == 400
    else:
        raise AssertionError("Expected TaskCenterError")


def test_task_center_service_blocks_parallel_write_scope_conflicts() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build frontend UI"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-layout-a", description="Implement layout A", stage="development", kind="ui_implementation"),
            WorkItem(id="workitem-layout-b", description="Implement layout B", stage="development", kind="ui_implementation"),
        ],
        task_assignments=[
            TaskAssignment(id="assignment-layout-a", workitem_id="workitem-layout-a", role="frontend_engineer"),
            TaskAssignment(id="assignment-layout-b", workitem_id="workitem-layout-b", role="frontend_engineer"),
        ],
        agent_activations=[
            AgentActivation(
                role="frontend_engineer",
                agent_id="agent-frontend-layout-a",
                stage="development",
                reason="layout scope",
                related_workitem_kinds=["ui_implementation"],
                dynamic=True,
                parallel_safe=True,
                write_scope=["frontend layout files"],
            ),
            AgentActivation(
                role="frontend_engineer",
                agent_id="agent-frontend-layout-b",
                stage="development",
                reason="layout scope",
                related_workitem_kinds=["ui_implementation"],
                dynamic=True,
                parallel_safe=True,
                write_scope=["frontend layout files"],
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)

    first = service.claim("project-service", "assignment-layout-a", agent_id="agent-frontend-layout-a")
    latest = first.state
    second = next(item for item in latest.task_assignments if item.id == "assignment-layout-b")

    assert service.write_scope_conflicts(latest, second, agent_id="agent-frontend-layout-b") == ["assignment-layout-a"]
    assert service.claimable(latest, second, agent_id="agent-frontend-layout-b") is False
    assert service.summary(latest)["blocked_by_write_scope"] == 1
    try:
        service.claim("project-service", "assignment-layout-b", agent_id="agent-frontend-layout-b")
    except TaskCenterError as error:
        assert "write scope conflicts" in str(error)
        assert "assignment-layout-a" in str(error)
    else:
        raise AssertionError("Expected TaskCenterError")


def test_task_center_audit_flags_claimed_write_scope_conflicts() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build frontend UI"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(
                id="workitem-layout-a",
                description="Implement layout A",
                stage="development",
                kind="ui_implementation",
                status=WorkItemStatus.RUNNING,
            ),
            WorkItem(
                id="workitem-layout-b",
                description="Implement layout B",
                stage="development",
                kind="ui_implementation",
                status=WorkItemStatus.RUNNING,
            ),
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-layout-a",
                workitem_id="workitem-layout-a",
                role="frontend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-frontend-layout-a",
                claim_token="token-a",
                claimed_at="2026-05-20T00:00:00+00:00",
                last_heartbeat_at="2026-05-20T00:00:00+00:00",
            ),
            TaskAssignment(
                id="assignment-layout-b",
                workitem_id="workitem-layout-b",
                role="frontend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-frontend-layout-b",
                claim_token="token-b",
                claimed_at="2026-05-20T00:00:00+00:00",
                last_heartbeat_at="2026-05-20T00:00:00+00:00",
            ),
        ],
        agent_activations=[
            AgentActivation(
                role="frontend_engineer",
                agent_id="agent-frontend-layout-a",
                stage="development",
                reason="layout scope",
                related_workitem_kinds=["ui_implementation"],
                dynamic=True,
                parallel_safe=True,
                write_scope=["index.html"],
            ),
            AgentActivation(
                role="frontend_engineer",
                agent_id="agent-frontend-layout-b",
                stage="development",
                reason="layout scope",
                related_workitem_kinds=["ui_implementation"],
                dynamic=True,
                parallel_safe=True,
                write_scope=["index.html"],
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)

    findings = service.audit(state, now=datetime(2026, 5, 20, 0, 5, tzinfo=timezone.utc))
    conflict_findings = [finding for finding in findings if finding.code == "write_scope_conflict"]

    assert len(conflict_findings) == 2
    assert all(finding.severity == "error" for finding in conflict_findings)
    assert any(
        finding.assignment_id == "assignment-layout-a"
        and finding.related_assignment_ids == ["assignment-layout-b"]
        for finding in conflict_findings
    )
    assert any(
        finding.assignment_id == "assignment-layout-a" and "assignment-layout-b" in finding.message
        for finding in conflict_findings
    )


def test_task_center_service_audit_finds_incomplete_returns_and_recoverable_claims() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-completed", description="Completed", stage="development", status=WorkItemStatus.DONE),
            WorkItem(id="workitem-failed", description="Failed", stage="development", status=WorkItemStatus.FAILED),
            WorkItem(id="workitem-claimed", description="Claimed", stage="development", status=WorkItemStatus.RUNNING),
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-completed",
                workitem_id="workitem-completed",
                role="backend_engineer",
                status=TaskAssignmentStatus.COMPLETED,
            ),
            TaskAssignment(
                id="assignment-failed",
                workitem_id="workitem-failed",
                role="backend_engineer",
                status=TaskAssignmentStatus.FAILED,
            ),
            TaskAssignment(
                id="assignment-claimed",
                workitem_id="workitem-claimed",
                role="backend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-worker",
                claimed_at="2026-05-08T00:00:00+00:00",
                last_heartbeat_at="2026-05-08T00:00:00+00:00",
                lease_seconds=60,
                lease_expires_at="2026-05-08T00:01:00+00:00",
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)

    findings = service.audit(
        state,
        stale_after_seconds=3600,
        now=datetime(2026, 5, 8, 2, 0, tzinfo=timezone.utc),
    )
    codes = [finding.code for finding in findings]

    assert "completed_without_evidence" in codes
    assert "failed_without_reason" in codes
    assert "claimed_missing_token" in codes
    assert "lease_expired" in codes
    assert "stale_claimed" in codes
    assert any(finding.severity == "error" for finding in findings)


def test_task_center_service_audit_checks_artifact_references_and_queued_state() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[
            WorkItem(
                id="workitem-queued",
                description="Queued but running",
                stage="development",
                status=WorkItemStatus.RUNNING,
            ),
            WorkItem(
                id="workitem-done",
                description="Done with artifact references",
                stage="development",
                status=WorkItemStatus.DONE,
            ),
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-queued",
                workitem_id="workitem-queued",
                role="backend_engineer",
                status=TaskAssignmentStatus.QUEUED,
            ),
            TaskAssignment(
                id="assignment-done",
                workitem_id="workitem-done",
                role="backend_engineer",
                status=TaskAssignmentStatus.COMPLETED,
                result_summary="done",
                input_artifact_ids=["artifact-missing-input"],
                output_artifact_ids=["artifact-real-output", "artifact-missing-output"],
                transition_history=[{"action": "return"}],
            ),
        ],
        artifacts=[
            Artifact(
                id="artifact-real-output",
                project_id="project-service",
                workitem_id="workitem-done",
                agent_id="agent-worker",
                kind="external_result",
                title="Worker output",
                content="done",
            )
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)

    findings = service.audit(state)
    findings_by_code = {finding.code: finding for finding in findings}

    assert findings_by_code["queued_workitem_not_pending"].severity == "error"
    assert findings_by_code["missing_input_artifact"].severity == "warning"
    assert "artifact-missing-input" in findings_by_code["missing_input_artifact"].message
    assert findings_by_code["missing_output_artifact"].severity == "error"
    assert "artifact-missing-output" in findings_by_code["missing_output_artifact"].message


def test_task_center_audit_flags_assignment_lifecycle_field_drift() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-queued", description="Queued", stage="development"),
            WorkItem(id="workitem-claimed", description="Claimed", stage="development", status=WorkItemStatus.RUNNING),
            WorkItem(id="workitem-done", description="Done", stage="development", status=WorkItemStatus.DONE),
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-queued",
                workitem_id="workitem-queued",
                role="backend_engineer",
                status=TaskAssignmentStatus.QUEUED,
                assigned_agent_id="agent-stale",
                claim_token="stale-token",
                claimed_at="2026-05-20T00:00:00+00:00",
            ),
            TaskAssignment(
                id="assignment-claimed",
                workitem_id="workitem-claimed",
                role="backend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                claim_token="token",
                returned_at="2026-05-20T00:10:00+00:00",
            ),
            TaskAssignment(
                id="assignment-done",
                workitem_id="workitem-done",
                role="backend_engineer",
                status=TaskAssignmentStatus.COMPLETED,
                result_summary="done",
                transition_history=[{"action": "return"}],
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)

    findings = service.audit(state)
    findings_by_code = {finding.code: finding for finding in findings}

    assert findings_by_code["queued_has_claim_state"].severity == "error"
    assert findings_by_code["claimed_missing_agent"].severity == "error"
    assert findings_by_code["claimed_missing_timestamp"].severity == "error"
    assert findings_by_code["claimed_has_returned_at"].severity == "error"
    assert findings_by_code["missing_claim_transition"].severity == "warning"
    assert findings_by_code["missing_return_timestamp"].severity == "warning"


def test_task_center_audit_flags_transition_status_mismatches() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-queued", description="Queued", stage="development"),
            WorkItem(id="workitem-claimed", description="Claimed", stage="development", status=WorkItemStatus.RUNNING),
            WorkItem(id="workitem-done", description="Done", stage="development", status=WorkItemStatus.DONE),
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-queued",
                workitem_id="workitem-queued",
                role="backend_engineer",
                status=TaskAssignmentStatus.QUEUED,
                transition_history=[{"action": "claim"}],
            ),
            TaskAssignment(
                id="assignment-claimed",
                workitem_id="workitem-claimed",
                role="backend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-worker",
                claim_token="token",
                claimed_at="2026-05-20T00:00:00+00:00",
                transition_history=[{"action": "claim"}, {"action": "return"}],
            ),
            TaskAssignment(
                id="assignment-done",
                workitem_id="workitem-done",
                role="backend_engineer",
                status=TaskAssignmentStatus.COMPLETED,
                result_summary="done",
                returned_at="2026-05-20T00:10:00+00:00",
                transition_history=[
                    {"action": "claim", "at": "2026-05-20T00:00:00+00:00"},
                    {"action": "return", "at": "2026-05-20T00:08:00+00:00"},
                ],
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)

    findings = service.audit(state)
    mismatch_findings = [finding for finding in findings if finding.code == "transition_status_mismatch"]
    returned_at_findings = [finding for finding in findings if finding.code == "returned_at_transition_mismatch"]

    assert {finding.assignment_id for finding in mismatch_findings} == {"assignment-queued", "assignment-claimed"}
    assert returned_at_findings[0].assignment_id == "assignment-done"
    assert returned_at_findings[0].severity == "error"


def test_task_center_audit_flags_malformed_transition_history() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-done", description="Done", stage="development", status=WorkItemStatus.DONE),
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-done",
                workitem_id="workitem-done",
                role="backend_engineer",
                status=TaskAssignmentStatus.COMPLETED,
                result_summary="done",
                returned_at="2026-05-20T00:10:00+00:00",
                transition_history=[
                    "bad",
                    {"action": ""},
                    {"action": "teleport"},
                    {
                        "action": "return",
                        "at": "2026-05-20T00:10:00+00:00",
                        "details": "bad",
                    },
                ],
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)

    findings = service.audit(state)
    malformed_findings = [finding for finding in findings if finding.code == "transition_history_malformed"]
    unknown_action_findings = [
        finding for finding in findings if finding.code == "transition_history_unknown_action"
    ]
    details_findings = [
        finding for finding in findings if finding.code == "transition_history_details_not_object"
    ]

    assert len(malformed_findings) == 2
    assert {finding.severity for finding in malformed_findings} == {"error"}
    assert unknown_action_findings[0].severity == "warning"
    assert details_findings[0].severity == "warning"
    assert not [finding for finding in findings if finding.code == "transition_status_mismatch"]
    assert not [finding for finding in findings if finding.code == "returned_at_transition_mismatch"]


def test_task_center_audit_allows_gate_failure_transferred_to_rework() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="design",
        workitems=[
            WorkItem(
                id="workitem-design",
                description="Original design",
                stage="design",
                kind="design_overview",
                status=WorkItemStatus.DONE,
                blocked_reason="Design gate failed and transferred to rework WorkItem",
            ),
            WorkItem(
                id="workitem-rework",
                description="Rework design",
                stage="design",
                kind="design_overview",
                rework_of="workitem-design",
                feedback_from=["workitem-design"],
            ),
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-design",
                workitem_id="workitem-design",
                role="designer",
                status=TaskAssignmentStatus.FAILED,
                result_summary="Design gate rework created: workitem-rework",
                blocked_reason="validation_failed",
                transition_history=[{"action": "return"}],
            )
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)

    findings = service.audit(state)

    assert "failed_workitem_not_failed" not in [finding.code for finding in findings]


def test_task_center_service_file_store_reloads_under_lock_before_claim(tmp_path) -> None:
    state = SharedProjectState(
        project=Project(id="project-file-lock", goal="Build a local tool"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[WorkItem(id="workitem-open", description="Open task", stage="development")],
        task_assignments=[
            TaskAssignment(
                id="assignment-open",
                workitem_id="workitem-open",
                role="backend_engineer",
            ),
        ],
    )
    first_store = FileStateStore(tmp_path / "state")
    first_store.save_state(state)
    second_store = FileStateStore(tmp_path / "state")
    first_service = TaskCenterService(first_store)
    second_service = TaskCenterService(second_store)

    first_service.claim("project-file-lock", "assignment-open", agent_id="agent-a")

    try:
        second_service.claim("project-file-lock", "assignment-open", agent_id="agent-b")
    except TaskCenterError as error:
        assert str(error) == "Task assignment is not queued: claimed"
    else:
        raise AssertionError("Expected TaskCenterError")

    reloaded = FileStateStore(tmp_path / "state").get_state("project-file-lock")
    assert reloaded.task_assignments[0].assigned_agent_id == "agent-a"
    assert reloaded.workitems[0].owner_agent == "agent-a"
    assert reloaded.task_assignments[0].transition_history[-1]["action"] == "claim"


def test_task_center_service_rejects_claim_with_unsatisfied_dependencies() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-dependency", description="Dependency", stage="development"),
            WorkItem(
                id="workitem-blocked",
                description="Blocked task",
                stage="development",
                dependencies=["workitem-dependency"],
            ),
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-blocked",
                workitem_id="workitem-blocked",
                role="backend_engineer",
                dependencies=["workitem-dependency"],
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)

    try:
        service.claim("project-service", "assignment-blocked", agent_id="agent-backend")
    except TaskCenterError as error:
        assert str(error) == "Task assignment dependencies are not satisfied: workitem-dependency"
        assert error.status_code == 409
    else:
        raise AssertionError("Expected TaskCenterError")


def test_task_center_service_complete_syncs_workitem_and_assignment() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[WorkItem(id="workitem-open", description="Open task", stage="development")],
        task_assignments=[
            TaskAssignment(
                id="assignment-open",
                workitem_id="workitem-open",
                role="backend_engineer",
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)

    service.claim("project-service", "assignment-open", agent_id="agent-backend")
    transition = service.complete(
        "project-service",
        "assignment-open",
        result_summary="implemented",
        output_artifact_ids=["artifact-1"],
    )

    assert transition.assignment.status == TaskAssignmentStatus.COMPLETED
    assert transition.assignment.output_artifact_ids == ["artifact-1"]
    assert transition.assignment.claimed_at
    assert transition.assignment.returned_at
    assert transition.state.workitems[0].status == WorkItemStatus.DONE
    assert transition.state.workitems[0].result == "implemented"
    assert transition.state.workitems[0].output_artifact_ids == ["artifact-1"]


def test_task_center_service_rejects_return_before_claim() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[WorkItem(id="workitem-open", description="Open task", stage="development")],
        task_assignments=[
            TaskAssignment(
                id="assignment-open",
                workitem_id="workitem-open",
                role="backend_engineer",
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)

    try:
        service.complete("project-service", "assignment-open", result_summary="done")
    except TaskCenterError as error:
        assert str(error) == "Task assignment is not claimed: queued"
        assert error.status_code == 409
    else:
        raise AssertionError("Expected TaskCenterError")


def test_task_center_service_release_requeues_claimed_assignment() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[WorkItem(id="workitem-open", description="Open task", stage="development")],
        task_assignments=[
            TaskAssignment(
                id="assignment-open",
                workitem_id="workitem-open",
                role="backend_engineer",
                prompt_file="C:/demo/.conductor/task_center/prompts/task.md",
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)

    service.claim("project-service", "assignment-open", agent_id="agent-backend")
    transition = service.release("project-service", "assignment-open", release_reason="worker interrupted")

    assert transition.assignment.status == TaskAssignmentStatus.QUEUED
    assert transition.assignment.assigned_agent_id is None
    assert transition.assignment.claim_token == ""
    assert transition.assignment.claim_reason == "worker interrupted"
    assert transition.assignment.claimed_at == ""
    assert transition.assignment.last_heartbeat_at == ""
    assert transition.assignment.returned_at == ""
    assert transition.assignment.prompt_file == ""
    assert [item["action"] for item in transition.assignment.transition_history] == ["claim", "release"]
    assert transition.assignment.transition_history[-1]["reason"] == "worker interrupted"
    assert transition.state.workitems[0].status == WorkItemStatus.PENDING
    assert transition.state.workitems[0].owner_agent == ""
    assert service.summary(transition.state)["claimable"] == 1


def test_task_center_service_counts_stale_claimed_assignments() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[WorkItem(id="workitem-open", description="Open task", stage="development")],
        task_assignments=[
            TaskAssignment(
                id="assignment-open",
                workitem_id="workitem-open",
                role="backend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                claimed_at="2026-05-08T00:00:00+00:00",
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)
    assignment = state.task_assignments[0]
    now = datetime(2026, 5, 8, 2, 0, tzinfo=timezone.utc)

    assert service.claimed_age_seconds(assignment, now=now) == 7200
    assert service.stale_claimed(assignment, stale_after_seconds=3600, now=now) is True
    assert service.summary(state, stale_after_seconds=3600, now=now)["stale_claimed"] == 1


def test_task_center_service_heartbeat_refreshes_stale_activity() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[
            WorkItem(
                id="workitem-open",
                description="Open task",
                stage="development",
                status=WorkItemStatus.RUNNING,
                owner_agent="agent-backend",
            )
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-open",
                workitem_id="workitem-open",
                role="backend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-backend",
                claimed_at="2026-05-08T00:00:00+00:00",
                last_heartbeat_at="2026-05-08T00:00:00+00:00",
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)

    transition = service.heartbeat(
        "project-service",
        "assignment-open",
        agent_id="agent-backend",
        now=datetime(2026, 5, 8, 1, 30, tzinfo=timezone.utc),
    )

    assert transition.assignment.last_heartbeat_at == "2026-05-08T01:30:00+00:00"
    assert transition.assignment.transition_history[-1]["action"] == "heartbeat"
    assert transition.assignment.transition_history[-1]["at"] == "2026-05-08T01:30:00+00:00"
    assert service.claimed_age_seconds(
        transition.assignment,
        now=datetime(2026, 5, 8, 2, 0, tzinfo=timezone.utc),
    ) == 7200
    assert service.heartbeat_age_seconds(
        transition.assignment,
        now=datetime(2026, 5, 8, 2, 0, tzinfo=timezone.utc),
    ) == 1800
    assert service.stale_claimed(
        transition.assignment,
        stale_after_seconds=3600,
        now=datetime(2026, 5, 8, 2, 0, tzinfo=timezone.utc),
    ) is False


def test_task_center_service_claim_lease_can_be_renewed_by_heartbeat() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[WorkItem(id="workitem-open", description="Open task", stage="development")],
        task_assignments=[
            TaskAssignment(
                id="assignment-open",
                workitem_id="workitem-open",
                role="backend_engineer",
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)

    claimed = service.claim("project-service", "assignment-open", agent_id="agent-backend", lease_seconds=60)
    claimed_at = datetime.fromisoformat(claimed.assignment.claimed_at)
    lease_expires_at = datetime.fromisoformat(claimed.assignment.lease_expires_at)

    assert claimed.assignment.lease_seconds == 60
    assert lease_expires_at == claimed_at + timedelta(seconds=60)
    assert service.lease_expired(
        claimed.assignment,
        now=claimed_at + timedelta(seconds=59),
    ) is False
    assert service.lease_expired(
        claimed.assignment,
        now=claimed_at + timedelta(seconds=60),
    ) is True
    assert service.summary(
        claimed.state,
        now=claimed_at + timedelta(seconds=60),
    )["lease_expired"] == 1

    renewed = service.heartbeat(
        "project-service",
        "assignment-open",
        agent_id="agent-backend",
        lease_seconds=120,
        now=claimed_at + timedelta(seconds=30),
    )
    renewed_at = datetime.fromisoformat(renewed.assignment.last_heartbeat_at)
    renewed_expires_at = datetime.fromisoformat(renewed.assignment.lease_expires_at)

    assert renewed.assignment.lease_seconds == 120
    assert renewed_expires_at == renewed_at + timedelta(seconds=120)
    assert renewed.assignment.transition_history[-1]["action"] == "heartbeat"


def test_task_center_service_rejects_heartbeat_for_wrong_agent() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[WorkItem(id="workitem-open", description="Open task", stage="development")],
        task_assignments=[
            TaskAssignment(
                id="assignment-open",
                workitem_id="workitem-open",
                role="backend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-owner",
                claimed_at="2026-05-08T00:00:00+00:00",
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)

    try:
        service.heartbeat("project-service", "assignment-open", agent_id="agent-other")
    except TaskCenterError as error:
        assert str(error) == "Task assignment is claimed by another agent: agent-owner"
        assert error.status_code == 403
    else:
        raise AssertionError("Expected TaskCenterError")


def test_task_center_service_rejects_return_for_wrong_agent() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[WorkItem(id="workitem-open", description="Open task", stage="development")],
        task_assignments=[
            TaskAssignment(
                id="assignment-open",
                workitem_id="workitem-open",
                role="backend_engineer",
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)
    service.claim("project-service", "assignment-open", agent_id="agent-owner")

    try:
        service.complete("project-service", "assignment-open", result_summary="done", agent_id="agent-other")
    except TaskCenterError as error:
        assert str(error) == "Task assignment is claimed by another agent: agent-owner"
        assert error.status_code == 403
    else:
        raise AssertionError("Expected TaskCenterError")


def test_task_center_service_rejects_return_for_stale_claim_token() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[WorkItem(id="workitem-open", description="Open task", stage="development")],
        task_assignments=[
            TaskAssignment(
                id="assignment-open",
                workitem_id="workitem-open",
                role="backend_engineer",
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)
    transition = service.claim("project-service", "assignment-open", agent_id="agent-owner")

    try:
        service.complete(
            "project-service",
            "assignment-open",
            result_summary="done",
            agent_id="agent-owner",
            claim_token=f"stale-{transition.assignment.claim_token}",
        )
    except TaskCenterError as error:
        assert str(error) == "Task assignment claim token does not match."
        assert error.status_code == 403
    else:
        raise AssertionError("Expected TaskCenterError")


def test_task_center_service_accepts_matching_claim_token() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[WorkItem(id="workitem-open", description="Open task", stage="development")],
        task_assignments=[
            TaskAssignment(
                id="assignment-open",
                workitem_id="workitem-open",
                role="backend_engineer",
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)
    transition = service.claim("project-service", "assignment-open", agent_id="agent-owner")

    completed = service.complete(
        "project-service",
        "assignment-open",
        result_summary="done",
        agent_id="agent-owner",
        claim_token=transition.assignment.claim_token,
    )

    assert completed.assignment.status == TaskAssignmentStatus.COMPLETED
    assert completed.assignment.claim_token == transition.assignment.claim_token
    assert [item["action"] for item in completed.assignment.transition_history] == ["claim", "return"]
    assert completed.assignment.transition_history[-1]["details"]["result_summary"] == "done"


def test_task_center_service_rotates_claim_token_after_release_and_reclaim() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[WorkItem(id="workitem-open", description="Open task", stage="development")],
        task_assignments=[
            TaskAssignment(
                id="assignment-open",
                workitem_id="workitem-open",
                role="backend_engineer",
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)
    first_claim = service.claim("project-service", "assignment-open", agent_id="agent-owner")
    old_token = first_claim.assignment.claim_token
    service.release(
        "project-service",
        "assignment-open",
        release_reason="worker interrupted",
        agent_id="agent-owner",
        claim_token=old_token,
    )
    second_claim = service.claim("project-service", "assignment-open", agent_id="agent-owner")

    assert second_claim.assignment.claim_token
    assert second_claim.assignment.claim_token != old_token
    try:
        service.complete(
            "project-service",
            "assignment-open",
            result_summary="done",
            agent_id="agent-owner",
            claim_token=old_token,
        )
    except TaskCenterError as error:
        assert str(error) == "Task assignment claim token does not match."
        assert error.status_code == 403
    else:
        raise AssertionError("Expected TaskCenterError")


def test_task_center_service_rejects_release_for_wrong_agent() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[WorkItem(id="workitem-open", description="Open task", stage="development")],
        task_assignments=[
            TaskAssignment(
                id="assignment-open",
                workitem_id="workitem-open",
                role="backend_engineer",
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)
    service.claim("project-service", "assignment-open", agent_id="agent-owner")

    try:
        service.release("project-service", "assignment-open", release_reason="interrupt", agent_id="agent-other")
    except TaskCenterError as error:
        assert str(error) == "Task assignment is claimed by another agent: agent-owner"
        assert error.status_code == 403
    else:
        raise AssertionError("Expected TaskCenterError")


def test_task_center_service_release_stale_requeues_only_stale_claimed() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[
            WorkItem(
                id="workitem-stale",
                description="Stale task",
                stage="development",
                status=WorkItemStatus.RUNNING,
                owner_agent="agent-old",
            ),
            WorkItem(
                id="workitem-fresh",
                description="Fresh task",
                stage="development",
                status=WorkItemStatus.RUNNING,
                owner_agent="agent-new",
            ),
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-stale",
                workitem_id="workitem-stale",
                role="backend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-old",
                claimed_at="2026-05-08T00:00:00+00:00",
            ),
            TaskAssignment(
                id="assignment-fresh",
                workitem_id="workitem-fresh",
                role="backend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-new",
                claimed_at="2026-05-08T01:59:59+00:00",
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)
    now = datetime(2026, 5, 8, 2, 0, tzinfo=timezone.utc)

    transition = service.release_stale(
        "project-service",
        stale_after_seconds=3600,
        release_reason="stale cleanup",
        now=now,
    )

    assert [item.id for item in transition.assignments] == ["assignment-stale"]
    assignments = {item.id: item for item in transition.state.task_assignments}
    workitems = {item.id: item for item in transition.state.workitems}
    assert assignments["assignment-stale"].status == TaskAssignmentStatus.QUEUED
    assert assignments["assignment-stale"].assigned_agent_id is None
    assert assignments["assignment-stale"].claim_reason == "stale cleanup"
    assert workitems["workitem-stale"].status == WorkItemStatus.PENDING
    assert assignments["assignment-fresh"].status == TaskAssignmentStatus.CLAIMED
    assert workitems["workitem-fresh"].status == WorkItemStatus.RUNNING


def test_task_center_service_release_expired_leases_requeues_only_expired_claims() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[
            WorkItem(
                id="workitem-expired",
                description="Expired lease task",
                stage="development",
                status=WorkItemStatus.RUNNING,
                owner_agent="agent-old",
            ),
            WorkItem(
                id="workitem-active",
                description="Active lease task",
                stage="development",
                status=WorkItemStatus.RUNNING,
                owner_agent="agent-new",
            ),
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-expired",
                workitem_id="workitem-expired",
                role="backend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-old",
                claimed_at="2026-05-08T00:00:00+00:00",
                last_heartbeat_at="2026-05-08T00:00:00+00:00",
                lease_seconds=60,
                lease_expires_at="2026-05-08T00:01:00+00:00",
            ),
            TaskAssignment(
                id="assignment-active",
                workitem_id="workitem-active",
                role="backend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-new",
                claimed_at="2026-05-08T00:00:00+00:00",
                last_heartbeat_at="2026-05-08T00:00:00+00:00",
                lease_seconds=7200,
                lease_expires_at="2026-05-08T02:00:00+00:00",
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)
    now = datetime(2026, 5, 8, 1, 0, tzinfo=timezone.utc)

    transition = service.release_expired_leases(
        "project-service",
        release_reason="lease cleanup",
        now=now,
    )

    assert [item.id for item in transition.assignments] == ["assignment-expired"]
    assignments = {item.id: item for item in transition.state.task_assignments}
    workitems = {item.id: item for item in transition.state.workitems}
    assert assignments["assignment-expired"].status == TaskAssignmentStatus.QUEUED
    assert assignments["assignment-expired"].assigned_agent_id is None
    assert assignments["assignment-expired"].lease_seconds == 0
    assert assignments["assignment-expired"].lease_expires_at == ""
    assert assignments["assignment-expired"].claim_reason == "lease cleanup"
    assert assignments["assignment-expired"].transition_history[-1]["action"] == "release"
    assert workitems["workitem-expired"].status == WorkItemStatus.PENDING
    assert assignments["assignment-active"].status == TaskAssignmentStatus.CLAIMED
    assert workitems["workitem-active"].status == WorkItemStatus.RUNNING


def test_task_center_service_sweep_releases_expired_leases_before_stale_claims() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
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
                claimed_at="2026-05-08T00:00:00+00:00",
                last_heartbeat_at="2026-05-08T00:00:00+00:00",
                lease_seconds=60,
                lease_expires_at="2026-05-08T00:01:00+00:00",
            ),
            TaskAssignment(
                id="assignment-stale",
                workitem_id="workitem-stale",
                role="backend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-stale",
                claimed_at="2026-05-08T00:00:00+00:00",
                last_heartbeat_at="2026-05-08T00:00:00+00:00",
            ),
            TaskAssignment(
                id="assignment-fresh",
                workitem_id="workitem-fresh",
                role="backend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-fresh",
                claimed_at="2026-05-08T00:59:30+00:00",
                last_heartbeat_at="2026-05-08T00:59:30+00:00",
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)

    transition = service.sweep(
        "project-service",
        stale_after_seconds=3600,
        expired_lease_release_reason="lease sweep",
        stale_release_reason="stale sweep",
        now=datetime(2026, 5, 8, 1, 0, tzinfo=timezone.utc),
    )

    assert [item.id for item in transition.expired_lease_assignments] == ["assignment-expired"]
    assert [item.id for item in transition.stale_assignments] == ["assignment-stale"]
    assignments = {item.id: item for item in transition.state.task_assignments}
    assert assignments["assignment-expired"].status == TaskAssignmentStatus.QUEUED
    assert assignments["assignment-expired"].claim_reason == "lease sweep"
    assert assignments["assignment-stale"].status == TaskAssignmentStatus.QUEUED
    assert assignments["assignment-stale"].claim_reason == "stale sweep"
    assert assignments["assignment-fresh"].status == TaskAssignmentStatus.CLAIMED


def test_task_center_service_rejects_release_after_completion() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-service", goal="Build a local tool"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="development",
        workitems=[WorkItem(id="workitem-open", description="Open task", stage="development")],
        task_assignments=[
            TaskAssignment(
                id="assignment-open",
                workitem_id="workitem-open",
                role="backend_engineer",
            ),
        ],
    )
    store.save_state(state)
    service = TaskCenterService(store)

    service.claim("project-service", "assignment-open", agent_id="agent-backend")
    service.complete("project-service", "assignment-open", result_summary="done")

    try:
        service.release("project-service", "assignment-open")
    except TaskCenterError as error:
        assert str(error) == "Task assignment cannot be released: completed"
        assert error.status_code == 409
    else:
        raise AssertionError("Expected TaskCenterError")
