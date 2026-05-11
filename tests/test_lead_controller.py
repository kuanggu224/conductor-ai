"""LeadController 测试。"""

from conductor.controller.lead_controller import LeadController
from conductor.collaboration.models import Collaboration, CollaborationStatus
from conductor.collaboration.policy import CollaborationPolicy
from conductor.domain.models import Artifact, ProjectStatus, TaskAssignmentStatus, WorkItem, WorkItemStatus
from conductor.execution.runner import Runner
from conductor.state.store import InMemoryStateStore
from conductor.workflow.template import WorkflowTemplate


class FailingRequirementCollaborationRunner:
    policy = CollaborationPolicy(
        enabled=True,
        lead_role_by_stage={"requirement": "requirement_designer"},
        peer_reviewer_roles_by_stage={"requirement": ["designer"]},
        reviewer_roles_by_stage={"requirement": ["tester"]},
        enabled_kinds={"requirement_spec"},
    )

    def should_collaborate(self, project_id: str, workitem: WorkItem) -> bool:
        return workitem.kind == "requirement_spec"

    def run_review_loop(self, project_id: str, workitem: WorkItem, draft_artifact):
        return Collaboration(
            id=f"collaboration-{workitem.id}",
            project_id=project_id,
            workitem_id=workitem.id,
            lead_agent_id="agent-requirement-designer",
            reviewer_agent_ids=[],
            status=CollaborationStatus.FAILED,
            max_rounds=1,
            current_round=1,
        )


def build_controller() -> LeadController:
    state_store = InMemoryStateStore()
    runner = Runner(state_store=state_store)
    workflow = WorkflowTemplate()
    return LeadController(workflow_template=workflow, state_store=state_store, runner=runner)


def build_controller_with_failing_requirement_collaboration() -> LeadController:
    state_store = InMemoryStateStore()
    runner = Runner(state_store=state_store)
    workflow = WorkflowTemplate()
    return LeadController(
        workflow_template=workflow,
        state_store=state_store,
        runner=runner,
        collaboration_runner=FailingRequirementCollaborationRunner(),
    )


def test_initialize_project_creates_requirement_workitem() -> None:
    controller = build_controller()

    state = controller.initialize_project("实现最小骨架")

    assert state.project.current_stage == "requirement"
    assert state.current_stage == "requirement"
    assert len(state.workitems) == 1
    assert state.workitems[0].stage == "requirement"
    assert state.workitems[0].kind == "requirement_spec"
    assert state.planned_roles == ["requirement_designer"]
    assert len(state.agent_activations) == 1
    assert state.agent_activations[0].agent_id == "agent-requirement-designer"
    assert state.agent_activations[0].role == "requirement_designer"
    assert state.agent_activations[0].stage == "requirement"
    assert any("创建 Agent agent-requirement-designer" in event for event in state.recent_events)


def test_initialize_project_uses_requirement_keywords_to_expand_workitems() -> None:
    controller = build_controller()

    state = controller.initialize_project("设计一个 API 接口和 UI 页面，并补充测试")
    state = controller.advance(state)
    state = controller.advance(state)

    design_workitems = [item for item in state.workitems if item.stage == "design"]
    assert [item.kind for item in design_workitems] == [
        "design_overview",
        "ui_design",
        "api_design",
        "test_design",
    ]
    assert state.planned_roles == ["requirement_designer", "designer"]


def test_requirement_gate_failure_creates_rework_workitem() -> None:
    controller = build_controller_with_failing_requirement_collaboration()
    state = controller.initialize_project("实现一个读书清单，支持导出 CSV")

    state = controller.advance(state)

    requirement_items = [item for item in state.workitems if item.stage == "requirement"]
    original = next(item for item in requirement_items if item.id == "workitem-001")
    rework = next(item for item in requirement_items if item.id != "workitem-001")
    original_assignment = next(item for item in state.task_assignments if item.workitem_id == original.id)
    rework_assignment = next(item for item in state.task_assignments if item.workitem_id == rework.id)

    assert original.status == WorkItemStatus.DONE
    assert original.blocked_reason == "需求门禁失败已转入返工 WorkItem"
    assert rework.status == WorkItemStatus.PENDING
    assert rework.kind == "requirement_spec"
    assert rework.feedback_from == [original.id]
    assert rework.input_artifact_ids
    assert original_assignment.status == TaskAssignmentStatus.FAILED
    assert rework_assignment.status == TaskAssignmentStatus.QUEUED
    assert any("需求门禁返工" in event for event in state.recent_events)


