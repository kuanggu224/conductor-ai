"""多 Agent 协作策略。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class CollaborationPolicy:
    """串行审阅协作配置。"""

    enabled: bool = True
    max_rounds: int = 2
    lead_role_by_stage: dict[str, str] = field(default_factory=lambda: {"requirement": "requirement_designer", "design": "designer"})
    lead_role_by_kind: dict[str, str] = field(
        default_factory=lambda: {
            "requirement_spec": "requirement_designer",
            "design_overview": "designer",
            "feature_slice_plan": "designer",
            "ui_design": "designer",
            "api_design": "designer",
            "test_design": "designer",
            "api_implementation": "backend_engineer",
            "data_implementation": "backend_engineer",
            "generic_implementation": "backend_engineer",
            "ui_implementation": "frontend_engineer",
            "acceptance_check": "tester",
            "automated_test": "tester",
            "api_validation": "tester",
            "ui_validation": "tester",
        }
    )
    peer_reviewer_roles_by_stage: dict[str, list[str]] = field(
        default_factory=lambda: {
            "requirement": ["designer", "solution_designer"],
            "design": ["requirement_designer", "solution_designer"],
            "development": ["backend_engineer", "frontend_engineer"],
            "testing": ["tester"],
        }
    )
    reviewer_roles_by_stage: dict[str, list[str]] = field(
        default_factory=lambda: {
            "requirement": ["backend_engineer", "frontend_engineer", "tester"],
            "design": ["backend_engineer", "frontend_engineer", "tester"],
            "development": ["solution_designer", "tester"],
            "testing": ["backend_engineer", "frontend_engineer", "solution_designer"],
        }
    )
    enabled_kinds: set[str] = field(default_factory=lambda: {"requirement_spec", "design_overview"})
    dynamic_requirement_review_enabled: bool = True
