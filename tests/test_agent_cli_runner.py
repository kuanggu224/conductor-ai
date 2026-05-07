"""Agent CLI 执行链路测试。"""

from conductor.agents.agent import Agent
from conductor.agents.profile import build_default_agent_profiles
from conductor.config.cli import CLISelectionConfig
from conductor.domain.models import Capability, ExecutionStatus, Project, ProjectStatus, SharedProjectState, WorkItem, WorkItemStatus
from conductor.harness.base import BaseHarness
from conductor.harness.models import HarnessRequest, HarnessResult
from conductor.execution.runner import Runner
from conductor.state.store import InMemoryStateStore


class FakeAgentCLIHarness(BaseHarness):
    name = "shell"

    def __init__(self, success: bool = True) -> None:
        self.success = success
        self.last_request: HarnessRequest | None = None

    def run(self, request: HarnessRequest) -> HarnessResult:
        self.last_request = request
        if self.success:
            return HarnessResult(
                success=True,
                exit_code=0,
                stdout="# Claude 设计文档\n\n这是来自 CLI 的输出。",
                stderr="",
                duration_ms=25,
            )
        return HarnessResult(
            success=False,
            exit_code=1,
            stdout="",
            stderr="cli failed",
            duration_ms=25,
        )


class FakeCompatibilityFailHarness(BaseHarness):
    name = "shell"

    def run(self, request: HarnessRequest) -> HarnessResult:
        return HarnessResult(
            success=False,
            exit_code=1,
            stdout='API Error: 400 {"error":{"message":"Unsupported reasoning_effort type"}}',
            stderr="",
            duration_ms=25,
        )


