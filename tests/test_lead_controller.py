"""LeadController 测试。"""

from conductor.controller.lead_controller import LeadController
from conductor.domain.models import ProjectStatus, TaskAssignmentStatus, WorkItem, WorkItemStatus
from conductor.execution.runner import Runner
from conductor.state.store import InMemoryStateStore
from conductor.workflow.template import WorkflowTemplate


def build_controller() -> LeadController:
    state_store = InMemoryStateStore()
    runner = Runner(state_store=state_store)
    workflow = WorkflowTemplate()
    return LeadController(workflow_template=workflow, state_store=state_store, runner=runner)


def test_initialize_project_creates_design_workitem() -> None:
    controller = build_controller()

    state = controller.initialize_project("实现最小骨架")

    assert state.project.current_stage == "design"
    assert state.current_stage == "design"
    assert len(state.workitems) == 1
    assert state.workitems[0].stage == "design"
    assert state.workitems[0].kind == "design_overview"
    assert state.planned_roles == ["designer"]
    assert len(state.agent_activations) == 1
    assert state.agent_activations[0].agent_id == "agent-designer"
    assert state.agent_activations[0].role == "designer"
    assert state.agent_activations[0].stage == "design"
    assert any("创建 Agent agent-designer" in event for event in state.recent_events)


def test_initialize_project_uses_requirement_keywords_to_expand_workitems() -> None:
    controller = build_controller()

    state = controller.initialize_project("设计一个 API 接口和 UI 页面，并补充测试")

    design_workitems = [item for item in state.workitems if item.stage == "design"]
    assert [item.kind for item in design_workitems] == [
        "design_overview",
        "ui_design",
        "api_design",
        "test_design",
    ]
    assert state.planned_roles == ["designer"]


def test_advance_can_run_and_finish_project() -> None:
    controller = build_controller()
    state = controller.initialize_project("实现最小骨架")

    for _ in range(6):
        state = controller.advance(state)

    assert state.project_status.value == "completed"
    assert len(state.executions) == 3
    assert state.gate_history[-1] == "testing:pass"
    assert [decision.selected_agent for decision in state.route_decisions] == [
        "agent-designer",
        "agent-backend",
        "agent-tester",
    ]
    assert [activation.role for activation in state.agent_activations] == [
        "designer",
        "backend_engineer",
        "tester",
    ]
    assert [execution.workitem_id for execution in state.executions] == [
        "workitem-001",
        "workitem-002",
        "workitem-003",
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
    assert state.task_assignments[0].assigned_agent_id == "agent-designer"
    assert state.task_assignments[0].output_artifact_ids
    assert state.agent_capability_stats[0].agent_id == "agent-designer"
    assert state.agent_capability_stats[0].completed_count == 1
    assert "design_overview" in state.agent_capability_stats[0].workitem_kinds
    assert any("任务中心" in event for event in state.recent_events)


def test_next_stage_workitems_depend_on_previous_stage() -> None:
    controller = build_controller()
    state = controller.initialize_project("实现 API 和 UI 页面")

    while state.current_stage == "design":
        state = controller.advance(state)

    development_items = [item for item in state.workitems if item.stage == "development"]
    design_ids = {item.id for item in state.workitems if item.stage == "design"}

    assert development_items
    assert all(set(item.dependencies) == design_ids for item in development_items)
    assert all(assignment.dependencies for assignment in state.task_assignments if assignment.workitem_id in {item.id for item in development_items})


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
    state.workitems = [design, development, failed_test]
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
    assert state.pending_test_scope == ["ui_validation"]
    assert any(assignment.workitem_id == rework_items[0].id for assignment in state.task_assignments)
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
