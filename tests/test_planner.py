"""规则版 Planner 测试。"""

from conductor.execution.planner import Planner
from conductor.workflow.template import WorkflowTemplate


def test_planner_generates_keyword_specific_workitems() -> None:
    workflow = WorkflowTemplate()
    planner = Planner()
    requirement = "设计一个包含 API 接口、UI 页面并补充 pytest 测试的功能"

    requirement_workitems = planner.plan_stage_workitems(workflow.get_first_stage(), requirement)
    design_workitems = planner.plan_stage_workitems(workflow.get_next_stage("requirement"), requirement)
    development_workitems = planner.plan_stage_workitems(workflow.get_next_stage("design"), requirement)
    testing_workitems = planner.plan_stage_workitems(workflow.get_next_stage("development"), requirement)

    assert [item.kind for item in requirement_workitems] == ["requirement_spec"]
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


def test_planner_skips_backend_and_api_work_for_static_frontend_only_requirement() -> None:
    workflow = WorkflowTemplate()
    planner = Planner()
    requirement = (
        "做一个本地静态 Web 读书清单应用，使用 localStorage 保存数据，"
        "只做前端静态页面，不接后端，不接数据库。"
    )

    design_workitems = planner.plan_stage_workitems(workflow.get_next_stage("requirement"), requirement)
    development_workitems = planner.plan_stage_workitems(workflow.get_next_stage("design"), requirement)
    testing_workitems = planner.plan_stage_workitems(workflow.get_next_stage("development"), requirement)

    assert [item.kind for item in design_workitems] == ["design_overview", "ui_design"]
    assert [item.kind for item in development_workitems] == ["ui_implementation"]
    assert [item.kind for item in testing_workitems] == ["acceptance_check", "ui_validation"]


def test_planner_adds_requirement_coverage_criteria_to_acceptance_check() -> None:
    workflow = WorkflowTemplate()
    planner = Planner()
    requirement = (
        "\u7528\u6237\u53ef\u4ee5\u6dfb\u52a0\u4e66\u7c4d\uff0c"
        "\u5237\u65b0\u540e\u4fdd\u7559\u6570\u636e\uff0c"
        "\u5e76\u5bfc\u51fa CSV\u3002"
    )

    testing_workitems = planner.plan_stage_workitems(workflow.get_next_stage("development"), requirement)
    acceptance_check = testing_workitems[0]

    assert acceptance_check.kind == "acceptance_check"
    assert "Provide validation evidence for frozen requirement: add item interaction" in acceptance_check.acceptance_criteria
    assert "Provide validation evidence for frozen requirement: refresh persistence" in acceptance_check.acceptance_criteria
    assert "Provide validation evidence for frozen requirement: CSV export/download" in acceptance_check.acceptance_criteria
