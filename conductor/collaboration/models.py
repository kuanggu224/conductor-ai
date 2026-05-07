"""多 Agent 协作数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class CollaborationStatus(StrEnum):
    """协作流程状态。"""

    RUNNING = "running"
    ACCEPTED = "accepted"
    MAX_ROUNDS_REACHED = "max_rounds_reached"
    FAILED = "failed"


class ReviewDecision(StrEnum):
    """审阅决策。"""

    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"


@dataclass(slots=True)
class ReviewContribution:
    """单个 reviewer 在某一轮中的审阅贡献。"""

    id: str
    round_index: int
    agent_id: str
    role: str
    decision: ReviewDecision
    content: str
    phase: str = "cross_functional_review"
    source_backend: str = ""
    model: str = ""
    output_path: str = ""
    duration_ms: int = 0


@dataclass(slots=True)
class CollaborationDraftVersion:
    """Draft snapshot produced during a collaboration session."""

    version: int
    round_index: int
    author_agent_id: str
    content: str
    review_ids: list[str] = field(default_factory=list)
    source_backend: str = ""
    model: str = ""
    output_path: str = ""
    duration_ms: int = 0


@dataclass(slots=True)
class Collaboration:
    """串行审阅协作记录。"""

    id: str
    project_id: str
    workitem_id: str
    lead_agent_id: str
    reviewer_agent_ids: list[str]
    status: CollaborationStatus
    max_rounds: int
    current_round: int
    contributions: list[ReviewContribution] = field(default_factory=list)
    draft_versions: list[CollaborationDraftVersion] = field(default_factory=list)
    final_artifact_id: str | None = None