def test_requirement_gate_rework_limit_blocks_project() -> None:
    controller = build_controller_with_failing_requirement_collaboration()
    state = controller.initialize_project("实现一个读书清单，支持导出 CSV")

    for _ in range(4):
        state = controller.advance(state)

    requirement_items = [item for item in state.workitems if item.stage == "requirement"]

    assert state.project_status == ProjectStatus.BLOCKED
    assert len(requirement_items) == 4
    assert state.blockers
    assert "需求门禁连续返工仍未通过" in state.blockers[-1]
    assert any("需求门禁返工上限触发" in event for event in state.recent_events)


def test_advance_can_run_and_finish_project() -> None:
    controller = build_controller()
    state = controller.initialize_project("实现最小骨架")

    for _ in range(8):
        state = controller.advance(state)

    assert state.project_status.value == "completed"
    assert len(state.executions) == 4
    assert state.gate_history[-1] == "testing:pass"
    assert [decision.selected_agent for decision in state.route_decisions] == [
        "agent-requirement-designer",
        "agent-designer",
        "agent-backend",
        "agent-tester",
    ]
    assert [activation.role for activation in state.agent_activations] == [
        "requirement_designer",
        "designer",
        "backend_engineer",
        "tester",
    ]
    assert [execution.workitem_id for execution in state.executions] == [
        "workitem-001",
        "workitem-002",
        "workitem-003",
        "workitem-004",
    ]


def test_failed_workitem_can_retry_and_recover() -> None:
    controller = build_controller()
    state = controller.initialize_project("实现最小骨架")
    fail_workitem = WorkItem(
        id="workitem-fail",
        description="模拟一次失败后重试",
        stage="design",
        kind="fail_once",
    )
    state.workitems = [fail_workitem]
    state.current_stage = "design"
    state.project.current_stage = "design"
    controller.state_store.save_state(state)

    state = controller.advance(state)
    assert state.workitems[0].status.value == "failed"
    assert state.gate_history[-1] == "design:rework"

    state = controller.advance(state)
    assert state.workitems[0].status.value == "pending"
    assert state.workitems[0].retry_count == 1
    assert state.gate_history[-1] == "design:retry"

    state = controller.advance(state)
    assert state.workitems[0].status.value == "done"


def test_failed_workitem_escalates_when_retry_exhausted() -> None:
    controller = build_controller()
    state = controller.initialize_project("实现最小骨架")
    fail_workitem = WorkItem(
        id="workitem-fail",
        description="模拟失败且不可重试",
        stage="design",
        kind="fail_once",
        max_retries=0,
    )
    state.workitems = [fail_workitem]
    state.current_stage = "design"
    state.project.current_stage = "design"
    controller.state_store.save_state(state)

    state = controller.advance(state)
    state = controller.advance(state)

    assert state.project_status == ProjectStatus.BLOCKED
    assert state.blockers
    assert state.gate_history[-1] == "design:escalate"


def test_non_retryable_failed_workitem_blocks_without_retry() -> None:
    controller = build_controller()
    state = controller.initialize_project("需要真实设计产物")
    failed_workitem = WorkItem(
        id="workitem-config",
        description="真实后端未配置",
        stage="design",
        kind="design_overview",
        status=WorkItemStatus.FAILED,
        failure_type="configuration_required",
        retryable=False,
        failure_summary="missing cli",
        blocked_reason="failure_type=configuration_required; retryable=false; summary=missing cli",
    )
    state.workitems = [failed_workitem]
    state.current_stage = "design"
    state.project.current_stage = "design"
    controller.state_store.save_state(state)

    state = controller.advance(state)

    assert state.project_status == ProjectStatus.BLOCKED
    assert "configuration_required" in state.blockers[-1]


def test_task_center_records_claim_and_return() -> None:
    controller = build_controller()
    state = controller.initialize_project("实现最小骨架")

    assert len(state.task_assignments) == 1
    assert state.task_assignments[0].status == TaskAssignmentStatus.QUEUED

    state = controller.advance(state)

    assert state.task_assignments[0].status == TaskAssignmentStatus.COMPLETED
    assert state.task_assignments[0].assigned_agent_id == "agent-requirement-designer"
    assert state.task_assignments[0].output_artifact_ids
    assert state.agent_capability_stats[0].agent_id == "agent-requirement-designer"
    assert state.agent_capability_stats[0].completed_count == 1
    assert "requirement_spec" in state.agent_capability_stats[0].workitem_kinds
    assert any("任务中心" in event for event in state.recent_events)


