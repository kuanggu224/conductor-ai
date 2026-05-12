"""Runner 与基础流程测试。"""

import json
from dataclasses import replace

from conductor.agents.agent import Agent
from conductor.agents.llm import MockCloudLLMBackend
from conductor.config.cli import CLISelectionConfig
from conductor.config.llm import LLMUsagePolicy
from conductor.agents.profile import build_default_agent_profiles
from conductor.controller.lead_controller import LeadController
from conductor.domain.models import Artifact, Capability, ExecutionStatus, WorkItem, WorkItemStatus
from conductor.harness.base import BaseHarness
from conductor.harness.models import HarnessRequest, HarnessResult
from conductor.execution.runner import Runner
from conductor.manifest import RunManifestWriter
from conductor.state.store import InMemoryStateStore
from conductor.workflow.template import WorkflowTemplate


class FakeSuccessHarness(BaseHarness):
    name = "shell"

    def run(self, request: HarnessRequest) -> HarnessResult:
        return HarnessResult(
            success=True,
            exit_code=0,
            stdout="3 passed",
            stderr="",
            duration_ms=12,
        )


class FakePartialStaticValidationHarness(BaseHarness):
    name = "static_web"

    def run(self, request: HarnessRequest) -> HarnessResult:
        return HarnessResult(
            success=True,
            exit_code=0,
            stdout="\n".join(
                [
                    "Static Web Validation: PASS",
                    "Browser form interaction updated visible state: sample",
                    "Browser export/download action triggered",
                ]
            ),
            stderr="",
            duration_ms=15,
        )


class FakeFailHarness(BaseHarness):
    name = "shell"

    def run(self, request: HarnessRequest) -> HarnessResult:
        return HarnessResult(
            success=False,
            exit_code=1,
            stdout="1 failed",
            stderr="traceback",
            duration_ms=18,
        )


class FakeNoTestsHarness(BaseHarness):
    name = "shell"

    def run(self, request: HarnessRequest) -> HarnessResult:
        return HarnessResult(
            success=False,
            exit_code=5,
            stdout="no tests ran in 0.00s",
            stderr="",
            duration_ms=9,
        )


class FakeCodeExecutionNoTestsHarness(BaseHarness):
    name = "shell"

    def __init__(self) -> None:
        self.requests: list[HarnessRequest] = []

    def run(self, request: HarnessRequest) -> HarnessResult:
        self.requests.append(request)
        if request.description.startswith("backend_engineer:"):
            return HarnessResult(
                success=True,
                exit_code=0,
                stdout="code changed",
                stderr="",
                duration_ms=20,
                changed_files=["app.py"],
            )
        return HarnessResult(
            success=False,
            exit_code=5,
            stdout="no tests ran in 0.00s",
            stderr="",
            duration_ms=10,
        )


class FakeCodeExecutionHarness(BaseHarness):
    name = "shell"

    def __init__(self, validation_success: bool = True) -> None:
        self.requests: list[HarnessRequest] = []
        self.validation_success = validation_success

    def run(self, request: HarnessRequest) -> HarnessResult:
        self.requests.append(request)
        if request.description.startswith("backend_engineer:") or request.description.startswith("frontend_engineer:"):
            return HarnessResult(
                success=True,
                exit_code=0,
                stdout="已完成代码修改",
                stderr="",
                duration_ms=30,
                changed_files=["conductor/demo.py"],
            )
        return HarnessResult(
            success=self.validation_success,
            exit_code=0 if self.validation_success else 1,
            stdout="2 passed" if self.validation_success else "1 failed",
            stderr="" if self.validation_success else "traceback",
            duration_ms=22,
        )


class FakeDocumentationHarness(BaseHarness):
    name = "shell"

    def __init__(self, success: bool = True) -> None:
        self.requests: list[HarnessRequest] = []
        self.success = success

    def run(self, request: HarnessRequest) -> HarnessResult:
        self.requests.append(request)
        return HarnessResult(
            success=self.success,
            exit_code=0 if self.success else 1,
            stdout="# 真实需求设计文档\n\n## 目标\n由 Agent CLI 生成。",
            stderr="" if self.success else "cli failed",
            duration_ms=25,
        )


