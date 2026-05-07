"""多 Agent 协作策略。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class CollaborationPolicy:
    """串行审阅协作配置。"""

    enabled: bool = True
    max_rounds: int = 2
    lead_role_by_stage: dict[str, str] = field(default_factory=lambda: {"design": "designer"})
    peer_reviewer_roles_by_stage: dict[str, list[str]] = field(
        default_factory=lambda: {
            "design": ["requirement_designer", "solution_designer"],
        }
    )
    reviewer_roles_by_stage: dict[str, list[str]] = field(
        default_factory=lambda: {
            "design": ["backend_engineer", "frontend_engineer", "tester"],
        }
    )
    enabled_kinds: set[str] = field(default_factory=lambda: {"design_overview"})
