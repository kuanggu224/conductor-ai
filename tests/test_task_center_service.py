"""Task Center service tests."""

from datetime import datetime, timezone

from conductor.domain.models import (
    Project,
    ProjectStatus,
    SharedProjectState,
    TaskAssignment,
    TaskAssignmentStatus,
    WorkItem,
    WorkItemStatus,
)
from conductor.state.store import InMemoryStateStore
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
    assert transition.assignment.claimed_at
    assert transition.assignment.returned_at == ""
    assert transition.state.workitems[2].status == WorkItemStatus.RUNNING
    assert transition.state.workitems[2].owner_agent == "agent-backend"


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
    assert transition.assignment.claim_reason == "worker interrupted"
    assert transition.assignment.claimed_at == ""
    assert transition.assignment.last_heartbeat_at == ""
    assert transition.assignment.returned_at == ""
    assert transition.assignment.prompt_file == ""
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