def test_runner_returns_execution_result() -> None:
    state_store = InMemoryStateStore()
    runner = Runner(state_store)
    controller = LeadController(
        workflow_template=WorkflowTemplate(),
        state_store=state_store,
        runner=runner,
    )
    state = controller.initialize_project("实现 Runner")
    workitem = state.workitems[0]
    agent = Agent(id="agent-1", role="executor", capabilities=[Capability.CODING], backend="mock")

    execution = runner.run(project_id=state.project.id, workitem=workitem, agent=agent)
    latest = state_store.get_state(state.project.id)

    assert execution.workitem_id == workitem.id
    assert execution.agent_id == "agent-1"
    assert latest.workitems[0].status.value == "done"
    assert latest.artifacts[0].workitem_id == workitem.id
    assert latest.artifacts[0].agent_id == "agent-1"
    assert latest.artifacts[0].content == execution.result
    assert latest.artifacts[0].source_backend == "mock"
    assert latest.artifacts[0].version == 1
    assert latest.artifacts[0].parent_artifact_id is None
    assert "# 执行说明文档" in latest.artifacts[0].content
    assert "## 目标" in latest.artifacts[0].content
    assert "执行完成" in latest.recent_events[-1]


def test_runner_can_fail_once_for_retry_flow() -> None:
    state_store = InMemoryStateStore()
    runner = Runner(state_store)
    controller = LeadController(
        workflow_template=WorkflowTemplate(),
        state_store=state_store,
        runner=runner,
    )
    state = controller.initialize_project("实现 Runner")
    fail_workitem = WorkItem(
        id="workitem-fail",
        description="模拟一次失败后重试",
        stage="design",
        kind="fail_once",
    )
    state.workitems = [fail_workitem]
    state_store.save_state(state)
    agent = Agent(id="agent-1", role="executor", capabilities=[Capability.CODING], backend="mock")

    first_execution = runner.run(project_id=state.project.id, workitem=fail_workitem, agent=agent)
    first_state = state_store.get_state(state.project.id)
    state_store.update_workitem(
        project_id=state.project.id,
        workitem_id=fail_workitem.id,
        status=WorkItemStatus.PENDING,
        retry_count=1,
    )
    retry_source = state_store.get_state(state.project.id).workitems[0]
    second_execution = runner.run(project_id=state.project.id, workitem=retry_source, agent=agent)
    second_state = state_store.get_state(state.project.id)

    assert first_execution.status == ExecutionStatus.FAILED
    assert second_execution.status == ExecutionStatus.SUCCESS
    assert second_state.workitems[0].status.value == "done"


def test_runner_creates_new_artifact_version_on_repeat_success() -> None:
    state_store = InMemoryStateStore()
    runner = Runner(state_store)
    controller = LeadController(
        workflow_template=WorkflowTemplate(),
        state_store=state_store,
        runner=runner,
    )
    state = controller.initialize_project("实现 Runner")
    workitem = WorkItem(
        id="workitem-repeat",
        description="重复执行以生成新版本产物",
        stage="design",
        kind="design_overview",
    )
    state.workitems = [workitem]
    state_store.save_state(state)
    agent = Agent(id="agent-1", role="executor", capabilities=[Capability.CODING], backend="mock")

    runner.run(project_id=state.project.id, workitem=workitem, agent=agent)
    latest_after_first = state_store.get_state(state.project.id)
    retry_workitem = replace(latest_after_first.workitems[0], status=WorkItemStatus.PENDING, retry_count=1)
    state_store.save_state(replace(latest_after_first, workitems=[retry_workitem]))
    retry_source = state_store.get_state(state.project.id).workitems[0]
    runner.run(project_id=state.project.id, workitem=retry_source, agent=agent)
    latest = state_store.get_state(state.project.id)

    assert len(latest.artifacts) == 2
    assert latest.artifacts[0].id == "artifact-workitem-repeat"
    assert latest.artifacts[0].version == 1
    assert latest.artifacts[1].id == "artifact-workitem-repeat-v2"
    assert latest.artifacts[1].version == 2
    assert latest.artifacts[1].parent_artifact_id == "artifact-workitem-repeat"


