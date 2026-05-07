"""Controlled LLM code-generation path tests."""

import sys

from conductor.agents.agent import Agent
from conductor.agents.llm import LLMHTTPConfig
from conductor.agents.profile import build_default_agent_profiles
from conductor.domain.models import Artifact, Capability, Project, ProjectStatus, SharedProjectState, WorkItem, WorkItemStatus
from conductor.execution.runner import Runner
from conductor.harness.llm import LLMHarnessResult
from conductor.state.store import InMemoryStateStore


class FakeCodeLLMHarness:
    name = "llm"

    def __init__(self, content: str) -> None:
        self.content = content
        self.last_prompt = ""

    def run(self, request):
        self.last_prompt = request.prompt
        return LLMHarnessResult(
            success=True,
            content=self.content,
            duration_ms=10,
            model_name=request.config.model_name,
            output_path=None,
        )


def test_llm_code_harness_reads_design_context_and_writes_files(tmp_path) -> None:
    state_store = InMemoryStateStore()
    project_id = "project-code"
    design_workitem = WorkItem(
        id="workitem-001",
        description="Produce design",
        stage="design",
        kind="design_overview",
        status=WorkItemStatus.DONE,
    )
    code_workitem = WorkItem(
        id="workitem-002",
        description="Implement the expense tracker UI",
        stage="development",
        kind="ui_implementation",
        dependencies=[design_workitem.id],
        acceptance_criteria=["UI file exists"],
    )
    design_artifact = Artifact(
        id="artifact-design",
        project_id=project_id,
        workitem_id=design_workitem.id,
        agent_id="agent-designer",
        kind="design_overview",
        title="Expense tracker design",
        content="Domain rule: expenses have amount, category, date, and note.",
        source_backend="llm_harness/qwen2.5-coder-14b-instruct",
    )
    state_store.save_state(
        SharedProjectState(
            project=Project(id=project_id, goal="Build an expense tracker", current_stage="development", project_root=str(tmp_path)),
            project_status=ProjectStatus.IN_PROGRESS,
            current_stage="development",
            workitems=[design_workitem, code_workitem],
            artifacts=[design_artifact],
        )
    )
    harness = FakeCodeLLMHarness(
        '{"files":[{"path":"index.html","content":"<main>Expense tracker</main>"}]}'
    )
    runner = Runner(
        state_store=state_store,
        require_real_code_outputs=True,
        llm_harness=harness,
        llm_harness_config=LLMHTTPConfig(
            base_url="http://127.0.0.1:1234/v1",
            model_name="qwen2.5-coder-14b-instruct",
            enabled=True,
        ),
    )
    profile = next(profile for profile in build_default_agent_profiles() if profile.role_name == "frontend_engineer")
    agent = Agent(
        id="agent-frontend",
        role="frontend_engineer",
        profile=profile,
        capabilities=[Capability.CODING],
        execution_backend="cli",
    )

    execution = runner.run(project_id, code_workitem, agent)
    latest = state_store.get_state(project_id)

    assert (tmp_path / "index.html").read_text(encoding="utf-8") == "<main>Expense tracker</main>"
    assert "Domain rule: expenses have amount, category, date, and note." in harness.last_prompt
    assert execution.source_backend == "llm_harness_code/qwen2.5-coder-14b-instruct"
    assert execution.changed_files == ["index.html"]
    assert execution.validation_success is True
    assert latest.workitems[1].status == WorkItemStatus.DONE
    assert latest.artifacts[-1].source_backend == "llm_harness_code/qwen2.5-coder-14b-instruct"


def test_llm_code_harness_rejects_protected_paths(tmp_path) -> None:
    state_store = InMemoryStateStore()
    project_id = "project-code-fail"
    workitem = WorkItem(
        id="workitem-001",
        description="Implement UI",
        stage="development",
        kind="ui_implementation",
    )
    state_store.save_state(
        SharedProjectState(
            project=Project(id=project_id, goal="Build UI", current_stage="development", project_root=str(tmp_path)),
            project_status=ProjectStatus.IN_PROGRESS,
            current_stage="development",
            workitems=[workitem],
        )
    )
    harness = FakeCodeLLMHarness(
        '{"files":[{"path":".conductor/evil.txt","content":"bad"}]}'
    )
    runner = Runner(
        state_store=state_store,
        require_real_code_outputs=True,
        llm_harness=harness,
        llm_harness_config=LLMHTTPConfig(
            base_url="http://127.0.0.1:1234/v1",
            model_name="qwen2.5-coder-14b-instruct",
            enabled=True,
        ),
    )
    profile = next(profile for profile in build_default_agent_profiles() if profile.role_name == "frontend_engineer")
    agent = Agent(
        id="agent-frontend",
        role="frontend_engineer",
        profile=profile,
        capabilities=[Capability.CODING],
        execution_backend="cli",
    )

    execution = runner.run(project_id, workitem, agent)
    latest = state_store.get_state(project_id)

    assert execution.status.value == "failed"
    assert "protected directory" in execution.failure_summary
    assert latest.workitems[0].status == WorkItemStatus.FAILED
    assert latest.workitems[0].retryable is False


def test_runner_prefers_static_web_validation_for_static_project(tmp_path) -> None:
    (tmp_path / "static").mkdir()
    (tmp_path / "index.html").write_text("<html><body><script src='static/app.js'></script></body></html>", encoding="utf-8")
    (tmp_path / "static" / "app.js").write_text("console.log('ok');\n", encoding="utf-8")
    runner = Runner(state_store=InMemoryStateStore())

    command = runner._select_test_command(str(tmp_path))

    assert command[:3] == [sys.executable, "-m", "conductor.harness.static_web_cli"]
