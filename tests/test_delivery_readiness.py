"""Delivery readiness assessment tests."""

from conductor.delivery_readiness import evaluate_delivery_readiness, render_delivery_readiness_markdown
from conductor.domain.models import (
    Artifact,
    Execution,
    ExecutionStatus,
    Project,
    ProjectStatus,
    SharedProjectState,
    WorkItem,
    WorkItemStatus,
)


def test_delivery_readiness_blocks_without_frozen_requirement() -> None:
    state = SharedProjectState(
        project=Project(id="project-readiness-missing", goal="Build app"),
        project_status=ProjectStatus.COMPLETED,
        current_stage="testing",
        workitems=[],
    )

    result = evaluate_delivery_readiness(state)

    assert result.status == "blocked"
    assert result.blocking_count >= 1
    assert any(check.id == "frozen_requirement" and check.status == "fail" for check in result.checks)


def test_delivery_readiness_detects_ready_handoff_evidence() -> None:
    state = SharedProjectState(
        project=Project(id="project-readiness-ready", goal="Build static reading list"),
        project_status=ProjectStatus.COMPLETED,
        current_stage="testing",
        workitems=[
            WorkItem(id="workitem-design", description="Design", stage="design", kind="design_overview", status=WorkItemStatus.DONE),
            WorkItem(id="workitem-code", description="Implement", stage="development", kind="ui_implementation", status=WorkItemStatus.DONE),
            WorkItem(id="workitem-test", description="Validate", stage="testing", kind="acceptance_check", status=WorkItemStatus.DONE),
        ],
        executions=[
            Execution(
                workitem_id="workitem-code",
                agent_id="agent-frontend",
                result="Implemented static reading list",
                status=ExecutionStatus.SUCCESS,
                changed_files=["index.html", "static/app.js"],
            ),
            Execution(
                workitem_id="workitem-test",
                agent_id="agent-tester",
                result="Validation passed",
                status=ExecutionStatus.SUCCESS,
                source_backend="cli/static_web",
                validation_success=True,
            ),
        ],
        artifacts=[
            Artifact(
                id="artifact-frozen",
                project_id="project-readiness-ready",
                workitem_id="workitem-req",
                agent_id="agent-requirement-designer",
                kind="frozen_requirement_spec",
                title="Frozen Requirement",
                content="目标：Build static reading list\n非目标：不接后端",
            ),
            Artifact(
                id="artifact-design",
                project_id="project-readiness-ready",
                workitem_id="workitem-design",
                agent_id="agent-designer",
                kind="frozen_design_spec",
                title="Frozen Design",
                content="Static page design. 不接后端。",
                source_backend="collaboration",
            ),
            Artifact(
                id="artifact-design-draft",
                project_id="project-readiness-ready",
                workitem_id="workitem-design",
                agent_id="agent-designer",
                kind="design_overview",
                title="Design",
                content="Static page design. 不接后端。",
                source_backend="llm/local",
            ),
            Artifact(
                id="artifact-code",
                project_id="project-readiness-ready",
                workitem_id="workitem-code",
                agent_id="agent-frontend",
                kind="ui_implementation",
                title="Code",
                content="Implemented static page. 不接后端。",
                source_backend="agent_cli/codex",
            ),
        ],
    )

    result = evaluate_delivery_readiness(state)

    assert result.status == "ready"
    assert result.score == 100
    assert result.blocking_count == 0
    assert all(check.status == "pass" for check in result.checks)
    assert "- Status: ready" in render_delivery_readiness_markdown(result)


def test_delivery_readiness_blocks_downstream_without_frozen_design() -> None:
    state = SharedProjectState(
        project=Project(id="project-readiness-no-design", goal="Build static app"),
        project_status=ProjectStatus.COMPLETED,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-code", description="Implement", stage="development", kind="ui_implementation", status=WorkItemStatus.DONE)
        ],
        artifacts=[
            Artifact(
                id="artifact-frozen",
                project_id="project-readiness-no-design",
                workitem_id="workitem-req",
                agent_id="agent-requirement-designer",
                kind="frozen_requirement_spec",
                title="Frozen Requirement",
                content="目标：Build static app",
            )
        ],
    )

    result = evaluate_delivery_readiness(state)

    assert result.status == "blocked"
    assert any(check.id == "frozen_design" and check.status == "fail" for check in result.checks)


