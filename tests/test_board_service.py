"""BoardSnapshot 组装测试。"""

import json
from datetime import datetime, timedelta, timezone

from conductor.board.service import BoardService
from conductor.collaboration.models import Collaboration, CollaborationStatus
from conductor.config.cli import CLISelectionConfig
from conductor.config.llm import LLMHTTPConfig, LLMRuntimeConfig, LLMUsagePolicy
from conductor.controller.lead_controller import LeadController
from conductor.domain.models import (
    AgentActivation,
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
from conductor.execution.runner import Runner
from conductor.state.store import InMemoryStateStore
from conductor.workflow.template import WorkflowTemplate


def test_board_service_builds_snapshot_from_state() -> None:
    state_store = InMemoryStateStore()
    controller = LeadController(
        workflow_template=WorkflowTemplate(),
        state_store=state_store,
        runner=Runner(state_store),
    )
    state = controller.initialize_project("实现一个包含 API 和测试的功能")
    for _ in range(8):
        if state.project_status.value in {"completed", "blocked"}:
            break
        state = controller.advance(state)

    snapshot = BoardService().build_snapshot(
        state,
        cli_config=CLISelectionConfig(codex_model="gpt-5.4-mini", codex_reasoning_effort="medium"),
        llm_runtime_config=LLMRuntimeConfig(
            local=LLMHTTPConfig(base_url="http://127.0.0.1:11434/v1", model_name="local-test", enabled=False),
            cloud=LLMHTTPConfig(base_url="https://api.example.com/v1", model_name="cloud-test", enabled=False),
            usage=LLMUsagePolicy(runner_enabled=False),
        ),
    )

    assert snapshot.project_id == state.project.id
    assert snapshot.project_status == state.project_status.value
    assert snapshot.workitems
    assert snapshot.artifacts
    assert snapshot.artifacts[0].title
    assert snapshot.artifacts[0].content
    assert snapshot.artifacts[0].source_backend_label
    assert snapshot.project_agents
    assert snapshot.project_agents[0].reason
    assert snapshot.activation_nodes
    assert any(node.active for node in snapshot.activation_nodes)
    assert snapshot.design_collaboration.enabled is True
    assert snapshot.design_collaboration.current_document_title
    assert snapshot.design_collaboration.agents
    assert snapshot.recent_events
    assert snapshot.execution_runtime.available is True
    assert snapshot.execution_runtime.headline
    assert snapshot.execution_runtime.stage_label
    assert snapshot.execution_runtime.stage_progress_label
    assert snapshot.execution_runtime.task_position_label
    assert snapshot.execution_runtime.output_summary
    assert snapshot.execution_runtime.working_directory
    assert snapshot.task_assignments
    assert snapshot.task_center_summary["total"] == len(state.task_assignments)
    assert "claimable" in snapshot.task_center_summary
    assert snapshot.task_assignments[0].claimable in {True, False}
    assert isinstance(snapshot.task_assignments[0].claim_token, str)
    assert isinstance(snapshot.task_assignments[0].unmet_dependency_ids, list)
    assert snapshot.task_assignments[0].claimed_age_seconds is None or isinstance(
        snapshot.task_assignments[0].claimed_age_seconds, int
    )
    assert snapshot.task_assignments[0].heartbeat_age_seconds is None or isinstance(
        snapshot.task_assignments[0].heartbeat_age_seconds, int
    )
    assert isinstance(snapshot.task_assignments[0].lease_seconds, int)
    assert isinstance(snapshot.task_assignments[0].lease_expires_at, str)
    assert snapshot.task_assignments[0].lease_expired in {True, False}
    assert snapshot.task_assignments[0].stale_claimed in {True, False}
    assert isinstance(snapshot.workitems[0].remediation_suggestions, list)
    assert isinstance(snapshot.executions[0].remediation_suggestions, list)
    assert snapshot.preflight_gate.recorded is False
    assert snapshot.preflight_gate.status == "not_recorded"
    assert snapshot.run_audit.risk_level in {"normal", "medium", "high"}
    assert isinstance(snapshot.run_audit.failed_workitem_ids, list)
    assert snapshot.run_audit.delivery_readiness_status in {"ready", "at_risk", "blocked", "incomplete"}
    assert isinstance(snapshot.run_audit.delivery_readiness_score, int)
    assert snapshot.human_control.active is False
    assert snapshot.human_control.action_count == len(state.human_control_actions)


def test_board_service_exposes_active_human_control_state() -> None:
    state = SharedProjectState(
        project=Project(id="project-human-board", goal="human hold", current_stage="testing", project_root="C:/demo"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="testing",
        human_control_actions=[
            HumanControlAction(
                id="human-approval",
                project_id="project-human-board",
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

    snapshot = BoardService().build_snapshot(state)
    summaries = BoardService().build_project_summaries([state])

    assert snapshot.human_control.active is True
    assert snapshot.human_control.action == "request_approval"
    assert snapshot.human_control.action_label == "等待人工审批"
    assert snapshot.human_control.actor == "tl_agent"
    assert snapshot.human_control.reason == "high risk escalation"
    assert snapshot.human_control.workitem_id == "workitem-risk"
    assert snapshot.human_control.payload == {"controller_action": "escalate_project", "stage": "testing"}
    assert snapshot.human_control.hold_reason == "human_approval_required: high risk escalation"
    assert snapshot.human_control.action_count == 1
    assert summaries[0].human_control_active is True
    assert summaries[0].human_control_label == "等待人工审批"


def test_board_service_exposes_run_audit_risk_summary(tmp_path) -> None:
    state = SharedProjectState(
        project=Project(id="project-audit", goal="audit risk", current_stage="design", project_root=str(tmp_path)),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="design",
        blockers=["Scope violation requires review"],
        workitems=[
            WorkItem(
                id="workitem-retry",
                description="Retried validation",
                stage="testing",
                kind="automated_test",
                status=WorkItemStatus.FAILED,
                retry_count=2,
                max_retries=2,
                failure_type="validation_failed",
                failure_summary="pytest failed",
                blocked_reason="failure_type=validation_failed; retryable=true; summary=pytest failed",
            )
        ],
        artifacts=[
            Artifact(
                id="artifact-frozen",
                project_id="project-audit",
                workitem_id="workitem-req",
                agent_id="agent-requirement",
                kind="frozen_requirement_spec",
                title="Frozen Requirement",
                content="非目标：不接后端，不做登录。",
            ),
            Artifact(
                id="artifact-design",
                project_id="project-audit",
                workitem_id="workitem-design",
                agent_id="agent-designer",
                kind="design_overview",
                title="Design",
                content="方案：新增 FastAPI endpoint，并实现 login token session 管理。",
            ),
        ],
    )

    snapshot = BoardService().build_snapshot(state)

    assert snapshot.run_audit.retry_history_count == 1
    assert snapshot.run_audit.retry_attempt_count == 2
    assert snapshot.run_audit.failed_workitem_ids == ["workitem-retry"]
    assert snapshot.run_audit.scope_contract_status == "violation"
    assert snapshot.run_audit.scope_contract_status_label == "范围风险"
    assert snapshot.run_audit.scope_contract_violation_count == 2
    assert snapshot.run_audit.delivery_readiness_status == "blocked"
    assert snapshot.run_audit.delivery_readiness_blocking_count >= 1
    assert snapshot.run_audit.risk_level == "high"
    assert snapshot.run_audit.risk_level_label == "高风险"


def test_board_service_extracts_code_execution_reports() -> None:
    state = SharedProjectState(
        project=Project(id="project-1", goal="实现前后端", current_stage="development", project_root="C:/demo"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-backend", description="后端实现", stage="development", kind="api_implementation"),
            WorkItem(id="workitem-frontend", description="前端实现", stage="development", kind="ui_implementation"),
        ],
        artifacts=[
            Artifact(
                id="artifact-backend",
                project_id="project-1",
                workitem_id="workitem-backend",
                agent_id="agent-backend",
                kind="api_implementation",
                title="代码执行报告 - workitem-backend",
                content="backend report",
                source_backend="agent_cli/codex",
            ),
            Artifact(
                id="artifact-frontend",
                project_id="project-1",
                workitem_id="workitem-frontend",
                agent_id="agent-frontend",
                kind="ui_implementation",
                title="代码执行报告 - workitem-frontend",
                content="frontend report",
                source_backend="agent_cli/codex",
            ),
        ],
    )

    snapshot = BoardService().build_snapshot(state, cli_config=CLISelectionConfig())

    assert len(snapshot.code_execution_artifacts) == 2
    assert snapshot.code_execution_artifacts[0].source_backend_label == "Codex CLI"


def test_board_service_exposes_requirement_team_plan() -> None:
    state = SharedProjectState(
        project=Project(id="project-team", goal="complex requirement", current_stage="requirement", project_root="C:/demo"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="requirement",
        collaborations=[
            Collaboration(
                id="collaboration-1",
                project_id="project-team",
                workitem_id="workitem-1",
                lead_agent_id="agent-requirement-designer",
                reviewer_agent_ids=["agent-designer:designer.interaction"],
                status=CollaborationStatus.RUNNING,
                max_rounds=2,
                current_round=1,
                team_plan={
                    "complexity_level": "complex",
                    "complexity_score": 6,
                    "peer_seats": [
                        {
                            "role": "designer",
                            "seat_id": "designer.interaction",
                            "phase": "design_peer_review",
                            "focus": "Review interaction paths.",
                        }
                    ],
                    "functional_seats": [],
                    "reasons": ["user interaction and visible states"],
                },
            )
        ],
    )

    snapshot = BoardService().build_snapshot(state)

    assert snapshot.design_collaboration.team_plan["complexity_level"] == "complex"
    assert snapshot.design_collaboration.team_plan["peer_seats"][0]["seat_id"] == "designer.interaction"


def test_board_service_exposes_preflight_gate_summary(tmp_path) -> None:
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
                "execution_readiness": {
                    "status": "blocked",
                    "selected_agent_cli": "",
                    "selected_llm_backend": "local",
                    "runner_enabled": False,
                    "blocking_reasons": ["local LLM preflight failed"],
                    "warnings": [],
                    "recommendations": ["Check local server"],
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    state = SharedProjectState(
        project=Project(
            id="project-preflight-board",
            goal="show preflight gate",
            current_stage="requirement",
            project_root=str(project_root),
        ),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="requirement",
    )

    snapshot = BoardService().build_snapshot(state)

    assert snapshot.preflight_gate.recorded is True
    assert snapshot.preflight_gate.status == "fail"
    assert snapshot.preflight_gate.status_label == "失败"
    assert snapshot.preflight_gate.project_root == str(project_root.resolve())
    assert snapshot.preflight_gate.path == str(gate_path)
    assert snapshot.preflight_gate.errors == ["local LLM preflight failed"]
    assert snapshot.preflight_gate.recommendations == ["Check local server"]
    assert snapshot.preflight_gate.execution_readiness["status"] == "blocked"


def test_board_service_project_summaries_include_preflight_gate_status(tmp_path) -> None:
    project_root = tmp_path / "project"
    gate_path = project_root / ".conductor" / "diagnostics" / "run-preflight" / "preflight-gate.json"
    gate_path.parent.mkdir(parents=True)
    gate_path.write_text(
        json.dumps(
            {
                "ok": True,
                "preflight_gate": {
                    "errors": [],
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
            id="project-preflight-summary",
            goal="show summary gate",
            current_stage="requirement",
            project_root=str(project_root),
        ),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="requirement",
    )

    summaries = BoardService().build_project_summaries([state])

    assert summaries[0].preflight_gate_status == "pass"
    assert summaries[0].preflight_gate_status_label == "通过"


def test_board_service_project_summaries_include_run_audit_status(tmp_path) -> None:
    state = SharedProjectState(
        project=Project(id="project-summary-audit", goal="summary audit", current_stage="testing", project_root=str(tmp_path)),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="testing",
        workitems=[
            WorkItem(
                id="workitem-failed",
                description="failed validation",
                stage="testing",
                kind="automated_test",
                status=WorkItemStatus.FAILED,
                retry_count=1,
                max_retries=2,
                failure_type="validation_failed",
            )
        ],
        artifacts=[
            Artifact(
                id="artifact-frozen",
                project_id="project-summary-audit",
                workitem_id="workitem-req",
                agent_id="agent-requirement",
                kind="frozen_requirement_spec",
                title="Frozen Requirement",
                content="非目标：不做登录。",
            ),
            Artifact(
                id="artifact-design",
                project_id="project-summary-audit",
                workitem_id="workitem-design",
                agent_id="agent-designer",
                kind="design_overview",
                title="Design",
                content="设计登录 token。",
            ),
        ],
    )

    summaries = BoardService().build_project_summaries([state])

    assert summaries[0].risk_level == "high"
    assert summaries[0].risk_level_label == "高风险"
    assert summaries[0].retry_history_count == 1
    assert summaries[0].scope_contract_status == "violation"
    assert summaries[0].scope_contract_status_label == "范围风险"
    assert summaries[0].scope_contract_violation_count == 1
    assert summaries[0].delivery_readiness_status == "blocked"
    assert isinstance(summaries[0].delivery_readiness_score, int)


def test_board_service_exposes_task_center_readiness() -> None:
    state = SharedProjectState(
        project=Project(id="project-task-center", goal="task center readiness", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-dependency", description="Dependency", stage="development"),
            WorkItem(
                id="workitem-blocked",
                description="Blocked",
                stage="development",
                dependencies=["workitem-dependency"],
            ),
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-blocked",
                workitem_id="workitem-blocked",
                role="backend_engineer",
                dependencies=["workitem-dependency"],
                prompt_file="C:/demo/.conductor/task_center/prompts/assignment-blocked.md",
            )
        ],
    )

    snapshot = BoardService().build_snapshot(state)

    assert snapshot.task_center_summary["total"] == 1
    assert snapshot.task_center_summary["claimable"] == 0
    assert snapshot.task_center_summary["blocked_by_dependencies"] == 1
    assert snapshot.task_assignments[0].claimable is False
    assert snapshot.task_assignments[0].unmet_dependency_ids == ["workitem-dependency"]
    assert snapshot.task_assignments[0].prompt_file == "C:/demo/.conductor/task_center/prompts/assignment-blocked.md"


def test_board_service_exposes_write_scope_conflicts() -> None:
    state = SharedProjectState(
        project=Project(id="project-task-scope-conflict", goal="parallel UI work", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(
                id="workitem-claimed",
                description="Claimed layout task",
                stage="development",
                kind="ui_implementation",
                status=WorkItemStatus.RUNNING,
                owner_agent="agent-layout-a",
            ),
            WorkItem(
                id="workitem-queued",
                description="Queued layout task",
                stage="development",
                kind="ui_implementation",
            ),
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-claimed",
                workitem_id="workitem-claimed",
                role="frontend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-layout-a",
                claim_token="token-a",
            ),
            TaskAssignment(
                id="assignment-queued",
                workitem_id="workitem-queued",
                role="frontend_engineer",
            ),
        ],
        agent_activations=[
            AgentActivation(
                role="frontend_engineer",
                agent_id="agent-layout-a",
                stage="development",
                reason="layout scope",
                related_workitem_kinds=["ui_implementation"],
                dynamic=True,
                parallel_safe=True,
                write_scope=["index.html"],
            ),
            AgentActivation(
                role="frontend_engineer",
                agent_id="agent-layout-b",
                stage="development",
                reason="layout scope",
                related_workitem_kinds=["ui_implementation"],
                dynamic=True,
                parallel_safe=True,
                write_scope=["index.html"],
            ),
        ],
    )

    snapshot = BoardService().build_snapshot(state)
    queued = next(item for item in snapshot.task_assignments if item.id == "assignment-queued")

    assert snapshot.task_center_summary["blocked_by_write_scope"] == 1
    assert queued.claimable is False
    assert queued.write_scope_conflict_assignment_ids == ["assignment-claimed"]


def test_board_service_exposes_stale_task_assignment_state() -> None:
    state = SharedProjectState(
        project=Project(id="project-stale-task", goal="stale task center readiness", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-stale", description="Stale claimed task", stage="development"),
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-stale",
                workitem_id="workitem-stale",
                role="backend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-backend",
                claimed_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            )
        ],
    )

    snapshot = BoardService().build_snapshot(state)

    assert snapshot.task_center_summary["stale_claimed"] == 1
    assert snapshot.task_assignments[0].claimed_age_seconds is not None
    assert snapshot.task_assignments[0].claimed_age_seconds >= 7200
    assert snapshot.task_assignments[0].heartbeat_age_seconds is not None
    assert snapshot.task_assignments[0].heartbeat_age_seconds >= 7200
    assert snapshot.task_assignments[0].stale_claimed is True


def test_board_service_uses_heartbeat_for_stale_task_assignment_state() -> None:
    state = SharedProjectState(
        project=Project(id="project-heartbeat-task", goal="heartbeat task center readiness", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-heartbeat", description="Heartbeat claimed task", stage="development"),
        ],
        task_assignments=[
            TaskAssignment(
                id="assignment-heartbeat",
                workitem_id="workitem-heartbeat",
                role="backend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-backend",
                claimed_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                last_heartbeat_at=datetime.now(timezone.utc).isoformat(),
            )
        ],
    )

    snapshot = BoardService().build_snapshot(state)

    assert snapshot.task_center_summary["stale_claimed"] == 0
    assert snapshot.task_assignments[0].claimed_age_seconds is not None
    assert snapshot.task_assignments[0].claimed_age_seconds >= 7200
    assert snapshot.task_assignments[0].last_heartbeat_at
    assert snapshot.task_assignments[0].heartbeat_age_seconds is not None
    assert snapshot.task_assignments[0].heartbeat_age_seconds < 60
    assert snapshot.task_assignments[0].stale_claimed is False


def test_board_service_exposes_failure_remediation_suggestions() -> None:
    state = SharedProjectState(
        project=Project(id="project-failure-board", goal="recover failed task", current_stage="testing"),
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

    snapshot = BoardService().build_snapshot(state)

    assert snapshot.workitems[0].failure_type == "timeout"
    assert "Increase the CLI or LLM timeout" in snapshot.workitems[0].remediation_suggestions[0]
    assert snapshot.executions[0].failure_type == "timeout"
    assert snapshot.executions[0].remediation_suggestions
