"""系统级配置管理模块。

将硬编码的系统配置（如角色映射、工作项类型配置、Agent 配置等）
抽象为可配置的系统，支持从配置文件读取或通过代码配置。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from conductor.config.defaults import CONFIG_DIR


@dataclass(slots=True)
class WorkflowConfig:
    """工作流程配置。"""

    stages: list[dict[str, str]] = field(
        default_factory=lambda: [
            {
                "name": "design",
                "objective": "明确需求并形成设计方案",
                "expected_output": "设计结论与初步实现方向",
            },
            {
                "name": "development",
                "objective": "完成需求对应的实现",
                "expected_output": "可运行的功能代码",
            },
            {
                "name": "testing",
                "objective": "验证交付结果满足要求",
                "expected_output": "测试结果与交付确认",
            },
        ]
    )


@dataclass(slots=True)
class RoleMappingConfig:
    """角色与工作项类型映射配置。"""

    workitem_kind_to_role: dict[str, str] = field(
        default_factory=lambda: {
            "design_overview": "designer",
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

    def get_role_for_kind(self, kind: str) -> str:
        """根据工作项类型获取对应的角色。"""
        return self.workitem_kind_to_role.get(kind, "backend_engineer")

    def get_kinds_for_role(self, role: str) -> list[str]:
        """根据角色获取对应的工作项类型列表。"""
        return [
            kind for kind, role_name in self.workitem_kind_to_role.items() if role_name == role
        ]


@dataclass(slots=True)
class PlannerConfig:
    """规划器配置。"""

    ui_keywords: tuple[str, ...] = ("ui", "页面", "前端", "界面", "交互")
    api_keywords: tuple[str, ...] = ("api", "接口", "服务", "后端")
    test_keywords: tuple[str, ...] = ("测试", "test", "pytest", "验证")
    data_keywords: tuple[str, ...] = ("数据", "schema", "模型", "存储")

    design_workitem_kinds: list[str] = field(
        default_factory=lambda: [
            "design_overview",
            "ui_design",
            "api_design",
            "test_design",
        ]
    )

    development_workitem_kinds: list[str] = field(
        default_factory=lambda: [
            "api_implementation",
            "data_implementation",
            "generic_implementation",
            "ui_implementation",
        ]
    )

    testing_workitem_kinds: list[str] = field(
        default_factory=lambda: [
            "acceptance_check",
            "automated_test",
            "api_validation",
            "ui_validation",
        ]
    )


@dataclass(slots=True)
class CollaborationConfig:
    """协作配置。"""

    enabled: bool = True
    max_rounds: int = 2
    lead_role_by_stage: dict[str, str] = field(
        default_factory=lambda: {"design": "designer"}
    )
    reviewer_roles_by_stage: dict[str, list[str]] = field(
        default_factory=lambda: {
            "design": ["backend_engineer", "frontend_engineer", "tester"],
        }
    )
    enabled_kinds: set[str] = field(default_factory=lambda: {"design_overview"})


@dataclass(slots=True)
class AgentConfig:
    """Agent 配置。"""

    default_profiles: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {
                "role_name": "designer",
                "mission": "Transform natural language requirements into structured design documentation and revise uniformly after collaborative review.",
                "output_contract": ["objectives", "key_assumptions", "solutions", "deliverables", "risks"],
                "review_focus": ["requirements_boundary", "non_goal_scope", "completeness_of_acceptance_criteria"],
                "revision_rules": [
                    "Revise uniformly after summarizing all reviewer comments",
                    "Do not patch locally item by item",
                ],
                "preferred_backend": "cloud",
                "allowed_collaboration_modes": ["sequential_review"],
                "execution_backend": "llm",
                "default_cli_name": None,
                "capabilities": ["planning"],
                "default_workitem_kinds": [
                    "design_overview",
                    "ui_design",
                    "api_design",
                    "test_design",
                ],
                "context_preferences": [
                    "项目目标",
                    "历史需求文档",
                    "review 汇总",
                ],
            },
            {
                "role_name": "backend_engineer",
                "mission": "从后端实现角度审阅需求和设计，并产出接口与数据结构说明。",
                "output_contract": ["接口边界", "数据结构", "关键流程", "风险"],
                "review_focus": ["接口契约", "状态流转", "错误处理", "幂等性"],
                "revision_rules": ["review 只指出问题，不直接改 draft"],
                "preferred_backend": "local",
                "allowed_collaboration_modes": ["sequential_review"],
                "execution_backend": "cli",
                "default_cli_name": "codex",
                "capabilities": ["coding", "debugging"],
                "default_workitem_kinds": [
                    "api_implementation",
                    "data_implementation",
                    "generic_implementation",
                ],
                "context_preferences": [
                    "设计文档",
                    "接口设计",
                    "历史实现说明",
                ],
            },
            {
                "role_name": "frontend_engineer",
                "mission": "从前端交互与页面实现角度审阅设计，并产出页面结构说明。",
                "output_contract": ["页面结构", "组件拆分", "状态变化", "交互风险"],
                "review_focus": [
                    "页面闭环",
                    "空状态",
                    "错误状态",
                    "交互反馈",
                ],
                "revision_rules": ["review 只给意见，不修改原始 draft"],
                "preferred_backend": "local",
                "allowed_collaboration_modes": ["sequential_review"],
                "execution_backend": "cli",
                "default_cli_name": "codex",
                "capabilities": ["coding"],
                "default_workitem_kinds": ["ui_implementation"],
                "context_preferences": [
                    "设计文档",
                    "界面设计",
                    "验收标准",
                ],
            },
            {
                "role_name": "tester",
                "mission": "从可测试性和验收视角审阅文档，并产出测试计划与验收说明。",
                "output_contract": ["测试范围", "测试用例", "验收标准", "风险"],
                "review_focus": [
                    "可测试性",
                    "边界条件",
                    "异常路径",
                    "验收口径",
                ],
                "revision_rules": ["review 专注发现模糊点和未覆盖路径"],
                "preferred_backend": "local",
                "allowed_collaboration_modes": ["sequential_review"],
                "execution_backend": "cli",
                "default_cli_name": "qwen",
                "capabilities": ["testing"],
                "default_workitem_kinds": [
                    "acceptance_check",
                    "automated_test",
                    "api_validation",
                    "ui_validation",
                ],
                "context_preferences": [
                    "设计文档",
                    "实现说明",
                    "历史测试计划",
                ],
            },
        ]
    )


@dataclass(slots=True)
class SystemConfig:
    """系统全局配置。"""

    workflow: WorkflowConfig = field(default_factory=WorkflowConfig)
    role_mapping: RoleMappingConfig = field(default_factory=RoleMappingConfig)
    planner: PlannerConfig = field(default_factory=PlannerConfig)
    collaboration: CollaborationConfig = field(default_factory=CollaborationConfig)
    agents: AgentConfig = field(default_factory=AgentConfig)

    config_path: Path = field(default_factory=lambda: CONFIG_DIR / "system.config.json")

    @classmethod
    def load(cls, config_path: Path | None = None) -> SystemConfig:
        """加载系统配置。"""
        config_path = config_path or CONFIG_DIR / "system.config.json"
        if config_path.exists():
            # 这里可以实现从 JSON 文件加载配置
            pass
        return cls()

    def save(self, config_path: Path | None = None) -> None:
        """保存系统配置。"""
        config_path = config_path or self.config_path
        config_path.parent.mkdir(parents=True, exist_ok=True)
        # 这里可以实现配置的序列化
        pass
