"""System configuration for Conductor."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from conductor.config.defaults import CONFIG_DIR

SYSTEM_CONFIG_PATH = CONFIG_DIR / "system.config.json"
_DISABLED_CLIENT_KINDS = {"ui" + "_design", "ui" + "_implementation", "ui" + "_validation"}
_DISABLED_CLIENT_ROLE = "front" + "end_engineer"


def _default_workflow_stages() -> list[dict[str, str]]:
    return [
        {"name": "requirement", "objective": "Clarify and freeze executable requirements.", "expected_output": "Frozen requirement specification."},
        {"name": "design", "objective": "Define backend/API implementation boundaries.", "expected_output": "Backend/API design notes."},
        {"name": "development", "objective": "Implement backend/API workitems.", "expected_output": "Backend/API code changes or implementation notes."},
        {"name": "testing", "objective": "Validate backend/API delivery.", "expected_output": "Validation results and acceptance conclusion."},
    ]


def _default_role_mapping() -> dict[str, str]:
    return {
        "requirement_spec": "requirement_designer",
        "design_overview": "designer",
        "feature_slice_plan": "designer",
        "api_design": "designer",
        "test_design": "designer",
        "api_implementation": "backend_engineer",
        "data_implementation": "backend_engineer",
        "generic_implementation": "backend_engineer",
        "acceptance_check": "tester",
        "automated_test": "tester",
        "api_validation": "tester",
    }


def _default_planner_keywords() -> dict[str, tuple[str, ...]]:
    return {
        "api_keywords": ("api", "接口", "服务", "后端", "endpoint", "http"),
        "test_keywords": ("测试", "test", "pytest", "验证"),
        "data_keywords": ("数据", "schema", "模型", "存储", "数据库", "sqlite"),
    }


def _default_agent_profiles() -> list[dict[str, Any]]:
    return [
        {
            "role_name": "designer",
            "mission": "Turn requirements into backend/API design boundaries.",
            "output_contract": ["design notes", "assumptions", "risks"],
            "review_focus": ["requirement completeness", "implementation feasibility", "scope control"],
            "revision_rules": ["clarify missing information", "do not expand unrelated scope"],
            "preferred_backend": "local",
            "allowed_collaboration_modes": ["sequential_review", "human_review"],
            "execution_backend": "cli",
            "default_cli_name": "codex",
            "capabilities": ["planning"],
            "default_workitem_kinds": ["requirement_spec", "design_overview", "feature_slice_plan", "api_design", "test_design"],
            "context_preferences": ["requirements", "recent_state"],
        },
        {
            "role_name": "requirement_designer",
            "mission": "Review user goals, requirement clarity, scope boundaries, and business rules.",
            "output_contract": ["requirement review", "scope risks", "acceptance suggestions"],
            "review_focus": ["user goal fit", "scope clarity", "verifiable business rules"],
            "revision_rules": ["clarify scope boundaries", "avoid premature implementation detail"],
            "preferred_backend": "local",
            "allowed_collaboration_modes": ["design_peer_review"],
            "execution_backend": "cli",
            "default_cli_name": "codex",
            "capabilities": ["planning"],
            "default_workitem_kinds": ["requirement_spec", "design_overview"],
            "context_preferences": ["requirements", "design_output"],
        },
        {
            "role_name": "solution_designer",
            "mission": "Review solution consistency, information structure, flow completeness, and validation feasibility.",
            "output_contract": ["solution review", "flow gaps", "validation suggestions"],
            "review_focus": ["solution coherence", "flow completeness", "testable acceptance"],
            "revision_rules": ["fill flow and edge-case gaps", "make abstract acceptance concrete"],
            "preferred_backend": "local",
            "allowed_collaboration_modes": ["design_peer_review"],
            "execution_backend": "cli",
            "default_cli_name": "codex",
            "capabilities": ["planning"],
            "default_workitem_kinds": ["design_overview"],
            "context_preferences": ["requirements", "design_output"],
        },
        {
            "role_name": "backend_engineer",
            "mission": "Implement backend logic, service APIs, and data processing.",
            "output_contract": ["implementation notes", "key APIs", "changed files"],
            "review_focus": ["API contract", "data flow", "error handling"],
            "revision_rules": ["fix failing tests first", "avoid broad refactors"],
            "preferred_backend": "local",
            "allowed_collaboration_modes": ["sequential_review"],
            "execution_backend": "cli",
            "default_cli_name": "codex",
            "capabilities": ["coding", "debugging"],
            "default_workitem_kinds": ["api_implementation", "data_implementation", "generic_implementation"],
            "context_preferences": ["design_output", "tests"],
        },
        {
            "role_name": "tester",
            "mission": "Validate deliverables and produce acceptance conclusions.",
            "output_contract": ["test plan", "test results", "acceptance conclusion"],
            "review_focus": ["coverage", "regression risk", "acceptance criteria"],
            "revision_rules": ["cover missing scenarios first", "make results reproducible"],
            "preferred_backend": "local",
            "allowed_collaboration_modes": ["sequential_review"],
            "execution_backend": "cli",
            "default_cli_name": "codex",
            "capabilities": ["testing"],
            "default_workitem_kinds": ["acceptance_check", "automated_test", "api_validation"],
            "context_preferences": ["implementation_output", "acceptance_criteria"],
        },
    ]


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: _jsonable(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted(_jsonable(item) for item in value)
    if isinstance(value, Path):
        return str(value)
    return value


@dataclass(slots=True)
class WorkflowConfig:
    stages: list[dict[str, str]] = field(default_factory=_default_workflow_stages)


@dataclass(slots=True)
class PlannerConfig:
    api_keywords: tuple[str, ...] = field(default_factory=lambda: _default_planner_keywords()["api_keywords"])
    test_keywords: tuple[str, ...] = field(default_factory=lambda: _default_planner_keywords()["test_keywords"])
    data_keywords: tuple[str, ...] = field(default_factory=lambda: _default_planner_keywords()["data_keywords"])
    requirement_workitem_kinds: list[str] = field(default_factory=lambda: ["requirement_spec"])
    design_workitem_kinds: list[str] = field(default_factory=lambda: ["design_overview", "feature_slice_plan", "api_design", "test_design"])
    development_workitem_kinds: list[str] = field(default_factory=lambda: ["api_implementation", "data_implementation", "generic_implementation"])
    testing_workitem_kinds: list[str] = field(default_factory=lambda: ["acceptance_check", "automated_test", "api_validation"])


@dataclass(slots=True)
class CollaborationConfig:
    enabled: bool = True
    max_rounds: int = 2
    lead_role_by_stage: dict[str, str] = field(default_factory=lambda: {"requirement": "requirement_designer", "design": "designer"})
    lead_role_by_kind: dict[str, str] = field(default_factory=_default_role_mapping)
    peer_reviewer_roles_by_stage: dict[str, list[str]] = field(
        default_factory=lambda: {
            "requirement": ["designer", "solution_designer"],
            "design": ["requirement_designer", "solution_designer"],
            "development": ["backend_engineer"],
            "testing": ["tester"],
        }
    )
    reviewer_roles_by_stage: dict[str, list[str]] = field(
        default_factory=lambda: {
            "requirement": ["backend_engineer", "tester"],
            "design": ["backend_engineer", "tester"],
            "development": ["solution_designer", "tester"],
            "testing": ["backend_engineer", "solution_designer"],
        }
    )
    enabled_kinds: set[str] = field(default_factory=lambda: {"requirement_spec", "design_overview"})
    dynamic_requirement_review_enabled: bool = True


@dataclass(slots=True)
class RoleMappingConfig:
    kind_to_role: dict[str, str] = field(default_factory=_default_role_mapping)
    default_role: str = "backend_engineer"

    def __post_init__(self) -> None:
        self.kind_to_role = dict(self.kind_to_role)

    @property
    def workitem_kind_to_role(self) -> dict[str, str]:
        return self.kind_to_role

    @workitem_kind_to_role.setter
    def workitem_kind_to_role(self, value: dict[str, str]) -> None:
        self.kind_to_role = dict(value)

    def get_role_for_kind(self, kind: str) -> str:
        return self.kind_to_role.get(kind, self.default_role)

    def get_kinds_for_role(self, role: str) -> list[str]:
        return [kind for kind, mapped_role in self.kind_to_role.items() if mapped_role == role]


@dataclass(slots=True)
class AgentsConfig:
    default_profiles: list[dict[str, Any]] = field(default_factory=_default_agent_profiles)


AgentConfig = AgentsConfig


@dataclass(slots=True)
class SystemConfig:
    workflow: WorkflowConfig = field(default_factory=WorkflowConfig)
    role_mapping: RoleMappingConfig = field(default_factory=RoleMappingConfig)
    planner: PlannerConfig = field(default_factory=PlannerConfig)
    collaboration: CollaborationConfig = field(default_factory=CollaborationConfig)
    agents: AgentsConfig = field(default_factory=AgentsConfig)
    config_path: Path = field(default_factory=lambda: SYSTEM_CONFIG_PATH)

    @classmethod
    def load(cls, config_path: str | Path | None = None, *, path: str | Path | None = None) -> "SystemConfig":
        resolved_path = path if path is not None else config_path
        config_path_obj = Path(resolved_path) if resolved_path is not None else SYSTEM_CONFIG_PATH
        if not config_path_obj.exists():
            return cls(config_path=config_path_obj)
        with config_path_obj.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        return cls._from_payload(payload, config_path=config_path_obj)

    @classmethod
    def _from_payload(cls, payload: dict[str, Any], *, config_path: str | Path | None = None) -> "SystemConfig":
        defaults = cls(config_path=Path(config_path) if config_path is not None else SYSTEM_CONFIG_PATH)
        workflow_section = payload.get("workflow", {})
        planner_section = payload.get("planner", {})
        collaboration_section = payload.get("collaboration", {})
        role_mapping_section = payload.get("role_mapping", {})
        agents_section = payload.get("agents", {})
        keywords = _default_planner_keywords()

        raw_kind_to_role = role_mapping_section.get("kind_to_role")
        if raw_kind_to_role is None:
            raw_kind_to_role = role_mapping_section.get("workitem_kind_to_role", _default_role_mapping())
        kind_to_role = {
            kind: role
            for kind, role in dict(raw_kind_to_role).items()
            if kind not in _DISABLED_CLIENT_KINDS and role != _DISABLED_CLIENT_ROLE
        }
        for kind, role in _default_role_mapping().items():
            kind_to_role.setdefault(kind, role)

        peer_reviewers = _filter_disabled_client_roles(
            dict(collaboration_section.get("peer_reviewer_roles_by_stage", defaults.collaboration.peer_reviewer_roles_by_stage))
        )
        functional_reviewers = _filter_disabled_client_roles(
            dict(collaboration_section.get("reviewer_roles_by_stage", defaults.collaboration.reviewer_roles_by_stage))
        )
        enabled_kinds = {
            str(kind)
            for kind in collaboration_section.get("enabled_kinds", defaults.collaboration.enabled_kinds)
            if str(kind) not in _DISABLED_CLIENT_KINDS
        }
        enabled_kinds.add("requirement_spec")

        profiles = [
            profile
            for profile in list(agents_section.get("default_profiles", _default_agent_profiles()))
            if profile.get("role_name") != _DISABLED_CLIENT_ROLE
        ]

        return cls(
            workflow=WorkflowConfig(stages=_with_requirement_stage(list(workflow_section.get("stages", _default_workflow_stages())))),
            role_mapping=RoleMappingConfig(
                kind_to_role=kind_to_role,
                default_role=str(role_mapping_section.get("default_role", "backend_engineer")),
            ),
            planner=PlannerConfig(
                api_keywords=tuple(planner_section.get("api_keywords", keywords["api_keywords"])),
                test_keywords=tuple(planner_section.get("test_keywords", keywords["test_keywords"])),
                data_keywords=tuple(planner_section.get("data_keywords", keywords["data_keywords"])),
                requirement_workitem_kinds=_filter_disabled_client_kinds(planner_section.get("requirement_workitem_kinds", defaults.planner.requirement_workitem_kinds)),
                design_workitem_kinds=_filter_disabled_client_kinds(planner_section.get("design_workitem_kinds", defaults.planner.design_workitem_kinds)),
                development_workitem_kinds=_filter_disabled_client_kinds(planner_section.get("development_workitem_kinds", defaults.planner.development_workitem_kinds)),
                testing_workitem_kinds=_filter_disabled_client_kinds(planner_section.get("testing_workitem_kinds", defaults.planner.testing_workitem_kinds)),
            ),
            collaboration=CollaborationConfig(
                enabled=bool(collaboration_section.get("enabled", True)),
                max_rounds=int(collaboration_section.get("max_rounds", 2)),
                lead_role_by_stage=dict(collaboration_section.get("lead_role_by_stage", defaults.collaboration.lead_role_by_stage)),
                lead_role_by_kind={
                    kind: role
                    for kind, role in dict(collaboration_section.get("lead_role_by_kind", kind_to_role)).items()
                    if kind not in _DISABLED_CLIENT_KINDS and role != _DISABLED_CLIENT_ROLE
                },
                peer_reviewer_roles_by_stage=peer_reviewers,
                reviewer_roles_by_stage=functional_reviewers,
                enabled_kinds=enabled_kinds,
                dynamic_requirement_review_enabled=bool(collaboration_section.get("dynamic_requirement_review_enabled", True)),
            ),
            agents=AgentsConfig(default_profiles=profiles),
            config_path=Path(config_path) if config_path is not None else config_path_obj,
        )

    def save(self, config_path: str | Path | None = None) -> Path:
        resolved_path = Path(config_path) if config_path is not None else self.config_path
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        payload = _jsonable(self)
        payload.pop("config_path", None)
        payload["role_mapping"]["workitem_kind_to_role"] = payload["role_mapping"]["kind_to_role"]
        payload["collaboration"]["enabled_kinds"] = sorted(payload["collaboration"]["enabled_kinds"])
        with resolved_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        return resolved_path


def _filter_disabled_client_kinds(kinds: Any) -> list[str]:
    return [str(kind) for kind in list(kinds) if str(kind) not in _DISABLED_CLIENT_KINDS]


def _filter_disabled_client_roles(mapping: dict[str, list[str]]) -> dict[str, list[str]]:
    return {stage: [role for role in roles if role != _DISABLED_CLIENT_ROLE] for stage, roles in mapping.items()}


def _with_requirement_stage(stages: list[dict[str, str]]) -> list[dict[str, str]]:
    if any(stage.get("name") == "requirement" for stage in stages):
        return stages
    return [_default_workflow_stages()[0], *stages]


__all__ = [
    "AgentConfig",
    "AgentsConfig",
    "CollaborationConfig",
    "PlannerConfig",
    "RoleMappingConfig",
    "SYSTEM_CONFIG_PATH",
    "SystemConfig",
    "WorkflowConfig",
]
