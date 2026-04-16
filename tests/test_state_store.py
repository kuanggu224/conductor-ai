"""内存状态存储测试。"""

from conductor.domain.models import Artifact, Project, ProjectStatus, SharedProjectState, WorkItem, WorkItemStatus
from conductor.state.store import InMemoryStateStore


def test_state_store_can_save_update_and_record_event() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-1", goal="测试", current_stage="design"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="design",
        workitems=[WorkItem(id="workitem-1", description="设计", stage="design")],
    )

    store.save_state(state)
    updated = store.update_workitem("project-1", "workitem-1", WorkItemStatus.RUNNING, owner_agent="agent-1")
    updated = store.update_workitem("project-1", "workitem-1", WorkItemStatus.DONE, owner_agent="agent-1", result="完成")
    updated = store.add_event("project-1", "设计阶段完成")

    assert updated.workitems[0].status == WorkItemStatus.DONE
    assert updated.workitems[0].owner_agent == "agent-1"
    assert updated.workitems[0].result == "完成"
    assert updated.project_status == ProjectStatus.IN_PROGRESS
    assert updated.recent_events[-1] == "设计阶段完成"


def test_state_store_upserts_artifact_by_id() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-2", goal="测试产物", current_stage="design"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="design",
    )
    store.save_state(state)

    first = Artifact(
        id="artifact-workitem-001",
        project_id="project-2",
        workitem_id="workitem-001",
        agent_id="agent-designer",
        kind="design_overview",
        title="v1",
        content="draft",
        version=1,
    )
    second = Artifact(
        id="artifact-workitem-001",
        project_id="project-2",
        workitem_id="workitem-001",
        agent_id="agent-designer",
        kind="design_overview",
        title="v2",
        content="revised",
        version=2,
        parent_artifact_id="artifact-workitem-000",
    )

    store.add_artifact("project-2", first)
    updated = store.add_artifact("project-2", second)

    assert len(updated.artifacts) == 1
    assert updated.artifacts[0].title == "v2"
    assert updated.artifacts[0].version == 2
