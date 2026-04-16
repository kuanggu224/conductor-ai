"""规则版 Planner 测试。"""

from conductor.execution.planner import Planner
from conductor.workflow.template import WorkflowTemplate


def test_planner_generates_keyword_specific_workitems() -> None:
    workflow = WorkflowTemplate()
    planner = Planner()
    requirement = "设计一个包含 API 接口、UI 页面并补充 pytest 测试的功能"

    design_workitems = planner.plan_stage_workitems(workflow.get_first_stage(), requirement)
    development_workitems = planner.plan_stage_workitems(workflow.get_next_stage("design"), requirement)
    testing_workitems = planner.plan_stage_workitems(workflow.get_next_stage("development"), requirement)

    assert [item.kind for item in design_workitems] == [
        "design_overview",
        "ui_design",
        "api_design",
        "test_design",
    ]
    assert [item.kind for item in development_workitems] == [
        "api_implementation",
        "ui_implementation",
    ]
    assert [item.kind for item in testing_workitems] == [
        "acceptance_check",
        "automated_test",
        "api_validation",
        "ui_validation",
    ]
