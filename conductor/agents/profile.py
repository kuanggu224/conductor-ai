"""Agent 角色规格定义。

支持从配置系统读取 Agent 配置，而不是硬编码。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from conductor.config.system import SystemConfig
from conductor.domain.models import Capability


@dataclass(slots=True)
class AgentProfile:
    """描述一个 Agent 角色的完整规格。"""

    role_name: str
    mission: str
    output_contract: list[str] = field(default_factory=list)
    review_focus: list[str] = field(default_factory=list)
    revision_rules: list[str] = field(default_factory=list)
    preferred_backend: str = "local"
    allowed_collaboration_modes: list[str] = field(default_factory=list)
    execution_backend: str = "mock"
    default_cli_name: str | None = None
    capabilities: list[Capability] = field(default_factory=list)
    default_workitem_kinds: list[str] = field(default_factory=list)
    context_preferences: list[str] = field(default_factory=list)


def build_default_agent_profiles(config: SystemConfig | None = None) -> list[AgentProfile]:
    """构建默认角色规格集合。

    Args:
        config: 系统配置对象，如果为 None 则使用默认配置。
    """
    config = config or SystemConfig.load()
    profiles = []

    for profile_config in config.agents.default_profiles:
        # 转换 capability 字符串为枚举类型
        capabilities = [
            Capability[capability.upper()]
            for capability in profile_config.get("capabilities", [])
            if capability.upper() in Capability.__members__
        ]

        profiles.append(
            AgentProfile(
                role_name=profile_config["role_name"],
                mission=profile_config["mission"],
                output_contract=profile_config.get("output_contract", []),
                review_focus=profile_config.get("review_focus", []),
                revision_rules=profile_config.get("revision_rules", []),
                preferred_backend=profile_config.get("preferred_backend", "local"),
                allowed_collaboration_modes=profile_config.get("allowed_collaboration_modes", []),
                execution_backend=profile_config.get("execution_backend", "mock"),
                default_cli_name=profile_config.get("default_cli_name"),
                capabilities=capabilities,
                default_workitem_kinds=profile_config.get("default_workitem_kinds", []),
                context_preferences=profile_config.get("context_preferences", []),
            )
        )

    return profiles