def test_runner_uses_agent_cli_for_real_design_document(monkeypatch) -> None:
    monkeypatch.setattr("conductor.agents.cli_executor.shutil.which", lambda name: f"C:/bin/{name}.cmd")
    state_store = InMemoryStateStore()
    harness = FakeDocumentationHarness(success=True)
    runner = Runner(
        state_store,
        shell_harness=harness,
        cli_selection_config=CLISelectionConfig(
            selected_cli_names=["codex"],
            role_cli_bindings={"designer": "codex"},
        ),
        require_real_design_outputs=True,
    )
    controller = LeadController(
        workflow_template=WorkflowTemplate(),
        state_store=state_store,
        runner=runner,
    )
    state = controller.initialize_project("生成真实需求设计文档")
    workitem = WorkItem(
        id="workitem-design",
        description="形成需求设计文档",
        stage="design",
        kind="design_overview",
    )
    state.workitems = [workitem]
    state_store.save_state(state)
    profile = next(profile for profile in build_default_agent_profiles() if profile.role_name == "designer")
    agent = Agent(
        id="agent-designer",
        role="designer",
        profile=profile,
        capabilities=[Capability.PLANNING],
        backend="llm",
        execution_backend="llm",
    )

    execution = runner.run(project_id=state.project.id, workitem=workitem, agent=agent)
    latest = state_store.get_state(state.project.id)

    assert execution.status == ExecutionStatus.SUCCESS
    assert latest.workitems[0].status == WorkItemStatus.DONE
    assert latest.artifacts[0].source_backend == "agent_cli/codex"
    assert "真实需求设计文档" in latest.artifacts[0].content


def test_runner_blocks_real_design_when_no_real_backend_available() -> None:
    state_store = InMemoryStateStore()
    runner = Runner(state_store, require_real_design_outputs=True)
    controller = LeadController(
        workflow_template=WorkflowTemplate(),
        state_store=state_store,
        runner=runner,
    )
    state = controller.initialize_project("生成真实需求设计文档")
    workitem = WorkItem(
        id="workitem-design",
        description="形成需求设计文档",
        stage="design",
        kind="design_overview",
    )
    state.workitems = [workitem]
    state_store.save_state(state)
    profile = next(profile for profile in build_default_agent_profiles() if profile.role_name == "designer")
    agent = Agent(
        id="agent-designer",
        role="designer",
        profile=profile,
        capabilities=[Capability.PLANNING],
        backend="llm",
        execution_backend="llm",
    )

    execution = runner.run(project_id=state.project.id, workitem=workitem, agent=agent)
    latest = state_store.get_state(state.project.id)

    assert execution.status == ExecutionStatus.FAILED
    assert latest.workitems[0].status == WorkItemStatus.FAILED
    assert latest.artifacts[0].source_backend == "real_backend_required"
    assert latest.workitems[0].failure_type == "configuration_required"
    assert latest.workitems[0].retryable is False
    assert "不会再生成模拟交付物" in latest.artifacts[0].content


def test_runner_uses_shell_harness_for_tester_workitems() -> None:
    state_store = InMemoryStateStore()
    runner = Runner(
        state_store,
        shell_harness=FakeSuccessHarness(),
        enable_tester_harness=True,
    )
    controller = LeadController(
        workflow_template=WorkflowTemplate(),
        state_store=state_store,
        runner=runner,
    )
    state = controller.initialize_project("实现 API 和测试")
    test_workitem = WorkItem(
        id="workitem-test",
        description="执行自动化测试",
        stage="testing",
        kind="automated_test",
    )
    state.workitems = [test_workitem]
    state_store.save_state(state)
    tester_profile = next(profile for profile in build_default_agent_profiles() if profile.role_name == "tester")
    agent = Agent(
        id="agent-tester",
        role="tester",
        profile=tester_profile,
        capabilities=[Capability.TESTING],
        backend="mock",
        execution_backend="cli",
    )

    execution = runner.run(project_id=state.project.id, workitem=test_workitem, agent=agent)
    latest = state_store.get_state(state.project.id)

    assert execution.status == ExecutionStatus.SUCCESS
    assert latest.workitems[0].status == WorkItemStatus.DONE
    assert latest.artifacts[0].source_backend == "cli/shell"
    assert "测试执行报告" in latest.artifacts[0].content
    assert "Exit Code: `0`" in latest.artifacts[0].content