def test_delivery_readiness_accepts_validated_static_web_files_without_new_diff(tmp_path) -> None:
    (tmp_path / "static").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><script src='static/app.js'></script>", encoding="utf-8")
    (tmp_path / "static" / "app.js").write_text("localStorage.setItem('ok', '1');", encoding="utf-8")
    (tmp_path / "static" / "style.css").write_text("body { font-family: sans-serif; }", encoding="utf-8")

    state = SharedProjectState(
        project=Project(
            id="project-readiness-static-web",
            goal="Build a browser-only static web app with localStorage.",
            status=ProjectStatus.COMPLETED,
            current_stage="testing",
            project_root=str(tmp_path),
        ),
        project_status=ProjectStatus.COMPLETED,
        current_stage="testing",
        workitems=[
            WorkItem(id="workitem-design", description="Design", stage="design", kind="design_overview", status=WorkItemStatus.DONE),
            WorkItem(id="workitem-code", description="Implement", stage="development", kind="ui_implementation", status=WorkItemStatus.DONE),
            WorkItem(id="workitem-test", description="Validate", stage="testing", kind="acceptance_check", status=WorkItemStatus.DONE),
        ],
        executions=[
            Execution(
                workitem_id="workitem-code",
                agent_id="agent-frontend",
                result="Static web delivery validated existing files.",
                status=ExecutionStatus.SUCCESS,
                source_backend="static_web_delivery",
                working_directory=str(tmp_path),
                validation_success=True,
                changed_files=[],
            ),
            Execution(
                workitem_id="workitem-test",
                agent_id="agent-tester",
                result="Static Web Validation: PASS",
                status=ExecutionStatus.SUCCESS,
                source_backend="cli/static_web",
                validation_success=True,
            ),
        ],
        artifacts=[
            Artifact(
                id="artifact-frozen",
                project_id="project-readiness-static-web",
                workitem_id="workitem-req",
                agent_id="agent-requirement-designer",
                kind="frozen_requirement_spec",
                title="Frozen Requirement",
                content="Build a browser-only static web app. No backend, no login, no payments.",
            ),
            Artifact(
                id="artifact-design",
                project_id="project-readiness-static-web",
                workitem_id="workitem-design",
                agent_id="agent-designer",
                kind="frozen_design_spec",
                title="Frozen Design",
                content="Use index.html, static/app.js, static/style.css, localStorage, filters, and CSV export. No backend.",
                source_backend="collaboration",
            ),
            Artifact(
                id="artifact-code",
                project_id="project-readiness-static-web",
                workitem_id="workitem-code",
                agent_id="agent-frontend",
                kind="ui_implementation",
                title="Code",
                content="Generated validated static web files only. No backend.",
                source_backend="static_web_delivery",
            ),
        ],
    )

    result = evaluate_delivery_readiness(state)

    assert result.status == "ready"
    assert any(
        check.id == "code_evidence" and check.status == "pass" and "validated static web deliverables" in check.evidence
        for check in result.checks
    )


def test_delivery_readiness_flags_failed_validation_as_blocker() -> None:
    state = SharedProjectState(
        project=Project(id="project-readiness-failed", goal="Build app"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="testing",
        workitems=[
            WorkItem(id="workitem-test", description="Validate", stage="testing", kind="acceptance_check", status=WorkItemStatus.FAILED)
        ],
        executions=[
            Execution(
                workitem_id="workitem-test",
                agent_id="agent-tester",
                result="Validation failed",
                status=ExecutionStatus.FAILED,
                validation_success=False,
            )
        ],
        artifacts=[
            Artifact(
                id="artifact-frozen",
                project_id="project-readiness-failed",
                workitem_id="workitem-req",
                agent_id="agent-requirement-designer",
                kind="frozen_requirement_spec",
                title="Frozen Requirement",
                content="目标：Build app",
            )
        ],
    )

    result = evaluate_delivery_readiness(state)

    assert result.status == "blocked"
    assert any(check.id == "validation_evidence" and check.status == "fail" for check in result.checks)
