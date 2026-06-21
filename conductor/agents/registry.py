"""AgentRegistry，负责维护可用 Agent。"""

from __future__ import annotations

from dataclasses import dataclass, field

from conductor.agents.agent import Agent
from conductor.agents.llm import LLMBackend
from conductor.agents.profile import AgentProfile, build_default_agent_profiles
from conductor.config.system import SystemConfig


@dataclass(slots=True)
class AgentRegistry:
    """最小 Agent 注册表。"""

    agents: list[Agent] = field(default_factory=list)
    profiles: list[AgentProfile] = field(default_factory=list)
    llm_backend: LLMBackend | None = None
    config: SystemConfig | None = None

    def __post_init__(self) -> None:
        """在未传入 agents 时注入默认角色集合。"""
        if not self.agents:
            self.profiles = self.profiles or build_default_agent_profiles(self.config)
            self.agents = [self._build_agent(profile) for profile in self.profiles]
        elif not self.profiles:
            self.profiles = [agent.profile for agent in self.agents if agent.profile is not None]

    def get_agent_by_role(self, role: str) -> Agent:
        """按角色名获取 Agent。"""
        for agent in self.agents:
            if agent.role == role:
                return agent
        raise KeyError(f"未找到角色对应的 Agent: {role}")

    def get_agent_by_id(self, agent_id: str) -> Agent:
        """Return an Agent by concrete runtime id."""
        for agent in self.agents:
            if agent.id == agent_id:
                return agent
        raise KeyError(f"Agent not found: {agent_id}")

    def register_dynamic_agent(
        self,
        profile: AgentProfile,
        *,
        agent_id: str,
        base_role: str | None = None,
    ) -> Agent:
        """Register or return a runtime-generated Agent instance."""
        for agent in self.agents:
            if agent.id == agent_id:
                return agent
        agent = Agent(
            id=agent_id,
            role=base_role or profile.role_name,
            profile=profile,
            capabilities=profile.capabilities,
            backend="mock",
            llm_backend=self.llm_backend,
            execution_backend=profile.execution_backend,
            preferred_llm_backend=profile.preferred_backend,
        )
        self.agents.append(agent)
        self.profiles.append(profile)
        return agent

    def list_roles(self) -> list[str]:
        """返回当前可用角色列表。"""
        return [agent.role for agent in self.agents]

    def get_profile_by_role(self, role: str) -> AgentProfile:
        """按角色获取角色规格。"""
        for profile in self.profiles:
            if profile.role_name == role:
                return profile
        raise KeyError(f"未找到角色规格: {role}")

    def _build_agent(self, profile: AgentProfile) -> Agent:
        """根据角色规格创建默认 Agent。"""
        agent_id = {
            "designer": "agent-designer",
            "requirement_designer": "agent-requirement-designer",
            "solution_designer": "agent-solution-designer",
            "backend_engineer": "agent-backend",
            "tester": "agent-tester",
        }.get(profile.role_name, f"agent-{profile.role_name}")

        if profile.role_name == "designer":
            from conductor.agents.designer import DesignerAgent
            return DesignerAgent(profile=profile, llm_backend=self.llm_backend)
        else:
            return Agent(
                id=agent_id,
                role=profile.role_name,
                profile=profile,
                capabilities=profile.capabilities,
                backend="mock",
                llm_backend=self.llm_backend,
                execution_backend=profile.execution_backend,
                preferred_llm_backend=profile.preferred_backend,
            )