def test_next_stage_workitems_depend_on_previous_stage() -> None:
    controller = build_controller()
    state = controller.initialize_project("实现 API 和 UI 页面")

    while state.current_stage == "requirement":
        state = controller.advance(state)
    while state.current_stage == "design":
        state = controller.advance(state)

    development_items = [item for item in state.workitems if item.stage == "development"]
    design_ids = {item.id for item in state.workitems if item.stage == "design"}
    requirement_artifact_ids = {
        artifact.id for artifact in state.artifacts if artifact.kind in {"requirement_spec", "frozen_requirement_spec"}
    }
    design_artifact_ids = {artifact.id for artifact in state.artifacts if artifact.workitem_id in design_ids}
    development_assignments = [
        assignment
        for assignment in state.task_assignments
        if assignment.workitem_id in {item.id for item in development_items}
    ]

    assert development_items
    assert all(set(item.dependencies) == design_ids for item in development_items)
    assert all(requirement_artifact_ids.intersection(item.input_artifact_ids) for item in development_items)
    assert all(design_artifact_ids.intersection(item.input_artifact_ids) for item in development_items)
    assert all(assignment.dependencies for assignment in state.task_assignments if assignment.workitem_id in {item.id for item in development_items})
    assert development_assignments
    assert all(requirement_artifact_ids.intersection(assignment.input_artifact_ids) for assignment in development_assignments)
    assert all(design_artifact_ids.intersection(assignment.input_artifact_ids) for assignment in development_assignments)


def test_dependency_failure_blocks_downstream_workitem() -> None:
    controller = build_controller()
    state = controller.initialize_project("实现 API")
    design = state.workitems[0]
    downstream = WorkItem(
        id="workitem-downstream",
        description="依赖失败的后续任务",
        stage="development",
        kind="api_implementation",
        dependencies=[design.id],
    )
    state = controller.state_store.update_workitem(
        project_id=state.project.id,
        workitem_id=design.id,
        status=WorkItemStatus.RUNNING,
    )
    state = controller.state_store.update_workitem(
        project_id=state.project.id,
        workitem_id=design.id,
        status=WorkItemStatus.FAILED,
    )
    state.workitems = [*state.workitems, downstream]
    state.current_stage = "development"
    state.project.current_stage = "development"
    controller.state_store.save_state(state)

    state = controller.advance(state)

    assert state.project_status == ProjectStatus.BLOCKED
    assert state.blockers
    assert any(item.id == "workitem-downstream" and item.status == WorkItemStatus.FAILED for item in state.workitems)


def test_testing_failure_creates_development_feedback_rework() -> None:
    controller = build_controller()
    state = controller.initialize_project("实现 API 和 UI 页面并测试")
    design = state.workitems[0]
    development = WorkItem(
        id="workitem-010",
        description="已完成的前端实现",
        stage="development",
        kind="ui_implementation",
        status=WorkItemStatus.DONE,
        dependencies=[design.id],
    )
    failed_test = WorkItem(
        id="workitem-011",
        description="界面验证失败",
        stage="testing",
        kind="ui_validation",
        status=WorkItemStatus.FAILED,
        dependencies=[development.id],
        retry_count=0,
        max_retries=0,
        result="按钮点击后没有更新列表",
    )
    failed_test_artifact = Artifact(
        id="artifact-failed-ui-validation",
        project_id=state.project.id,
        workitem_id=failed_test.id,
        agent_id="agent-tester",
        kind="ui_validation",
        title="Failed UI Validation",
        content="Clicking the button did not update the visible list.",
    )
    frozen_requirement_artifact = Artifact(
        id="artifact-frozen-requirement",
        project_id=state.project.id,
        workitem_id=design.id,
        agent_id="agent-requirement",
        kind="frozen_requirement_spec",
        title="Frozen Requirement",
        content="必须实现前端页面，并保持本地静态范围。",
    )
    design_artifact = Artifact(
        id="artifact-design",
        project_id=state.project.id,
        workitem_id=design.id,
        agent_id="agent-designer",
        kind="design_overview",
        title="Design",
        content="前端页面采用静态 HTML/JS 实现。",
    )
    original_implementation_artifact = Artifact(
        id="artifact-original-ui-implementation",
        project_id=state.project.id,
        workitem_id=development.id,
        agent_id="agent-frontend",
        kind="ui_implementation",
        title="Original UI Implementation",
        content="初始实现只创建了按钮，但没有更新列表。",
    )
    state.workitems = [design, development, failed_test]
    state.artifacts = [
        frozen_requirement_artifact,
        design_artifact,
        original_implementation_artifact,
        failed_test_artifact,
    ]
    state.current_stage = "testing"
    state.project.current_stage = "testing"
    state.project_status = ProjectStatus.IN_PROGRESS
    controller.state_store.save_state(state)

    state = controller.advance(state)

    rework_items = [item for item in state.workitems if item.feedback_from == [failed_test.id]]
    assert state.current_stage == "development"
    assert state.project_status == ProjectStatus.IN_PROGRESS
    assert len(rework_items) == 1
    assert rework_items[0].kind == "ui_implementation"
    assert rework_items[0].rework_of == development.id
    assert failed_test_artifact.id in rework_items[0].input_artifact_ids
    assert original_implementation_artifact.id in rework_items[0].input_artifact_ids
    assert frozen_requirement_artifact.id in rework_items[0].input_artifact_ids
    assert design_artifact.id in rework_items[0].input_artifact_ids
    assert "原始实现 WorkItem" in rework_items[0].description
    assert "保持冻结需求和设计产物定义的范围边界" in rework_items[0].acceptance_criteria
    assert state.pending_test_scope == ["ui_validation"]
    rework_assignment = next(assignment for assignment in state.task_assignments if assignment.workitem_id == rework_items[0].id)
    assert failed_test_artifact.id in rework_assignment.input_artifact_ids
    assert original_implementation_artifact.id in rework_assignment.input_artifact_ids
    assert frozen_requirement_artifact.id in rework_assignment.input_artifact_ids
    assert design_artifact.id in rework_assignment.input_artifact_ids
    assert any("测试失败回流" in event for event in state.recent_events)


