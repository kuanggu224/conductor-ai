"""核心模型测试。"""

from conductor.context.models import ContextPack
from conductor.domain.models import (
    Capability,
    Execution,
    ExecutionStatus,
    Artifact,
    Project,
    ProjectStatus,
    RouteDecision,
    SharedProjectState,
    Stage,
    WorkItem,
)
from conductor.memory.models import GlobalMemory


def test_core_models_can_be_created() -> None:
    stage = Stage(name="design", objective="定义方案", expected_output="设计文档")
    project = Project(id="project-1", goal="实现骨架", current_stage=stage.name)
    workitem = WorkItem(id="workitem-1", description="完成设计", stage=stage.name)
    execution = Execution(
        workitem_id=workitem.id,
        agent_id="agent-1",
        result="ok",
        status=ExecutionStatus.SUCCESS,
        input_artifact_ids=["artifact-0"],
    )
    route = RouteDecision(workitem_id=workitem.id, selected_agent="agent-1")
    artifact = Artifact(
        id="artifact-1",
        project_id=project.id,
        workitem_id=workitem.id,
        agent_id="agent-1",
        kind="design_overview",
        title="设计文档",
        content="ok",
        version=1,
        derived_from=["artifact-0"],
    )
    state = SharedProjectState(
        project=project,
        project_status=ProjectStatus.INITIALIZED,
        current_stage=stage.name,
        workitems=[workitem],
        artifacts=[artifact],
    )
    memory = GlobalMemory(project_memory=["需求已记录"])
    context = ContextPack(
        current_workitem=workitem,
        relevant_state=state,
        relevant_memory=memory,
        acceptance_criteria=["输出可用于后续阶段"],
    )

    assert Capability.CODING.value == "coding"
    assert project.current_stage == "design"
    assert execution.status == ExecutionStatus.SUCCESS
    assert execution.input_artifact_ids == ["artifact-0"]
    assert route.selected_agent == "agent-1"
    assert state.artifacts[0].title == "设计文档"
    assert state.artifacts[0].version == 1
    assert state.artifacts[0].derived_from == ["artifact-0"]
    assert context.current_workitem is workitem
    assert memory.project_memory == ["需求已记录"]
