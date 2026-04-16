"""LLMBackend 抽象测试。"""

from conductor.agents.agent import Agent
from conductor.agents.llm import (
    HybridLLMBackend,
    LLMHTTPConfig,
    LLMProvider,
    LLMRequest,
    LocalModelHTTPBackend,
    MockCloudLLMBackend,
    MockLocalLLMBackend,
    OpenAICompatibleCloudLLMBackend,
)
from conductor.context.models import ContextPack
from conductor.domain.models import Capability, Project, ProjectStatus, SharedProjectState, WorkItem
from conductor.memory.models import GlobalMemory


def build_context_pack() -> ContextPack:
    workitem = WorkItem(id="workitem-1", description="生成方案", stage="design")
    state = SharedProjectState(
        project=Project(id="project-1", goal="实现混合大脑", current_stage="design"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="design",
        workitems=[workitem],
    )
    return ContextPack(
        current_workitem=workitem,
        relevant_state=state,
        relevant_memory=GlobalMemory(project_memory=["需求已建立"]),
        artifacts=["## 历史设计文档\n设计内容"],
    )


def test_hybrid_llm_backend_selects_cloud_for_high_quality_requests() -> None:
    hybrid = HybridLLMBackend(
        local_backend=MockLocalLLMBackend(),
        cloud_backend=MockCloudLLMBackend(),
    )

    response = hybrid.generate(
        LLMRequest(
            user_prompt="请生成高质量方案",
            metadata={"quality_tier": "high"},
        )
    )

    assert response.provider == LLMProvider.CLOUD
    assert response.backend_name == "mock_cloud"


def test_agent_think_uses_llm_backend_when_available() -> None:
    agent = Agent(
        id="agent-designer",
        role="designer",
        capabilities=[Capability.PLANNING],
        llm_backend=HybridLLMBackend(
            local_backend=MockLocalLLMBackend(),
            cloud_backend=MockCloudLLMBackend(),
        ),
    )

    result = agent.think(
        prompt="基于当前上下文生成设计建议",
        context_pack=build_context_pack(),
        preferred_backend="local",
    )

    assert "[local]" in result
    assert "workitem=workitem-1" in result


def test_agent_think_returns_fallback_when_llm_backend_missing() -> None:
    agent = Agent(id="agent-1", role="tester", capabilities=[Capability.TESTING])

    result = agent.think("执行测试分析")

    assert "未配置 LLM backend" in result


def test_openai_compatible_cloud_backend_returns_disabled_message_when_not_enabled() -> None:
    backend = OpenAICompatibleCloudLLMBackend(
        config=LLMHTTPConfig(
            base_url="https://api.example.com/v1",
            model_name="gpt-demo",
            enabled=False,
        )
    )

    response = backend.generate(LLMRequest(user_prompt="生成方案"))

    assert response.provider == LLMProvider.CLOUD
    assert "[cloud-disabled]" in response.content


def test_openai_compatible_cloud_backend_builds_chat_messages() -> None:
    backend = OpenAICompatibleCloudLLMBackend(
        config=LLMHTTPConfig(
            base_url="https://api.example.com/v1",
            model_name="gpt-demo",
            enabled=False,
        )
    )

    messages = backend._build_chat_messages(
        LLMRequest(
            user_prompt="生成设计建议",
            system_prompt="你是架构助手",
            context_pack=build_context_pack(),
        )
    )

    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "当前工作项" in messages[1]["content"]


def test_local_model_http_backend_builds_context_aware_payload() -> None:
    backend = LocalModelHTTPBackend(
        config=LLMHTTPConfig(
            base_url="http://127.0.0.1:11434/v1",
            model_name="qwen-local",
            enabled=False,
        )
    )

    payload = backend._build_generation_payload(
        LLMRequest(
            user_prompt="生成设计建议",
            context_pack=build_context_pack(),
            metadata={"latency_tier": "low"},
        )
    )

    assert payload["model"] == "qwen-local"
    assert "当前工作项" in payload["messages"][0]["content"]
    assert "历史设计文档" in payload["messages"][0]["content"]
    assert payload["metadata"]["latency_tier"] == "low"
