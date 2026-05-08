"""Conductor 核心数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from conductor.collaboration.models import Collaboration


class ProjectStatus(StrEnum):
    """Project 生命周期状态。"""

    INITIALIZED = "initialized"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class WorkItemStatus(StrEnum):
    """WorkItem 生命周期状态。"""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class TaskAssignmentStatus(StrEnum):
    """Task center assignment lifecycle."""

    QUEUED = "queued"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class ExecutionStatus(StrEnum):
    """Execution 执行结果状态。"""

    SUCCESS = "success"
    FAILED = "failed"


class Capability(StrEnum):
    """Agent 能力枚举。"""

    PLANNING = "planning"
    CODING = "coding"
    TESTING = "testing"
    DEBUGGING = "debugging"
    SHELL_EXECUTION = "shell_execution"


@dataclass(slots=True)
class Stage:
    """Workflow 阶段定义。"""

    name: str
    objective: str
    expected_output: str


@dataclass(slots=True)
class Project:
    """项目顶层对象。"""

    id: str
    goal: str
    status: ProjectStatus = ProjectStatus.INITIALIZED
    current_stage: str | None = None
    milestones: list[str] = field(default_factory=list)
    project_root: str = ""


@dataclass(slots=True)
class WorkItem:
    """可执行工作项。"""

    id: str
    description: str
    stage: str
    kind: str = "generic"
    owner_agent: str | None = None
    status: WorkItemStatus = WorkItemStatus.PENDING
    dependencies: list[str] = field(default_factory=list)
    input_artifact_ids: list[str] = field(default_factory=list)
    output_artifact_ids: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    result: str | None = None
    retry_count: int = 0
    max_retries: int = 1
    blocked_reason: str | None = None
    failure_type: str = ""
    retryable: bool = True
    failure_summary: str = ""
    collaboration_session_id: str | None = None
    feedback_from: list[str] = field(default_factory=list)
    rework_of: str | None = None


@dataclass(slots=True)
class TaskAssignment:
    """Task Center record: one WorkItem claimed and returned by one Agent."""

    id: str
    workitem_id: str
    role: str
    status: TaskAssignmentStatus = TaskAssignmentStatus.QUEUED
    assigned_agent_id: str | None = None
    claim_token: str = ""
    claim_reason: str = ""
    dependencies: list[str] = field(default_factory=list)
    input_artifact_ids: list[str] = field(default_factory=list)
    output_artifact_ids: list[str] = field(default_factory=list)
    result_summary: str = ""
    blocked_reason: str | None = None
    claimed_at: str = ""
    last_heartbeat_at: str = ""
    returned_at: str = ""
    prompt_file: str = ""


@dataclass(slots=True)
class AgentActivation:
    """Project-scoped on-demand Agent creation record."""

    role: str
    agent_id: str
    stage: str
    reason: str
    related_workitem_kinds: list[str] = field(default_factory=list)
    execution_backend: str = "mock"
    preferred_backend: str = "local"


@dataclass(slots=True)
class Execution:
    """一次 WorkItem 执行记录。"""

    workitem_id: str
    agent_id: str
    result: str
    status: ExecutionStatus
    source_backend: str = ""
    cli_name: str = ""
    model: str = ""
    working_directory: str = ""
    execution_command: list[str] = field(default_factory=list)
    execution_exit_code: int | None = None
    execution_duration_ms: int | None = None
    prompt_hash: str = ""
    input_artifact_ids: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    validation_command: list[str] = field(default_factory=list)
    validation_exit_code: int | None = None
    validation_success: bool | None = None
    cli_stdout_tail: str = ""
    cli_stderr_tail: str = ""
    failure_type: str = ""
    failure_summary: str = ""


@dataclass(slots=True)
class AgentCapabilityStats:
    """Runtime capability profile aggregated from completed assignments."""

    agent_id: str
    role: str
    completed_count: int = 0
    failed_count: int = 0
    workitem_kinds: list[str] = field(default_factory=list)
    last_workitem_id: str | None = None
    last_status: str | None = None


@dataclass(slots=True)
class RouteDecision:
    """WorkItem 到 Agent 的路由结果。"""

    workitem_id: str
    selected_agent: str


@dataclass(slots=True)
class Artifact:
    """Agent 执行后沉淀的项目产物。"""

    id: str
    project_id: str
    workitem_id: str
    agent_id: str
    kind: str
    title: str
    content: str
    path: str | None = None
    source_backend: str = "unknown"
    parent_artifact_id: str | None = None
    derived_from: list[str] = field(default_factory=list)
    review_of: str | None = None
    version: int = 1
    collaboration_session_id: str | None = None


@dataclass(slots=True)
class SharedProjectState:
    """Shared State，作为项目唯一事实源。"""

    project: Project
    project_status: ProjectStatus
    current_stage: str | None
    workitems: list[WorkItem] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    recent_events: list[str] = field(default_factory=list)
    executions: list[Execution] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    collaborations: list[Collaboration] = field(default_factory=list)
    task_assignments: list[TaskAssignment] = field(default_factory=list)
    agent_activations: list[AgentActivation] = field(default_factory=list)
    planned_roles: list[str] = field(default_factory=list)
    route_decisions: list[RouteDecision] = field(default_factory=list)
    gate_history: list[str] = field(default_factory=list)
    pending_test_scope: list[str] = field(default_factory=list)
    agent_capability_stats: list[AgentCapabilityStats] = field(default_factory=list)


# Sprint 1 兼容别名：部分文档会把 Execution 称为 ExecutionResult。
ExecutionResult = Execution

ExecutionResult = Execution

__all__ = [
    "AgentCapabilityStats",
    "AgentActivation",
    "Artifact",
    "Capability",
    "Execution",
    "ExecutionResult",
    "ExecutionStatus",
    "Project",
    "ProjectStatus",
    "RouteDecision",
    "SharedProjectState",
    "Stage",
    "TaskAssignment",
    "TaskAssignmentStatus",
    "WorkItem",
    "WorkItemStatus",
]
