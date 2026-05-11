"""Structured testing failure feedback tests."""

from conductor.domain.models import Artifact, WorkItem
from conductor.testing.failure_feedback import build_testing_failure_feedback


def test_testing_failure_feedback_extracts_static_web_errors_and_suggestions() -> None:
    workitem = WorkItem(
        id="workitem-ui-validation",
        description="Validate UI",
        stage="testing",
        kind="ui_validation",
        failure_type="validation_failed",
        failure_summary="Validation exit_code=1",
    )
    artifact = Artifact(
        id="artifact-ui-validation",
        project_id="project-1",
        workitem_id=workitem.id,
        agent_id="agent-tester",
        kind="ui_validation",
        title="Failed UI Validation",
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
    assert "检查表单/按钮事件绑定" in markdown
    assert "检查 localStorage 写入" in markdown


def test_testing_failure_feedback_extracts_requirement_coverage_gaps() -> None:
    workitem = WorkItem(
        id="workitem-coverage",
        description="Acceptance check",
        stage="testing",
        kind="acceptance_check",
        failure_summary="Requirement coverage missing: refresh persistence, CSV export/download",
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
    assert "补齐缺失的冻结需求验收证据" in feedback.render_markdown()
