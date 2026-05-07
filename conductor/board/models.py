"""Board 视图模型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class BoardWorkItemView:
    """用于页面展示的 WorkItem 视图模型。"""

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
    """用于页面展示的 Execution 视图模型。"""

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
    """用于页面展示的 Artifact 视图模型。"""

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
    claimable: bool = False
    unmet_dependency_ids: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    input_artifact_ids: list[str] = field(default_factory=list)
    output_artifact_ids: list[str] = field(default_factory=list)
    claim_reason: str = ""
    blocked_reason: str = ""
    claimed_age_seconds: int | None = None
    stale_claimed: bool = False
    prompt_file: str = ""


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
class BoardDesignCollaborationView:
    """需求/设计阶段会议桌视图。"""

    enabled: bool = False
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
    """Board 页面快照。"""

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
