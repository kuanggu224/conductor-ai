"""WorkItem runner."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from conductor.agents.agent import Agent
from conductor.agents.cli_executor import AgentCLIExecution, AgentCLIExecutor
from conductor.artifacts.store import ArtifactStore
from conductor.config.cli import CLISelectionConfig
from conductor.config.llm import LLMUsagePolicy
from conductor.context.builder import ContextBuilder
from conductor.context.models import ContextPack
from conductor.domain.models import Artifact, Execution, ExecutionStatus, WorkItem, WorkItemStatus
from conductor.harness.base import BaseHarness
from conductor.harness.models import HarnessRequest, HarnessResult
from conductor.harness.shell import ShellHarness
from conductor.execution.runtime_stream import RuntimeStreamStore
from conductor.state.store import InMemoryStateStore


@dataclass(slots=True)
class WorkItemRunResult:
    """Normalized runner result."""

    content: str
    source_backend: str
    succeeded: bool = True


class Runner:
    """Execute workitems through Agent CLI, harness, LLM, or mock fallback."""

    HARNESS_WORKITEM_KINDS = {"automated_test", "api_validation", "ui_validation"}
    CODE_EDIT_WORKITEM_KINDS = {"api_implementation", "data_implementation", "generic_implementation", "ui_implementation"}
    CODE_EDIT_AGENT_ROLES = {"backend_engineer", "frontend_engineer"}

    def __init__(
        self,
        state_store: InMemoryStateStore,
        llm_usage_policy: LLMUsagePolicy | None = None,
        artifact_store: ArtifactStore | None = None,
        shell_harness: BaseHarness | None = None,
        enable_tester_harness: bool = False,
        cli_selection_config: CLISelectionConfig | None = None,
        runtime_stream_store: RuntimeStreamStore | None = None,
    ) -> None:
        self.state_store = state_store
        self.llm_usage_policy = llm_usage_policy or LLMUsagePolicy()
        self.artifact_store = artifact_store or ArtifactStore()
        self.context_builder = ContextBuilder(artifact_store=self.artifact_store)
        self._failed_once_workitems: set[str] = set()
        self.shell_harness = shell_harness or ShellHarness()
        self.enable_tester_harness = enable_tester_harness
        self.cli_selection_config = cli_selection_config or CLISelectionConfig()
        self.runtime_stream_store = runtime_stream_store or RuntimeStreamStore()
        self.agent_cli_executor = AgentCLIExecutor(
            cli_selection_config=self.cli_selection_config,
            shell_harness=self.shell_harness,
        )

    def run(self, project_id: str, workitem: WorkItem, agent: Agent) -> Execution:
        """Execute a workitem and persist state updates."""
        self._start_runtime_stream(project_id, workitem, agent)
        self.state_store.update_workitem(
            project_id=project_id,
            workitem_id=workitem.id,
            status=WorkItemStatus.RUNNING,
            owner_agent=agent.id,
        )
        self.state_store.add_event(project_id, f"WorkItem {workitem.id} 开始执行，Agent={agent.id}")

        if self._should_fail_once(workitem):
            result = f"模拟失败: {workitem.description}"
            self._failed_once_workitems.add(workitem.id)
            execution = Execution(
                workitem_id=workitem.id,
                agent_id=agent.id,
                result=result,
                status=ExecutionStatus.FAILED,
            )
            self.state_store.update_workitem(
                project_id=project_id,
                workitem_id=workitem.id,
                status=WorkItemStatus.FAILED,
                owner_agent=agent.id,
                result=result,
            )
            self.state_store.add_event(project_id, f"WorkItem {workitem.id} 执行失败")
            self._finish_runtime_stream(project_id, succeeded=False, message=f"[mock] {result}")
            return execution

        run_result = self._execute_workitem(project_id, workitem, agent)
        execution_status = ExecutionStatus.SUCCESS if run_result.succeeded else ExecutionStatus.FAILED
        workitem_status = WorkItemStatus.DONE if run_result.succeeded else WorkItemStatus.FAILED
        execution = Execution(
            workitem_id=workitem.id,
            agent_id=agent.id,
            result=run_result.content,
            status=execution_status,
        )
        self.state_store.update_workitem(
            project_id=project_id,
            workitem_id=workitem.id,
            status=workitem_status,
            owner_agent=agent.id,
            result=run_result.content,
        )
        self._create_document_artifact(project_id, workitem, agent, run_result)
        self.state_store.add_event(
            project_id,
            f"WorkItem {workitem.id} {'执行完成' if run_result.succeeded else '执行失败'}",
        )
        self._finish_runtime_stream(
            project_id,
            succeeded=run_result.succeeded,
            message=self._summarize_runtime_result(run_result.content),
        )
        return execution

    def _execute_workitem(self, project_id: str, workitem: WorkItem, agent: Agent) -> WorkItemRunResult:
        """Choose the right execution path for the workitem."""
        project_root = self._project_root(project_id)
        if self.agent_cli_executor.is_binding_disabled(agent):
            self.state_store.add_event(
                project_id,
                f"Agent {agent.id} 绑定的 CLI 因 provider 兼容性问题已在当前进程内停用，直接回退到后备执行链路。",
            )
        if self._should_use_agent_cli(agent):
            is_code_edit = self._should_use_agent_cli_code_execution(workitem, agent)
            cli_name = self._resolve_agent_cli_binding(agent) or "-"
            prompt = (
                self._build_code_execution_prompt(project_id, workitem, agent)
                if is_code_edit
                else self._build_agent_cli_document_prompt(workitem, agent, cli_name)
            )
            self.state_store.add_event(project_id, f"WorkItem {workitem.id} 使用 Agent CLI 执行，role={agent.role}, cli={cli_name}")
            try:
                cli_execution = (
                    self._execute_code_edit_with_retry(project_id, workitem, agent, prompt)
                    if is_code_edit
                    else self.agent_cli_executor.execute(
                        agent,
                        prompt,
                        execution_mode="documentation",
                        timeout_seconds=180.0,
                        working_directory=project_root,
                        stream_callback=self._build_stream_callback(project_id),
                    )
                )
                if cli_execution is None:
                    raise RuntimeError("未找到有效 Agent CLI 绑定")
                if is_code_edit:
                    return self._finalize_code_execution(project_id, workitem, agent, cli_execution)
                cli_stdout = (cli_execution.result.stdout or "").strip()
                if cli_execution.result.success and cli_stdout:
                    return WorkItemRunResult(
                        content=cli_stdout,
                        source_backend=f"agent_cli/{cli_execution.cli_name}",
                    )
                if "Unsupported reasoning_effort type" in cli_stdout:
                    self.state_store.add_event(
                        project_id,
                        f"WorkItem {workitem.id} 的 {cli_name} provider 与 reasoning_effort 参数不兼容，当前进程内已停用该绑定并回退。",
                    )
                self.state_store.add_event(
                    project_id,
                    f"WorkItem {workitem.id} Agent CLI 执行失败，回退到后备执行链路。exit_code={cli_execution.result.exit_code}",
                )
            except Exception as error:
                self.state_store.add_event(project_id, f"WorkItem {workitem.id} Agent CLI 调用异常，回退到后备执行链路: {error}")

        if self._should_use_harness(workitem, agent):
            request = self._build_harness_request(workitem, project_root, self._build_stream_callback(project_id))
            self.state_store.add_event(
                project_id,
                f"WorkItem {workitem.id} 使用 Harness 执行，role={agent.role}, harness={self.shell_harness.name}",
            )
            try:
                harness_result = self.shell_harness.run(request)
                return WorkItemRunResult(
                    content=self._build_harness_report(workitem, agent, request, harness_result),
                    source_backend=f"cli/{self.shell_harness.name}",
                    succeeded=harness_result.success,
                )
            except Exception as error:
                self.state_store.add_event(project_id, f"WorkItem {workitem.id} Harness 执行失败，使用 mock fallback: {error}")
                return WorkItemRunResult(
                    content=self._build_mock_document(workitem, agent, "Harness 调用失败后的模拟兜底产物"),
                    source_backend="mock_fallback",
                )

        if self._should_use_llm(workitem, agent):
            preferred_backend = self.llm_usage_policy.preferred_backend or agent.preferred_llm_backend
            self.state_store.add_event(project_id, f"WorkItem {workitem.id} 使用 LLM 执行，role={agent.role}, backend={preferred_backend}")
            try:
                return WorkItemRunResult(
                    content=agent.think(
                        prompt=self._build_document_prompt(workitem, agent),
                        context_pack=self._build_context_pack(project_id, workitem),
                        preferred_backend=preferred_backend,
                    ),
                    source_backend=f"llm/{preferred_backend}",
                )
            except Exception as error:
                self.state_store.add_event(project_id, f"WorkItem {workitem.id} LLM 执行失败，使用 mock fallback: {error}")
                return WorkItemRunResult(
                    content=self._build_mock_document(workitem, agent, "LLM 调用失败后的模拟兜底产物"),
                    source_backend="mock_fallback",
                )

        if agent.execution_backend == "cli":
            self.state_store.add_event(project_id, f"Agent {agent.id} 预设 CLI backend，当前未启用，使用 mock fallback")
            return WorkItemRunResult(
                content=self._build_mock_document(workitem, agent, "CLI 未启用时的模拟兜底产物"),
                source_backend="mock_fallback",
            )
        return WorkItemRunResult(
            content=self._build_mock_document(workitem, agent, "开发期模拟产物"),
            source_backend="mock",
        )

    def _finalize_code_execution(
        self,
        project_id: str,
        workitem: WorkItem,
        agent: Agent,
        cli_execution: AgentCLIExecution,
    ) -> WorkItemRunResult:
        """Close the loop for real code-edit execution."""
        cli_result = cli_execution.result
        cli_stdout = (cli_result.stdout or "").strip()
        cli_stderr = (cli_result.stderr or "").strip()
        changed_files = cli_result.changed_files
        if not cli_result.success:
            report = self._build_code_execution_report(
                workitem=workitem,
                agent=agent,
                cli_name=cli_execution.cli_name,
                changed_files=changed_files,
                cli_stdout=cli_stdout,
                cli_stderr=cli_stderr,
                validation_result=None,
                success=False,
            )
            return WorkItemRunResult(
                content=report,
                source_backend=f"agent_cli/{cli_execution.cli_name}",
                succeeded=False,
            )
        if not changed_files:
            self.state_store.add_event(project_id, f"WorkItem {workitem.id} Agent CLI 未产生代码变更")
            report = self._build_code_execution_report(
                workitem=workitem,
                agent=agent,
                cli_name=cli_execution.cli_name,
                changed_files=[],
                cli_stdout=cli_stdout,
                cli_stderr=cli_stderr,
                validation_result=None,
                success=False,
            )
            return WorkItemRunResult(
                content=report,
                source_backend=f"agent_cli/{cli_execution.cli_name}",
                succeeded=False,
            )

        validation_result = self._run_post_edit_validation(workitem, self._project_root(project_id))
        if validation_result.success:
            self.state_store.add_event(project_id, f"WorkItem {workitem.id} 代码变更后自动验证通过")
        else:
            self.state_store.add_event(project_id, f"WorkItem {workitem.id} 代码变更后自动验证失败")
        report = self._build_code_execution_report(
            workitem=workitem,
            agent=agent,
            cli_name=cli_execution.cli_name,
            changed_files=changed_files,
            cli_stdout=cli_stdout,
            cli_stderr=cli_stderr,
            validation_result=validation_result,
            success=validation_result.success,
        )
        return WorkItemRunResult(
            content=report,
            source_backend=f"agent_cli/{cli_execution.cli_name}",
            succeeded=validation_result.success,
        )

    def _execute_code_edit_with_retry(
        self,
        project_id: str,
        workitem: WorkItem,
        agent: Agent,
        prompt: str,
    ) -> AgentCLIExecution | None:
        """Execute code-edit mode and retry once when the CLI returns without actual edits."""
        project_root = self._project_root(project_id)
        cli_execution = self.agent_cli_executor.execute(
            agent,
            prompt,
            execution_mode="code_edit",
            timeout_seconds=600.0,
            track_workspace_changes=True,
            workspace_root=project_root,
            working_directory=project_root,
            stream_callback=self._build_stream_callback(project_id),
        )
        if cli_execution is None:
            return None
        if not cli_execution.result.success or cli_execution.result.changed_files:
            return cli_execution
        self.state_store.add_event(project_id, f"WorkItem {workitem.id} 首次代码执行未产生变更，触发一次强化重试")
        retry_prompt = (
            f"{prompt}\n\n"
            "# 上一次执行结果\n"
            f"{(cli_execution.result.stdout or '').strip()}\n\n"
            "上一次你没有真正修改任何文件，这次必须直接修改代码文件并让测试通过。"
            "禁止只输出建议、说明或手工步骤；如果没有完成实际修改，这次执行视为失败。"
        )
        retry_execution = self.agent_cli_executor.execute(
            agent,
            retry_prompt,
            execution_mode="code_edit",
            timeout_seconds=600.0,
            track_workspace_changes=True,
            workspace_root=project_root,
            working_directory=project_root,
            stream_callback=self._build_stream_callback(project_id),
        )
        return retry_execution or cli_execution

    def _should_fail_once(self, workitem: WorkItem) -> bool:
        """Return whether the workitem should fail once for retry tests."""
        return workitem.kind == "fail_once" and workitem.id not in self._failed_once_workitems

    def _should_use_harness(self, workitem: WorkItem, agent: Agent) -> bool:
        """Return whether the workitem should use tester harness."""
        return (
            self.enable_tester_harness
            and self.shell_harness is not None
            and not self._should_use_agent_cli(agent)
            and agent.role == "tester"
            and agent.execution_backend == "cli"
            and workitem.kind in self.HARNESS_WORKITEM_KINDS
        )

    def _should_use_agent_cli(self, agent: Agent) -> bool:
        """Return whether the agent has an active bound CLI."""
        return bool(self._resolve_agent_cli_binding(agent))

    def _should_use_agent_cli_code_execution(self, workitem: WorkItem, agent: Agent) -> bool:
        """Return whether the current workitem should use code-edit mode."""
        return agent.role in self.CODE_EDIT_AGENT_ROLES and workitem.kind in self.CODE_EDIT_WORKITEM_KINDS

    def _resolve_agent_cli_binding(self, agent: Agent) -> str | None:
        """Resolve the active CLI binding for the agent."""
        return self.agent_cli_executor.resolve_binding(agent)

    def _should_use_llm(self, workitem: WorkItem, agent: Agent) -> bool:
        """Return whether LLM execution is allowed for this agent and workitem."""
        return (
            self.llm_usage_policy.runner_enabled
            and agent.llm_backend is not None
            and agent.role in self.llm_usage_policy.runner_allowed_roles
            and workitem.kind in self.llm_usage_policy.runner_allowed_kinds
        )

    def _build_document_prompt(self, workitem: WorkItem, agent: Agent) -> str:
        """Build the document-style execution prompt."""
        criteria = "\n".join(f"- {item}" for item in workitem.acceptance_criteria) or "- 无显式验收标准"
        role_instruction = {
            "designer": "请产出产品/设计文档，不要反问用户；信息不足时基于现有需求给出合理假设。",
            "backend_engineer": "请产出后端实现说明文档，不要写入文件、不执行命令；包含接口、数据结构、关键流程和风险。",
            "frontend_engineer": "请产出前端实现说明文档，不要写入文件、不执行命令；包含页面结构、组件拆分、状态和交互。",
            "tester": "请产出测试/验收文档，不要执行命令；包含测试范围、测试用例、验收标准和风险。",
        }.get(agent.role, "请产出该 WorkItem 的执行说明文档，不要写入文件、不执行命令。")
        return (
            f"你是 Conductor 的 {agent.role} agent。\n"
            f"{role_instruction}\n"
            f"WorkItem ID: {workitem.id}\n"
            f"类型: {workitem.kind}\n"
            f"描述: {workitem.description}\n"
            f"验收标准:\n{criteria}\n\n"
            "请使用中文输出结构化 Markdown 文档，包含：目标、关键假设、方案、交付物、风险。"
        )

    def _build_agent_cli_document_prompt(self, workitem: WorkItem, agent: Agent, cli_name: str) -> str:
        """Build CLI-specific document prompts when a provider has special constraints."""
        if cli_name == "claude":
            return self._build_compact_claude_document_prompt(workitem, agent)
        return self._build_document_prompt(workitem, agent)

    def _build_compact_claude_document_prompt(self, workitem: WorkItem, agent: Agent) -> str:
        """Build a compact English prompt for Claude CLI, but keep Chinese output."""
        kind_focus = {
            "design_overview": "Create an overall requirement and design brief with implementation boundaries.",
            "ui_design": "Describe UI structure, core interactions, primary views, and state changes.",
            "api_design": "Describe API boundaries, payloads, main endpoints, and failure handling.",
            "test_design": "Describe testing scope, acceptance checks, edge cases, and validation focus.",
            "acceptance_check": "Summarize acceptance status, unresolved risks, and release readiness.",
        }.get(workitem.kind, "Produce a concise execution document for this work item.")
        criteria = "\n".join(f"- {item}" for item in workitem.acceptance_criteria) or "- No explicit acceptance criteria"
        return (
            f"You are the {agent.role} agent in Conductor.\n"
            f"Task focus: {kind_focus}\n"
            f"Work item kind: {workitem.kind}\n"
            f"Stage: {workitem.stage}\n"
            f"Acceptance checklist:\n{criteria}\n\n"
            "Return concise Chinese markdown.\n"
            "Do not ask follow-up questions.\n"
            "Use these sections: 目标, 关键假设, 方案, 交付物, 风险.\n"
        )

    def _build_code_execution_prompt(self, project_id: str, workitem: WorkItem, agent: Agent) -> str:
        """Build the real code-edit prompt."""
        context_pack = self._build_context_pack(project_id, workitem)
        criteria = "; ".join(workitem.acceptance_criteria) or "no explicit acceptance criteria"
        artifact_context = "\n\n".join(
            f"- {artifact[:400]}"
            for artifact in context_pack.artifacts[-3:]
        )
        role_hint = ""
        if agent.role == "frontend_engineer":
            role_hint = "Focus on UI files first, such as html, css, js, tsx, jsx, or template files.\n"
        elif agent.role == "backend_engineer":
            role_hint = "Focus on backend and service files first, then update tests if needed.\n"

        prompt = (
            "You must directly edit files in the current workspace.\n"
            "Do not ask follow-up questions. Do not stop at analysis. Do not only describe a plan.\n"
            "Inspect the relevant files, make the minimum code changes required, run pytest -q, and finish.\n\n"
            f"Agent role: {agent.role}\n"
            f"{role_hint}"
            f"Task: {workitem.description}\n"
            f"WorkItem kind: {workitem.kind}\n"
            f"Acceptance criteria: {criteria}\n"
            "If the task text mentions a file name, start from that file.\n"
        )
        if artifact_context:
            prompt += f"\nUpstream context:\n{artifact_context}\n"
        prompt += "\nAfter the code and tests are done, output exactly: done"
        return prompt

    def _build_context_pack(self, project_id: str, workitem: WorkItem) -> ContextPack:
        """Build a lightweight context pack for the current workitem."""
        state = self.state_store.get_state(project_id)
        return self.context_builder.build(state=state, workitem=workitem)

    def _build_harness_request(self, workitem: WorkItem, working_directory: str, stream_callback=None) -> HarnessRequest:
        """Build tester harness request."""
        return HarnessRequest(
            command=self._select_test_command(),
            working_directory=working_directory,
            timeout_seconds=180.0,
            description=f"{workitem.kind}:{workitem.description}",
            stream_callback=stream_callback,
        )

    def _run_post_edit_validation(self, workitem: WorkItem, working_directory: str) -> HarnessResult:
        """Run a post-edit validation pass."""
        request = HarnessRequest(
            command=self._select_test_command(),
            working_directory=working_directory,
            timeout_seconds=300.0,
            description=f"post-validate:{workitem.id}",
            stream_callback=self._build_stream_callback_from_workitem(workitem.id),
        )
        return self.shell_harness.run(request)

    def _build_stream_callback(self, project_id: str):
        """Build a runtime stream callback for a project."""
        def callback(channel: str, line: str) -> None:
            self.runtime_stream_store.append(project_id, channel, line)

        return callback

    def _build_stream_callback_from_workitem(self, workitem_id: str):
        """Build a stream callback by resolving project from the workitem."""
        for state in self.state_store.list_states():
            if any(item.id == workitem_id for item in state.workitems):
                return self._build_stream_callback(state.project.id)
        return None

    def _start_runtime_stream(self, project_id: str, workitem: WorkItem, agent: Agent) -> None:
        """Initialize live stream metadata for one workitem run."""
        cli_name = self._resolve_agent_cli_binding(agent) or "-"
        backend = "llm" if self._should_use_llm(workitem, agent) else ("harness" if self._should_use_harness(workitem, agent) else "mock")
        if cli_name != "-":
            backend = "agent_cli"
        self.runtime_stream_store.start(
            project_id=project_id,
            workitem_id=workitem.id,
            agent_role=agent.role,
            backend=backend,
            cli_name=cli_name,
        )

    def _finish_runtime_stream(self, project_id: str, succeeded: bool, message: str) -> None:
        """Finalize live stream metadata for the workitem."""
        self.runtime_stream_store.finish(project_id, success=succeeded, message=message)

    def _summarize_runtime_result(self, content: str, limit: int = 160) -> str:
        """Build a compact terminal message for the runtime stream."""
        compact = " ".join(content.split())
        if not compact:
            return "[done]"
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1].rstrip() + "…"

    def _select_test_command(self) -> list[str]:
        """Choose the local validation command."""
        if shutil.which("uv"):
            return ["uv", "run", "python", "-m", "pytest", "-q"]
        if shutil.which("pytest"):
            return ["pytest", "-q"]
        return [sys.executable, "-m", "pytest", "-q"]

    def _build_harness_report(
        self,
        workitem: WorkItem,
        agent: Agent,
        request: HarnessRequest,
        result: HarnessResult,
    ) -> str:
        """Convert a harness result into a Markdown report."""
        status_label = "通过" if result.success else "失败"
        stdout = (result.stdout or "").strip() or "(无 stdout)"
        stderr = (result.stderr or "").strip() or "(无 stderr)"
        command = " ".join(request.command)
        return (
            f"# 测试执行报告 - {workitem.id}\n\n"
            "## 目标\n"
            f"- 由 `{agent.role}` 使用 Harness 对当前测试类 WorkItem 进行真实执行验证。\n"
            f"- 当前类型：`{workitem.kind}`。\n\n"
            "## 执行摘要\n"
            f"- 状态：{status_label}\n"
            f"- Exit Code: `{result.exit_code}`\n"
            f"- Duration: `{result.duration_ms}ms`\n"
            f"- Working Directory: `{request.working_directory}`\n"
            f"- Command: `{command}`\n\n"
            "## 原始工作项\n"
            f"- 描述：{workitem.description}\n"
            f"- 阶段：{workitem.stage}\n\n"
            f"## stdout\n```text\n{stdout}\n```\n\n"
            f"## stderr\n```text\n{stderr}\n```\n\n"
            "## 结论\n"
            f"- 当前测试执行{status_label}。\n"
            "- 若失败，Gate 应据此进入重试或升级路径。\n"
        )

    def _build_code_execution_report(
        self,
        workitem: WorkItem,
        agent: Agent,
        cli_name: str,
        changed_files: list[str],
        cli_stdout: str,
        cli_stderr: str,
        validation_result: HarnessResult | None,
        success: bool,
    ) -> str:
        """Convert a real code-edit execution into a Markdown report."""
        change_lines = "\n".join(f"- `{path}`" for path in changed_files) or "- 无"
        validation_stdout = ((validation_result.stdout or "") if validation_result else "").strip() or "(无 stdout)"
        validation_stderr = ((validation_result.stderr or "") if validation_result else "").strip() or "(无 stderr)"
        validation_section = (
            "## 自动验证\n"
            f"- Command: `{' '.join(self._select_test_command())}`\n"
            f"- Exit Code: `{validation_result.exit_code}`\n"
            f"- Duration: `{validation_result.duration_ms}ms`\n"
            f"```text\n{validation_stdout}\n```\n\n"
            f"```text\n{validation_stderr}\n```\n"
            if validation_result is not None
            else "## 自动验证\n- 未执行\n"
        )
        status_label = "成功" if success else "失败"
        cli_stdout = cli_stdout or "(无 stdout)"
        cli_stderr = cli_stderr or "(无 stderr)"
        return (
            f"# 代码执行报告 - {workitem.id}\n\n"
            "## 执行摘要\n"
            f"- 角色: `{agent.role}`\n"
            f"- CLI: `{cli_name}`\n"
            f"- WorkItem 类型: `{workitem.kind}`\n"
            f"- 结果: {status_label}\n\n"
            f"## 改动文件\n{change_lines}\n\n"
            f"## CLI 输出\n```text\n{cli_stdout}\n```\n\n"
            f"## CLI 错误输出\n```text\n{cli_stderr}\n```\n\n"
            f"{validation_section}\n"
            "## 结论\n"
            "- 本次执行已真实作用于当前仓库。\n"
            "- 若自动验证失败，当前 WorkItem 会进入失败路径，由 Gate 决定重试或升级。\n"
        )

    def _create_document_artifact(
        self,
        project_id: str,
        workitem: WorkItem,
        agent: Agent,
        run_result: WorkItemRunResult,
    ) -> None:
        """Persist the execution result as an artifact."""
        parent_artifact = self._find_previous_artifact(project_id, workitem.id, workitem.kind)
        next_version = (parent_artifact.version + 1) if parent_artifact else 1
        artifact_id = f"artifact-{workitem.id}" if next_version == 1 else f"artifact-{workitem.id}-v{next_version}"
        artifact = Artifact(
            id=artifact_id,
            project_id=project_id,
            workitem_id=workitem.id,
            agent_id=agent.id,
            kind=workitem.kind,
            title=self._build_artifact_title(workitem, agent, run_result),
            content=run_result.content,
            source_backend=run_result.source_backend,
            parent_artifact_id=parent_artifact.id if parent_artifact else None,
            derived_from=[parent_artifact.id] if parent_artifact else [],
            version=next_version,
        )
        persisted_artifact = self.artifact_store.save_markdown(artifact, project_root=self._project_root(project_id))
        self.state_store.add_artifact(project_id, persisted_artifact)
        self.state_store.add_event(project_id, f"产物 {artifact.id} 已创建，来源 WorkItem={workitem.id}")

    def _project_root(self, project_id: str) -> str:
        """Return project working directory."""
        state = self.state_store.get_state(project_id)
        return state.project.project_root or str(Path.cwd())

    def _build_artifact_title(
        self,
        workitem: WorkItem,
        agent: Agent,
        run_result: WorkItemRunResult | None = None,
    ) -> str:
        """Build artifact title."""
        if run_result and run_result.source_backend.startswith("cli/"):
            return f"测试执行报告 - {workitem.id}"
        if run_result and run_result.source_backend.startswith("agent_cli/") and workitem.kind in self.CODE_EDIT_WORKITEM_KINDS:
            return f"代码执行报告 - {workitem.id}"
        role_title = {
            "designer": "产品/设计文档",
            "backend_engineer": "后端实现说明",
            "frontend_engineer": "前端实现说明",
            "tester": "测试/验收文档",
        }.get(agent.role, "执行说明文档")
        return f"{role_title} - {workitem.id}"

    def _build_mock_document(self, workitem: WorkItem, agent: Agent, source_note: str) -> str:
        """Build a high-quality mock artifact."""
        criteria = "\n".join(f"- {item}" for item in workitem.acceptance_criteria) or "- 当前工作项未提供显式验收标准"
        role_sections = {
            "designer": self._mock_designer_sections(workitem),
            "backend_engineer": self._mock_backend_sections(workitem),
            "frontend_engineer": self._mock_frontend_sections(workitem),
            "tester": self._mock_tester_sections(workitem),
        }.get(agent.role, self._mock_generic_sections(workitem))
        return (
            f"# {self._build_artifact_title(workitem, agent)}\n\n"
            f"> 来源说明：{source_note}。该内容用于开发期验证，不代表真实 Agent 交付物。\n\n"
            "## 目标\n"
            f"围绕 `{workitem.kind}` 完成工作项 `{workitem.id}` 的文档化输出，支撑后续阶段理解需求边界和执行重点。\n\n"
            "## 输入\n"
            f"- WorkItem: {workitem.description}\n"
            f"- 阶段: {workitem.stage}\n"
            f"- 角色: {agent.role}\n\n"
            f"## 验收标准\n{criteria}\n\n"
            f"{role_sections}\n\n"
            "## 风险与后续\n"
            "- 当前内容为模拟文档，需要在真实 LLM 或 CLI backend 接入后由真实产物替换。\n"
            "- 可通过 Artifact 的 `source_backend` 字段识别该产物是否来自 mock、mock fallback 或真实后端。\n"
        )

    def _find_previous_artifact(self, project_id: str, workitem_id: str, kind: str) -> Artifact | None:
        """Find the latest artifact for the same workitem and kind."""
        state = self.state_store.get_state(project_id)
        for artifact in reversed(state.artifacts):
            if artifact.workitem_id == workitem_id and artifact.kind == kind:
                return artifact
        return None

    def _mock_designer_sections(self, workitem: WorkItem) -> str:
        return (
            "## 方案\n"
            "- 明确用户目标、核心场景和非目标范围。\n"
            "- 将需求拆成页面、接口、验收三类交付关注点。\n"
            "- 优先保证主路径闭环，再补充异常和边界条件。\n\n"
            "## 交付物\n"
            "- 一份产品/设计说明。\n"
            "- 一组可交给研发和测试使用的验收口径。\n"
        )

    def _mock_backend_sections(self, workitem: WorkItem) -> str:
        return (
            "## 方案\n"
            "- 定义 REST API 的资源、动作和错误返回。\n"
            "- 将核心状态变化封装在服务层，避免接口层直接操作内部状态。\n"
            "- 保留输入校验和幂等性检查位置。\n\n"
            "## 交付物\n"
            "- API 设计说明。\n"
            "- 数据结构和关键流程说明。\n"
        )

    def _mock_frontend_sections(self, workitem: WorkItem) -> str:
        return (
            "## 方案\n"
            "- 页面围绕主操作流组织：输入、列表、状态反馈和错误提示。\n"
            "- 将组件拆分为容器、表单、列表项和状态展示区。\n"
            "- 保持交互轻量，先不引入复杂前端状态管理。\n\n"
            "## 交付物\n"
            "- 页面结构说明。\n"
            "- 组件拆分和交互状态说明。\n"
        )

    def _mock_tester_sections(self, workitem: WorkItem) -> str:
        return (
            "## 方案\n"
            "- 覆盖主路径、异常输入、边界条件和回归风险。\n"
            "- 将测试用例按接口、页面交互和验收标准分组。\n"
            "- 对不可自动化的检查项保留人工验收说明。\n\n"
            "## 交付物\n"
            "- 测试计划。\n"
            "- 验收用例清单。\n"
        )

    def _mock_generic_sections(self, workitem: WorkItem) -> str:
        return (
            "## 方案\n"
            "- 明确当前工作项的输入、输出和完成条件。\n"
            "- 保持产物为文档形式，不执行真实环境修改。\n\n"
            "## 交付物\n"
            "- 工作项执行说明。\n"
        )
