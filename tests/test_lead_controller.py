"""LeadController 测试。"""

from conductor.controller.lead_controller import LeadController
from conductor.domain.models import ProjectStatus, WorkItem
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
    assert any("激活 Agent agent-designer" in event for event in state.recent_events)


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
