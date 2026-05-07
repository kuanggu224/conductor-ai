"""Router 与 AgentRegistry 测试。"""

from conductor.agents.registry import AgentRegistry
from conductor.domain.models import WorkItem
from conductor.execution.router import Router


def test_router_maps_workitem_kind_to_agent_role() -> None:
    registry = AgentRegistry()
    router = Router(registry)

    agent, decision = router.route(
        WorkItem(id="workitem-1", description="实现接口", stage="development", kind="api_implementation")
    )

    assert agent.role == "backend_engineer"
    assert decision.selected_agent == "agent-backend"


def test_registry_exposes_default_roles() -> None:
    registry = AgentRegistry()

    assert registry.list_roles() == [
        "designer",
        "requirement_designer",
        "solution_designer",
        "backend_engineer",
        "frontend_engineer",
        "tester",
    ]
    assert all(agent.llm_backend is None for agent in registry.agents)
    assert registry.get_profile_by_role("designer").mission
    assert "sequential_review" in registry.get_profile_by_role("designer").allowed_collaboration_modes


def test_registry_can_attach_shared_llm_backend() -> None:
    from conductor.agents.llm import HybridLLMBackend, MockCloudLLMBackend, MockLocalLLMBackend

    registry = AgentRegistry(
        llm_backend=HybridLLMBackend(
            local_backend=MockLocalLLMBackend(),
            cloud_backend=MockCloudLLMBackend(),
        )
    )

    assert all(agent.llm_backend is not None for agent in registry.agents)
