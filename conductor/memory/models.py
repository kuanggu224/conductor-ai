"""Memory 相关最小数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class GlobalMemory:
    """全局记忆容器。"""

    project_memory: list[str] = field(default_factory=list)
    decision_memory: list[str] = field(default_factory=list)
    artifact_memory: list[str] = field(default_factory=list)
    agent_memory: dict[str, list[str]] = field(default_factory=dict)
