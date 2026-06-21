"""Board 视图模型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class BoardWorkItemView:
    """WorkItem snapshot model for board consumers."""

    id: str
    stage: str
    kind: str
    status: str
    owner_agent: str
    description: str
    retry_text: str
    stage_label: str = ""
    kind_label: str = ""
    status_label: str = ""
    owner_agent_label: str = ""
    failure_type: str = ""
    failure_summary: str = ""
    remediation_suggestions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BoardExecutionView:
    """Execution snapshot model for board consumers."""

    workitem_id: str
    agent_id: str
    status: str
    result: str
    agent_label: str = ""
    status_label: str = ""
    failure_type: str = ""
    failure_summary: str = ""
    remediation_suggestions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BoardArtifactView:
    """Artifact snapshot model for board consumers."""

    id: str
    title: str
    kind: str
    kind_label: str
    agent_id: str
    agent_label: str
    workitem_id: str
    content: str
    path: str
    source_backend: str
    source_backend_label: str = ""
    version: int = 1
    parent_artifact_id: str | None = None
    review_of: str | None = None
    detail_api_path: str = ""


@dataclass(slots=True)
class BoardMeetingAgentView:
    """需求会议桌中的 Agent 展示状态。"""

    role: str
    label: str
    agent_id: str
    status: str
    status_label: str


@dataclass(slots=True)
class BoardReviewView:
    """需求协作审阅展示项。"""

    round_index: int
    role: str
    role_label: str
    decision: str
    decision_label: str
    content: str


@dataclass(slots=True)
class BoardProjectAgentView:
    """项目中已创建/激活的 Agent 展示项。"""

    role: str
    role_label: str
    agent_id: str
    mission: str
    reason: str
    related_kinds: list[str] = field(default_factory=list)
    task_api_path: str = ""
    claim_task_api_path: str = ""


@dataclass(slots=True)
class BoardActivationNodeView:
    """按需激活 Agent 的可视化节点。"""

    role: str
    role_label: str
    short_label: str
    position_class: str
    active: bool
    status_label: str
    reason: str = ""
    mission: str = ""
    related_kinds: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BoardTaskAssignmentView:
    """Task Center assignment display item."""

    id: str
    workitem_id: str
    role: str
    role_label: str
    status: str
    status_label: str
    assigned_agent_id: str
    assigned_agent_label: str
    claim_token: str = ""
    claimable: bool = False
    write_scope_conflict_assignment_ids: list[str] = field(default_factory=list)
    unmet_dependency_ids: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    input_artifact_ids: list[str] = field(default_factory=list)
    output_artifact_ids: list[str] = field(default_factory=list)
    claim_reason: str = ""
    blocked_reason: str = ""
    claimed_age_seconds: int | None = None
    last_heartbeat_at: str = ""
    heartbeat_age_seconds: int | None = None
    lease_seconds: int = 0
    lease_expires_at: str = ""
    lease_expired: bool = False
    stale_claimed: bool = False
    prompt_file: str = ""
    claim_api_path: str = ""
    context_api_path: str = ""
    return_api_paths: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class BoardExecutionRuntimeView:
    """当前或最近一次执行的运行上下文。"""

    available: bool = False
    is_running: bool = False
    headline: str = ""
    workitem_id: str = ""
    workitem_kind_label: str = ""
    stage_label: str = ""
    agent_label: str = ""
    backend_label: str = ""
    cli_label: str = "-"
    model_label: str = "-"
    working_directory: str = ""
    execution_mode_label: str = ""
    state_label: str = ""
    stage_progress_label: str = ""
    stage_progress_percent: int = 0
    task_position_label: str = ""
    output_summary_title: str = ""
    output_summary: str = ""


@dataclass(slots=True)
class BoardPreflightGateView:
    """Run preflight gate audit summary for Board consumers."""

    recorded: bool = False
    status: str = "not_recorded"
    status_label: str = "未记录"
    project_root: str = ""
    path: str = ""
    errors: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    execution_readiness: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class BoardRunAuditView:
    """Compact run audit summary for Board consumers."""

    retry_history_count: int = 0
    retry_attempt_count: int = 0
    failed_workitem_ids: list[str] = field(default_factory=list)
    scope_contract_status: str = "not_evaluated"
    scope_contract_status_label: str = "未评估"
    scope_contract_violation_count: int = 0
    scope_contract_violations: list[dict[str, object]] = field(default_factory=list)
    delivery_readiness_status: str = "not_evaluated"
    delivery_readiness_status_label: str = "未评估"
    delivery_readiness_score: int = 0
    delivery_readiness_blocking_count: int = 0
    delivery_readiness_warning_count: int = 0
    delivery_readiness_checks: list[dict[str, object]] = field(default_factory=list)
    risk_level: str = "normal"
    risk_level_label: str = "正常"


@dataclass(slots=True)
class BoardHumanControlView:
    """Human takeover and approval state for Board consumers."""

    active: bool = False
    hold_reason: str = ""
    action: str = ""
    action_label: str = ""
    actor: str = ""
    reason: str = ""
    stage: str = ""
    workitem_id: str = ""
    payload: dict[str, object] = field(default_factory=dict)
    created_at: str = ""
    available_actions: list[str] = field(default_factory=list)
    operator_guidance: str = ""
    operator_commands: list[str] = field(default_factory=list)
    action_count: int = 0


@dataclass(slots=True)
class BoardOperationActionView:
    """Operator-facing action exposed by the Board operation console."""

    id: str
    label: str
    category: str
    api_method: str = ""
    api_path: str = ""
    command: str = ""
    enabled: bool = True
    reason: str = ""
    severity: str = "info"


@dataclass(slots=True)
class BoardOperationConsoleView:
    """Compact operator console for Board automation and recovery actions."""

    available: bool = True
    attention_count: int = 0
    guidance: str = ""
    actions: list[BoardOperationActionView] = field(default_factory=list)


@dataclass(slots=True)
class BoardDesignCollaborationView:
    """需求/设计阶段会议桌视图。"""

    enabled: bool = False
    status: str = "waiting"
    current_step_label: str = "等待需求设计"
    status_label: str = "未开始"
    current_document_title: str = "暂无需求文档"
    current_document_content: str = ""
    current_document_source: str = ""
    agents: list[BoardMeetingAgentView] = field(default_factory=list)
    reviews: list[BoardReviewView] = field(default_factory=list)
    history: list[BoardArtifactView] = field(default_factory=list)
    team_plan: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class BoardSnapshot:
    """Board state snapshot."""

    project_id: str
    project_goal: str
    project_root: str
    project_status: str
    current_stage: str
    project_status_label: str = ""
    current_stage_label: str = ""
    planned_roles: list[str] = field(default_factory=list)
    planned_role_labels: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    gate_history: list[str] = field(default_factory=list)
    recent_events: list[str] = field(default_factory=list)
    recent_events_tail: list[str] = field(default_factory=list)
    workitems: list[BoardWorkItemView] = field(default_factory=list)
    executions: list[BoardExecutionView] = field(default_factory=list)
    code_execution_artifacts: list[BoardArtifactView] = field(default_factory=list)
    artifacts: list[BoardArtifactView] = field(default_factory=list)
    route_lines: list[str] = field(default_factory=list)
    project_agents: list[BoardProjectAgentView] = field(default_factory=list)
    activation_nodes: list[BoardActivationNodeView] = field(default_factory=list)
    task_center_summary: dict[str, int] = field(default_factory=dict)
    task_assignments: list[BoardTaskAssignmentView] = field(default_factory=list)
    preflight_gate: BoardPreflightGateView = field(default_factory=BoardPreflightGateView)
    run_audit: BoardRunAuditView = field(default_factory=BoardRunAuditView)
    human_control: BoardHumanControlView = field(default_factory=BoardHumanControlView)
    operation_console: BoardOperationConsoleView = field(default_factory=BoardOperationConsoleView)
    execution_runtime: BoardExecutionRuntimeView = field(default_factory=BoardExecutionRuntimeView)
    design_collaboration: BoardDesignCollaborationView = field(default_factory=BoardDesignCollaborationView)


@dataclass(slots=True)
class BoardProjectSummary:
    """项目列表摘要。"""

    project_id: str
    goal: str
    project_root: str
    status: str
    current_stage: str
    status_label: str = ""
    current_stage_label: str = ""
    preflight_gate_status: str = "not_recorded"
    preflight_gate_status_label: str = "未记录"
    risk_level: str = "normal"
    risk_level_label: str = "正常"
    retry_history_count: int = 0
    human_control_active: bool = False
    human_control_label: str = ""
    scope_contract_status: str = "not_evaluated"
    scope_contract_status_label: str = "未评估"
    scope_contract_violation_count: int = 0
    delivery_readiness_status: str = "not_evaluated"
    delivery_readiness_score: int = 0