def test_runner_blocks_validation_when_frozen_requirement_coverage_is_missing(tmp_path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>App</title>", encoding="utf-8")
    state_store = InMemoryStateStore()
    runner = Runner(
        state_store,
        shell_harness=FakePartialStaticValidationHarness(),
        enable_tester_harness=True,
    )
    controller = LeadController(
        workflow_template=WorkflowTemplate(),
        state_store=state_store,
        runner=runner,
    )
    state = controller.initialize_project(
        "\u7528\u6237\u53ef\u4ee5\u6dfb\u52a0\u4e66\u7c4d\uff0c"
        "\u5237\u65b0\u540e\u4fdd\u7559\u6570\u636e\uff0c"
        "\u5e76\u5bfc\u51fa CSV\u3002"
    )
    state.project.project_root = str(tmp_path)
    test_workitem = WorkItem(
        id="workitem-coverage",
        description="\u9a8c\u6536\u9759\u6001 Web \u4ea4\u4ed8\u7269",
        stage="testing",
        kind="acceptance_check",
    )
    state.workitems = [test_workitem]
    state.artifacts = [
        Artifact(
            id="artifact-frozen",
            project_id=state.project.id,
            workitem_id="workitem-requirement",
            agent_id="agent-designer",
            kind="frozen_requirement_spec",
            title="Frozen Requirement",
            content=state.project.goal,
        )
    ]
    state_store.save_state(state)
    tester_profile = next(profile for profile in build_default_agent_profiles() if profile.role_name == "tester")
    agent = Agent(
        id="agent-tester",
        role="tester",
        profile=tester_profile,
        capabilities=[Capability.TESTING],
        backend="mock",
        execution_backend="cli",
    )

    execution = runner.run(project_id=state.project.id, workitem=test_workitem, agent=agent)
    latest = state_store.get_state(state.project.id)

    assert execution.status == ExecutionStatus.FAILED
    assert execution.failure_type == "validation_failed"
    assert execution.failure_summary == "Requirement coverage missing: refresh persistence"
    assert latest.workitems[0].status == WorkItemStatus.FAILED
    assert "Requirement Coverage" in latest.artifacts[-1].content
    assert "Status: `missing_coverage`" in latest.artifacts[-1].content
    assert "refresh persistence" in latest.artifacts[-1].content


def test_tester_validation_prefers_shell_harness_even_when_bound_to_agent_cli(monkeypatch) -> None:
    monkeypatch.setattr("conductor.agents.cli_executor.shutil.which", lambda name: f"C:/bin/{name}.cmd")
    state_store = InMemoryStateStore()
    runner = Runner(
        state_store,
        shell_harness=FakeSuccessHarness(),
        enable_tester_harness=True,
        cli_selection_config=CLISelectionConfig(
            selected_cli_names=["codex"],
            role_cli_bindings={"tester": "codex"},
        ),
    )
    controller = LeadController(
        workflow_template=WorkflowTemplate(),
        state_store=state_store,
        runner=runner,
    )
    state = controller.initialize_project("验证 API")
    test_workitem = WorkItem(
        id="workitem-test",
        description="执行 API 验证",
        stage="testing",
        kind="api_validation",
    )
    state.workitems = [test_workitem]
    state_store.save_state(state)
    tester_profile = next(profile for profile in build_default_agent_profiles() if profile.role_name == "tester")
    agent = Agent(
        id="agent-tester",
        role="tester",
        profile=tester_profile,
        capabilities=[Capability.TESTING],
        backend="mock",
        execution_backend="cli",
    )

    execution = runner.run(project_id=state.project.id, workitem=test_workitem, agent=agent)
    latest = state_store.get_state(state.project.id)

    assert execution.status == ExecutionStatus.SUCCESS
    assert latest.artifacts[0].source_backend == "cli/shell"
    assert not latest.artifacts[0].source_backend.startswith("agent_cli/")


def test_runner_marks_failed_when_shell_harness_fails() -> None:
    state_store = InMemoryStateStore()
    runner = Runner(
        state_store,
        shell_harness=FakeFailHarness(),
        enable_tester_harness=True,
    )
    controller = LeadController(
        workflow_template=WorkflowTemplate(),
        state_store=state_store,
        runner=runner,
    )
    state = controller.initialize_project("实现 API 和测试")
    test_workitem = WorkItem(
        id="workitem-test",
        description="执行自动化测试",
        stage="testing",
        kind="automated_test",
    )
    state.workitems = [test_workitem]
    state_store.save_state(state)
    tester_profile = next(profile for profile in build_default_agent_profiles() if profile.role_name == "tester")
    agent = Agent(
        id="agent-tester",
        role="tester",
        profile=tester_profile,
        capabilities=[Capability.TESTING],
        backend="mock",
        execution_backend="cli",
    )

    execution = runner.run(project_id=state.project.id, workitem=test_workitem, agent=agent)
    latest = state_store.get_state(state.project.id)

    assert execution.status == ExecutionStatus.FAILED
    assert latest.workitems[0].status == WorkItemStatus.FAILED
    assert latest.artifacts[0].source_backend == "cli/shell"
    assert "Exit Code: `1`" in latest.artifacts[0].content


def test_runner_does_not_block_when_pytest_finds_no_tests() -> None:
    state_store = InMemoryStateStore()
    runner = Runner(
        state_store,
        shell_harness=FakeNoTestsHarness(),
        enable_tester_harness=True,
    )
    controller = LeadController(
        workflow_template=WorkflowTemplate(),
        state_store=state_store,
        runner=runner,
    )
    state = controller.initialize_project("验证没有测试文件时不阻塞")
    test_workitem = WorkItem(
        id="workitem-no-tests",
        description="执行自动化测试",
        stage="testing",
        kind="automated_test",
    )
    state.workitems = [test_workitem]
    state_store.save_state(state)
    tester_profile = next(profile for profile in build_default_agent_profiles() if profile.role_name == "tester")
    agent = Agent(
        id="agent-tester",
        role="tester",
        profile=tester_profile,
        capabilities=[Capability.TESTING],
        backend="mock",
        execution_backend="cli",
    )

    execution = runner.run(project_id=state.project.id, workitem=test_workitem, agent=agent)
    latest = state_store.get_state(state.project.id)

    assert execution.status == ExecutionStatus.SUCCESS
    assert latest.workitems[0].status == WorkItemStatus.DONE
    assert latest.artifacts[0].source_backend == "cli/shell"
    assert "Exit Code: `5`" in latest.artifacts[0].content
    assert "无测试文件" in latest.artifacts[0].content


def test_runner_executes_real_code_loop_for_backend_agent(monkeypatch) -> None:
    monkeypatch.setattr("conductor.agents.cli_executor.shutil.which", lambda name: f"C:/bin/{name}.cmd")
    state_store = InMemoryStateStore()
    harness = FakeCodeExecutionHarness(validation_success=True)
    runner = Runner(
        state_store,
        shell_harness=harness,
        cli_selection_config=CLISelectionConfig(
            selected_cli_names=["claude"],
            role_cli_bindings={"backend_engineer": "claude"},
        ),
    )
    controller = LeadController(
        workflow_template=WorkflowTemplate(),
        state_store=state_store,
        runner=runner,
    )
    state = controller.initialize_project("实现 API")
    workitem = WorkItem(
        id="workitem-backend",
        description="实现后端接口",
        stage="development",
        kind="api_implementation",
        acceptance_criteria=["修改代码并通过测试"],
    )
    state.workitems = [workitem]
    state_store.save_state(state)
    profile = next(profile for profile in build_default_agent_profiles() if profile.role_name == "backend_engineer")
    agent = Agent(
        id="agent-backend",
        role="backend_engineer",
        profile=profile,
        capabilities=[Capability.CODING],
        backend="mock",
        execution_backend="cli",
    )

    execution = runner.run(project_id=state.project.id, workitem=workitem, agent=agent)
    latest = state_store.get_state(state.project.id)

    assert execution.status == ExecutionStatus.SUCCESS
    assert latest.workitems[0].status == WorkItemStatus.DONE
    assert latest.artifacts[0].source_backend == "agent_cli/claude"
    assert "代码执行报告" in latest.artifacts[0].content
    assert "## Delivery Contract" in latest.artifacts[0].content
    assert "## Acceptance Trace" in latest.artifacts[0].content
    assert "[passed]" in latest.artifacts[0].content
    assert "`conductor/demo.py`" in latest.artifacts[0].content
    assert "2 passed" in latest.artifacts[0].content
    assert len(harness.requests) == 2
    assert harness.requests[0].track_workspace_changes is True
    assert "--dangerously-skip-permissions" in " ".join(harness.requests[0].command)


def test_runner_fails_code_execution_when_post_validation_fails(monkeypatch) -> None:
    monkeypatch.setattr("conductor.agents.cli_executor.shutil.which", lambda name: f"C:/bin/{name}.cmd")
    state_store = InMemoryStateStore()
    harness = FakeCodeExecutionHarness(validation_success=False)
    runner = Runner(
        state_store,
        shell_harness=harness,
        cli_selection_config=CLISelectionConfig(
            selected_cli_names=["claude"],
            role_cli_bindings={"frontend_engineer": "claude"},
        ),
    )
    controller = LeadController(
        workflow_template=WorkflowTemplate(),
        state_store=state_store,
        runner=runner,
    )
    state = controller.initialize_project("实现页面")
    workitem = WorkItem(
        id="workitem-frontend",
        description="实现前端页面",
        stage="development",
        kind="ui_implementation",
        acceptance_criteria=["修改代码并通过测试"],
    )
    state.workitems = [workitem]
    state_store.save_state(state)
    profile = next(profile for profile in build_default_agent_profiles() if profile.role_name == "frontend_engineer")
    agent = Agent(
        id="agent-frontend",
        role="frontend_engineer",
        profile=profile,
        capabilities=[Capability.CODING],
        backend="mock",
        execution_backend="cli",
    )

    execution = runner.run(project_id=state.project.id, workitem=workitem, agent=agent)
    latest = state_store.get_state(state.project.id)

    assert execution.status == ExecutionStatus.FAILED
    assert latest.workitems[0].status == WorkItemStatus.FAILED
    assert latest.artifacts[0].source_backend == "agent_cli/claude"
    assert "Exit Code: `1`" in latest.artifacts[0].content


def test_runner_requires_frontend_files_for_ui_implementation() -> None:
    runner = Runner(InMemoryStateStore())
    workitem = WorkItem(
        id="workitem-frontend-contract",
        description="Implement UI",
        stage="development",
        kind="ui_implementation",
    )
    agent = Agent(
        id="agent-frontend",
        role="frontend_engineer",
        capabilities=[Capability.CODING],
        backend="mock",
        execution_backend="cli",
    )

    assert runner._changed_files_satisfy_workitem(workitem, agent, ["app.py"]) is False
    assert runner._changed_files_satisfy_workitem(workitem, agent, ["index.html"]) is True
    assert runner._changed_files_satisfy_workitem(workitem, agent, ["static/app.js"]) is True


def test_runner_code_prompt_includes_full_requirement_and_avoids_generic_task_board() -> None:
    state_store = InMemoryStateStore()
    runner = Runner(state_store)
    controller = LeadController(
        workflow_template=WorkflowTemplate(),
        state_store=state_store,
        runner=runner,
    )
    state = controller.initialize_project("实现 Bug triage 看板：录入 bug、严重级别、复现步骤和状态流转")
    workitem = WorkItem(
        id="workitem-frontend",
        description="实现前端页面",
        stage="development",
        kind="ui_implementation",
    )
    state.workitems = [*state.workitems, workitem]
    state.artifacts = [
        Artifact(
            id="artifact-frozen",
            project_id=state.project.id,
            workitem_id="workitem-req",
            agent_id="agent-requirement",
            kind="frozen_requirement_spec",
            title="Frozen Requirement",
            content="Scope: bug triage only. Non-goal: no account system. Acceptance: create bug, change status, filter severity.",
        )
    ]
    state_store.save_state(state)
    agent = Agent(
        id="agent-frontend",
        role="frontend_engineer",
        capabilities=[Capability.CODING],
        backend="mock",
        execution_backend="cli",
    )

    prompt = runner._build_code_execution_prompt(state.project.id, workitem, agent, cli_name="opencode")

    assert "Bug triage" in prompt
    assert "Frozen Requirement Baseline" in prompt
    assert "Non-goal: no account system" in prompt
    assert "actual business domain" in prompt
    assert "task board" not in prompt.lower()


def test_runner_records_context_input_artifact_ids() -> None:
    state_store = InMemoryStateStore()
    runner = Runner(state_store)
    controller = LeadController(
        workflow_template=WorkflowTemplate(),
        state_store=state_store,
        runner=runner,
    )
    state = controller.initialize_project("Build a reading list with CSV export")
    workitem = WorkItem(
        id="workitem-backend",
        description="Implement backend behavior",
        stage="development",
        kind="api_implementation",
    )
    state.workitems = [*state.workitems, workitem]
    state.artifacts = [
        Artifact(
            id="artifact-frozen",
            project_id=state.project.id,
            workitem_id="workitem-req",
            agent_id="agent-requirement",
            kind="frozen_requirement_spec",
            title="Frozen Requirement",
            content="Acceptance: add book, persist refresh, export CSV.",
        )
    ]
    state_store.save_state(state)
    agent = Agent(
        id="agent-backend",
        role="backend_engineer",
        capabilities=[Capability.CODING],
        backend="mock",
    )

    execution = runner.run(state.project.id, workitem, agent)

    assert execution.input_artifact_ids == ["artifact-frozen"]


def test_runner_records_actual_llm_model_in_execution_and_manifest(tmp_path) -> None:
    state_store = InMemoryStateStore()
    runner = Runner(
        state_store,
        llm_usage_policy=LLMUsagePolicy(runner_enabled=True, preferred_backend="cloud"),
    )
    controller = LeadController(
        workflow_template=WorkflowTemplate(),
        state_store=state_store,
        runner=runner,
    )
    state = controller.initialize_project("Generate a design document")
    state.project.project_root = str(tmp_path)
    workitem = WorkItem(
        id="workitem-design",
        description="Create design overview",
        stage="design",
        kind="design_overview",
    )
    state.workitems = [workitem]
    state_store.save_state(state)
    agent = Agent(
        id="agent-designer",
        role="designer",
        capabilities=[Capability.PLANNING],
        backend="llm",
        execution_backend="llm",
        llm_backend=MockCloudLLMBackend(model_name="jiutian-lan-comv3"),
    )

    execution = runner.run(state.project.id, workitem, agent)
    latest = replace(state_store.get_state(state.project.id), executions=[execution])
    manifest_path = RunManifestWriter().write(
        latest,
        cli_config=CLISelectionConfig(),
        run_profile="test",
        report_path=tmp_path / "report.md",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert execution.source_backend == "llm/cloud"
    assert execution.model == "jiutian-lan-comv3"
    assert latest.artifacts[0].source_backend == "llm/cloud"
    assert latest.executions[0].model == "jiutian-lan-comv3"
    assert manifest["executions"][0]["model"] == "jiutian-lan-comv3"
    assert manifest["llm_runs"][0]["model"] == "jiutian-lan-comv3"


def test_runner_blocks_code_mock_when_real_code_required() -> None:
    state_store = InMemoryStateStore()
    runner = Runner(state_store, require_real_code_outputs=True)
    controller = LeadController(
        workflow_template=WorkflowTemplate(),
        state_store=state_store,
        runner=runner,
    )
    state = controller.initialize_project("实现费用报销系统")
    workitem = WorkItem(
        id="workitem-backend",
        description="实现报销 API",
        stage="development",
        kind="api_implementation",
    )
    state.workitems = [workitem]
    state_store.save_state(state)
    agent = Agent(
        id="agent-backend",
        role="backend_engineer",
        capabilities=[Capability.CODING],
        backend="mock",
        execution_backend="mock",
    )

    execution = runner.run(state.project.id, workitem, agent)

    assert execution.status == ExecutionStatus.FAILED
    assert "不会用 mock 文档冒充实现" in execution.result


def test_runner_code_execution_allows_no_tests_until_testing_stage(monkeypatch) -> None:
    monkeypatch.setattr("conductor.agents.cli_executor.shutil.which", lambda name: f"C:/bin/{name}.cmd")
    state_store = InMemoryStateStore()
    runner = Runner(
        state_store,
        shell_harness=FakeCodeExecutionNoTestsHarness(),
        cli_selection_config=CLISelectionConfig(
            selected_cli_names=["codex"],
            role_cli_bindings={"backend_engineer": "codex"},
        ),
    )
    controller = LeadController(
        workflow_template=WorkflowTemplate(),
        state_store=state_store,
        runner=runner,
    )
    state = controller.initialize_project("实现后端 API，测试阶段后补测试")
    workitem = WorkItem(
        id="workitem-backend",
        description="实现后端 API",
        stage="development",
        kind="api_implementation",
    )
    state.workitems = [workitem]
    state_store.save_state(state)
    agent = Agent(
        id="agent-backend",
        role="backend_engineer",
        capabilities=[Capability.CODING],
        backend="mock",
        execution_backend="cli",
    )

    execution = runner.run(state.project.id, workitem, agent)

    assert execution.status == ExecutionStatus.SUCCESS
    assert "Exit Code: `5`" in execution.result
