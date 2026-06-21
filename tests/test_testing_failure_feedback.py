"""Structured testing failure feedback tests."""

from conductor.domain.models import Artifact, Execution, ExecutionStatus, Project, ProjectStatus, SharedProjectState, WorkItem
from conductor.testing.failure_feedback import build_testing_failure_feedback, build_testing_feedback_for_workitem


def test_testing_failure_feedback_extracts_api_mock_errors_and_suggestions() -> None:
    workitem = WorkItem(
        id="workitem-api-validation",
        description="Validate API",
        stage="testing",
        kind="api_validation",
        failure_type="validation_failed",
        failure_summary="Validation exit_code=1",
    )
    artifact = Artifact(
        id="artifact-ui-validation",
        project_id="project-1",
        workitem_id=workitem.id,
        agent_id="agent-tester",
        kind="api_validation",
        title="Failed API Validation",
        content=(
            "Static Web Validation: FAIL\n\n"
            "Errors:\n"
            "- Browser form submit did not change visible page state\n"
            "- Browser reload did not preserve submitted values: sample\n"
        ),
    )

    feedback = build_testing_failure_feedback(workitem, [artifact])
    markdown = feedback.render_markdown()

    assert feedback.failure_type == "validation_failed"
    assert "Browser form submit did not change visible page state" in feedback.failing_checks
    assert "检查请求处理" in markdown
    assert "检查持久化写入" in markdown


def test_testing_failure_feedback_extracts_requirement_coverage_gaps() -> None:
    workitem = WorkItem(
        id="workitem-coverage",
        description="Acceptance check",
        stage="testing",
        kind="acceptance_check",
        failure_summary="Requirement coverage missing: refresh persistence, CSV export/download",
        testing_checklist=[
            {
                "rule_id": "persistence",
                "label": "refresh persistence",
                "status": "pending",
                "requirement_terms": ["刷新后"],
                "required_evidence_terms": ["api client reload preserved submitted values"],
            },
            {
                "rule_id": "export_csv",
                "label": "CSV export/download",
                "status": "pending",
                "requirement_terms": ["CSV"],
                "required_evidence_terms": ["api client export/download action triggered"],
            },
        ],
    )
    artifact = Artifact(
        id="artifact-coverage",
        project_id="project-1",
        workitem_id=workitem.id,
        agent_id="agent-tester",
        kind="acceptance_check",
        title="Coverage Report",
        content=(
            "## Requirement Coverage\n"
            "- Status: `missing_coverage`\n"
            "| Rule | Status | Requirement Signal | Validation Evidence |\n"
            "|---|---|---|---|\n"
            "| refresh persistence | missing | refresh | - |\n"
        ),
    )

    feedback = build_testing_failure_feedback(workitem, [artifact])

    assert feedback.missing_coverage == ["refresh persistence", "CSV export/download"]
    assert [item["rule_id"] for item in feedback.missing_checklist_items] == ["persistence", "export_csv"]
    assert all(item["status"] == "missing" for item in feedback.missing_checklist_items)
    assert "api client reload preserved submitted values" in feedback.render_markdown()
    assert "补齐缺失的冻结需求验收证据" in feedback.render_markdown()


def test_testing_failure_feedback_suggests_filter_fix_for_missing_filter_coverage() -> None:
    workitem = WorkItem(
        id="workitem-filter",
        description="Acceptance check",
        stage="testing",
        kind="acceptance_check",
        failure_summary="Requirement coverage missing: filter interaction",
        testing_checklist=[
            {
                "rule_id": "filter",
                "label": "filter interaction",
                "status": "pending",
                "requirement_terms": ["filter"],
                "required_evidence_terms": ["api client filter interaction changed visible results"],
            }
        ],
    )

    feedback = build_testing_failure_feedback(workitem, [])
    markdown = feedback.render_markdown()

    assert feedback.missing_coverage == ["filter interaction"]
    assert feedback.missing_checklist_items[0]["rule_id"] == "filter"
    assert "api client filter interaction changed visible results" in markdown
    assert "\u68c0\u67e5\u7b5b\u9009/\u641c\u7d22\u63a7\u4ef6\u4e8b\u4ef6\u7ed1\u5b9a" in markdown


def test_testing_failure_feedback_suggests_delete_fix_for_missing_delete_coverage() -> None:
    workitem = WorkItem(
        id="workitem-delete",
        description="Acceptance check",
        stage="testing",
        kind="acceptance_check",
        failure_summary="Requirement coverage missing: delete item interaction",
        testing_checklist=[
            {
                "rule_id": "delete_item",
                "label": "delete item interaction",
                "status": "pending",
                "requirement_terms": ["delete"],
                "required_evidence_terms": ["api client delete interaction removed visible item"],
            }
        ],
    )

    feedback = build_testing_failure_feedback(workitem, [])
    markdown = feedback.render_markdown()

    assert feedback.missing_coverage == ["delete item interaction"]
    assert feedback.missing_checklist_items[0]["rule_id"] == "delete_item"
    assert "api client delete interaction removed visible item" in markdown
    assert "\u68c0\u67e5\u5220\u9664/\u79fb\u9664\u6309\u94ae\u4e8b\u4ef6\u7ed1\u5b9a" in markdown


