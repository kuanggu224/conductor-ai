"""Task Center service tests."""

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
