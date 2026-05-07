"""ContextBuilder 测试。"""

from conductor.context.builder import ContextBuilder
from conductor.domain.models import Artifact, Project, ProjectStatus, SharedProjectState, WorkItem


def test_context_builder_includes_previous_artifacts() -> None:
    design_workitem = WorkItem(
        id="workitem-design",
        description="输出设计方案",
        stage="design",
        kind="design_overview",
    )
    backend_workitem = WorkItem(
        id="workitem-backend",
        description="输出后端实现说明",
        stage="development",
        kind="api_implementation",
        acceptance_criteria=["必须参考设计方案"],
    )
    state = SharedProjectState(
        project=Project(id="project-1", goal="实现任务管理", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[design_workitem, backend_workitem],
        artifacts=[
            Artifact(
                id="artifact-design",
                project_id="project-1",
                workitem_id="workitem-design",
                agent_id="agent-designer",
                kind="design_overview",
                title="产品/设计文档",
                content="任务管理设计内容",
                source_backend="llm/cloud",
            )
        ],
        gate_history=["GateDecision: design -> pass"],
    )

    context = ContextBuilder().build(state=state, workitem=backend_workitem)

    assert context.current_workitem is backend_workitem
    assert context.artifact_ids == ["artifact-design"]
    assert context.acceptance_criteria == ["必须参考设计方案"]
    assert "任务管理设计内容" in context.artifacts[0]
    assert "产品/设计文档" in context.relevant_memory.artifact_memory[0]
    assert context.relevant_memory.decision_memory == ["GateDecision: design -> pass"]


def test_context_builder_keeps_mock_artifacts_visible_for_now() -> None:
    design_workitem = WorkItem(
        id="workitem-design",
        description="输出设计方案",
        stage="design",
        kind="design_overview",
    )
    backend_workitem = WorkItem(
        id="workitem-backend",
        description="输出后端实现说明",
        stage="development",
        kind="api_implementation",
    )
    state = SharedProjectState(
        project=Project(id="project-1", goal="实现任务管理", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[design_workitem, backend_workitem],
        artifacts=[
            Artifact(
                id="artifact-mock",
                project_id="project-1",
                workitem_id="workitem-design",
                agent_id="agent-designer",
                kind="design_overview",
                title="模拟设计文档",
                content="这是一份 mock 内容",
                source_backend="mock",
            ),
            Artifact(
                id="artifact-llm",
                project_id="project-1",
                workitem_id="workitem-design",
                agent_id="agent-designer",
                kind="design_overview",
                title="真实设计文档",
                content="这是一份 LLM 内容",
                source_backend="llm/cloud",
            ),
        ],
    )

    context = ContextBuilder().build(state=state, workitem=backend_workitem)

    assert len(context.artifacts) == 2
    assert "mock 内容" in "\n".join(context.artifacts)
    assert "LLM 内容" in "\n".join(context.artifacts)


def test_context_builder_prefers_artifact_file_content(tmp_path) -> None:
    artifact_path = tmp_path / "artifact.md"
    artifact_path.write_text("文件中的真实产物内容", encoding="utf-8")
    design_workitem = WorkItem(
        id="workitem-design",
        description="输出设计方案",
        stage="design",
        kind="design_overview",
    )
    backend_workitem = WorkItem(
        id="workitem-backend",
        description="输出后端实现说明",
        stage="development",
        kind="api_implementation",
    )
    state = SharedProjectState(
        project=Project(id="project-1", goal="实现任务管理", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[design_workitem, backend_workitem],
        artifacts=[
            Artifact(
                id="artifact-design",
                project_id="project-1",
                workitem_id="workitem-design",
                agent_id="agent-designer",
                kind="design_overview",
                title="产品/设计文档",
                content="内存里的旧内容",
                path=str(artifact_path),
                source_backend="llm/cloud",
            )
        ],
    )

    context = ContextBuilder().build(state=state, workitem=backend_workitem)

    assert "文件中的真实产物内容" in context.artifacts[0]
    assert "内存里的旧内容" not in context.artifacts[0]
def test_context_builder_keeps_dependency_and_design_artifacts_when_many_recents() -> None:
    design = WorkItem(id="workitem-design", description="design", stage="design", kind="design_overview")
    backend = WorkItem(
        id="workitem-backend",
        description="backend",
        stage="development",
        kind="api_implementation",
        dependencies=["workitem-design"],
    )
    artifacts = [
        Artifact(
            id="artifact-design",
            project_id="project-1",
            workitem_id="workitem-design",
            agent_id="agent-designer",
            kind="design_overview",
            title="Domain design",
            content="业务领域设计不能丢",
        ),
        *[
            Artifact(
                id=f"artifact-recent-{index}",
                project_id="project-1",
                workitem_id=f"workitem-recent-{index}",
                agent_id="agent",
                kind="api_implementation",
                title=f"Recent {index}",
                content=f"recent {index}",
            )
            for index in range(10)
        ],
    ]
    state = SharedProjectState(
        project=Project(id="project-1", goal="实现业务系统", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[design, backend],
        artifacts=artifacts,
    )

    context = ContextBuilder(max_artifacts=4).build(state, backend)

    assert "业务领域设计不能丢" in "\n".join(context.artifacts)


def test_context_builder_always_carries_frozen_requirement_into_later_stages() -> None:
    requirement = WorkItem(id="workitem-req", description="requirement", stage="requirement", kind="requirement_spec")
    design = WorkItem(id="workitem-design", description="design", stage="design", kind="design_overview")
    testing = WorkItem(
        id="workitem-test",
        description="test",
        stage="testing",
        kind="acceptance_check",
        dependencies=["workitem-design"],
    )
    artifacts = [
        Artifact(
            id="artifact-frozen",
            project_id="project-1",
            workitem_id="workitem-req",
            agent_id="agent-requirement",
            kind="frozen_requirement_spec",
            title="Frozen Requirement",
            content="需求基线：测试必须覆盖导出、异常和持久化。",
        ),
        Artifact(
            id="artifact-design",
            project_id="project-1",
            workitem_id="workitem-design",
            agent_id="agent-designer",
            kind="design_overview",
            title="Design",
            content="设计方案：静态 Web 应用。",
        ),
        *[
            Artifact(
                id=f"artifact-recent-{index}",
                project_id="project-1",
                workitem_id=f"workitem-recent-{index}",
                agent_id="agent",
                kind="api_implementation",
                title=f"Recent {index}",
                content=f"recent {index}",
            )
            for index in range(8)
        ],
    ]
    state = SharedProjectState(
        project=Project(id="project-1", goal="实现静态 Web 应用", current_stage="testing"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="testing",
        workitems=[requirement, design, testing],
        artifacts=artifacts,
    )

    context = ContextBuilder(max_artifacts=3).build(state, testing)
    rendered = "\n".join(context.artifacts)

    assert "artifact-frozen" in context.artifact_ids
    assert "需求基线" in rendered
    assert "静态 Web 应用" in rendered
