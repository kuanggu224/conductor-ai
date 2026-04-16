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
    acceptance_criteria: list[str] = field(default_factory=list)
    result: str | None = None
    retry_count: int = 0
    max_retries: int = 1


@dataclass(slots=True)
class Execution:
    """一次 WorkItem 执行记录。"""

    workitem_id: str
    agent_id: str
    result: str
    status: ExecutionStatus


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
    planned_roles: list[str] = field(default_factory=list)
    route_decisions: list[RouteDecision] = field(default_factory=list)
    gate_history: list[str] = field(default_factory=list)