def test_testing_failure_feedback_suggests_file_import_fix() -> None:
    workitem = WorkItem(
        id="workitem-file-import",
        description="Acceptance check",
        stage="testing",
        kind="acceptance_check",
        failure_summary="Requirement coverage missing: file import/upload",
        testing_checklist=[
            {
                "rule_id": "file_import",
                "label": "file import/upload",
                "status": "pending",
                "requirement_terms": ["import"],
                "required_evidence_terms": ["api client file import processed sample file"],
            }
        ],
    )

    feedback = build_testing_failure_feedback(workitem, [])
    markdown = feedback.render_markdown()

    assert feedback.missing_coverage == ["file import/upload"]
    assert feedback.missing_checklist_items[0]["rule_id"] == "file_import"
    assert "api client file import processed sample file" in markdown
    assert "Check file selection, import/upload handlers" in markdown


def test_testing_failure_feedback_suggests_api_validation_fix() -> None:
    workitem = WorkItem(
        id="workitem-api-validation",
        description="Acceptance check",
        stage="testing",
        kind="api_validation",
        failure_summary="Requirement coverage missing: API endpoint behavior",
        testing_checklist=[
            {
                "rule_id": "api_behavior",
                "label": "API endpoint behavior",
                "status": "pending",
                "requirement_terms": ["api"],
                "required_evidence_terms": ["api validation exercised endpoint behavior"],
            }
        ],
    )

    feedback = build_testing_failure_feedback(workitem, [])
    markdown = feedback.render_markdown()

    assert feedback.missing_coverage == ["API endpoint behavior"]
    assert feedback.missing_checklist_items[0]["rule_id"] == "api_behavior"
    assert "endpoint path" in feedback.missing_checklist_items[0]["required_evidence_terms"]
    assert "HTTP status code" in feedback.missing_checklist_items[0]["required_evidence_terms"]
    assert "response payload or body" in feedback.missing_checklist_items[0]["required_evidence_terms"]
    assert "api validation exercised endpoint behavior" in markdown
    assert "Check API route wiring" in markdown
    assert "POST /api/items -> status_code=201 response payload" in markdown


def test_testing_feedback_for_rework_follows_feedback_from_testing_workitem() -> None:
    failed_test = WorkItem(
        id="workitem-api-test",
        description="Validate API",
        stage="testing",
        kind="api_validation",
        failure_type="validation_failed",
        failure_summary="Validation exit_code=1",
    )
    rework = WorkItem(
        id="workitem-api-rework",
        description="Fix API",
        stage="development",
        kind="api_implementation",
        feedback_from=[failed_test.id],
    )
    artifact = Artifact(
        id="artifact-ui-test",
        project_id="project-1",
        workitem_id=failed_test.id,
        agent_id="agent-tester",
        kind="api_validation",
        title="Failed API Validation",
        content="- Browser form submit did not change visible page state",
    )
    state = SharedProjectState(
        project=Project(id="project-1", goal="Build API", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[failed_test, rework],
        artifacts=[artifact],
    )

    feedback = build_testing_feedback_for_workitem(state, rework)

    assert [item.workitem_id for item in feedback] == [failed_test.id]
    assert "Browser form submit did not change visible page state" in feedback[0].failing_checks


def test_testing_feedback_includes_latest_validation_execution_evidence() -> None:
    failed_test = WorkItem(
        id="workitem-api-test",
        description="Validate API",
        stage="testing",
        kind="api_validation",
        failure_type="validation_failed",
        failure_summary="Requirement coverage missing: API endpoint behavior",
    )
    rework = WorkItem(
        id="workitem-api-rework",
        description="Fix API",
        stage="development",
        kind="api_implementation",
        feedback_from=[failed_test.id],
    )
    state = SharedProjectState(
        project=Project(id="project-api", goal="Build API", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[failed_test, rework],
        executions=[
            Execution(
                workitem_id=failed_test.id,
                agent_id="agent-tester",
                result="older failed run",
                status=ExecutionStatus.FAILED,
                validation_command=["python", "-m", "pytest", "tests/test_old.py"],
                validation_exit_code=2,
            ),
            Execution(
                workitem_id=failed_test.id,
                agent_id="agent-tester",
                result="latest failed run",
                status=ExecutionStatus.FAILED,
                validation_command=["python", "-m", "pytest", "tests/test_api.py", "-q"],
                validation_exit_code=1,
            ),
        ],
    )

    feedback = build_testing_feedback_for_workitem(state, rework)[0]
    markdown = feedback.render_markdown()

    assert feedback.exit_code == "1"
    assert feedback.validation_exit_code == "1"
    assert feedback.validation_command == ["python", "-m", "pytest", "tests/test_api.py", "-q"]
    assert "Validation Command: `python -m pytest tests/test_api.py -q`" in markdown
