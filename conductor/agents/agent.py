"""Agent 抽象定义。"""

from __future__ import annotations

from dataclasses import dataclass, field

from conductor.agents.llm import LLMBackend, LLMRequest
from conductor.agents.profile import AgentProfile
from conductor.context.models import ContextPack
from conductor.domain.models import Capability, WorkItem


@dataclass(slots=True)
class Agent:
    """最小 Agent 抽象。"""

    id: str
    role: str
    profile: AgentProfile | None = None
    capabilities: list[Capability] = field(default_factory=list)
    backend: str = "mock"
    llm_backend: LLMBackend | None = None
    execution_backend: str = "mock"
    preferred_llm_backend: str = "local"

    @property
    def mission(self) -> str:
        """返回角色任务描述。"""
        return self.profile.mission if self.profile else ""

    def execute(self, workitem: WorkItem) -> str:
        """执行 WorkItem 并返回 mock 结果。"""
        return f"[{self.role}] 已处理工作项 {workitem.id}: {workitem.description}"

    def think(self, prompt: str, context_pack: ContextPack | None = None, preferred_backend: str | None = None) -> str:
        """通过可选 LLM backend 执行一次推理。"""
        if self.llm_backend is None:
            return f"[{self.role}] 当前未配置 LLM backend: {prompt}"
        response = self.llm_backend.generate(
            LLMRequest(
                user_prompt=prompt,
                context_pack=context_pack,
                preferred_backend=preferred_backend,
            )
        )
        return response.content
