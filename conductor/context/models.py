"""Context 相关最小数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field

from conductor.domain.models import SharedProjectState, WorkItem
from conductor.memory.models import GlobalMemory


@dataclass(slots=True)
class ContextPack:
    """按需组装的最小上下文。"""

    current_workitem: WorkItem | None
    relevant_state: SharedProjectState | None
    relevant_memory: GlobalMemory | None
    artifacts: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
