"""Designer agent for backend/API planning artifacts."""

from __future__ import annotations

from conductor.agents.agent import Agent
from conductor.agents.llm import LLMBackend
from conductor.agents.profile import AgentProfile
from conductor.domain.models import Capability, WorkItem


class DesignerAgent(Agent):
    """Generate lightweight backend/API design documents."""

    def __init__(
        self,
        profile: AgentProfile | None = None,
        llm_backend: LLMBackend | None = None,
    ) -> None:
        super().__init__(
            id="agent-designer",
            role="designer",
            profile=profile,
            capabilities=[Capability.PLANNING],
            backend="mock",
            llm_backend=llm_backend,
            execution_backend=profile.execution_backend if profile else "mock",
            preferred_llm_backend=profile.preferred_backend if profile else "local",
        )

    def run(self, workitem: WorkItem) -> str:
        """Return a design artifact for the current WorkItem."""
        workitem_kind = workitem.kind or "design_overview"
        if workitem_kind == "api_design":
            return self._generate_api_design(workitem)
        if workitem_kind == "test_design":
            return self._generate_test_design(workitem)
        return self._generate_design_overview(workitem)

    def _generate_design_overview(self, workitem: WorkItem) -> str:
        return (
            "## Design Overview\n\n"
            f"WorkItem: {workitem.description}\n\n"
            "### Goals\n"
            "- Clarify backend/API scope boundaries.\n"
            "- Identify service responsibilities, data flow, and validation points.\n"
            "- Preserve non-goals and avoid client UI scope.\n\n"
            "### Deliverables\n"
            "- Backend/API design notes.\n"
            "- Data and testing considerations.\n"
        )

    def _generate_api_design(self, workitem: WorkItem) -> str:
        return (
            "## API Design\n\n"
            f"WorkItem: {workitem.description}\n\n"
            "### API Boundaries\n"
            "- Define resources, operations, request payloads, and response payloads.\n"
            "- Specify status codes and error formats.\n\n"
            "### Validation\n"
            "- Include positive, negative, and boundary cases.\n"
        )

    def _generate_test_design(self, workitem: WorkItem) -> str:
        return (
            "## Test Design\n\n"
            f"WorkItem: {workitem.description}\n\n"
            "### Coverage\n"
            "- Verify API behavior, data behavior, and acceptance criteria.\n"
            "- Record command, status code, and response payload evidence where applicable.\n"
        )


__all__ = ["DesignerAgent"]
