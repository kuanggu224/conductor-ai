"""Router，根据 WorkItem.kind 选择角色和 Agent。

支持从配置系统读取角色映射配置，而不是硬编码。
"""

from __future__ import annotations

from conductor.agents.agent import Agent
from conductor.agents.registry import AgentRegistry
from conductor.config.system import SystemConfig
from conductor.domain.models import RouteDecision, WorkItem


class Router:
    """基于 WorkItem 类型做规则路由。"""

    def __init__(
        self,
        registry: AgentRegistry,
        config: SystemConfig | None = None,
    ) -> None:
        self.registry = registry
        self.config = config or SystemConfig.load()

    def plan_roles_for_workitems(self, workitems: list[WorkItem]) -> list[str]:
        """根据 WorkItem 集合推导需要的角色。"""
        roles: list[str] = []
        for workitem in workitems:
            role = self.resolve_role(workitem)
            if role not in roles:
                roles.append(role)
        return roles

    def route(self, workitem: WorkItem) -> tuple[Agent, RouteDecision]:
        """为 WorkItem 选择 Agent。"""
        role = self.resolve_role(workitem)
        agent = self.registry.get_agent_by_role(role)
        decision = RouteDecision(workitem_id=workitem.id, selected_agent=agent.id)
        return agent, decision

    def resolve_role(self, workitem: WorkItem) -> str:
        """根据 WorkItem.kind 解析角色。"""
        return self.config.role_mapping.get_role_for_kind(workitem.kind)