def test_feedback_rework_limits_next_testing_scope() -> None:
    controller = build_controller()
    state = controller.initialize_project("实现 API 和 UI 页面并测试")
    design = state.workitems[0]
    development = WorkItem(
        id="workitem-010",
        description="已完成前端返工",
        stage="development",
        kind="ui_implementation",
        status=WorkItemStatus.DONE,
        dependencies=[design.id],
        feedback_from=["workitem-009"],
    )
    state.workitems = [design, development]
    state.current_stage = "development"
    state.project.current_stage = "development"
    state.project_status = ProjectStatus.IN_PROGRESS
    state.pending_test_scope = ["ui_validation"]
    controller.state_store.save_state(state)

    state = controller.advance(state)

    testing_items = [item for item in state.workitems if item.stage == "testing"]
    assert [item.kind for item in testing_items] == ["ui_validation"]
    assert state.pending_test_scope == []


def test_testing_feedback_rework_has_project_level_limit() -> None:
    controller = build_controller()
    state = controller.initialize_project("Build static UI and validate it")
    design = state.workitems[0]
    first_rework = WorkItem(
        id="workitem-010",
        description="First feedback fix",
        stage="development",
        kind="ui_implementation",
        status=WorkItemStatus.DONE,
        dependencies=[design.id],
        feedback_from=["workitem-008"],
    )
    second_rework = WorkItem(
        id="workitem-011",
        description="Second feedback fix",
        stage="development",
        kind="ui_implementation",
        status=WorkItemStatus.DONE,
        dependencies=[first_rework.id],
        feedback_from=["workitem-009"],
    )
    failed_test = WorkItem(
        id="workitem-012",
        description="UI validation still fails",
        stage="testing",
        kind="ui_validation",
        status=WorkItemStatus.FAILED,
        dependencies=[second_rework.id],
        retry_count=0,
        max_retries=0,
        result="No implementation files were produced.",
    )
    state.workitems = [design, first_rework, second_rework, failed_test]
    state.current_stage = "testing"
    state.project.current_stage = "testing"
    state.project_status = ProjectStatus.IN_PROGRESS
    controller.state_store.save_state(state)

    state = controller.advance(state)

    assert state.project_status == ProjectStatus.BLOCKED
    assert state.blockers
    assert not any(item.feedback_from == [failed_test.id] for item in state.workitems)


def test_testing_stage_planning_uses_frozen_requirement_for_coverage_scope() -> None:
    controller = build_controller()
    state = controller.initialize_project("Build a simple local app")
    state.current_stage = "development"
    state.project.current_stage = "development"
    state.artifacts = [
        Artifact(
            id="artifact-frozen",
            project_id=state.project.id,
            workitem_id="workitem-req",
            agent_id="agent-requirement",
            kind="frozen_requirement_spec",
            title="Frozen Requirement",
            content=(
                "\u7528\u6237\u53ef\u4ee5\u6dfb\u52a0\u4e66\u7c4d\uff0c"
                "\u5237\u65b0\u540e\u4fdd\u7559\u6570\u636e\uff0c"
                "\u5e76\u5bfc\u51fa CSV\u3002"
            ),
        )
    ]
    controller.state_store.save_state(state)

    state = controller._advance_stage(state)
    acceptance_check = next(item for item in state.workitems if item.stage == "testing" and item.kind == "acceptance_check")

    assert "Provide validation evidence for frozen requirement: add item interaction" in acceptance_check.acceptance_criteria
    assert "Provide validation evidence for frozen requirement: refresh persistence" in acceptance_check.acceptance_criteria
    assert "Provide validation evidence for frozen requirement: CSV export/download" in acceptance_check.acceptance_criteria
