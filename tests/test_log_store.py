"""Log persistence tests."""

import json

from conductor.config.cli import CLISelectionConfig
from conductor.controller.engine import ConductorEngine
from conductor.domain.models import (
    Artifact,
    Execution,
    ExecutionStatus,
    HumanControlAction,
    HumanControlActionType,
    Project,
    ProjectStatus,
    SharedProjectState,
    TaskAssignment,
    TaskAssignmentStatus,
    WorkItem,
    WorkItemStatus,
)
from conductor.logging.store import ProjectLogStore


def test_project_log_store_can_append_and_read_entries(tmp_path) -> None:
    store = ProjectLogStore(tmp_path)

    store.append_event("project-1", 0, "项目已创建")
    store.append_event("project-1", 1, "进入阶段 design")
    store.append_event("project-1", 2, "TaskCenterCLI: agent-backend claimed workitem-001")
    entries = store.read_events("project-1")

    assert len(entries) == 3
    assert entries[0].project_id == "project-1"
    assert entries[1].message == "进入阶段 design"
    assert entries[1].event_type == "stage_transition"
    assert entries[2].event_type == "task_center"


def test_project_log_store_writes_structured_state_events_and_report(tmp_path) -> None:
    engine = ConductorEngine(
        log_dir=tmp_path / "logs",
        artifact_dir=tmp_path / "artifacts",
        cli_selection_config=CLISelectionConfig(),
    )
    state = engine.create_project("实现一个 API 和 UI")
    state = engine.step_project(state.project.id)

    entries = engine.read_project_logs(state.project.id)
    report_path = engine.write_project_report(state.project.id)

    assert entries
    assert entries[0].event_type in {"event", "stage_transition", "agent_activation"}
    assert entries[0].stage
    assert entries[0].project_status
    assert entries[0].metadata is not None
    assert "workitem_counts" in entries[0].metadata
    assert report_path.exists()
    report = report_path.read_text(encoding="utf-8")
    assert "## Preflight Gate" in report
    assert "- Not recorded" in report
    assert "## Human Control" in report
    assert "- Active: false" in report
    assert "- Actions: none" in report
    assert "## Activated Agents" in report
    assert "## Task Center" in report
    assert "- Summary: total=" in report
    assert "blocked_by_dependencies=" in report
    assert "claimable=" in report
    assert "unmet_dependencies=" in report
    assert "input_artifacts=" in report
    assert "output_artifacts=" in report
    assert "claimed_at=" in report
    assert "returned_at=" in report
    assert "claimed_age_seconds=" in report
    assert "lease_seconds=" in report
    assert "lease_expires_at=" in report
    assert "lease_expired=" in report
    assert "stale_claimed=" in report
    assert "prompt_file=" in report
    assert "## Executions" in report
    assert "changed_files=" in report
    assert "acceptance_trace:" in report
    assert "## Delivery Readiness" in report
    assert "- Status:" in report
    assert "## Event Timeline" in report


def test_project_report_includes_task_center_audit_artifact_links(tmp_path) -> None:
    store = ProjectLogStore(tmp_path)
    state = SharedProjectState(
        project=Project(id="project-task-audit-report", goal="repair task center", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(
                id="workitem-implementation",
                description="Implement feature",
                stage="development",
                kind="implementation",
            )
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-implementation",
                workitem_id="workitem-implementation",
                role="backend_engineer",
                status=TaskAssignmentStatus.QUEUED,
                input_artifact_ids=["artifact-missing-input"],
            )
        ],
    )

    report = store.render_project_report(state, [])

    assert "- Audit: finding_count=1 | errors=0 | warnings=1" in report
    assert "missing_input_artifact | severity=warning" in report
    assert "assignment=assignment-implementation" in report
    assert "missing_artifacts=artifact-missing-input" in report
    assert "Restore the input artifact files or regenerate task context." in report