def test_runner_uses_bound_agent_cli_for_designer_workitem(monkeypatch) -> None:
    monkeypatch.setattr("conductor.execution.runner.shutil.which", lambda name: f"C:/bin/{name}.cmd")
    state_store = InMemoryStateStore()
    harness = FakeAgentCLIHarness(success=True)
    runner = Runner(
        state_store=state_store,
        shell_harness=harness,
        cli_selection_config=CLISelectionConfig(
            selected_cli_names=["claude"],
            role_cli_bindings={"designer": "claude"},
        ),
    )
    project_id = "project-cli"
    state = type("State", (), {})()
    from conductor.domain.models import Project, ProjectStatus, SharedProjectState

    shared_state = SharedProjectState(
        project=Project(id=project_id, goal="做设计", status=ProjectStatus.INITIALIZED, current_stage="design"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="design",
        workitems=[],
    )
    state_store.save_state(shared_state)
    workitem = WorkItem(id="workitem-001", description="形成总体设计", stage="design", kind="design_overview")
    shared_state.workitems = [workitem]
    state_store.save_state(shared_state)
    profile = next(profile for profile in build_default_agent_profiles() if profile.role_name == "designer")
    agent = Agent(
        id="agent-designer",
        role="designer",
        profile=profile,
        capabilities=[Capability.PLANNING],
        execution_backend="llm",
    )

    execution = runner.run(project_id=project_id, workitem=workitem, agent=agent)
    latest = state_store.get_state(project_id)

    assert execution.status == ExecutionStatus.SUCCESS
    assert latest.workitems[0].status == WorkItemStatus.DONE
    assert latest.artifacts[0].source_backend == "agent_cli/claude"
    assert harness.last_request is not None
    assert harness.last_request.command[0].lower().endswith("claude.cmd")
    assert harness.last_request.command[1] == "-p"
    assert "--effort" in harness.last_request.command
    assert "high" in harness.last_request.command


def test_runner_falls_back_when_agent_cli_fails(monkeypatch) -> None:
    monkeypatch.setattr("conductor.execution.runner.shutil.which", lambda name: f"C:/bin/{name}.cmd")
    state_store = InMemoryStateStore()
    harness = FakeAgentCLIHarness(success=False)
    runner = Runner(
        state_store=state_store,
        shell_harness=harness,
        cli_selection_config=CLISelectionConfig(
            selected_cli_names=["claude"],
            role_cli_bindings={"designer": "claude"},
        ),
    )
    from conductor.domain.models import Project, ProjectStatus, SharedProjectState

    project_id = "project-fallback"
    shared_state = SharedProjectState(
        project=Project(id=project_id, goal="做设计", status=ProjectStatus.INITIALIZED, current_stage="design"),
        project_status=ProjectStatus.INITIALIZED,
        current_stage="design",
        workitems=[],
    )
    state_store.save_state(shared_state)
    workitem = WorkItem(id="workitem-001", description="形成总体设计", stage="design", kind="design_overview")
    shared_state.workitems = [workitem]
    state_store.save_state(shared_state)
    profile = next(profile for profile in build_default_agent_profiles() if profile.role_name == "designer")
    agent = Agent(
        id="agent-designer",
        role="designer",
        profile=profile,
        capabilities=[Capability.PLANNING],
        execution_backend="llm",
    )

    execution = runner.run(project_id=project_id, workitem=workitem, agent=agent)
    latest = state_store.get_state(project_id)

    assert execution.status == ExecutionStatus.SUCCESS
    assert latest.artifacts[0].source_backend == "mock"


def test_codex_code_edit_command_uses_model_and_reasoning(monkeypatch) -> None:
    monkeypatch.setattr("conductor.agents.cli_executor.shutil.which", lambda name: f"C:/bin/{name}.cmd")
    from conductor.agents.cli_executor import AgentCLIExecutor

    profile = next(profile for profile in build_default_agent_profiles() if profile.role_name == "backend_engineer")
    agent = Agent(
        id="agent-backend",
        role="backend_engineer",
        profile=profile,
        capabilities=[Capability.CODING],
        execution_backend="cli",
    )
    executor = AgentCLIExecutor(
        cli_selection_config=CLISelectionConfig(
            selected_cli_names=["codex"],
            role_cli_bindings={"backend_engineer": "codex"},
            codex_model="gpt-5.4-mini",
            codex_reasoning_effort="medium",
        ),
        shell_harness=FakeAgentCLIHarness(success=True),
    )

    execution = executor.execute(
        agent=agent,
        prompt="Edit app.py and run tests",
        execution_mode="code_edit",
        timeout_seconds=30.0,
        track_workspace_changes=True,
        workspace_root="C:/repo",
    )

    assert execution is not None
    command = execution.result
    assert executor.shell_harness.last_request is not None
    request = executor.shell_harness.last_request
    assert request.command[0].lower().endswith("codex.cmd")
    assert "-m" in request.command
    assert "gpt-5.4-mini" in request.command
    assert '-c' in request.command
    assert 'model_reasoning_effort="medium"' in request.command
    assert "--dangerously-bypass-approvals-and-sandbox" in request.command
    assert "-s" in request.command
    assert "workspace-write" in request.command


def test_opencode_command_passes_prompt_as_argument(monkeypatch) -> None:
    monkeypatch.setattr("conductor.agents.cli_executor.shutil.which", lambda name: f"C:/bin/{name}.cmd")
    from conductor.agents.cli_executor import AgentCLIExecutor

    profile = next(profile for profile in build_default_agent_profiles() if profile.role_name == "designer")
    agent = Agent(
        id="agent-designer",
        role="designer",
        profile=profile,
        capabilities=[Capability.PLANNING],
        execution_backend="cli",
    )
    executor = AgentCLIExecutor(
        cli_selection_config=CLISelectionConfig(
            selected_cli_names=["opencode"],
            role_cli_bindings={"designer": "opencode"},
        ),
        shell_harness=FakeAgentCLIHarness(success=True),
    )

    execution = executor.execute(
        agent=agent,
        prompt="Write DESIGN.md",
        execution_mode="documentation",
        timeout_seconds=30.0,
        working_directory="C:/repo",
    )

    assert execution is not None
    assert executor.shell_harness.last_request is not None
    request = executor.shell_harness.last_request
    assert request.command[:3] == ["C:/bin/opencode.cmd", "run", "--dir"]
    assert "C:/repo" in request.command
    assert request.command[-1] == "Write DESIGN.md"
    assert request.stdin_text is None


def test_aspirecode_command_uses_opencode_protocol_with_model(monkeypatch) -> None:
    monkeypatch.setattr("conductor.agents.cli_executor.shutil.which", lambda name: f"C:/bin/{name}.cmd")
    from conductor.agents.cli_executor import AgentCLIExecutor

    profile = next(profile for profile in build_default_agent_profiles() if profile.role_name == "designer")
    agent = Agent(
        id="agent-designer",
        role="designer",
        profile=profile,
        capabilities=[Capability.PLANNING],
        execution_backend="cli",
    )
    executor = AgentCLIExecutor(
        cli_selection_config=CLISelectionConfig(
            selected_cli_names=["aspirecode"],
            role_cli_bindings={"designer": "aspirecode"},
            aspirecode_model="lmstudio-local/qwen3.6-35b-a3b",
        ),
        shell_harness=FakeAgentCLIHarness(success=True),
    )

    execution = executor.execute(
        agent=agent,
        prompt="Write DESIGN.md",
        execution_mode="documentation",
        timeout_seconds=30.0,
        working_directory="C:/repo",
    )

    assert execution is not None
    assert executor.shell_harness.last_request is not None
    request = executor.shell_harness.last_request
    assert request.command[:3] == ["C:/bin/aspirecode.cmd", "run", "--dir"]
    assert "--model" in request.command
    assert "lmstudio-local/qwen3.6-35b-a3b" in request.command
    assert "--dangerously-skip-permissions" not in request.command
    assert request.command[-1] == "Write DESIGN.md"
    assert request.stdin_text is None


def test_opencode_document_prompt_uses_file_output(tmp_path) -> None:
    runner = Runner(state_store=InMemoryStateStore())
    profile = next(profile for profile in build_default_agent_profiles() if profile.role_name == "designer")
    agent = Agent(
        id="agent-designer",
        role="designer",
        profile=profile,
        capabilities=[Capability.PLANNING],
        execution_backend="cli",
    )
    workitem = WorkItem(id="workitem-001", description="Create design", stage="design", kind="design_overview")

    prompt = runner._build_agent_cli_document_prompt(workitem, agent, "opencode")
    output_file = tmp_path / "CONDUCTOR_OUTPUT_workitem-001.md"
    output_file.write_text("# 目标\n真实产出", encoding="utf-8")

    assert "CONDUCTOR_OUTPUT_workitem-001.md" in prompt
    assert runner._read_agent_cli_document_file(str(tmp_path), workitem, "opencode") == "# 目标\n真实产出"


def test_requirement_document_prompts_include_quality_gate_sections() -> None:
    state_store = InMemoryStateStore()
    state_store.save_state(
        SharedProjectState(
            project=Project(id="project-prompts", goal="Build a reading list", current_stage="requirement"),
            project_status=ProjectStatus.IN_PROGRESS,
            current_stage="requirement",
        )
    )
    runner = Runner(state_store=state_store)
    profile = next(profile for profile in build_default_agent_profiles() if profile.role_name == "requirement_designer")
    agent = Agent(
        id="agent-requirement-designer",
        role="requirement_designer",
        profile=profile,
        capabilities=[Capability.PLANNING],
        execution_backend="llm",
    )
    workitem = WorkItem(id="workitem-req", description="Clarify requirement", stage="requirement", kind="requirement_spec")

    harness_prompt = runner._build_llm_harness_document_prompt("project-prompts", workitem, agent)
    codex_prompt = runner._build_agent_cli_document_prompt(workitem, agent, "codex")
    opencode_prompt = runner._build_agent_cli_document_prompt(workitem, agent, "opencode")

    for prompt in [harness_prompt, codex_prompt, opencode_prompt]:
        assert "非目标" in prompt
        assert "边界/异常场景" in prompt
        assert "待确认问题" in prompt
        assert "下游交付约束" in prompt


def test_claude_binding_is_disabled_after_provider_compatibility_failure(monkeypatch) -> None:
    monkeypatch.setattr("conductor.agents.cli_executor.shutil.which", lambda name: f"C:/bin/{name}.cmd")
    from conductor.agents.cli_executor import AgentCLIExecutor

    profile = next(profile for profile in build_default_agent_profiles() if profile.role_name == "designer")
    agent = Agent(
        id="agent-designer",
        role="designer",
        profile=profile,
        capabilities=[Capability.PLANNING],
        execution_backend="llm",
    )
    executor = AgentCLIExecutor(
        cli_selection_config=CLISelectionConfig(
            selected_cli_names=["claude"],
            role_cli_bindings={"designer": "claude"},
        ),
        shell_harness=FakeCompatibilityFailHarness(),
    )

    execution = executor.execute(
        agent=agent,
        prompt="short prompt",
        execution_mode="documentation",
        timeout_seconds=30.0,
    )

    assert execution is not None
    assert execution.result.success is False
    assert executor.is_binding_disabled(agent) is True
    assert executor.resolve_binding(agent) is None
