"""System configuration for Conductor."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from conductor.config.defaults import CONFIG_DIR

SYSTEM_CONFIG_PATH = CONFIG_DIR / "system.config.json"


def _default_workflow_stages() -> list[dict[str, str]]:
    return [
        {
            "name": "requirement",
            "objective": "澄清需求、收敛范围并冻结可执行需求规格",
            "expected_output": "冻结需求规格、验收标准、范围边界和风险假设",
        },
        {
            "name": "design",
            "objective": "把需求拆解成可实施方案",
            "expected_output": "设计说明和实现边界",
        },
        {
            "name": "development",
            "objective": "实现设计中定义的工作项",
            "expected_output": "代码变更或实现说明",
        },
        {
            "name": "testing",
            "objective": "验证交付结果是否满足要求",
            "expected_output": "测试结论和验收结论",
        },
    ]


def _default_role_mapping() -> dict[str, str]:
    return {
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


def _default_planner_keywords() -> dict[str, tuple[str, ...]]:
    return {
        "ui_keywords": ("ui", "页面", "前端", "界面", "交互"),
        "api_keywords": ("api", "接口", "服务", "后端"),
        "test_keywords": ("测试", "test", "pytest", "验证"),
        "data_keywords": ("数据", "schema", "模型", "存储"),
    }


def _default_agent_profiles() -> list[dict[str, Any]]:
    return [
        {
            "role_name": "designer",
            "mission": "把需求转成清晰的设计方案和实现边界。",
            "output_contract": ["设计说明", "关键假设", "风险与边界"],
            "review_focus": ["需求完整性", "实现可行性", "范围控制"],
            "revision_rules": ["优先补齐缺失信息", "不要扩展到无关范围"],
            "preferred_backend": "local",
            "allowed_collaboration_modes": ["sequential_review", "human_review"],
            "execution_backend": "cli",
            "default_cli_name": "codex",
            "capabilities": ["planning"],
            "default_workitem_kinds": ["requirement_spec", "design_overview", "feature_slice_plan", "ui_design", "api_design", "test_design"],
            "context_preferences": ["requirements", "recent_state"],
        },
        {
            "role_name": "requirement_designer",
            "mission": "从用户目标、需求合理性、范围边界和业务规则角度审阅需求设计。",
            "output_contract": ["需求审阅意见", "范围风险", "可执行验收建议"],
            "review_focus": ["需求是否符合用户目标", "范围是否清晰", "业务规则是否可验证"],
            "revision_rules": ["优先收敛需求边界", "避免过早进入技术实现细节"],
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
            "mission": "从方案一致性、信息结构、流程完整性和交付可验收性角度审阅设计。",
            "output_contract": ["方案审阅意见", "流程缺口", "验收断言建议"],
            "review_focus": ["方案是否自洽", "流程是否完整", "验收标准是否能驱动测试"],
            "revision_rules": ["补齐流程和异常路径", "把抽象验收转成具体断言"],
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
            "mission": "实现后端逻辑、服务接口和数据处理。",
            "output_contract": ["实现说明", "关键接口", "变更文件"],
            "review_focus": ["接口契约", "数据流", "错误处理"],
            "revision_rules": ["优先修复失败测试", "避免大范围重构"],
            "preferred_backend": "local",
            "allowed_collaboration_modes": ["sequential_review"],
            "execution_backend": "cli",
            "default_cli_name": "codex",
            "capabilities": ["coding", "debugging"],
            "default_workitem_kinds": ["api_implementation", "data_implementation", "generic_implementation"],
            "context_preferences": ["design_output", "tests"],
        },
        {
            "role_name": "frontend_engineer",
            "mission": "实现前端界面、交互和状态展示。",
            "output_contract": ["实现说明", "交互说明", "变更文件"],
            "review_focus": ["界面结构", "交互流程", "状态更新"],
            "revision_rules": ["先保持简单", "避免不必要的框架调整"],
            "preferred_backend": "local",
            "allowed_collaboration_modes": ["sequential_review"],
            "execution_backend": "cli",
            "default_cli_name": "codex",
            "capabilities": ["coding"],
            "default_workitem_kinds": ["ui_implementation"],
            "context_preferences": ["ui_design", "design_output"],
        },
        {
            "role_name": "tester",
            "mission": "验证交付物并给出验收结论。",
            "output_contract": ["测试计划", "测试结果", "验收结论"],
            "review_focus": ["覆盖范围", "回归风险", "验收标准"],
            "revision_rules": ["优先补齐未覆盖场景", "结果要可复现"],
            "preferred_backend": "local",
            "allowed_collaboration_modes": ["sequential_review"],
            "execution_backend": "cli",
            "default_cli_name": "codex",
            "capabilities": ["testing"],
            "default_workitem_kinds": ["acceptance_check", "automated_test", "api_validation", "ui_validation"],
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
    ui_keywords: tuple[str, ...] = field(default_factory=lambda: _default_planner_keywords()["ui_keywords"])
    api_keywords: tuple[str, ...] = field(default_factory=lambda: _default_planner_keywords()["api_keywords"])
    test_keywords: tuple[str, ...] = field(default_factory=lambda: _default_planner_keywords()["test_keywords"])
    data_keywords: tuple[str, ...] = field(default_factory=lambda: _default_planner_keywords()["data_keywords"])
    requirement_workitem_kinds: list[str] = field(default_factory=lambda: ["requirement_spec"])
    design_workitem_kinds: list[str] = field(default_factory=lambda: ["design_overview", "feature_slice_plan", "ui_design", "api_design", "test_design"])
    development_workitem_kinds: list[str] = field(default_factory=lambda: ["api_implementation", "data_implementation", "generic_implementation", "ui_implementation"])
    testing_workitem_kinds: list[str] = field(default_factory=lambda: ["acceptance_check", "automated_test", "api_validation", "ui_validation"])


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
        workflow_section = payload.get("workflow", {})
        planner_section = payload.get("planner", {})
        collaboration_section = payload.get("collaboration", {})
        role_mapping_section = payload.get("role_mapping", {})
        agents_section = payload.get("agents", {})
        keywords = _default_planner_keywords()

        kind_to_role = role_mapping_section.get("kind_to_role")
        if kind_to_role is None:
            kind_to_role = role_mapping_section.get("workitem_kind_to_role", _default_role_mapping())
        kind_to_role = dict(kind_to_role)
        kind_to_role.setdefault("requirement_spec", "requirement_designer")
        workflow_stages = _with_requirement_stage(list(workflow_section.get("stages", _default_workflow_stages())))
        lead_roles = dict(collaboration_section.get("lead_role_by_stage", {"design": "designer"}))
        lead_roles.setdefault("requirement", "requirement_designer")
        lead_roles_by_kind = dict(collaboration_section.get("lead_role_by_kind", kind_to_role))
        peer_reviewers = dict(
            collaboration_section.get(
                "peer_reviewer_roles_by_stage",
                {"design": ["requirement_designer", "solution_designer"]},
            )
        )
        peer_reviewers.setdefault("requirement", ["designer", "solution_designer"])
        peer_reviewers.setdefault("development", ["backend_engineer", "frontend_engineer"])
        peer_reviewers.setdefault("testing", ["tester"])
        functional_reviewers = dict(
            collaboration_section.get(
                "reviewer_roles_by_stage",
                {"design": ["backend_engineer", "frontend_engineer", "tester"]},
            )
        )
        functional_reviewers.setdefault("requirement", ["backend_engineer", "frontend_engineer", "tester"])
        functional_reviewers.setdefault("development", ["solution_designer", "tester"])
        functional_reviewers.setdefault("testing", ["backend_engineer", "frontend_engineer", "solution_designer"])
        enabled_kinds = set(collaboration_section.get("enabled_kinds", {"design_overview"}))
        enabled_kinds.add("requirement_spec")

        return cls(
            workflow=WorkflowConfig(stages=workflow_stages),
            role_mapping=RoleMappingConfig(
                kind_to_role=kind_to_role,
                default_role=str(role_mapping_section.get("default_role", "backend_engineer")),
            ),
            planner=PlannerConfig(
                ui_keywords=tuple(planner_section.get("ui_keywords", keywords["ui_keywords"])),
                api_keywords=tuple(planner_section.get("api_keywords", keywords["api_keywords"])),
                test_keywords=tuple(planner_section.get("test_keywords", keywords["test_keywords"])),
                data_keywords=tuple(planner_section.get("data_keywords", keywords["data_keywords"])),
                requirement_workitem_kinds=list(planner_section.get("requirement_workitem_kinds", PlannerConfig().requirement_workitem_kinds)),
                design_workitem_kinds=list(planner_section.get("design_workitem_kinds", PlannerConfig().design_workitem_kinds)),
                development_workitem_kinds=list(planner_section.get("development_workitem_kinds", PlannerConfig().development_workitem_kinds)),
                testing_workitem_kinds=list(planner_section.get("testing_workitem_kinds", PlannerConfig().testing_workitem_kinds)),
            ),
            collaboration=CollaborationConfig(
                enabled=bool(collaboration_section.get("enabled", True)),
                max_rounds=int(collaboration_section.get("max_rounds", 2)),
                lead_role_by_stage=lead_roles,
                lead_role_by_kind=lead_roles_by_kind,
                peer_reviewer_roles_by_stage=peer_reviewers,
                reviewer_roles_by_stage=functional_reviewers,
                enabled_kinds=enabled_kinds,
                dynamic_requirement_review_enabled=bool(
                    collaboration_section.get("dynamic_requirement_review_enabled", True)
                ),
            ),
            agents=AgentsConfig(default_profiles=list(agents_section.get("default_profiles", _default_agent_profiles()))),
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


def _with_requirement_stage(stages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return workflow stages with the formal requirement stage inserted first."""
    if any(stage.get("name") == "requirement" for stage in stages):
        return stages
    requirement_stage = {
        "name": "requirement",
        "objective": "澄清需求、收敛范围并冻结可执行需求规格",
        "expected_output": "冻结需求规格、验收标准、范围边界和风险假设",
    }
    return [requirement_stage, *stages]


__all__ = [
    "AgentConfig",
    "AgentsConfig",
    "CollaborationConfig",
    "PlannerConfig",
    "RoleMappingConfig",
    "SystemConfig",
    "WorkflowConfig",
]
