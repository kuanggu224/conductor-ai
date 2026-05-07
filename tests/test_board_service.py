"""BoardSnapshot 组装测试。"""

from datetime import datetime, timedelta, timezone

from conductor.board.service import BoardService
from conductor.collaboration.models import Collaboration, CollaborationStatus
from conductor.config.cli import CLISelectionConfig
from conductor.config.llm import LLMHTTPConfig, LLMRuntimeConfig, LLMUsagePolicy
from conductor.controller.lead_controller import LeadController
from conductor.domain.models import (
    Artifact,
    Execution,
    ExecutionStatus,
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
    assert snapshot.task_assignments[0].stale_claimed in {True, False}
    assert isinstance(snapshot.workitems[0].remediation_suggestions, list)
    assert isinstance(snapshot.executions[0].remediation_suggestions, list)


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
