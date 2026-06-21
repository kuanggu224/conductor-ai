"""Planner rule tests."""

from conductor.domain.models import Stage
from conductor.execution.planner import Planner
from conductor.workflow.template import WorkflowTemplate


def _kinds(items):
    return [item.kind for item in items]


def test_planner_generates_keyword_specific_workitems() -> None:
    workflow = WorkflowTemplate()
    planner = Planner()
    requirement = "Design a feature with API endpoints and pytest tests."

    requirement_workitems = planner.plan_stage_workitems(workflow.get_first_stage(), requirement)
    design_workitems = planner.plan_stage_workitems(workflow.get_next_stage("requirement"), requirement)
    development_workitems = planner.plan_stage_workitems(workflow.get_next_stage("design"), requirement)
    testing_workitems = planner.plan_stage_workitems(workflow.get_next_stage("development"), requirement)

    assert _kinds(requirement_workitems) == ["requirement_spec"]
    assert _kinds(design_workitems) == ["design_overview", "api_design", "test_design"]
    assert _kinds(development_workitems) == ["api_implementation"]
    assert _kinds(testing_workitems) == ["acceptance_check", "automated_test", "api_validation"]


def test_planner_plans_data_work_for_persistent_api_requirement() -> None:
    workflow = WorkflowTemplate()
    planner = Planner()
    requirement = "Build a local backend API for a reading list with SQLite persistence."

    design_workitems = planner.plan_stage_workitems(workflow.get_next_stage("requirement"), requirement)
    development_workitems = planner.plan_stage_workitems(workflow.get_next_stage("design"), requirement)
    testing_workitems = planner.plan_stage_workitems(workflow.get_next_stage("development"), requirement)

    assert _kinds(design_workitems) == ["design_overview", "api_design"]
    assert _kinds(development_workitems) == ["api_implementation", "data_implementation"]
    assert _kinds(testing_workitems) == ["acceptance_check", "api_validation"]


def test_planner_adds_requirement_coverage_criteria_to_acceptance_check() -> None:
    workflow = WorkflowTemplate()
    planner = Planner()
    requirement = "Users can add books, preserve data after refresh, and export CSV."

    testing_workitems = planner.plan_stage_workitems(workflow.get_next_stage("development"), requirement)
    acceptance_check = testing_workitems[0]

    assert acceptance_check.kind == "acceptance_check"
    assert "Provide validation evidence for frozen requirement: add item interaction" in acceptance_check.acceptance_criteria
    assert "Provide validation evidence for frozen requirement: refresh persistence" in acceptance_check.acceptance_criteria
    assert "Provide validation evidence for frozen requirement: CSV export/download" in acceptance_check.acceptance_criteria
    assert [item["rule_id"] for item in acceptance_check.testing_checklist] == ["add_item", "persistence", "export_csv"]
    assert acceptance_check.testing_checklist[0]["status"] == "pending"
    assert "api client form interaction updated visible state" in acceptance_check.testing_checklist[0]["required_evidence_terms"]


def test_planner_adds_filter_evidence_to_testing_checklist() -> None:
    workflow = WorkflowTemplate()
    planner = Planner()
    requirement = "The page supports keyword search for items."

    testing_workitems = planner.plan_stage_workitems(workflow.get_next_stage("development"), requirement)
    acceptance_check = testing_workitems[0]

    assert "Provide validation evidence for frozen requirement: filter interaction" in acceptance_check.acceptance_criteria
    assert [item["rule_id"] for item in acceptance_check.testing_checklist] == ["filter"]
    assert acceptance_check.testing_checklist[0]["required_evidence_terms"] == [
        "api client filter interaction changed visible results"
    ]


def test_planner_adds_delete_evidence_to_testing_checklist() -> None:
    workflow = WorkflowTemplate()
    planner = Planner()
    requirement = "The page supports deleting items."

    testing_workitems = planner.plan_stage_workitems(workflow.get_next_stage("development"), requirement)
    acceptance_check = testing_workitems[0]

    assert "Provide validation evidence for frozen requirement: delete item interaction" in acceptance_check.acceptance_criteria
    assert [item["rule_id"] for item in acceptance_check.testing_checklist] == ["delete_item"]
    assert acceptance_check.testing_checklist[0]["required_evidence_terms"] == [
        "api client delete interaction removed visible item"
    ]


def test_planner_adds_file_import_evidence_to_testing_checklist() -> None:
    workflow = WorkflowTemplate()
    planner = Planner()
    requirement = "The page supports importing a CSV file and parsing items."

    testing_workitems = planner.plan_stage_workitems(workflow.get_next_stage("development"), requirement)
    acceptance_check = testing_workitems[0]

    assert "Provide validation evidence for frozen requirement: file import/upload" in acceptance_check.acceptance_criteria
    assert [item["rule_id"] for item in acceptance_check.testing_checklist] == ["file_import"]
    assert acceptance_check.testing_checklist[0]["required_evidence_terms"] == [
        "api client file import processed sample file"
    ]