def test_project_report_includes_requirement_coverage_traceability(tmp_path) -> None:
    store = ProjectLogStore(tmp_path)
    state = SharedProjectState(
        project=Project(
            id="project-trace",
            goal=(
                "\u6dfb\u52a0\u4e66\u7c4d\uff0c\u5237\u65b0\u540e\u4fdd\u7559\u6570\u636e\uff0c"
                "\u5bfc\u51fa CSV\uff0c\u6309\u72b6\u6001\u7b5b\u9009\uff0c\u5220\u9664\u6761\u76ee"
            ),
            current_stage="testing",
        ),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="testing",
        workitems=[
            WorkItem(
                id="workitem-validation",
                description="\u9a8c\u6536\u9879\u76ee",
                stage="testing",
                kind="acceptance_check",
                acceptance_criteria=["Provide validation evidence for frozen requirement: refresh persistence"],
            )
        ],
        executions=[
            Execution(
                workitem_id="workitem-validation",
                agent_id="agent-tester",
                result="\n".join(
                    [
                        "Browser form interaction updated visible state: sample",
                        "Browser filter interaction changed visible results",
                        "Browser delete interaction removed visible item",
                        "Browser export/download action triggered",
                    ]
                ),
                status=ExecutionStatus.FAILED,
            )
        ],
        artifacts=[
            Artifact(
                id="artifact-frozen",
                project_id="project-trace",
                workitem_id="workitem-req",
                agent_id="agent-requirement",
                kind="frozen_requirement_spec",
                title="Frozen Requirement",
                content=(
                    "\u652f\u6301\u6dfb\u52a0\u4e66\u7c4d\uff0c\u5237\u65b0\u540e\u4fdd\u7559\u6570\u636e\uff0c"
                    "\u5e76\u5bfc\u51fa CSV\uff0c\u6309\u72b6\u6001\u7b5b\u9009\u6761\u76ee\uff0c\u5220\u9664\u6761\u76ee\u3002"
                ),
            )
        ],
    )

    report = store.render_project_report(state, [])

    assert "## Requirement Coverage Traceability" in report
    assert "acceptance: Provide validation evidence for frozen requirement: refresh persistence" in report
    assert "WorkItem `workitem-validation` by `agent-tester`: missing_coverage" in report
    assert "add item interaction: `covered`" in report
    assert "refresh persistence: `missing`" in report
    assert "CSV export/download: `covered`" in report
    assert "filter interaction: `covered`" in report
    assert "delete item interaction: `covered`" in report


