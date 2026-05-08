"""Log persistence tests."""

import json

from conductor.config.cli import CLISelectionConfig
from conductor.controller.engine import ConductorEngine
from conductor.domain.models import Artifact, Execution, ExecutionStatus, Project, ProjectStatus, SharedProjectState, WorkItem, WorkItemStatus
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
    assert "stale_claimed=" in report
    assert "prompt_file=" in report
    assert "## Executions" in report
    assert "changed_files=" in report
    assert "## Event Timeline" in report


def test_project_report_includes_requirement_coverage_traceability(tmp_path) -> None:
    store = ProjectLogStore(tmp_path)
    state = SharedProjectState(
        project=Project(
            id="project-trace",
            goal="\u6dfb\u52a0\u4e66\u7c4d\uff0c\u5237\u65b0\u540e\u4fdd\u7559\u6570\u636e\uff0c\u5bfc\u51fa CSV",
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
                content="\u652f\u6301\u6dfb\u52a0\u4e66\u7c4d\uff0c\u5237\u65b0\u540e\u4fdd\u7559\u6570\u636e\uff0c\u5e76\u5bfc\u51fa CSV\u3002",
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

    assert "remediation:" in report
    assert "Increase the CLI or LLM timeout" in report


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