def test_planner_adds_api_behavior_evidence_to_testing_checklist() -> None:
    workflow = WorkflowTemplate()
    planner = Planner()
    requirement = "Build a backend API that supports creating and querying items."

    testing_workitems = planner.plan_stage_workitems(workflow.get_next_stage("development"), requirement)
    acceptance_check = testing_workitems[0]

    assert "Provide validation evidence for frozen requirement: API endpoint behavior" in acceptance_check.acceptance_criteria
    api_behavior = next(item for item in acceptance_check.testing_checklist if item["rule_id"] == "api_behavior")
    assert api_behavior["required_evidence_terms"] == ["api validation exercised endpoint behavior"]
    api_validation = next(item for item in testing_workitems if item.kind == "api_validation")
    assert "Endpoint/status/payload evidence is recorded." in api_validation.acceptance_criteria


def test_planner_does_not_treat_build_as_client_keyword_for_api_requirements() -> None:
    planner = Planner()
    requirement = "Build a backend REST API for todo items with create, list, update, delete, and stats endpoints."

    design_items = planner.plan_stage_workitems(Stage("design", "", ""), requirement)
    development_items = planner.plan_stage_workitems(Stage("development", "", ""), requirement)
    testing_items = planner.plan_stage_workitems(Stage("testing", "", ""), requirement)

    assert "api_design" in _kinds(design_items)
    assert _kinds(development_items) == ["api_implementation"]
    assert "api_validation" in _kinds(testing_items)
    acceptance_check = next(item for item in testing_items if item.kind == "acceptance_check")
    assert [item["rule_id"] for item in acceptance_check.testing_checklist] == ["api_behavior"]


def test_planner_keeps_api_requirement_non_goals_out_of_workitems() -> None:
    planner = Planner()
    requirement = "Build a backend REST API for todo items. Out of scope: API client surface, SQLite, and static assets."

    design_items = planner.plan_stage_workitems(Stage("design", "", ""), requirement)
    development_items = planner.plan_stage_workitems(Stage("development", "", ""), requirement)
    testing_items = planner.plan_stage_workitems(Stage("testing", "", ""), requirement)

    assert "api_design" in _kinds(design_items)
    assert _kinds(development_items) == ["api_implementation"]
    assert "api_validation" in _kinds(testing_items)


def test_planner_keeps_one_line_non_goals_out_of_feature_slices() -> None:
    planner = Planner()
    requirement = (
        "Build a api-only team task board with add task, assignee, status filter, priority, "
        "and SQLite persistence. Out of scope: delete items, CSV export, file import, backend, login, analytics."
    )

    design_items = planner.plan_stage_workitems(Stage("design", "", ""), requirement)
    development_items = planner.plan_stage_workitems(Stage("development", "", ""), requirement)
    testing_items = planner.plan_stage_workitems(Stage("testing", "", ""), requirement)

    assert _kinds(development_items) == ["api_implementation", "data_implementation"]
    assert "api_design" in _kinds(design_items)
    assert "api_validation" in _kinds(testing_items)
    feature_plan = next(item for item in design_items if item.kind == "feature_slice_plan")
    assert "delete_item" not in feature_plan.description
    assert "file_import" not in feature_plan.description
    assert "export_csv" not in feature_plan.description


def test_planner_adds_feature_slice_plan_for_multi_feature_api_requirement() -> None:
    planner = Planner()
    requirement = (
        "Build a api web app for todo items with a api client backend and backend REST API. "
        "Users can create items with a form, list items, filter items, delete items, and view stats."
    )

    design_items = planner.plan_stage_workitems(Stage("design", "", ""), requirement)
    development_items = planner.plan_stage_workitems(Stage("development", "", ""), requirement)
    testing_items = planner.plan_stage_workitems(Stage("testing", "", ""), requirement)

    feature_plan = next(item for item in design_items if item.kind == "feature_slice_plan")
    assert "create_item" in feature_plan.description
    assert "stats" in feature_plan.description
    assert any("milestone=M1 Core input" in item for item in feature_plan.acceptance_criteria)
    assert any("milestone=M3 Evidence" in item for item in feature_plan.acceptance_criteria)

    api_implementation = next(item for item in development_items if item.kind == "api_implementation")
    assert any(
        criterion.startswith("Implement feature slices in milestone order: create_item -> list_items")
        for criterion in api_implementation.acceptance_criteria
    )

    acceptance_check = next(item for item in testing_items if item.kind == "acceptance_check")
    assert "Verify feature slice create_item: create request and persisted/returned record" in acceptance_check.acceptance_criteria
    assert "Verify feature slice stats: stats payload or report evidence" in acceptance_check.acceptance_criteria