def test_project_report_includes_scope_contract_audit(tmp_path) -> None:
    store = ProjectLogStore(tmp_path)
    state = SharedProjectState(
        project=Project(id="project-scope", goal="scope audit", current_stage="design"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="design",
        artifacts=[
            Artifact(
                id="artifact-frozen",
                project_id="project-scope",
                workitem_id="workitem-req",
                agent_id="agent-requirement",
                kind="frozen_requirement_spec",
                title="Frozen Requirement",
                content="非目标：不接后端，不做登录。",
            ),
            Artifact(
                id="artifact-design",
                project_id="project-scope",
                workitem_id="workitem-design",
                agent_id="agent-designer",
                kind="design_overview",
                title="Design",
                content="方案：新增 FastAPI endpoint，并实现 login token session 管理。",
            ),
        ],
    )

    report = store.render_project_report(state, [])

    assert "## Scope Contract Audit" in report
    assert "Artifact `artifact-design` (design_overview): violation" in report
    assert "no backend/api" in report
    assert "no login/auth" in report


def test_project_report_includes_failure_remediation_suggestions(tmp_path) -> None:
    store = ProjectLogStore(tmp_path)
    state = SharedProjectState(
        project=Project(id="project-remediation", goal="recover failed task", current_stage="testing"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="testing",
        workitems=[
            WorkItem(
                id="workitem-timeout",
                description="Slow CLI task",
                stage="testing",
                kind="automated_test",
                status=WorkItemStatus.FAILED,
                failure_type="timeout",
                retryable=True,
                failure_summary="Agent CLI timed out",
            )
        ],
        executions=[
            Execution(
                workitem_id="workitem-timeout",
                agent_id="agent-tester",
                result="timeout",
                status=ExecutionStatus.FAILED,
                failure_type="timeout",
                failure_summary="Agent CLI timed out",
            )
        ],
    )

    report = store.render_project_report(state, [])

    assert "retry=0/1" in report
    assert "failure_type=timeout" in report
    assert "retryable=true" in report
    assert "remediation:" in report
    assert "Increase the CLI or LLM timeout" in report


def test_project_report_includes_workitem_retry_and_blocker_details(tmp_path) -> None:
    store = ProjectLogStore(tmp_path)
    state = SharedProjectState(
        project=Project(id="project-retry-report", goal="report retries", current_stage="testing"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="testing",
        workitems=[
            WorkItem(
                id="workitem-retry",
                description="Retry validation",
                stage="testing",
                kind="automated_test",
                status=WorkItemStatus.FAILED,
                retry_count=2,
                max_retries=2,
                blocked_reason="failure_type=validation_failed; retryable=true; summary=pytest failed",
                failure_type="validation_failed",
                retryable=True,
            )
        ],
    )

    report = store.render_project_report(state, [])

    assert "workitem-retry | stage=testing | kind=automated_test" in report
    assert "retry=2/2" in report
    assert "failure_type=validation_failed" in report
    assert "retryable=true" in report
    assert "blocked_reason=failure_type=validation_failed; retryable=true; summary=pytest failed" in report


def test_project_report_includes_structured_testing_feedback_for_rework(tmp_path) -> None:
    store = ProjectLogStore(tmp_path)
    failed_test = WorkItem(
        id="workitem-ui-test",
        description="Validate UI",
        stage="testing",
        kind="ui_validation",
        status=WorkItemStatus.DONE,
        failure_type="validation_failed",
        failure_summary="Validation exit_code=1",
        blocked_reason="测试失败已回流到研发返工",
    )
    rework = WorkItem(
        id="workitem-ui-rework",
        description="Fix UI validation failure",
        stage="development",
        kind="ui_implementation",
        feedback_from=[failed_test.id],
        rework_of="workitem-ui-implementation",
    )
    state = SharedProjectState(
        project=Project(id="project-feedback-report", goal="fix UI", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        pending_test_scope=["ui_validation"],
        workitems=[failed_test, rework],
        executions=[
            Execution(
                workitem_id=failed_test.id,
                agent_id="agent-tester",
                result="UI validation failed",
                status=ExecutionStatus.FAILED,
                validation_command=["python", "-m", "conductor.harness.static_web_cli"],
                validation_exit_code=1,
            )
        ],
        artifacts=[
            Artifact(
                id="artifact-ui-test",
                project_id="project-feedback-report",
                workitem_id=failed_test.id,
                agent_id="agent-tester",
                kind="ui_validation",
                title="Failed UI Validation",
                content=(
                    "Static Web Validation: FAIL\n\n"
                    "Errors:\n"
                    "- Browser form submit did not change visible page state\n"
                ),
            )
        ],
    )

    report = store.render_project_report(state, [])

    assert "structured_testing_feedback: source=workitem-ui-test" in report
    assert "Pending Test Scope: ui_validation" in report
    assert "validation_exit_code=1" in report
    assert "validation_command=python, -m, conductor.harness.static_web_cli" in report
    assert "Browser form submit did not change visible page state" in report
    assert "检查表单/按钮事件绑定" in report


def test_project_report_includes_preflight_gate_summary(tmp_path) -> None:
    store = ProjectLogStore(tmp_path)
    project_root = tmp_path / "project"
    gate_path = project_root / ".conductor" / "diagnostics" / "run-preflight" / "preflight-gate.json"
    gate_path.parent.mkdir(parents=True)
    gate_path.write_text(
        json.dumps(
            {
                "ok": False,
                "preflight_gate": {
                    "errors": ["local LLM preflight failed"],
                    "recommendations": ["Check local server"],
                    "diagnostics_path": str(gate_path),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    state = SharedProjectState(
        project=Project(
            id="project-preflight-report",
            goal="report preflight gate",
            current_stage="requirement",
            project_root=str(project_root),
        ),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="requirement",
    )

    report = store.render_project_report(state, [])

    assert "## Preflight Gate" in report
    assert f"- Path: {gate_path}" in report
    assert "- Status: fail" in report
    assert "- Error: local LLM preflight failed" in report
    assert "- Recommendation: Check local server" in report


def test_project_report_includes_human_control_actions(tmp_path) -> None:
    store = ProjectLogStore(tmp_path)
    state = SharedProjectState(
        project=Project(id="project-human-report", goal="report human control", current_stage="testing"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="testing",
        human_control_actions=[
            HumanControlAction(
                id="human-approval",
                project_id="project-human-report",
                action=HumanControlActionType.REQUEST_APPROVAL,
                actor="tl_agent",
                reason="high risk escalation",
                stage="testing",
                workitem_id="workitem-risk",
                payload={"controller_action": "escalate_project", "stage": "testing"},
                created_at="2026-05-20T00:00:00+00:00",
            )
        ],
    )

    report = store.render_project_report(state, [])

    assert "## Human Control" in report
    assert "- Active: true" in report
    assert "- Hold Reason: human_approval_required: high risk escalation" in report
    assert "- Action Count: 1" in report
    assert "human-approval | action=request_approval | actor=tl_agent" in report
    assert 'payload={"controller_action": "escalate_project", "stage": "testing"}' in report
