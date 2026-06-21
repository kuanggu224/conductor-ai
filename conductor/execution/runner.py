"""WorkItem runner."""

from __future__ import annotations

import shutil
import sys
import json
import re
import os
import hashlib
from dataclasses import dataclass
from pathlib import Path

from conductor.agents.agent import Agent
from conductor.agents.cli_executor import AgentCLIExecution, AgentCLIExecutor
from conductor.agents.llm import LLMHTTPConfig, LLMRequest
from conductor.artifacts.store import ArtifactStore
from conductor.artifacts.scope_contract import evaluate_scope_contract
from conductor.config.cli import CLISelectionConfig
from conductor.config.llm import LLMUsagePolicy
from conductor.context.builder import ContextBuilder
from conductor.context.models import ContextPack
from conductor.delivery_contract import (
    build_acceptance_trace,
    build_delivery_contract,
    render_acceptance_trace_markdown,
    render_delivery_contract_markdown,
)
from conductor.domain.models import Artifact, Execution, ExecutionStatus, WorkItem, WorkItemStatus
from conductor.harness.base import BaseHarness
from conductor.harness.llm import LLMHarnessRequest, OpenAICompatibleLLMHarness
from conductor.harness.models import HarnessRequest, HarnessResult
from conductor.harness.shell import ShellHarness
from conductor.io.encoding import looks_like_mojibake
from conductor.testing.coverage import CoverageResult, evaluate_requirement_coverage, infer_coverage_rules
from conductor.execution.runtime_stream import RuntimeStreamStore
from conductor.execution.failure_policy import (
    FailureDecision,
    FailureType,
    classify_cli_failure,
    classify_harness_failure,
    configuration_required,
    format_failure_reason,
    no_code_changes,
    validation_failed,
)
from conductor.state.store import InMemoryStateStore


@dataclass(frozen=True, slots=True)
class WorkItemRunResult:
    """Normalized runner result."""

    content: str
    source_backend: str
    succeeded: bool = True
    failure: FailureDecision | None = None
    cli_name: str = ""
    model: str = ""
    working_directory: str = ""
    execution_command: list[str] | None = None
    execution_exit_code: int | None = None
    execution_duration_ms: int | None = None
    prompt_hash: str = ""
    changed_files: list[str] | None = None
    validation_command: list[str] | None = None
    validation_exit_code: int | None = None
    validation_success: bool | None = None
    cli_stdout_tail: str = ""
    cli_stderr_tail: str = ""
    token_usage: dict[str, int] | None = None


class Runner:
    """Execute workitems through Agent CLI, harness, LLM, or mock fallback."""

    HARNESS_WORKITEM_KINDS = {"acceptance_check", "automated_test", "api_validation"}
    CODE_EDIT_WORKITEM_KINDS = {"api_implementation", "data_implementation", "generic_implementation"}
    CODE_EDIT_AGENT_ROLES = {"backend_engineer"}
    DESIGN_DOCUMENT_WORKITEM_KINDS = {"requirement_spec", "design_overview", "feature_slice_plan", "api_design", "test_design"}

    def __init__(
        self,
        state_store: InMemoryStateStore,
        llm_usage_policy: LLMUsagePolicy | None = None,
        artifact_store: ArtifactStore | None = None,
        shell_harness: BaseHarness | None = None,
        enable_tester_harness: bool = False,
        enable_api_mock_delivery: bool = False,
        enable_api_sqlite_delivery: bool = False,
        cli_selection_config: CLISelectionConfig | None = None,
        runtime_stream_store: RuntimeStreamStore | None = None,
        require_real_design_outputs: bool = False,
        require_real_code_outputs: bool = False,
        llm_harness: OpenAICompatibleLLMHarness | None = None,
        llm_harness_config: LLMHTTPConfig | None = None,
    ) -> None:
        self.state_store = state_store
        self.llm_usage_policy = llm_usage_policy or LLMUsagePolicy()
        self.artifact_store = artifact_store or ArtifactStore()
        self.context_builder = ContextBuilder(artifact_store=self.artifact_store)
        self._failed_once_workitems: set[str] = set()
        self._uses_default_shell_harness = shell_harness is None
        self.shell_harness = shell_harness or ShellHarness()
        self.enable_tester_harness = enable_tester_harness
        self.enable_api_mock_delivery = enable_api_mock_delivery
        self.enable_api_sqlite_delivery = enable_api_sqlite_delivery
        self.cli_selection_config = cli_selection_config or CLISelectionConfig()
        self.runtime_stream_store = runtime_stream_store or RuntimeStreamStore()
        self.require_real_design_outputs = require_real_design_outputs
        self.require_real_code_outputs = require_real_code_outputs
        self.llm_harness = llm_harness
        self.llm_harness_config = llm_harness_config
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
        self.state_store.add_event(project_id, f"WorkItem {workitem.id} 寮€濮嬫墽琛岋紝Agent={agent.id}")
        input_artifact_ids = self._context_artifact_ids(project_id, workitem)

        if self._should_fail_once(workitem):
            result = f"妯℃嫙澶辫触: {workitem.description}"
            failure = FailureDecision(FailureType.TRANSIENT, True, "Intentional fail_once retry test")
            self._failed_once_workitems.add(workitem.id)
            execution = Execution(
                workitem_id=workitem.id,
                agent_id=agent.id,
                result=result,
                status=ExecutionStatus.FAILED,
                input_artifact_ids=input_artifact_ids,
            )
            self.state_store.update_workitem(
                project_id=project_id,
                workitem_id=workitem.id,
                status=WorkItemStatus.FAILED,
                owner_agent=agent.id,
                result=result,
                blocked_reason=format_failure_reason(failure),
                failure_type=failure.failure_type.value,
                retryable=failure.retryable,
                failure_summary=failure.summary,
            )
            self.state_store.add_event(project_id, f"WorkItem {workitem.id} 鎵ц澶辫触")
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
            source_backend=run_result.source_backend,
            cli_name=run_result.cli_name,
            model=run_result.model,
            working_directory=run_result.working_directory or self._project_root(project_id),
            execution_command=[*(run_result.execution_command or [])],
            execution_exit_code=run_result.execution_exit_code,
            execution_duration_ms=run_result.execution_duration_ms,
            prompt_hash=run_result.prompt_hash,
            input_artifact_ids=input_artifact_ids,
            changed_files=[*(run_result.changed_files or [])],
            validation_command=[*(run_result.validation_command or [])],
            validation_exit_code=run_result.validation_exit_code,
            validation_success=run_result.validation_success,
            cli_stdout_tail=run_result.cli_stdout_tail,
            cli_stderr_tail=run_result.cli_stderr_tail,
            failure_type=run_result.failure.failure_type.value if run_result.failure else "",
            failure_summary=run_result.failure.summary if run_result.failure else "",
            token_usage=dict(run_result.token_usage or {}),
        )
        self.state_store.update_workitem(
            project_id=project_id,
            workitem_id=workitem.id,
            status=workitem_status,
            owner_agent=agent.id,
            result=run_result.content,
            blocked_reason=format_failure_reason(run_result.failure) if run_result.failure else None,
            failure_type=run_result.failure.failure_type.value if run_result.failure else "",
            retryable=run_result.failure.retryable if run_result.failure else True,
            failure_summary=run_result.failure.summary if run_result.failure else "",
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
        cli_failure_reason = ""
        if self.agent_cli_executor.is_binding_disabled(agent):
            self.state_store.add_event(
                project_id,
                f"Agent {agent.id} CLI binding is disabled; using fallback execution path.",
            )
        if self._should_use_llm_harness(workitem, agent):
            self.state_store.add_event(project_id, f"WorkItem {workitem.id} 浣跨敤 LLMHarness 鎵ц锛宺ole={agent.role}")
            return self._run_llm_harness(project_id, workitem, agent, project_root)

        if self._should_use_harness(workitem, agent):
            if self._uses_default_shell_harness and not self._has_project_deliverables(project_root, project_id):
                self.state_store.add_event(
                    project_id,
                    f"WorkItem {workitem.id} has no deliverable files; skipping validation harness.",
                )
                return WorkItemRunResult(
                    content=self._build_harness_skip_report(workitem, agent, project_root),
                    source_backend="cli/harness_skipped",
                )
            request = self._build_harness_request(workitem, project_root, self._build_stream_callback(project_id))
            self.state_store.add_event(
                project_id,
                f"WorkItem {workitem.id} 浣跨敤 Harness 鎵ц锛宺ole={agent.role}, harness={self.shell_harness.name}",
            )
            try:
                harness_result = self.shell_harness.run(request)
                no_tests_discovered = self._is_no_tests_discovered(harness_result)
                coverage_result = self._evaluate_requirement_coverage(project_id, harness_result, workitem)
                if no_tests_discovered:
                    self.state_store.add_event(
                        project_id,
                        f"WorkItem {workitem.id} found no tests; recording a pending-test report.",
                    )
                return WorkItemRunResult(
                    content=self._build_harness_report(workitem, agent, request, harness_result, coverage_result=coverage_result),
                    source_backend=f"cli/{self.shell_harness.name}",
                    succeeded=(harness_result.success or no_tests_discovered) and coverage_result.passed,
                    failure=(
                        None
                        if (harness_result.success or no_tests_discovered) and coverage_result.passed
                        else (
                            FailureDecision(FailureType.VALIDATION_FAILED, True, coverage_result.summary())
                            if not coverage_result.passed
                            else classify_harness_failure(harness_result)
                        )
                    ),
                    working_directory=request.working_directory,
                    execution_command=list(request.command),
                    execution_exit_code=harness_result.exit_code,
                    execution_duration_ms=harness_result.duration_ms,
                    validation_command=list(request.command),
                    validation_exit_code=harness_result.exit_code,
                    validation_success=harness_result.success and coverage_result.passed,
                    cli_stdout_tail=self._tail(harness_result.stdout),
                    cli_stderr_tail=self._tail(harness_result.stderr),
                )
            except Exception as error:
                self.state_store.add_event(project_id, f"WorkItem {workitem.id} Harness 鎵ц澶辫触锛屼娇鐢?mock fallback: {error}")
                return WorkItemRunResult(
                    content=self._build_mock_document(workitem, agent, "Harness 璋冪敤澶辫触鍚庣殑妯℃嫙鍏滃簳浜х墿"),
                    source_backend="mock_fallback",
                )

        if self._should_use_agent_cli(agent):
            is_code_edit = self._should_use_agent_cli_code_execution(workitem, agent)
            cli_name = self._resolve_agent_cli_binding(agent) or "-"
            prompt = (
                self._build_code_execution_prompt(project_id, workitem, agent, cli_name=cli_name)
                if is_code_edit
                else self._build_agent_cli_document_prompt(workitem, agent, cli_name, project_id=project_id)
            )
            self.state_store.add_event(project_id, f"WorkItem {workitem.id} 浣跨敤 Agent CLI 鎵ц锛宺ole={agent.role}, cli={cli_name}")
            try:
                cli_execution = (
                    self._execute_code_edit_with_retry(project_id, workitem, agent, prompt)
                    if is_code_edit
                    else self.agent_cli_executor.execute(
                        agent,
                        prompt,
                        execution_mode="documentation",
                        timeout_seconds=600.0 if cli_name == "opencode" else 180.0,
                        track_workspace_changes=cli_name == "opencode",
                        workspace_root=project_root if cli_name == "opencode" else None,
                        working_directory=project_root,
                        stream_callback=self._build_stream_callback(project_id),
                    )
                )
                if cli_execution is None:
                    raise RuntimeError("鏈壘鍒版湁鏁?Agent CLI 缁戝畾")
                if is_code_edit:
                    return self._finalize_code_execution(project_id, workitem, agent, cli_execution)
                file_output = self._read_agent_cli_document_file(project_root, workitem, cli_execution.cli_name)
                if file_output:
                    return WorkItemRunResult(
                        content=file_output,
                        source_backend=f"agent_cli/{cli_execution.cli_name}",
                        cli_name=cli_execution.cli_name,
                        model=self._model_for_agent_cli(cli_execution.cli_name),
                        working_directory=cli_execution.request.working_directory,
                        execution_command=list(cli_execution.redacted_command),
                        execution_exit_code=cli_execution.result.exit_code,
                        execution_duration_ms=cli_execution.result.duration_ms,
                        prompt_hash=cli_execution.prompt_hash,
                    )
                if cli_execution.cli_name == "opencode":
                    self.state_store.add_event(
                        project_id,
                        f"WorkItem {workitem.id} OpenCode did not write the required document file.",
                    )
                    return WorkItemRunResult(
                        content=self._real_backend_required_result(
                            workitem,
                            agent,
                            "OpenCode did not create the required document output file.",
                        ).content,
                        source_backend=f"agent_cli/{cli_execution.cli_name}",
                        succeeded=False,
                        failure=configuration_required("OpenCode did not create the required document output file."),
                        cli_name=cli_execution.cli_name,
                        model=self._model_for_agent_cli(cli_execution.cli_name),
                        working_directory=cli_execution.request.working_directory,
                        execution_command=list(cli_execution.redacted_command),
                        execution_exit_code=cli_execution.result.exit_code,
                        execution_duration_ms=cli_execution.result.duration_ms,
                        prompt_hash=cli_execution.prompt_hash,
                    )
                cli_stdout = (cli_execution.result.stdout or "").strip()
                if cli_execution.result.success and cli_stdout:
                    return WorkItemRunResult(
                        content=cli_stdout,
                        source_backend=f"agent_cli/{cli_execution.cli_name}",
                        cli_name=cli_execution.cli_name,
                        model=self._model_for_agent_cli(cli_execution.cli_name),
                        working_directory=cli_execution.request.working_directory,
                        execution_command=list(cli_execution.redacted_command),
                        execution_exit_code=cli_execution.result.exit_code,
                        execution_duration_ms=cli_execution.result.duration_ms,
                        prompt_hash=cli_execution.prompt_hash,
                    )
                if "Unsupported reasoning_effort type" in cli_stdout:
                    self.state_store.add_event(
                        project_id,
                        f"WorkItem {workitem.id} {cli_name} rejected reasoning_effort; disabling binding for this process.",
                    )
                self.state_store.add_event(
                    project_id,
                    f"WorkItem {workitem.id} Agent CLI 鎵ц澶辫触锛屽洖閫€鍒板悗澶囨墽琛岄摼璺€俥xit_code={cli_execution.result.exit_code}",
                )
            except Exception as error:
                self.state_store.add_event(project_id, f"WorkItem {workitem.id} Agent CLI 璋冪敤寮傚父锛屽洖閫€鍒板悗澶囨墽琛岄摼璺? {error}")

        if self._should_use_llm_code_harness(workitem, agent):
            self.state_store.add_event(project_id, f"WorkItem {workitem.id} 浣跨敤 LLMHarness 鐢熸垚浠ｇ爜锛宺ole={agent.role}")
            return self._run_llm_code_harness(project_id, workitem, agent, project_root)

        if self._should_use_harness(workitem, agent):
            if self._uses_default_shell_harness and not self._has_project_deliverables(project_root, project_id):
                self.state_store.add_event(
                    project_id,
                    f"WorkItem {workitem.id} has no deliverable files; skipping validation harness.",
                )
                return WorkItemRunResult(
                    content=self._build_harness_skip_report(workitem, agent, project_root),
                    source_backend="cli/harness_skipped",
                )
            request = self._build_harness_request(workitem, project_root, self._build_stream_callback(project_id))
            self.state_store.add_event(
                project_id,
                f"WorkItem {workitem.id} 浣跨敤 Harness 鎵ц锛宺ole={agent.role}, harness={self.shell_harness.name}",
            )
            try:
                harness_result = self.shell_harness.run(request)
                no_tests_discovered = self._is_no_tests_discovered(harness_result)
                coverage_result = self._evaluate_requirement_coverage(project_id, harness_result, workitem)
                if no_tests_discovered:
                    self.state_store.add_event(
                        project_id,
                        f"WorkItem {workitem.id} found no tests; recording a pending-test report.",
                    )
                return WorkItemRunResult(
                    content=self._build_harness_report(workitem, agent, request, harness_result, coverage_result=coverage_result),
                    source_backend=f"cli/{self.shell_harness.name}",
                    succeeded=(harness_result.success or no_tests_discovered) and coverage_result.passed,
                    failure=(
                        None
                        if (harness_result.success or no_tests_discovered) and coverage_result.passed
                        else (
                            FailureDecision(FailureType.VALIDATION_FAILED, True, coverage_result.summary())
                            if not coverage_result.passed
                            else classify_harness_failure(harness_result)
                        )
                    ),
                    working_directory=request.working_directory,
                    execution_command=list(request.command),
                    execution_exit_code=harness_result.exit_code,
                    execution_duration_ms=harness_result.duration_ms,
                    validation_command=list(request.command),
                    validation_exit_code=harness_result.exit_code,
                    validation_success=harness_result.success and coverage_result.passed,
                    cli_stdout_tail=self._tail(harness_result.stdout),
                    cli_stderr_tail=self._tail(harness_result.stderr),
                )
            except Exception as error:
                self.state_store.add_event(project_id, f"WorkItem {workitem.id} Harness 鎵ц澶辫触锛屼娇鐢?mock fallback: {error}")
                return WorkItemRunResult(
                    content=self._build_mock_document(workitem, agent, "Harness 璋冪敤澶辫触鍚庣殑妯℃嫙鍏滃簳浜х墿"),
                    source_backend="mock_fallback",
                )

        if self._should_use_llm(workitem, agent):
            preferred_backend = self.llm_usage_policy.preferred_backend or agent.preferred_llm_backend
            self.state_store.add_event(project_id, f"WorkItem {workitem.id} 浣跨敤 LLM 鎵ц锛宺ole={agent.role}, backend={preferred_backend}")
            try:
                response = agent.llm_backend.generate(
                    LLMRequest(
                        user_prompt=self._build_document_prompt(workitem, agent),
                        context_pack=self._build_context_pack(project_id, workitem),
                        preferred_backend=preferred_backend,
                    )
                )
                content = response.content
                if self._is_disabled_llm_response(content) and self._requires_real_design_output(workitem, agent):
                    return self._real_backend_required_result(workitem, agent, f"LLM backend `{preferred_backend}` is disabled.")
                self.state_store.add_event(
                    project_id,
                    f"WorkItem {workitem.id} LLM 鍝嶅簲瀹屾垚锛宲rovider={response.provider.value}, model={response.model_name}",
                )
                return WorkItemRunResult(
                    content=content,
                    source_backend=f"llm/{response.provider.value}",
                    model=response.model_name,
                )
            except Exception as error:
                self.state_store.add_event(project_id, f"WorkItem {workitem.id} LLM 鎵ц澶辫触锛屼娇鐢?mock fallback: {error}")
                if self._requires_real_design_output(workitem, agent):
                    return self._real_backend_required_result(workitem, agent, f"LLM 鎵ц澶辫触: {error}")
                return WorkItemRunResult(
                    content=self._build_mock_document(workitem, agent, "LLM 璋冪敤澶辫触鍚庣殑妯℃嫙鍏滃簳浜х墿"),
                    source_backend="mock_fallback",
                )

        if self._should_use_api_sqlite_delivery(project_id, workitem, agent):
            self.state_store.add_event(project_id, f"WorkItem {workitem.id} uses ApiSqliteDelivery for a verifiable SQLite API service")
            return self._run_api_sqlite_delivery(project_id, workitem, agent, project_root)

        if self._should_use_api_mock_delivery(project_id, workitem, agent):
            self.state_store.add_event(project_id, f"WorkItem {workitem.id} uses ApiMockDelivery for a verifiable API mock")
            return self._run_api_mock_delivery(project_id, workitem, agent, project_root)

        if self._requires_real_design_output(workitem, agent):
            return self._real_backend_required_result(
                workitem,
                agent,
                "璁捐闃舵瑕佹眰鐪熷疄 Agent 浜у嚭锛屼絾褰撳墠娌℃湁鍙敤 Agent CLI 鎴栧凡鍚敤鐨?LLM Runner",
            )

        if self._requires_real_code_output(workitem, agent):
            return self._real_backend_required_result(
                workitem,
                agent,
                "Code execution requires real output, but no usable Agent CLI binding is available.",
            )

        if agent.execution_backend == "cli":
            self.state_store.add_event(project_id, f"Agent {agent.id} 棰勮 CLI backend锛屽綋鍓嶆湭鍚敤锛屼娇鐢?mock fallback")
            return WorkItemRunResult(
                content=self._build_mock_document(workitem, agent, "CLI fallback mock artifact"),
                source_backend="mock_fallback",
            )
        return WorkItemRunResult(
            content=self._build_mock_document(workitem, agent, "寮€鍙戞湡妯℃嫙浜х墿"),
            source_backend="mock",
        )

    def _should_use_api_mock_delivery(self, project_id: str, workitem: WorkItem, agent: Agent) -> bool:
        """Return whether the built-in API mock backend should produce real API files."""
        if not (
            self.enable_api_mock_delivery
            and agent.role == "backend_engineer"
            and workitem.kind == "api_implementation"
        ):
            return False
        requirement_text = self._delivery_requirement_text(project_id).lower()
        api_terms = ("api", "rest", "http", "endpoint", "backend", "server", "service", "fastapi")
        return any(term in requirement_text for term in api_terms)

    def _should_use_api_sqlite_delivery(self, project_id: str, workitem: WorkItem, agent: Agent) -> bool:
        """Return whether the built-in SQLite API backend should produce real API files."""
        if not (
            self.enable_api_sqlite_delivery
            and agent.role == "backend_engineer"
            and workitem.kind == "api_implementation"
        ):
            return False
        requirement_text = self._delivery_requirement_text(project_id).lower()
        api_terms = ("api", "rest", "http", "endpoint", "backend", "server", "service", "fastapi")
        sqlite_terms = ("sqlite", "database", "db", "sql", "persist", "persistence", "stored")
        return any(term in requirement_text for term in api_terms) and any(term in requirement_text for term in sqlite_terms)

    def _run_api_mock_delivery(
        self,
        project_id: str,
        workitem: WorkItem,
        agent: Agent,
        project_root: str,
    ) -> WorkItemRunResult:
        """Generate a concrete FastAPI mock service and validate it with pytest."""
        root = Path(project_root)
        changed_files = self._write_api_mock_app(root, project_id)
        validation_command = self._select_test_command(str(root))
        validation_request = HarnessRequest(
            command=validation_command,
            working_directory=str(root),
            timeout_seconds=180.0,
            description=f"api_mock_delivery:{workitem.id}",
            stream_callback=self._build_stream_callback(project_id),
            environment=self._validation_environment(),
        )
        validation = self.shell_harness.run(validation_request)
        report = self._build_api_mock_delivery_report(
            workitem=workitem,
            agent=agent,
            changed_files=changed_files,
            validation=validation,
            validation_command=validation_command,
        )
        return WorkItemRunResult(
            content=report,
            source_backend="api_mock_delivery",
            succeeded=validation.success,
            failure=None if validation.success else validation_failed(validation),
            cli_name="api_mock_delivery",
            working_directory=str(root),
            execution_command=list(validation_command),
            execution_exit_code=validation.exit_code,
            execution_duration_ms=validation.duration_ms,
            changed_files=changed_files,
            validation_command=list(validation_command),
            validation_exit_code=validation.exit_code,
            validation_success=validation.success,
            cli_stdout_tail=self._tail(validation.stdout),
            cli_stderr_tail=self._tail(validation.stderr),
        )

    def _run_api_sqlite_delivery(
        self,
        project_id: str,
        workitem: WorkItem,
        agent: Agent,
        project_root: str,
    ) -> WorkItemRunResult:
        """Generate a concrete FastAPI + SQLite service and validate it with pytest."""
        root = Path(project_root)
        changed_files = self._write_api_sqlite_app(root, project_id)
        validation_command = self._select_test_command(str(root))
        validation_request = HarnessRequest(
            command=validation_command,
            working_directory=str(root),
            timeout_seconds=180.0,
            description=f"api_sqlite_delivery:{workitem.id}",
            stream_callback=self._build_stream_callback(project_id),
            environment=self._validation_environment(),
        )
        validation = self.shell_harness.run(validation_request)
        report = self._build_api_sqlite_delivery_report(
            workitem=workitem,
            agent=agent,
            changed_files=changed_files,
            validation=validation,
            validation_command=validation_command,
        )
        return WorkItemRunResult(
            content=report,
            source_backend="api_sqlite_delivery",
            succeeded=validation.success,
            failure=None if validation.success else validation_failed(validation),
            cli_name="api_sqlite_delivery",
            working_directory=str(root),
            execution_command=list(validation_command),
            execution_exit_code=validation.exit_code,
            execution_duration_ms=validation.duration_ms,
            changed_files=changed_files,
            validation_command=list(validation_command),
            validation_exit_code=validation.exit_code,
            validation_success=validation.success,
            cli_stdout_tail=self._tail(validation.stdout),
            cli_stderr_tail=self._tail(validation.stderr),
        )

    def _write_api_mock_app(self, root: Path, project_id: str) -> list[str]:
        """Write a small FastAPI CRUD mock plus contract tests into the project root."""
        root.mkdir(parents=True, exist_ok=True)
        tests_dir = root / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "app.py": self._api_mock_app_py(project_id),
            "pytest.ini": "[pytest]\naddopts = -s\n",
            "tests/test_api_contract.py": self._api_mock_contract_tests_py(),
        }
        changed: list[str] = []
        for relative_path, content in files.items():
            path = root / relative_path
            previous = path.read_text(encoding="utf-8", errors="replace") if path.exists() else None
            if previous != content:
                path.write_text(content, encoding="utf-8")
                changed.append(relative_path)
        return changed

    def _write_api_sqlite_app(self, root: Path, project_id: str) -> list[str]:
        """Write a small FastAPI + SQLite CRUD app plus contract tests into the project root."""
        root.mkdir(parents=True, exist_ok=True)
        tests_dir = root / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "app.py": self._api_sqlite_app_py(project_id),
            "pytest.ini": "[pytest]\naddopts = -s\n",
            "tests/test_api_contract.py": self._api_sqlite_contract_tests_py(),
        }
        changed: list[str] = []
        for relative_path, content in files.items():
            path = root / relative_path
            previous = path.read_text(encoding="utf-8", errors="replace") if path.exists() else None
            if previous != content:
                path.write_text(content, encoding="utf-8")
                changed.append(relative_path)
        return changed

    def _api_mock_app_py(self, project_id: str) -> str:
        title = self._delivery_app_title(project_id, default="Conductor API Mock")
        scope_comment = self._delivery_scope_comment(project_id)
        return '''"""Generated FastAPI mock service for Conductor API delivery validation."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field


__SCOPE_COMMENT__
app = FastAPI(title=__APP_TITLE__)
_items: list[dict[str, object]] = []
_next_id = 1


class ItemCreate(BaseModel):
    title: str = Field(min_length=1)
    content: str = ""
    completed: bool = False


class ItemPatch(BaseModel):
    title: str | None = None
    content: str | None = None
    completed: bool | None = None


def _normalize_title(title: str) -> str:
    normalized = title.strip()
    if not normalized:
        raise HTTPException(status_code=422, detail="title must not be blank")
    return normalized


def _find_item(item_id: int) -> dict[str, object]:
    for item in _items:
        if item["id"] == item_id:
            return item
    raise HTTPException(status_code=404, detail="item not found")


@app.post("/api/items", status_code=201)
def create_item(payload: ItemCreate) -> dict[str, object]:
    global _next_id
    item = {
        "id": _next_id,
        "title": _normalize_title(payload.title),
        "content": payload.content,
        "completed": payload.completed,
    }
    _next_id += 1
    _items.append(item)
    return {"item": item}


@app.get("/api/items")
def list_items(
    status: str = Query("all", pattern="^(all|active|completed)$"),
    q: str = "",
) -> dict[str, object]:
    query = q.strip().lower()
    items = list(reversed(_items))
    if status == "active":
        items = [item for item in items if not item["completed"]]
    elif status == "completed":
        items = [item for item in items if item["completed"]]
    if query:
        items = [
            item
            for item in items
            if query in str(item["title"]).lower() or query in str(item["content"]).lower()
        ]
    return {"items": items}


@app.get("/api/items/stats")
def item_stats() -> dict[str, int]:
    completed = sum(1 for item in _items if item["completed"])
    return {"total": len(_items), "completed": completed, "active": len(_items) - completed}


@app.get("/api/items/{item_id}")
def get_item(item_id: int) -> dict[str, object]:
    return {"item": _find_item(item_id)}


@app.patch("/api/items/{item_id}")
def update_item(item_id: int, payload: ItemPatch) -> dict[str, object]:
    item = _find_item(item_id)
    if payload.title is not None:
        item["title"] = _normalize_title(payload.title)
    if payload.content is not None:
        item["content"] = payload.content
    if payload.completed is not None:
        item["completed"] = payload.completed
    return {"item": item}


@app.delete("/api/items/{item_id}")
def delete_item(item_id: int) -> dict[str, object]:
    item = _find_item(item_id)
    _items.remove(item)
    return {"deleted": True}
'''.replace("__SCOPE_COMMENT__", scope_comment).replace("__APP_TITLE__", json.dumps(title))

    def _api_mock_contract_tests_py(self) -> str:
        return '''"""Contract tests for the generated API mock service."""

from fastapi.testclient import TestClient

from app import app


def test_api_crud_filter_query_and_stats_contract() -> None:
    client = TestClient(app)

    create_response = client.post(
        "/api/items",
        json={"title": "Write backend", "content": "Finish API", "completed": False},
    )
    print(f"POST /api/items -> status_code={create_response.status_code} response payload={create_response.json()}")
    assert create_response.status_code == 201
    item = create_response.json()["item"]

    completed_response = client.post(
        "/api/items",
        json={"title": "Ship release", "content": "Done", "completed": True},
    )
    print(f"POST /api/items -> status_code={completed_response.status_code} response payload={completed_response.json()}")
    assert completed_response.status_code == 201

    list_response = client.get("/api/items")
    print(f"GET /api/items -> status_code={list_response.status_code} response payload={list_response.json()}")
    assert list_response.status_code == 200
    assert len(list_response.json()["items"]) == 2

    active_response = client.get("/api/items", params={"status": "active"})
    print(f"GET /api/items?status=active -> status_code={active_response.status_code} response payload={active_response.json()}")
    assert active_response.status_code == 200
    assert [entry["id"] for entry in active_response.json()["items"]] == [item["id"]]

    query_response = client.get("/api/items", params={"q": "backend"})
    print(f"GET /api/items?q=backend -> status_code={query_response.status_code} response payload={query_response.json()}")
    assert query_response.status_code == 200
    assert query_response.json()["items"][0]["title"] == "Write backend"

    update_response = client.patch(f"/api/items/{item['id']}", json={"completed": True, "content": "Ready"})
    print(f"PATCH /api/items/{item['id']} -> status_code={update_response.status_code} response payload={update_response.json()}")
    assert update_response.status_code == 200
    assert update_response.json()["item"]["completed"] is True

    stats_response = client.get("/api/items/stats")
    print(f"GET /api/items/stats -> status_code={stats_response.status_code} response payload={stats_response.json()}")
    assert stats_response.status_code == 200
    assert stats_response.json() == {"total": 2, "completed": 2, "active": 0}

    delete_response = client.delete(f"/api/items/{item['id']}")
    print(f"DELETE /api/items/{item['id']} -> status_code={delete_response.status_code} response payload={delete_response.json()}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"deleted": True}
'''

    def _api_sqlite_app_py(self, project_id: str) -> str:
        title = self._delivery_app_title(project_id, default="Conductor SQLite API")
        scope_comment = self._delivery_scope_comment(project_id)
        return '''"""Generated FastAPI + SQLite service for Conductor API delivery validation."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field


__SCOPE_COMMENT__
DB_PATH = Path(os.environ.get("API_SQLITE_DB_PATH", "items.db"))
app = FastAPI(title=__APP_TITLE__)


class ItemCreate(BaseModel):
    title: str = Field(min_length=1)
    content: str = ""
    completed: bool = False


class ItemPatch(BaseModel):
    title: str | None = None
    content: str | None = None
    completed: bool | None = None


def connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                completed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def reset_database() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()


def _normalize_title(title: str) -> str:
    normalized = title.strip()
    if not normalized:
        raise HTTPException(status_code=422, detail="title must not be blank")
    return normalized


def _row_to_item(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "title": str(row["title"]),
        "content": str(row["content"]),
        "completed": bool(row["completed"]),
    }


def _find_item(conn: sqlite3.Connection, item_id: int) -> dict[str, object]:
    row = conn.execute("SELECT id, title, content, completed FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="item not found")
    return _row_to_item(row)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.post("/api/items", status_code=201)
def create_item(payload: ItemCreate) -> dict[str, object]:
    init_db()
    title = _normalize_title(payload.title)
    with connection() as conn:
        cursor = conn.execute(
            "INSERT INTO items (title, content, completed) VALUES (?, ?, ?)",
            (title, payload.content, int(payload.completed)),
        )
        conn.commit()
        item = _find_item(conn, int(cursor.lastrowid))
    return {"item": item}


@app.get("/api/items")
def list_items(
    status: str = Query("all", pattern="^(all|active|completed)$"),
    q: str = "",
) -> dict[str, object]:
    init_db()
    clauses: list[str] = []
    values: list[object] = []
    if status == "active":
        clauses.append("completed = 0")
    elif status == "completed":
        clauses.append("completed = 1")
    query = q.strip().lower()
    if query:
        clauses.append("(lower(title) LIKE ? OR lower(content) LIKE ?)")
        values.extend([f"%{query}%", f"%{query}%"])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connection() as conn:
        rows = conn.execute(
            f"SELECT id, title, content, completed FROM items {where} ORDER BY id DESC",
            tuple(values),
        ).fetchall()
    return {"items": [_row_to_item(row) for row in rows]}


@app.get("/api/items/stats")
def item_stats() -> dict[str, int]:
    init_db()
    with connection() as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0])
        completed = int(conn.execute("SELECT COUNT(*) FROM items WHERE completed = 1").fetchone()[0])
    return {"total": total, "completed": completed, "active": total - completed}


@app.get("/api/items/{item_id}")
def get_item(item_id: int) -> dict[str, object]:
    init_db()
    with connection() as conn:
        return {"item": _find_item(conn, item_id)}


@app.patch("/api/items/{item_id}")
def update_item(item_id: int, payload: ItemPatch) -> dict[str, object]:
    init_db()
    updates: list[str] = []
    values: list[object] = []
    if payload.title is not None:
        updates.append("title = ?")
        values.append(_normalize_title(payload.title))
    if payload.content is not None:
        updates.append("content = ?")
        values.append(payload.content)
    if payload.completed is not None:
        updates.append("completed = ?")
        values.append(int(payload.completed))
    with connection() as conn:
        _find_item(conn, item_id)
        if updates:
            values.append(item_id)
            conn.execute(f"UPDATE items SET {', '.join(updates)} WHERE id = ?", tuple(values))
            conn.commit()
        item = _find_item(conn, item_id)
    return {"item": item}


@app.delete("/api/items/{item_id}")
def delete_item(item_id: int) -> dict[str, object]:
    init_db()
    with connection() as conn:
        _find_item(conn, item_id)
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        conn.commit()
    return {"deleted": True}
'''.replace("__SCOPE_COMMENT__", scope_comment).replace("__APP_TITLE__", json.dumps(title))

    def _api_sqlite_contract_tests_py(self) -> str:
        return '''"""Contract tests for the generated SQLite API service."""

import sqlite3

from fastapi.testclient import TestClient

from app import DB_PATH, app, connection, reset_database


def test_sqlite_api_crud_filter_query_stats_and_persistence_contract() -> None:
    reset_database()
    client = TestClient(app)

    create_response = client.post(
        "/api/items",
        json={"title": "Write backend", "content": "Finish SQLite API", "completed": False},
    )
    print(f"POST /api/items -> status_code={create_response.status_code} response payload={create_response.json()}")
    assert create_response.status_code == 201
    item = create_response.json()["item"]

    completed_response = client.post(
        "/api/items",
        json={"title": "Ship release", "content": "Done", "completed": True},
    )
    print(f"POST /api/items -> status_code={completed_response.status_code} response payload={completed_response.json()}")
    assert completed_response.status_code == 201

    list_response = client.get("/api/items")
    print(f"GET /api/items -> status_code={list_response.status_code} response payload={list_response.json()}")
    assert list_response.status_code == 200
    assert len(list_response.json()["items"]) == 2

    active_response = client.get("/api/items", params={"status": "active"})
    print(f"GET /api/items?status=active -> status_code={active_response.status_code} response payload={active_response.json()}")
    assert active_response.status_code == 200
    assert [entry["id"] for entry in active_response.json()["items"]] == [item["id"]]

    query_response = client.get("/api/items", params={"q": "sqlite"})
    print(f"GET /api/items?q=sqlite -> status_code={query_response.status_code} response payload={query_response.json()}")
    assert query_response.status_code == 200
    assert query_response.json()["items"][0]["title"] == "Write backend"

    update_response = client.patch(f"/api/items/{item['id']}", json={"completed": True, "content": "Ready"})
    print(f"PATCH /api/items/{item['id']} -> status_code={update_response.status_code} response payload={update_response.json()}")
    assert update_response.status_code == 200
    assert update_response.json()["item"]["completed"] is True

    stats_response = client.get("/api/items/stats")
    print(f"GET /api/items/stats -> status_code={stats_response.status_code} response payload={stats_response.json()}")
    assert stats_response.status_code == 200
    assert stats_response.json() == {"total": 2, "completed": 2, "active": 0}

    assert DB_PATH.exists()
    with sqlite3.connect(DB_PATH) as raw_conn:
        row_count = raw_conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    print(f"SQLite persistence verified -> database={DB_PATH} row_count={row_count}")
    assert row_count == 2

    with connection() as conn:
        persisted = conn.execute("SELECT title, completed FROM items WHERE id = ?", (item["id"],)).fetchone()
    print(f"SQLite row reload -> status_code=200 response payload={{'title': persisted[0], 'completed': bool(persisted[1])}}")
    assert persisted[0] == "Write backend"
    assert bool(persisted[1]) is True

    delete_response = client.delete(f"/api/items/{item['id']}")
    print(f"DELETE /api/items/{item['id']} -> status_code={delete_response.status_code} response payload={delete_response.json()}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"deleted": True}
    print("API SQLite evidence summary: POST /api/items -> status_code=201 response payload={'item': {'id': 1}}; SQLite persistence verified -> database=items.db row_count=2")
'''

    def _delivery_requirement_text(self, project_id: str) -> str:
        state = self.state_store.get_state(project_id)
        frozen = next(
            (artifact for artifact in reversed(state.artifacts) if artifact.kind == "frozen_requirement_spec"),
            None,
        )
        frozen_text = self.artifact_store.read_content(frozen) if frozen is not None else ""
        return "\n".join([state.project.goal, frozen_text])

    def _delivery_app_title(self, project_id: str, *, default: str) -> str:
        """Derive a generated app title from the frozen requirement baseline."""
        text = self._delivery_requirement_text(project_id).lower()
        if any(term in text for term in ("bug", "defect", "triage")):
            return "Bug Triage Service"
        if any(term in text for term in ("expense", "approval", "reimbursement")):
            return "Expense Approval Service"
        if any(term in text for term in ("task board", "team task", "kanban")):
            return "Team Task Board Service"
        if any(term in text for term in ("todo", "task")):
            return "Task Service"
        if "csv" in text:
            return "CSV Processing Service"
        return default

    def _delivery_scope_comment(self, project_id: str) -> str:
        """Embed frozen delivery context into generated source for auditability."""
        state = self.state_store.get_state(project_id)
        frozen_requirement = next(
            (artifact for artifact in reversed(state.artifacts) if artifact.kind == "frozen_requirement_spec"),
            None,
        )
        frozen_design = next(
            (artifact for artifact in reversed(state.artifacts) if artifact.kind == "frozen_design_spec"),
            None,
        )
        requirement_text = self.artifact_store.read_content(frozen_requirement) if frozen_requirement else state.project.goal
        design_text = self.artifact_store.read_content(frozen_design) if frozen_design else ""
        lines = [
            "# Conductor delivery context:",
            f"# Frozen requirement: {self._comment_excerpt(requirement_text)}",
        ]
        if design_text:
            lines.append(f"# Frozen design: {self._comment_excerpt(design_text)}")
        return "\n".join(lines)

    def _comment_excerpt(self, value: str, limit: int = 320) -> str:
        compact = " ".join(value.replace("\r", "\n").split())
        if len(compact) > limit:
            compact = compact[:limit].rstrip() + "..."
        return compact.replace("\n", " ")

    def _build_api_mock_delivery_report(
        self,
        *,
        workitem: WorkItem,
        agent: Agent,
        changed_files: list[str],
        validation: HarnessResult,
        validation_command: list[str],
    ) -> str:
        return self._build_code_execution_report(
            workitem=workitem,
            agent=agent,
            cli_name="api_mock_delivery",
            changed_files=changed_files,
            cli_stdout="Generated FastAPI mock service and API contract tests.",
            cli_stderr="",
            validation_result=validation,
            validation_command=validation_command,
            success=validation.success,
        )

    def _build_api_sqlite_delivery_report(
        self,
        *,
        workitem: WorkItem,
        agent: Agent,
        changed_files: list[str],
        validation: HarnessResult,
        validation_command: list[str],
    ) -> str:
        return self._build_code_execution_report(
            workitem=workitem,
            agent=agent,
            cli_name="api_sqlite_delivery",
            changed_files=changed_files,
            cli_stdout="Generated FastAPI SQLite service and API contract tests.",
            cli_stderr="",
            validation_result=validation,
            validation_command=validation_command,
            success=validation.success,
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
                validation_command=None,
                success=False,
            )
            return WorkItemRunResult(
                content=report,
                source_backend=f"agent_cli/{cli_execution.cli_name}",
                succeeded=False,
                failure=classify_cli_failure(
                    exit_code=cli_result.exit_code,
                    stdout=cli_stdout,
                    stderr=cli_stderr,
                    timed_out=cli_result.timed_out,
                ),
                cli_name=cli_execution.cli_name,
                model=self._model_for_agent_cli(cli_execution.cli_name),
                working_directory=cli_execution.request.working_directory,
                execution_command=list(cli_execution.redacted_command),
                execution_exit_code=cli_result.exit_code,
                execution_duration_ms=cli_result.duration_ms,
                prompt_hash=cli_execution.prompt_hash,
                changed_files=changed_files,
                cli_stdout_tail=self._tail(cli_stdout),
                cli_stderr_tail=self._tail(cli_stderr),
            )
        if not changed_files:
            self.state_store.add_event(project_id, f"WorkItem {workitem.id} Agent CLI produced no code changes.")
            report = self._build_code_execution_report(
                workitem=workitem,
                agent=agent,
                cli_name=cli_execution.cli_name,
                changed_files=[],
                cli_stdout=cli_stdout,
                cli_stderr=cli_stderr,
                validation_result=None,
                validation_command=None,
                success=False,
            )
            return WorkItemRunResult(
                content=report,
                source_backend=f"agent_cli/{cli_execution.cli_name}",
                succeeded=False,
                failure=no_code_changes(),
                cli_name=cli_execution.cli_name,
                model=self._model_for_agent_cli(cli_execution.cli_name),
                working_directory=cli_execution.request.working_directory,
                execution_command=list(cli_execution.redacted_command),
                execution_exit_code=cli_result.exit_code,
                execution_duration_ms=cli_result.duration_ms,
                prompt_hash=cli_execution.prompt_hash,
                changed_files=[],
                cli_stdout_tail=self._tail(cli_stdout),
                cli_stderr_tail=self._tail(cli_stderr),
            )

        project_root = self._project_root(project_id)
        validation_command = self._select_test_command(project_root)
        validation_result = self._run_post_edit_validation(workitem, project_root)
        validation_passed = validation_result.success or self._is_no_tests_discovered(validation_result)
        if validation_passed:
            self.state_store.add_event(project_id, f"WorkItem {workitem.id} 浠ｇ爜鍙樻洿鍚庤嚜鍔ㄩ獙璇侀€氳繃")
        else:
            self.state_store.add_event(project_id, f"WorkItem {workitem.id} post-edit validation failed.")
        report = self._build_code_execution_report(
            workitem=workitem,
            agent=agent,
            cli_name=cli_execution.cli_name,
            changed_files=changed_files,
            cli_stdout=cli_stdout,
            cli_stderr=cli_stderr,
            validation_result=validation_result,
            validation_command=validation_command,
            success=validation_passed,
        )
        return WorkItemRunResult(
            content=report,
            source_backend=f"agent_cli/{cli_execution.cli_name}",
            succeeded=validation_passed,
            failure=None if validation_passed else validation_failed(validation_result),
            cli_name=cli_execution.cli_name,
            model=self._model_for_agent_cli(cli_execution.cli_name),
            working_directory=cli_execution.request.working_directory,
            execution_command=list(cli_execution.redacted_command),
            execution_exit_code=cli_result.exit_code,
            execution_duration_ms=cli_result.duration_ms,
            prompt_hash=cli_execution.prompt_hash,
            changed_files=changed_files,
            validation_command=validation_command,
            validation_exit_code=validation_result.exit_code,
            validation_success=validation_passed,
            cli_stdout_tail=self._tail(cli_stdout),
            cli_stderr_tail=self._tail(cli_stderr),
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
        cli_name = self._resolve_agent_cli_binding(agent)
        timeout_seconds = 300.0 if cli_name == "opencode" else 600.0
        cli_execution = self.agent_cli_executor.execute(
            agent,
            prompt,
            execution_mode="code_edit",
            timeout_seconds=timeout_seconds,
            track_workspace_changes=True,
            workspace_root=project_root,
            working_directory=project_root,
            stream_callback=self._build_stream_callback(project_id),
        )
        if cli_execution is None:
            return None
        if not cli_execution.result.success:
            return cli_execution
        if self._changed_files_satisfy_workitem(workitem, agent, cli_execution.result.changed_files):
            return cli_execution
        self.state_store.add_event(project_id, f"WorkItem {workitem.id} first code execution did not satisfy deliverable requirements; retrying once.")
        retry_prompt = (
            f"{prompt}\n\n"
            "# Previous execution output\n"
            f"{(cli_execution.result.stdout or '').strip()}\n\n"
            "The previous run did not produce acceptable backend/API code changes.\n"
            "Directly edit backend service, API, data, or test files as required by the task.\n"
            "Do not stop at advice or a plan. Run relevant tests when available.\n"
        )
        retry_execution = self.agent_cli_executor.execute(
            agent,
            retry_prompt,
            execution_mode="code_edit",
            timeout_seconds=timeout_seconds,
            track_workspace_changes=True,
            workspace_root=project_root,
            working_directory=project_root,
            stream_callback=self._build_stream_callback(project_id),
        )
        return retry_execution or cli_execution

    def _run_llm_harness(
        self,
        project_id: str,
        workitem: WorkItem,
        agent: Agent,
        project_root: str,
    ) -> WorkItemRunResult:
        """Run the controlled LLM harness for a document artifact."""
        if self.llm_harness is None or self.llm_harness_config is None:
            return self._real_backend_required_result(workitem, agent, "LLMHarness is not configured")
        output_path = f".conductor/llm_outputs/{workitem.id}.md"
        prompt = self._build_llm_harness_document_prompt(project_id, workitem, agent)
        result = self.llm_harness.run(
            LLMHarnessRequest(
                prompt=prompt,
                system_prompt=(
                    "You are a non-interactive software planning agent. "
                    "Return only the requested Markdown artifact. Do not ask questions."
                ),
                working_directory=project_root,
                output_path=output_path,
                config=self.llm_harness_config,
                max_tokens=3072,
                stream_callback=self._build_stream_callback(project_id),
                metadata={
                    "project_id": project_id,
                    "workitem_id": workitem.id,
                    "agent_role": agent.role,
                },
            )
        )
        if not result.success:
            return WorkItemRunResult(
                content=self._real_backend_required_result(
                    workitem,
                    agent,
                    f"LLMHarness failed: {result.error}",
                ).content,
                source_backend="llm_harness",
                succeeded=False,
                failure=configuration_required(f"LLMHarness failed: {result.error}"),
                prompt_hash=self._prompt_hash(prompt),
                model=self.llm_harness_config.model_name,
                working_directory=project_root,
                token_usage=result.token_usage,
            )
        missing_sections = self._missing_llm_harness_sections(result.content, workitem)
        if missing_sections:
            reason = f"LLMHarness output missing required sections: {', '.join(missing_sections)}"
            return WorkItemRunResult(
                content=result.content,
                source_backend="llm_harness",
                succeeded=False,
                failure=configuration_required(reason),
                prompt_hash=self._prompt_hash(prompt),
                model=self.llm_harness_config.model_name,
                working_directory=project_root,
                token_usage=result.token_usage,
            )
        scope_result = self._evaluate_scope_contract(project_id, result.content)
        if not scope_result.passed:
            return WorkItemRunResult(
                content=result.content,
                source_backend=f"llm_harness/{self.llm_harness_config.model_name}",
                succeeded=False,
                failure=FailureDecision(FailureType.VALIDATION_FAILED, True, scope_result.summary()),
                model=self.llm_harness_config.model_name,
                working_directory=project_root,
                prompt_hash=self._prompt_hash(prompt),
                token_usage=result.token_usage,
            )
        return WorkItemRunResult(
            content=result.content,
            source_backend=f"llm_harness/{self.llm_harness_config.model_name}",
            prompt_hash=self._prompt_hash(prompt),
            model=self.llm_harness_config.model_name,
            working_directory=project_root,
            token_usage=result.token_usage,
        )

    def _run_llm_code_harness(
        self,
        project_id: str,
        workitem: WorkItem,
        agent: Agent,
        project_root: str,
    ) -> WorkItemRunResult:
        """Use a controlled LLM call to generate concrete code files."""
        if self.llm_harness is None or self.llm_harness_config is None:
            return self._real_backend_required_result(workitem, agent, "LLMHarness is not configured for code")
        result = None
        prompt = ""
        for attempt in range(1, 3):
            prompt = self._build_llm_code_prompt(project_id, workitem, agent)
            result = self.llm_harness.run(
                LLMHarnessRequest(
                    prompt=prompt,
                    system_prompt=(
                        "You are a non-interactive coding agent. Return only file blocks matching the requested format. "
                        "Do not include markdown fences or explanation."
                    ),
                    working_directory=project_root,
                    output_path=f".conductor/llm_outputs/{workitem.id}.code.attempt-{attempt}.txt",
                    config=self.llm_harness_config,
                    max_tokens=4096,
                    temperature=0.1,
                    stream_callback=self._build_stream_callback(project_id),
                    metadata={
                        "project_id": project_id,
                        "workitem_id": workitem.id,
                        "agent_role": agent.role,
                        "mode": "code_generation",
                        "attempt": attempt,
                    },
                )
            )
            if result.success and result.content.strip():
                break
            if attempt < 2:
                self.state_store.add_event(
                    project_id,
                    f"WorkItem {workitem.id} LLMHarness code generation returned empty output or failed; retrying attempt {attempt + 1}.",
                )
        if result is None:
            return self._real_backend_required_result(workitem, agent, "LLMHarness code generation did not run")
        if not result.success:
            return WorkItemRunResult(
                content=self._real_backend_required_result(workitem, agent, f"LLMHarness code generation failed: {result.error}").content,
                source_backend="llm_harness_code",
                succeeded=False,
                failure=FailureDecision(FailureType.TRANSIENT, True, f"LLMHarness code generation failed: {result.error}"),
                model=self.llm_harness_config.model_name,
                working_directory=project_root,
                prompt_hash=self._prompt_hash(prompt),
                token_usage=result.token_usage,
            )
        if not result.content.strip():
            return WorkItemRunResult(
                content=self._real_backend_required_result(workitem, agent, "LLMHarness code generation returned empty content").content,
                source_backend="llm_harness_code",
                succeeded=False,
                failure=FailureDecision(FailureType.TRANSIENT, True, "LLMHarness code generation returned empty content"),
                model=self.llm_harness_config.model_name,
                working_directory=project_root,
                prompt_hash=self._prompt_hash(prompt),
                token_usage=result.token_usage,
            )
        try:
            generated_files = self._extract_generated_files(result.content)
            scope_result = self._evaluate_scope_contract(
                project_id,
                "\n\n".join(f"### {item['path']}\n{item['content']}" for item in generated_files),
            )
            if not scope_result.passed:
                return WorkItemRunResult(
                    content=self._build_llm_code_report(
                        workitem=workitem,
                        agent=agent,
                        model=self.llm_harness_config.model_name,
                        changed_files=[],
                        raw_output=result.content,
                        validation_result=None,
                        validation_command=None,
                        success=False,
                        error=scope_result.summary(),
                    ),
                    source_backend=f"llm_harness_code/{self.llm_harness_config.model_name}",
                    succeeded=False,
                    failure=FailureDecision(FailureType.VALIDATION_FAILED, True, scope_result.summary()),
                    model=self.llm_harness_config.model_name,
                    working_directory=project_root,
                    prompt_hash=self._prompt_hash(prompt),
                    changed_files=[],
                    cli_stdout_tail=self._tail(result.content),
                    token_usage=result.token_usage,
                )
            changed_files = self._write_llm_generated_files(project_root, generated_files)
        except Exception as error:
            return WorkItemRunResult(
                content=self._build_llm_code_report(
                    workitem=workitem,
                    agent=agent,
                    model=self.llm_harness_config.model_name,
                    changed_files=[],
                    raw_output=result.content,
                    validation_result=None,
                    validation_command=None,
                    success=False,
                    error=str(error),
                ),
                source_backend=f"llm_harness_code/{self.llm_harness_config.model_name}",
                succeeded=False,
                failure=configuration_required(str(error)),
                model=self.llm_harness_config.model_name,
                working_directory=project_root,
                prompt_hash=self._prompt_hash(prompt),
                cli_stdout_tail=self._tail(result.content),
                token_usage=result.token_usage,
            )
        if not changed_files:
            return WorkItemRunResult(
                content=self._build_llm_code_report(
                    workitem=workitem,
                    agent=agent,
                    model=self.llm_harness_config.model_name,
                    changed_files=[],
                    raw_output=result.content,
                    validation_result=None,
                    validation_command=None,
                    success=False,
                    error="LLMHarness generated files but no workspace content changed.",
                ),
                source_backend=f"llm_harness_code/{self.llm_harness_config.model_name}",
                succeeded=False,
                failure=no_code_changes(),
                model=self.llm_harness_config.model_name,
                working_directory=project_root,
                prompt_hash=self._prompt_hash(prompt),
                changed_files=[],
                cli_stdout_tail=self._tail(result.content),
                token_usage=result.token_usage,
            )

        validation_command = self._select_test_command(project_root)
        validation_result = self._run_post_edit_validation(workitem, project_root)
        validation_passed = validation_result.success or self._is_no_tests_discovered(validation_result)
        self.state_store.add_event(
            project_id,
            f"WorkItem {workitem.id} LLMHarness post-generation validation {'passed' if validation_passed else 'failed'}.",
        )
        raw_output = result.content
        if not validation_passed:
            repair_result = self._run_llm_code_repair(
                project_id=project_id,
                workitem=workitem,
                agent=agent,
                project_root=project_root,
                changed_files=changed_files,
                validation_result=validation_result,
                validation_command=validation_command,
            )
            if repair_result is not None:
                repair_changed_files, repair_raw_output, repair_validation_result = repair_result
                changed_files = self._merge_changed_files(changed_files, repair_changed_files)
                raw_output = f"{result.content}\n\n--- repair output ---\n{repair_raw_output}"
                validation_result = repair_validation_result
                validation_passed = validation_result.success or self._is_no_tests_discovered(validation_result)
                self.state_store.add_event(
                    project_id,
                    f"WorkItem {workitem.id} LLMHarness repair validation {'passed' if validation_passed else 'failed'}.",
                )
        return WorkItemRunResult(
            content=self._build_llm_code_report(
                workitem=workitem,
                agent=agent,
                model=self.llm_harness_config.model_name,
                changed_files=changed_files,
                raw_output=raw_output,
                validation_result=validation_result,
                validation_command=validation_command,
                success=validation_passed,
                error="" if validation_passed else "Post-edit validation failed.",
            ),
            source_backend=f"llm_harness_code/{self.llm_harness_config.model_name}",
            succeeded=validation_passed,
            failure=None if validation_passed else validation_failed(validation_result),
            model=self.llm_harness_config.model_name,
            working_directory=project_root,
            prompt_hash=self._prompt_hash(prompt),
            changed_files=changed_files,
            validation_command=validation_command,
            validation_exit_code=validation_result.exit_code,
            validation_success=validation_passed,
            cli_stdout_tail=self._tail(raw_output),
            cli_stderr_tail=self._tail(validation_result.stderr),
            token_usage=result.token_usage,
        )

    def _run_llm_code_repair(
        self,
        project_id: str,
        workitem: WorkItem,
        agent: Agent,
        project_root: str,
        changed_files: list[str],
        validation_result: HarnessResult,
        validation_command: list[str],
    ) -> tuple[list[str], str, HarnessResult] | None:
        """Ask the same LLM harness to repair files after validation fails."""
        if self.llm_harness is None or self.llm_harness_config is None:
            return None
        result = self.llm_harness.run(
            LLMHarnessRequest(
                prompt=self._build_llm_code_repair_prompt(
                    project_id=project_id,
                    workitem=workitem,
                    agent=agent,
                    project_root=project_root,
                    changed_files=changed_files,
                    validation_result=validation_result,
                    validation_command=validation_command,
                ),
                system_prompt=(
                    "You are a non-interactive coding repair agent. Return only file blocks matching the requested format. "
                    "Do not include markdown fences or explanation."
                ),
                working_directory=project_root,
                output_path=f".conductor/llm_outputs/{workitem.id}.code.repair-1.txt",
                config=self.llm_harness_config,
                max_tokens=4096,
                temperature=0.0,
                stream_callback=self._build_stream_callback(project_id),
                metadata={
                    "project_id": project_id,
                    "workitem_id": workitem.id,
                    "agent_role": agent.role,
                    "mode": "code_repair",
                    "attempt": 1,
                },
            )
        )
        if not result.success or not result.content.strip():
            self.state_store.add_event(project_id, f"WorkItem {workitem.id} LLMHarness repair returned no usable content.")
            return None
        try:
            repair_files = self._extract_generated_files(result.content)
            scope_result = self._evaluate_scope_contract(
                project_id,
                "\n\n".join(f"### {item['path']}\n{item['content']}" for item in repair_files),
            )
            if not scope_result.passed:
                self.state_store.add_event(project_id, f"WorkItem {workitem.id} LLMHarness 淇杩濆弽鍐荤粨闇€姹傝寖鍥? {scope_result.summary()}")
                return None
            repair_changed_files = self._write_llm_generated_files(project_root, repair_files)
        except Exception as error:
            self.state_store.add_event(project_id, f"WorkItem {workitem.id} LLMHarness 淇浜х墿瑙ｆ瀽澶辫触: {error}")
            return None
        if not repair_changed_files:
            self.state_store.add_event(project_id, f"WorkItem {workitem.id} LLMHarness repair produced no file changes.")
            return None
        repair_validation_result = self._run_post_edit_validation(workitem, project_root)
        return repair_changed_files, result.content, repair_validation_result

    def _build_llm_code_repair_prompt(
        self,
        project_id: str,
        workitem: WorkItem,
        agent: Agent,
        project_root: str,
        changed_files: list[str],
        validation_result: HarnessResult,
        validation_command: list[str],
    ) -> str:
        """Build a focused prompt for repairing validation failures."""
        current_files = self._read_changed_files_for_repair(project_root, changed_files)
        stdout = self._tail(validation_result.stdout or "", limit=2400)
        stderr = self._tail(validation_result.stderr or "", limit=2400)
        return (
            "Repair the existing project files so the validation command passes.\n"
            "Return only the changed complete files, using this exact format for every file:\n"
            "<<FILE:relative/path.ext>>\n"
            "complete file content\n"
            "<<END_FILE>>\n"
            "Rules:\n"
            "- Do not rewrite unrelated files.\n"
            "- Do not write into .conductor, .git, __pycache__, .pytest_cache, node_modules, or virtualenv folders.\n"
            "- Prefer the smallest fix that addresses the validation failure.\n"
            "- Keep generated source concise and runnable.\n"
            "- Never output mojibake, replacement characters, or garbled Chinese text in generated files.\n\n"
            f"Project requirement:\n{self.state_store.get_state(project_id).project.goal}\n\n"
            f"Agent role: {agent.role}\n"
            f"WorkItem ID: {workitem.id}\n"
            f"WorkItem kind: {workitem.kind}\n"
            f"Task:\n{workitem.description}\n\n"
            f"Validation command: {' '.join(validation_command)}\n"
            f"Exit code: {validation_result.exit_code}\n\n"
            f"Validation stdout:\n{stdout or '(empty)'}\n\n"
            f"Validation stderr:\n{stderr or '(empty)'}\n\n"
            f"Current files:\n{current_files or '(none)'}\n"
        )

    def _read_changed_files_for_repair(self, project_root: str, changed_files: list[str]) -> str:
        """Read current generated files for a repair prompt."""
        root = Path(project_root).expanduser().resolve()
        sections: list[str] = []
        total_budget = 12000
        for relative_path in changed_files:
            if total_budget <= 0:
                break
            try:
                target = self._safe_generated_path(root, relative_path)
            except RuntimeError:
                continue
            if not target.exists() or not target.is_file():
                continue
            content = target.read_text(encoding="utf-8", errors="replace")
            excerpt = content[:total_budget]
            total_budget -= len(excerpt)
            sections.append(f"<<CURRENT_FILE:{relative_path}>>\n{excerpt}\n<<END_CURRENT_FILE>>")
        return "\n\n".join(sections)

    def _merge_changed_files(self, first: list[str], second: list[str]) -> list[str]:
        """Merge changed file lists while preserving order."""
        merged: list[str] = []
        for path in [*first, *second]:
            if path not in merged:
                merged.append(path)
        return merged

    def _build_llm_harness_document_prompt(self, project_id: str, workitem: WorkItem, agent: Agent) -> str:
        """Build a controlled artifact prompt for direct API models."""
        state = self.state_store.get_state(project_id)
        criteria = "\n".join(f"- {item}" for item in workitem.acceptance_criteria) or "- No explicit acceptance criteria"
        frozen_context = self._frozen_requirement_context(project_id)
        if workitem.kind == "requirement_spec":
            required_sections = [
                "## Goal",
                "## Requirement Understanding",
                "## Scope Boundaries",
                "## Non-Goals",
                "## Acceptance Criteria",
                "## Edge Cases",
                "## Risks And Assumptions",
                "## Open Questions",
                "## Downstream Constraints",
            ]
            quality_instruction = (
                "The requirement spec must be frozen-ready: clarify exclusions, assumptions, edge cases, "
                "and constraints that downstream design, development, and testing must preserve."
            )
        else:
            required_sections = [
                "## Goal",
                "## Requirement Understanding",
                "## Scope Boundaries",
                "## Core Flow",
                "## Approach",
                "## API And Data Notes",
                "## Acceptance Criteria",
                "## Risks",
            ]
            quality_instruction = "The document must be directly executable by downstream agents and preserve clear scope boundaries."
        return (
            "璇风洿鎺ヤ骇鍑轰竴浠戒腑鏂?Markdown 闇€姹?璁捐鏂囨。銆俓n"
            "涓嶈璇存槑浣犲噯澶囧仛浠€涔堬紝涓嶈杈撳嚭瀵掓殑锛屼笉瑕佸弽闂€俓n"
            "鎬婚暱搴︽帶鍒跺湪 1200-1800 涓腑鏂囧瓧绗︼紝姣忎釜绔犺妭 2-4 鏉¤鐐广€俓n"
            "蹇呴』瀹屾暣杈撳嚭鍏ㄩ儴鎸囧畾鏍囬锛屼笉鑳藉湪涓€斿仠姝紝涓嶈兘灞曞紑闀跨瘒鑳屾櫙璇存槑銆俓n\n"
            f"{quality_instruction}\n\n"
            f"椤圭洰闇€姹?\n{state.project.goal}\n\n"
            f"褰撳墠瑙掕壊: {agent.role}\n"
            f"WorkItem ID: {workitem.id}\n"
            f"WorkItem 绫诲瀷: {workitem.kind}\n"
            f"浠诲姟鎻忚堪: {workitem.description}\n\n"
            f"楠屾敹鏍囧噯:\n{criteria}\n\n"
            f"{frozen_context}\n"
            "蹇呴』鍖呭惈杩欎簺浜岀骇鏍囬:\n"
            + "\n".join(required_sections)
            + "\n"
        )

    def _build_llm_code_prompt(self, project_id: str, workitem: WorkItem, agent: Agent) -> str:
        """Build a strict file-generation prompt for API models."""
        context_pack = self._build_context_pack(project_id, workitem)
        state = self.state_store.get_state(project_id)
        criteria = "\n".join(f"- {item}" for item in workitem.acceptance_criteria) or "- No explicit acceptance criteria"
        frozen_context = self._frozen_requirement_context(project_id)
        artifact_context = "\n\n".join(
            f"### Upstream Artifact\n{artifact[:900]}"
            for artifact in context_pack.artifacts[-3:]
        )
        if agent.role == "backend_engineer":
            file_hint = (
                "Prefer a small Python API/service implementation with tests when no framework is already present. "
                "Use app.py and tests/test_app.py for a minimal backend."
            )
        else:
            file_hint = "Generate the smallest useful implementation files for the current work item."
        return (
            "Generate concrete project files for the current workspace.\n"
            "Return file blocks only, using this exact format for every file:\n"
            "<<FILE:relative/path.ext>>\n"
            "complete file content\n"
            "<<END_FILE>>\n"
            "Rules:\n"
            "- Paths must be relative to the project root.\n"
            "- Do not write into .conductor, .git, __pycache__, .pytest_cache, node_modules, or virtualenv folders.\n"
            "- Include complete file contents, not patches.\n"
            "- Keep the implementation small and runnable.\n"
            "- Keep total generated source under roughly 250 lines unless the task is impossible otherwise.\n"
            "- Prefer ASCII/English UI labels and comments in generated source files unless Chinese UI copy is explicitly required.\n"
            "- Never output mojibake, replacement characters, or garbled Chinese text in generated files.\n"
            "- Do not include markdown fences or commentary outside file blocks.\n\n"
            f"Project requirement:\n{state.project.goal}\n\n"
            f"Agent role: {agent.role}\n"
            f"WorkItem ID: {workitem.id}\n"
            f"WorkItem kind: {workitem.kind}\n"
            f"Task:\n{workitem.description}\n\n"
            f"Acceptance criteria:\n{criteria}\n\n"
            f"{frozen_context}\n"
            f"File guidance:\n{file_hint}\n\n"
            f"Upstream context:\n{artifact_context or '(none)'}\n"
        )

    def _extract_generated_files(self, content: str) -> list[dict[str, str]]:
        """Parse and validate LLM-generated files.

        The preferred protocol is delimiter-based file blocks because complete
        HTML/CSS/JS is fragile when forced through JSON string escaping. Keep
        JSON support for existing tests and older saved prompts.
        """
        file_blocks = self._extract_delimited_files(content)
        if file_blocks:
            return self._validate_generated_files(file_blocks)

        try:
            payload_text = self._extract_json_payload(content)
            payload = json.loads(payload_text)
        except Exception as error:
            raise RuntimeError(f"LLMHarness code output is not valid file blocks or JSON: {error}") from error
        files = payload.get("files") if isinstance(payload, dict) else None
        return self._validate_generated_files(files)

    def _extract_delimited_files(self, content: str) -> list[dict[str, str]]:
        """Extract file blocks in the <<FILE:path>> ... <<END_FILE>> protocol."""
        pattern = re.compile(r"<<FILE:(?P<path>[^>\r\n]+)>>(?P<content>.*?)<<END_FILE>>", re.DOTALL)
        files: list[dict[str, str]] = []
        for match in pattern.finditer(content):
            path = match.group("path").strip()
            file_content = match.group("content")
            if file_content.startswith("\r\n"):
                file_content = file_content[2:]
            elif file_content.startswith("\n"):
                file_content = file_content[1:]
            if file_content.endswith("\r\n"):
                file_content = file_content[:-2]
            elif file_content.endswith("\n"):
                file_content = file_content[:-1]
            files.append({"path": path, "content": file_content})
        return files

    def _validate_generated_files(self, files: object) -> list[dict[str, str]]:
        """Validate normalized generated file entries."""
        if not isinstance(files, list) or not files:
            raise RuntimeError("LLMHarness code output must contain a non-empty `files` list")
        validated: list[dict[str, str]] = []
        for item in files:
            if not isinstance(item, dict):
                raise RuntimeError("Each generated file entry must be an object")
            path = item.get("path")
            file_content = item.get("content")
            if not isinstance(path, str) or not path.strip():
                raise RuntimeError("Generated file entry missing string `path`")
            if not isinstance(file_content, str):
                raise RuntimeError(f"Generated file `{path}` missing string `content`")
            if looks_like_mojibake(file_content):
                raise RuntimeError(f"Generated file `{path}` appears to contain mojibake/corrupted UTF-8 text")
            validated.append({"path": path, "content": file_content})
        return validated

    def _extract_json_payload(self, content: str) -> str:
        """Extract a JSON object from a raw model response."""
        stripped = content.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            stripped = "\n".join(lines).strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            return stripped
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise RuntimeError("LLMHarness code output is not valid JSON")
        return stripped[start : end + 1]

    def _write_llm_generated_files(self, project_root: str, files: list[dict[str, str]]) -> list[str]:
        """Write validated generated files and return changed relative paths."""
        root = Path(project_root).expanduser().resolve()
        changed: list[str] = []
        for item in files:
            relative_path = item["path"].replace("\\", "/").strip()
            target = self._safe_generated_path(root, relative_path)
            previous = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
            if previous == item["content"]:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item["content"], encoding="utf-8")
            changed.append(relative_path)
        return changed

    def _safe_generated_path(self, root: Path, relative_path: str) -> Path:
        """Resolve one generated path and enforce the workspace boundary."""
        if not relative_path or Path(relative_path).is_absolute():
            raise RuntimeError(f"Generated path must be relative: {relative_path}")
        blocked_prefixes = (
            ".conductor/",
            ".git/",
            ".pytest_cache/",
            "__pycache__/",
            "node_modules/",
            ".venv/",
            "venv/",
        )
        normalized = relative_path.lower()
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized.startswith(blocked_prefixes) or "/.conductor/" in normalized:
            raise RuntimeError(f"Generated path targets a protected directory: {relative_path}")
        target = (root / relative_path).resolve()
        try:
            target.relative_to(root)
        except ValueError as error:
            raise RuntimeError(f"Generated path escapes workspace: {relative_path}") from error
        return target

    def _build_llm_code_report(
        self,
        workitem: WorkItem,
        agent: Agent,
        model: str,
        changed_files: list[str],
        raw_output: str,
        validation_result: HarnessResult | None,
        validation_command: list[str] | None,
        success: bool,
        error: str = "",
    ) -> str:
        """Build a report for controlled LLM code generation."""
        changed_lines = "\n".join(f"- `{path}`" for path in changed_files) or "- none"
        stdout = ((validation_result.stdout or "") if validation_result else "").strip() or "(鏃?stdout)"
        stderr = ((validation_result.stderr or "") if validation_result else "").strip() or "(鏃?stderr)"
        validation_section = (
            "## 鑷姩楠岃瘉\n"
            f"- Command: `{' '.join(validation_command or [])}`\n"
            f"- Exit Code: `{validation_result.exit_code}`\n"
            f"- Duration: `{validation_result.duration_ms}ms`\n"
            f"```text\n{stdout}\n```\n\n"
            f"```text\n{stderr}\n```\n"
            if validation_result is not None
            else "## 鑷姩楠岃瘉\n- 鏈墽琛孿n"
        )
        status = "鎴愬姛" if success else "澶辫触"
        error_section = f"\n## 澶辫触鍘熷洜\n- {error}\n" if error else ""
        return (
            f"# LLMHarness 浠ｇ爜鐢熸垚鎶ュ憡 - {workitem.id}\n\n"
            "## 鎵ц鎽樿\n"
            f"- 瑙掕壊: `{agent.role}`\n"
            f"- 妯″瀷: `{model}`\n"
            f"- WorkItem 绫诲瀷: `{workitem.kind}`\n"
            f"- 缁撴灉: {status}\n\n"
            f"## 鍐欏叆鏂囦欢\n{changed_lines}\n"
            f"{error_section}\n"
            f"{validation_section}\n\n"
            "## LLM 鍘熷杈撳嚭鎽樿\n"
            f"```text\n{self._tail(raw_output, limit=1600)}\n```\n"
        )

    def _missing_llm_harness_sections(self, content: str, workitem: WorkItem) -> list[str]:
        """Validate the minimum sections needed by downstream agents."""
        if workitem.kind == "requirement_spec":
            required = ["Goal", "Requirement Understanding", "Scope Boundaries", "Non-Goals", "Acceptance Criteria"]
        elif workitem.kind == "design_overview":
            required = ["Goal", "Requirement Understanding", "Scope Boundaries", "Approach", "Acceptance Criteria"]
        elif workitem.kind == "api_design":
            required = ["Goal", "API", "Data", "Acceptance"]
        else:
            required = ["Goal", "Approach", "Acceptance", "Risks"]
        return [section for section in required if section not in content]

    def _changed_files_satisfy_workitem(self, workitem: WorkItem, agent: Agent, changed_files: list[str]) -> bool:
        """Return whether code-edit changed files match the agent's output contract."""
        if not changed_files:
            return False
        if agent.role == "backend_engineer" and workitem.kind in {"api_implementation", "data_implementation"}:
            return any(self._is_backend_file(path) for path in changed_files)
        return True

    def _is_backend_file(self, path: str) -> bool:
        """Return whether a changed file is a concrete backend/API deliverable."""
        normalized = path.replace("\\", "/").lower()
        backend_suffixes = (".py", ".sql", ".toml", ".ini", ".yaml", ".yml", ".json")
        backend_markers = (
            "/app/",
            "/conductor/",
            "/api/",
            "/services/",
            "/models/",
            "/schemas/",
            "/tests/",
            "app.py",
            "main.py",
            "requirements.txt",
            "pyproject.toml",
            "pytest.ini",
        )
        frontend_only_suffixes = (".html", ".css", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte")
        return (
            (normalized.endswith(backend_suffixes) or any(marker in normalized for marker in backend_markers))
            and not normalized.endswith(frontend_only_suffixes)
        )


    def _should_fail_once(self, workitem: WorkItem) -> bool:
        """Return whether the workitem should fail once for retry tests."""
        return workitem.kind == "fail_once" and workitem.id not in self._failed_once_workitems

    def _should_use_harness(self, workitem: WorkItem, agent: Agent) -> bool:
        """Return whether the workitem should use tester harness."""
        return (
            self.enable_tester_harness
            and self.shell_harness is not None
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

    def _should_use_llm_harness(self, workitem: WorkItem, agent: Agent) -> bool:
        """Return whether the controlled LLM harness should produce this document."""
        return (
            self.llm_harness is not None
            and self.llm_harness_config is not None
            and self.llm_harness_config.enabled
            and agent.role in {"designer", "requirement_designer"}
            and workitem.stage in {"requirement", "design"}
            and workitem.kind in self.DESIGN_DOCUMENT_WORKITEM_KINDS
        )

    def _should_use_llm_code_harness(self, workitem: WorkItem, agent: Agent) -> bool:
        """Return whether the controlled LLM harness should generate code files."""
        return (
            self.require_real_code_outputs
            and self.llm_harness is not None
            and self.llm_harness_config is not None
            and self.llm_harness_config.enabled
            and agent.role in self.CODE_EDIT_AGENT_ROLES
            and workitem.kind in self.CODE_EDIT_WORKITEM_KINDS
        )

    def _resolve_agent_cli_binding(self, agent: Agent) -> str | None:
        """Resolve the active CLI binding for the agent."""
        return self.agent_cli_executor.resolve_binding(agent)

    def _model_for_agent_cli(self, cli_name: str) -> str:
        """Return the configured model label for an Agent CLI."""
        if cli_name == "codex":
            return f"{self.cli_selection_config.codex_model}/{self.cli_selection_config.codex_reasoning_effort}"
        if cli_name:
            return "local-cli-config"
        return ""

    def _tail(self, text: str, limit: int = 2000) -> str:
        """Return a bounded tail for manifest-safe execution metadata."""
        if len(text) <= limit:
            return text
        return text[-limit:]

    def _prompt_hash(self, prompt: str) -> str:
        """Return a stable non-reversible prompt fingerprint for audit manifests."""
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else ""

    def _should_use_llm(self, workitem: WorkItem, agent: Agent) -> bool:
        """Return whether LLM execution is allowed for this agent and workitem."""
        return (
            self.llm_usage_policy.runner_enabled
            and agent.llm_backend is not None
            and agent.role in self.llm_usage_policy.runner_allowed_roles
            and workitem.kind in self.llm_usage_policy.runner_allowed_kinds
        )

    def _requires_real_design_output(self, workitem: WorkItem, agent: Agent) -> bool:
        """Return whether this design work must not fall back to mock output."""
        return (
            self.require_real_design_outputs
            and agent.role in {"designer", "requirement_designer"}
            and workitem.stage in {"requirement", "design"}
            and workitem.kind in self.DESIGN_DOCUMENT_WORKITEM_KINDS
        )

    def _requires_real_code_output(self, workitem: WorkItem, agent: Agent) -> bool:
        """Return whether code work must not fall back to mock output."""
        return (
            self.require_real_code_outputs
            and agent.role in self.CODE_EDIT_AGENT_ROLES
            and workitem.kind in self.CODE_EDIT_WORKITEM_KINDS
        )

    def _is_disabled_llm_response(self, content: str) -> bool:
        """Return whether a hybrid LLM backend returned a disabled-backend placeholder."""
        return content.startswith("[local-disabled]") or content.startswith("[cloud-disabled]")

    def _real_backend_required_result(self, workitem: WorkItem, agent: Agent, reason: str) -> WorkItemRunResult:
        """Return a failed document when a real design backend is required but unavailable."""
        failure = configuration_required(reason)
        return WorkItemRunResult(
            content=(
                f"# 真实 Agent 产出未完成 - {workitem.id}\n\n"
                "## 状态\n"
                "当前工作项要求由真实 Agent 产出，系统不会再生成模拟交付物。\n\n"
                "## 原因\n"
                f"- {reason}\n\n"
                "## 需要处理\n"
                f"- 为 `{agent.role}` 绑定可用 Agent CLI，或启用 LLM Runner。\n"
                "- 重新运行当前步骤后，才会生成真实需求/设计/代码产物。\n"
            ),
            source_backend="real_backend_required",
            succeeded=False,
            failure=failure,
        )

    def _build_document_prompt(self, workitem: WorkItem, agent: Agent) -> str:
        """Build the document-style execution prompt."""
        criteria = "\n".join(f"- {item}" for item in workitem.acceptance_criteria) or "- No explicit acceptance criteria"
        role_instruction = {
            "requirement_designer": (
                "Produce a frozen-ready requirement specification. Cover user goal, scope boundaries, "
                "non-goals, acceptance criteria, edge cases, risks, assumptions, open questions, and downstream constraints."
            ),
            "solution_designer": (
                "Produce a solution design document focused on process completeness, data/API boundaries, "
                "validation constraints, and downstream execution risks."
            ),
            "designer": (
                "Produce a design document that backend and testing agents can execute. "
                "Do not introduce client UI implementation scope."
            ),
            "backend_engineer": "Produce a backend/API implementation note covering services, data, tests, and risks.",
            "tester": "Produce a validation document covering API behavior, data behavior, acceptance evidence, and risks.",
        }.get(agent.role, "Produce a concise execution document for this WorkItem.")
        return (
            f"You are the Conductor {agent.role} agent.\n"
            f"{role_instruction}\n"
            f"WorkItem ID: {workitem.id}\n"
            f"Kind: {workitem.kind}\n"
            f"Description: {workitem.description}\n"
            f"Acceptance criteria:\n{criteria}\n\n"
            "Return structured Markdown. Keep the scope backend/API-only; do not add client-facing product surfaces."
        )

    def _build_agent_cli_document_prompt(
        self,
        workitem: WorkItem,
        agent: Agent,
        cli_name: str,
        project_id: str | None = None,
    ) -> str:
        """Build CLI-specific document prompts when a provider has special constraints."""
        frozen_context = self._frozen_requirement_context(project_id) if project_id else ""
        if cli_name == "claude":
            return self._append_frozen_requirement_context(
                self._build_compact_claude_document_prompt(workitem, agent),
                frozen_context,
            )
        if cli_name == "codex":
            return self._append_frozen_requirement_context(
                self._build_compact_codex_document_prompt(workitem, agent),
                frozen_context,
            )
        if cli_name == "opencode":
            return self._append_frozen_requirement_context(
                self._build_compact_opencode_document_prompt(workitem, agent),
                frozen_context,
            )
        return self._append_frozen_requirement_context(self._build_document_prompt(workitem, agent), frozen_context)

    def _build_compact_codex_document_prompt(self, workitem: WorkItem, agent: Agent) -> str:
        """Build a strict non-interactive document prompt for Codex CLI."""
        criteria = "\n".join(f"- {item}" for item in workitem.acceptance_criteria) or "- No explicit acceptance criteria"
        delivery_contract = self._delivery_contract_prompt(workitem, agent)
        if workitem.kind == "requirement_spec" or agent.role == "requirement_designer":
            role_goal = "Produce a frozen-ready requirement specification for downstream design, development, and testing agents."
            required_sections = "鐩爣, 闇€姹傜悊瑙? 鑼冨洿杈圭晫, 闈炵洰鏍? 楠屾敹鏍囧噯, 杈圭晫/寮傚父鍦烘櫙, 椋庨櫓涓庡亣璁? 寰呯‘璁ら棶棰? 涓嬫父浜や粯绾︽潫"
        elif agent.role == "designer":
            role_goal = (
                "Produce a complete requirement/design document that downstream backend, "
                "and testing agents can execute from."
            )
            required_sections = "鐩爣, 闇€姹傜悊瑙? 鑼冨洿杈圭晫, 鏍稿績娴佺▼, 椤甸潰璁捐鍏虫敞鐐? 鎺ュ彛涓庢暟鎹叧娉ㄧ偣, 楠屾敹鏍囧噯, 椋庨櫓"
        else:
            role_goal = f"Produce a practical execution document for the {agent.role} role."
            required_sections = "鐩爣, 闇€姹傜悊瑙? 鏂规, 浜や粯鐗? 楠屾敹鏍囧噯, 椋庨櫓"
        return (
            "You are running as a non-interactive Conductor agent.\n"
            "Do not introduce yourself. Do not ask follow-up questions. Do not say you are waiting for a task.\n"
            "You must directly produce the requested deliverable as Chinese Markdown.\n\n"
            f"Agent role: {agent.role}\n"
            f"WorkItem ID: {workitem.id}\n"
            f"Stage: {workitem.stage}\n"
            f"Kind: {workitem.kind}\n"
            f"Task description:\n{workitem.description}\n\n"
            f"Acceptance criteria:\n{criteria}\n\n"
            f"Delivery contract:\n{delivery_contract}\n\n"
            f"Goal: {role_goal}\n"
            f"Required sections: {required_sections}.\n"
            "For requirement_spec, do not promote unrequested features such as edit, delete, login, sync, or import into scope; keep them as open questions or non-goals unless explicitly requested.\n"
            "Return only the Markdown document."
        )

    def _build_compact_claude_document_prompt(self, workitem: WorkItem, agent: Agent) -> str:
        """Build a compact English prompt for Claude CLI, but keep Chinese output."""
        kind_focus = {
            "requirement_spec": "Create a frozen-ready requirement specification with user goals, scope boundaries, non-goals, acceptance cases, edge/error cases, risks, assumptions, open questions, and downstream handoff constraints.",
            "design_overview": "Create a requirement design document with user goals, scope boundaries, main flows, implementation constraints, acceptance criteria, and risks.",
            "feature_slice_plan": "Create a milestone-based feature-slice plan with dependencies, implementation boundaries, and validation evidence for each slice.",
            "api_design": "Describe API boundaries, payloads, main endpoints, and failure handling.",
            "test_design": "Describe testing scope, acceptance checks, edge cases, and validation focus.",
            "acceptance_check": "Summarize acceptance status, unresolved risks, and release readiness.",
        }.get(workitem.kind, "Produce a concise execution document for this work item.")
        criteria = "\n".join(f"- {item}" for item in workitem.acceptance_criteria) or "- No explicit acceptance criteria"
        delivery_contract = self._delivery_contract_prompt(workitem, agent)
        return (
            f"You are the {agent.role} agent in Conductor.\n"
            f"Task focus: {kind_focus}\n"
            f"Work item kind: {workitem.kind}\n"
            f"Stage: {workitem.stage}\n"
            f"Acceptance checklist:\n{criteria}\n\n"
            f"Delivery contract:\n{delivery_contract}\n\n"
            "Return concise Chinese markdown.\n"
            "Do not ask follow-up questions.\n"
            "For requirement_spec use sections: 鐩爣, 闇€姹傜悊瑙? 鑼冨洿杈圭晫, 闈炵洰鏍? 楠屾敹鏍囧噯, 杈圭晫/寮傚父鍦烘櫙, 椋庨櫓涓庡亣璁? 寰呯‘璁ら棶棰? 涓嬫父浜や粯绾︽潫.\n"
            "For requirement_spec, do not promote unrequested features such as edit, delete, login, sync, or import into scope; keep them as open questions or non-goals unless explicitly requested.\n"
            "For other document tasks use sections: 鐩爣, 闇€姹傜悊瑙? 鑼冨洿杈圭晫, 鍏抽敭鍋囪, 鏂规, 浜や粯鐗? 楠屾敹鏍囧噯, 椋庨櫓.\n"
        )

    def _build_compact_opencode_document_prompt(self, workitem: WorkItem, agent: Agent) -> str:
        """Build a file-output document prompt for OpenCode/local models."""
        output_path = f"CONDUCTOR_OUTPUT_{workitem.id}.md"
        if workitem.kind == "requirement_spec" or agent.role == "requirement_designer":
            sections = "鐩爣, 闇€姹傜悊瑙? 鑼冨洿杈圭晫, 闈炵洰鏍? 楠屾敹鏍囧噯, 杈圭晫/寮傚父鍦烘櫙, 椋庨櫓涓庡亣璁? 寰呯‘璁ら棶棰? 涓嬫父浜や粯绾︽潫"
        elif agent.role == "designer":
            sections = "鐩爣, 闇€姹傜悊瑙? 鑼冨洿杈圭晫, 鏍稿績娴佺▼, 鎺ュ彛涓庢暟鎹叧娉ㄧ偣, 楠屾敹鏍囧噯, 椋庨櫓"
        else:
            sections = "鐩爣, 闇€姹傜悊瑙? 鏂规, 浜や粯鐗? 楠屾敹鏍囧噯, 椋庨櫓"
        description = workitem.description.replace("\n", " ")
        criteria = "; ".join(workitem.acceptance_criteria) or "No explicit acceptance criteria"
        delivery_contract = self._delivery_contract_prompt(workitem, agent).replace("\n", " ")
        return (
            f"Create a file {output_path} in the project root.\n"
            "Write concise Chinese Markdown into that file.\n"
            f"The document is for role {agent.role}, work item {workitem.id}, kind {workitem.kind}.\n"
            f"Task: {description}\n"
            f"Acceptance criteria: {criteria}\n"
            f"Delivery contract: {delivery_contract}\n"
            f"Required Markdown sections: {sections}.\n"
            "For requirement_spec, unrequested edit/delete/login/sync/import features must stay out of scope or be listed only as open questions.\n"
            "Do not ask questions. After the file is written, reply exactly: DONE\n"
        )

    def _append_frozen_requirement_context(self, prompt: str, frozen_context: str) -> str:
        """Append frozen requirement instructions when a project baseline exists."""
        if not frozen_context:
            return prompt
        opencode_done_instruction = "Do not ask questions. After the file is written, reply exactly: DONE"
        if opencode_done_instruction in prompt:
            return prompt.replace(
                opencode_done_instruction,
                (
                    frozen_context
                    + "\nUse this frozen baseline as the controlling contract. Do not expand non-goals, "
                    "and make acceptance criteria directly traceable.\n"
                    + opencode_done_instruction
                ),
            )
        return (
            prompt.rstrip()
            + "\n\n"
            + frozen_context
            + "\nUse this frozen baseline as the controlling contract. Do not expand non-goals, "
            "and make acceptance criteria directly traceable.\n"
        )

    def _read_agent_cli_document_file(self, project_root: str, workitem: WorkItem, cli_name: str) -> str:
        """Read file-based document output from CLI agents that may not exit cleanly."""
        if cli_name != "opencode":
            return ""
        output_path = Path(project_root) / f"CONDUCTOR_OUTPUT_{workitem.id}.md"
        if not output_path.exists() or output_path.stat().st_size == 0:
            return ""
        return output_path.read_text(encoding="utf-8", errors="replace").strip()

    def _build_code_execution_prompt(
        self,
        project_id: str,
        workitem: WorkItem,
        agent: Agent,
        cli_name: str | None = None,
    ) -> str:
        """Build the real code-edit prompt."""
        context_pack = self._build_context_pack(project_id, workitem)
        state = self.state_store.get_state(project_id)
        project_goal = state.project.goal
        frozen_context = self._frozen_requirement_context(project_id)
        if cli_name == "opencode":
            return self._build_compact_opencode_code_prompt(workitem, agent, project_goal, frozen_context=frozen_context)
        criteria = "; ".join(workitem.acceptance_criteria) or "no explicit acceptance criteria"
        delivery_contract = self._delivery_contract_prompt(workitem, agent)
        artifact_context = "\n\n".join(
            f"### Upstream Artifact\n{artifact[:1600]}"
            for artifact in context_pack.artifacts[-5:]
        )
        role_hint = (
            "You own the backend/API deliverable. Implement endpoints, data structures, and tests that match "
            "the actual business domain from the project requirement. Do not build a generic project/task API "
            "unless the requirement explicitly asks for one.\n"
        )

        prompt = (
            "You must directly edit files in the current workspace.\n"
            "Do not ask follow-up questions. Do not stop at analysis. Do not only describe a plan.\n"
            "Ignore generated directories: .conductor/, .pytest_cache/, __pycache__, node_modules/.\n"
            "Inspect the relevant files, make the minimum code changes required, run the local tests when applicable, and finish.\n\n"
            f"Full project requirement:\n{project_goal}\n\n"
            f"Agent role: {agent.role}\n"
            f"{role_hint}"
            f"Task: {workitem.description}\n"
            f"WorkItem kind: {workitem.kind}\n"
            f"Acceptance criteria: {criteria}\n"
            f"Delivery contract:\n{delivery_contract}\n"
            f"{frozen_context}\n"
            "If the task text mentions a file name, start from that file.\n"
        )
        if artifact_context:
            prompt += f"\nUpstream context:\n{artifact_context}\n"
        prompt += "\nAfter the code and tests are done, output exactly: done"
        return prompt

    def _build_compact_opencode_code_prompt(
        self,
        workitem: WorkItem,
        agent: Agent,
        project_goal: str,
        frozen_context: str = "",
    ) -> str:
        """Build a short code-edit prompt for OpenCode to avoid slow artifact exploration."""
        criteria = "; ".join(workitem.acceptance_criteria) or "no explicit acceptance criteria"
        delivery_contract = self._delivery_contract_prompt(workitem, agent)
        return (
            f"You are the {agent.role}. Work only in the current directory.\n"
            "Do not inspect or edit .conductor/, .pytest_cache/, __pycache__, node_modules/, or generated logs.\n"
            f"Full project requirement: {project_goal}\n"
            f"{frozen_context}\n"
            f"Delivery contract:\n{delivery_contract}\n"
            f"Task: {workitem.description}\n"
            f"WorkItem kind: {workitem.kind}\n"
            f"Acceptance criteria: {criteria}\n"
            "Make the smallest real code changes that satisfy the business domain, run local tests if available, then output exactly: done"
        )

    def _delivery_contract_prompt(self, workitem: WorkItem, agent: Agent) -> str:
        """Render the shared delivery contract for direct Runner prompts."""
        contract = build_delivery_contract(
            stage=workitem.stage,
            kind=workitem.kind,
            role=agent.role,
            required_input_artifact_ids=list(workitem.input_artifact_ids),
            is_rework=bool(workitem.feedback_from or workitem.rework_of),
        )
        return "\n".join(render_delivery_contract_markdown(contract))

    def _build_context_pack(self, project_id: str, workitem: WorkItem) -> ContextPack:
        """Build a lightweight context pack for the current workitem."""
        state = self.state_store.get_state(project_id)
        return self.context_builder.build(state=state, workitem=workitem)

    def _context_artifact_ids(self, project_id: str, workitem: WorkItem) -> list[str]:
        """Return the artifact ids injected into the workitem context."""
        return self._build_context_pack(project_id, workitem).artifact_ids

    def _latest_frozen_requirement(self, project_id: str | None) -> Artifact | None:
        """Return the latest frozen requirement artifact for a project."""
        if not project_id:
            return None
        state = self.state_store.get_state(project_id)
        return next(
            (artifact for artifact in reversed(state.artifacts) if artifact.kind == "frozen_requirement_spec"),
            None,
        )

    def _frozen_requirement_context(self, project_id: str | None, *, max_chars: int = 4000) -> str:
        """Build a prompt-ready frozen requirement baseline section."""
        frozen_requirement = self._latest_frozen_requirement(project_id)
        if frozen_requirement is None:
            return ""
        content = self.artifact_store.read_content(frozen_requirement).strip()
        if len(content) > max_chars:
            content = content[:max_chars].rstrip() + "\n[truncated]"
        return (
            "Frozen Requirement Baseline:\n"
            "This baseline is the controlling contract for design, implementation, and testing.\n"
            "You must preserve its scope, non-goals, acceptance criteria, edge cases, risks, and downstream constraints.\n"
            "Do not introduce features that the baseline excludes or does not require.\n\n"
            f"{content}\n"
        )

    def _evaluate_scope_contract(self, project_id: str, candidate_content: str):
        """Validate candidate content against the latest frozen requirement."""
        frozen_requirement = self._latest_frozen_requirement(project_id)
        return evaluate_scope_contract(frozen_requirement, candidate_content)

    def _evaluate_requirement_coverage(
        self,
        project_id: str,
        result: HarnessResult,
        workitem: WorkItem | None = None,
    ) -> CoverageResult:
        """Validate harness evidence against the latest frozen requirement."""
        frozen_requirement = self._latest_frozen_requirement(project_id)
        output = f"{result.stdout or ''}\n{result.stderr or ''}"
        if workitem and workitem.kind == "api_validation" and result.success and self._has_api_endpoint_evidence(output):
            output = f"{output}\nAPI validation exercised endpoint behavior"
        return evaluate_requirement_coverage(frozen_requirement, output)

    def _has_api_endpoint_evidence(self, output: str) -> bool:
        """Return whether validation output proves API endpoint behavior, not just test success."""
        lowered = output.lower()
        if "api validation exercised endpoint behavior" in lowered:
            return True
        endpoint_signal = any(
            term in lowered
            for term in (
                "/api",
                "endpoint",
                "route",
                "testclient",
                "httpx",
                "requests.",
                "fastapi",
                "flask",
                "django",
            )
        )
        method_signal = re.search(r"\b(get|post|put|patch|delete|options|head)\s+[/\w-]", lowered) is not None
        status_signal = (
            re.search(r"\bstatus(?:_code| code)?\s*[:=]?\s*[1-5]\d\d\b", lowered) is not None
            or re.search(r"\bhttp\s+[1-5]\d\d\b", lowered) is not None
            or re.search(r"->\s*[1-5]\d\d\b", lowered) is not None
        )
        response_signal = any(term in lowered for term in ("response", "payload", "json body", "json=", "body="))
        return (endpoint_signal or method_signal) and (status_signal or response_signal)

    def _build_harness_request(self, workitem: WorkItem, working_directory: str, stream_callback=None) -> HarnessRequest:
        """Build tester harness request."""
        return HarnessRequest(
            command=self._select_test_command(working_directory),
            working_directory=working_directory,
            timeout_seconds=180.0,
            description=f"{workitem.kind}:{workitem.description}",
            stream_callback=stream_callback,
            environment=self._validation_environment(),
        )

    def _run_post_edit_validation(self, workitem: WorkItem, working_directory: str) -> HarnessResult:
        """Run a post-edit validation pass."""
        command = self._select_test_command(working_directory)
        request = HarnessRequest(
            command=command,
            working_directory=working_directory,
            timeout_seconds=300.0,
            description=f"post-validate:{workitem.id}",
            stream_callback=self._build_stream_callback_from_workitem(workitem.id),
            environment=self._validation_environment(),
        )
        return self.shell_harness.run(request)

    def _validation_environment(self) -> dict[str, str]:
        """Make Conductor's internal validation modules importable from project roots."""
        platform_root = str(Path(__file__).resolve().parents[2])
        existing = os.environ.get("PYTHONPATH", "")
        pythonpath = platform_root if not existing else os.pathsep.join([platform_root, existing])
        return {"PYTHONPATH": pythonpath}

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
        return compact[: limit - 1].rstrip() + "..."

    def _select_test_command(self, working_directory: str | None = None) -> list[str]:
        """Choose the local validation command."""
        root = Path(working_directory or Path.cwd())
        package_json = root / "package.json"
        if package_json.exists() and shutil.which("npm"):
            try:
                package = json.loads(package_json.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                package = {}
            if isinstance(package.get("scripts"), dict) and package["scripts"].get("test"):
                return ["npm", "test"]
        if (root / "pytest.ini").exists() or (root / "conftest.py").exists() or any(root.glob("test*.py")) or (root / "tests").exists():
            return [sys.executable, "-m", "pytest", "-q"]
        if self._pyproject_declares_pytest(root) and shutil.which("uv"):
            return ["uv", "run", "python", "-m", "pytest", "-q"]
        if shutil.which("pytest"):
            return ["pytest", "-q"]
        return [sys.executable, "-m", "pytest", "-q"]

    def _has_project_deliverables(self, working_directory: str, project_id: str | None = None) -> bool:
        """Return whether a project root contains files worth validating."""
        root = Path(working_directory)
        if self._is_conductor_source_root(root) and project_id and not self._has_recorded_code_changes(project_id):
            return False
        deliverable_paths = [
            "app.py",
            "main.py",
            "package.json",
            "pyproject.toml",
            "src",
            "static",
        ]
        if any((root / path).exists() for path in deliverable_paths):
            return True
        return False

    def _is_conductor_source_root(self, root: Path) -> bool:
        """Return whether the path is this platform's source checkout."""
        return (
            (root / "conductor" / "controller" / "engine.py").exists()
            and (root / "app" / "run_project.py").exists()
            and (root / "tests").exists()
        )

    def _has_recorded_code_changes(self, project_id: str) -> bool:
        """Return whether previous executions changed files in the current project."""
        try:
            state = self.state_store.get_state(project_id)
        except KeyError:
            return False
        return any(execution.changed_files for execution in state.executions)

    def _build_harness_skip_report(self, workitem: WorkItem, agent: Agent, working_directory: str) -> str:
        """Build a report when no concrete deliverable exists to validate."""
        return (
            f"# 楠屾敹妫€鏌ユ姤鍛?- {workitem.id}\n\n"
            "## 鎵ц鎽樿\n"
            f"- 瑙掕壊: `{agent.role}`\n"
            f"- WorkItem 绫诲瀷: `{workitem.kind}`\n"
            f"- Working Directory: `{working_directory}`\n"
            "- 缁撴灉: 璺宠繃鐪熷疄鍛戒护鎵ц\n\n"
            "## 鍘熷洜\n"
            "- 褰撳墠椤圭洰鐩綍鏈彂鐜板彲楠屾敹浜や粯鏂囦欢锛屽洜姝ゆ病鏈夎繍琛屾祴璇曞懡浠ゃ€俓n"
            "- 杩欏彲浠ラ伩鍏嶅湪骞冲彴婧愮爜鐩綍涓嬭瑙﹀彂骞冲彴鑷韩娴嬭瘯濂椾欢銆俓n\n"
            "## 缁撹\n"
            "- 璇?WorkItem 宸茶褰曚负鏃犲彲楠屾敹鐩爣锛涚湡瀹為」鐩簲鍦?development 闃舵浜х敓浜や粯鏂囦欢鍚庡啀杩涘叆楠屾敹銆俓n"
        )

    def _pyproject_declares_pytest(self, root: Path) -> bool:
        """Return whether pyproject explicitly declares pytest for uv-managed validation."""
        pyproject = root / "pyproject.toml"
        if not pyproject.exists():
            return False
        text = pyproject.read_text(encoding="utf-8", errors="ignore").lower()
        return "pytest" in text

    def _is_no_tests_discovered(self, result: HarnessResult) -> bool:
        """Pytest exit code 5 means collection found no tests, not a product failure."""
        output = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
        return result.exit_code == 5 and "no tests ran" in output

    def _build_harness_report(
        self,
        workitem: WorkItem,
        agent: Agent,
        request: HarnessRequest,
        result: HarnessResult,
        coverage_result: CoverageResult | None = None,
    ) -> str:
        """Convert a harness result into a Markdown report."""
        no_tests_discovered = self._is_no_tests_discovered(result)
        status_label = "no tests discovered" if no_tests_discovered else ("passed" if result.success else "failed")
        no_tests_note = "- No test files were discovered; this is recorded as a pending-test report.\n" if no_tests_discovered else ""
        stdout = (result.stdout or "").strip() or "(鏃?stdout)"
        stderr = (result.stderr or "").strip() or "(鏃?stderr)"
        command = " ".join(request.command)
        checklist_section = self._testing_checklist_report(workitem)
        coverage_section = f"{coverage_result.render_markdown()}\n" if coverage_result else ""
        return (
            f"# Validation Report - {workitem.id}\n\n"
            "## Summary\n"
            f"- Agent: `{agent.role}`\n"
            f"- Kind: `{workitem.kind}`\n"
            f"- Status: {status_label}\n"
            f"- Exit Code: `{result.exit_code}`\n"
            f"- Duration: `{result.duration_ms}ms`\n"
            f"- Working Directory: `{request.working_directory}`\n"
            f"- Command: `{command}`\n\n"
            "## WorkItem\n"
            f"- Description: {workitem.description}\n"
            f"- Stage: {workitem.stage}\n\n"
            f"{checklist_section}"
            f"## stdout\n```text\n{stdout}\n```\n\n"
            f"## stderr\n```text\n{stderr}\n```\n\n"
            f"{coverage_section}"
            "## Conclusion\n"
            f"- Validation {status_label}.\n"
            f"{no_tests_note}"
            "- Failed validation should trigger retry or escalation.\n"
        )

    def _testing_checklist_report(self, workitem: WorkItem) -> str:
        """Render machine-readable testing evidence contracts into harness reports."""
        if not workitem.testing_checklist:
            return ""
        lines = [
            "## Testing Checklist Evidence Contract",
            "- Tester output must provide observable evidence for each required evidence term below.",
        ]
        for item in workitem.testing_checklist:
            rule_id = str(item.get("rule_id", "")).strip() or "-"
            label = str(item.get("label", "")).strip() or "-"
            status = str(item.get("status", "")).strip() or "-"
            requirement_terms = self._join_checklist_terms(item.get("requirement_terms"))
            evidence_terms = self._join_checklist_terms(item.get("required_evidence_terms"))
            lines.append(
                f"- {rule_id} | {label} | status={status} | "
                f"requirement_terms={requirement_terms} | required_evidence={evidence_terms}"
            )
        return "\n".join(lines) + "\n\n"

    def _join_checklist_terms(self, value: object) -> str:
        if not isinstance(value, list):
            return "-"
        terms = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(terms) if terms else "-"

    def _build_code_execution_report(
        self,
        workitem: WorkItem,
        agent: Agent,
        cli_name: str,
        changed_files: list[str],
        cli_stdout: str,
        cli_stderr: str,
        validation_result: HarnessResult | None,
        validation_command: list[str] | None,
        success: bool,
    ) -> str:
        """Convert a real code-edit execution into a Markdown report."""
        delivery_contract = "\n".join(
            render_delivery_contract_markdown(
                build_delivery_contract(
                    stage=workitem.stage,
                    kind=workitem.kind,
                    role=agent.role,
                    required_input_artifact_ids=list(workitem.input_artifact_ids),
                    is_rework=bool(workitem.feedback_from or workitem.rework_of),
                )
            )
        )
        acceptance_trace = "\n".join(
            render_acceptance_trace_markdown(
                build_acceptance_trace(
                    list(workitem.acceptance_criteria),
                    validation_success=(validation_result.success if validation_result is not None else None),
                    changed_files=changed_files,
                )
            )
        )
        change_lines = "\n".join(f"- `{path}`" for path in changed_files) or "- 无"
        validation_stdout = ((validation_result.stdout or "") if validation_result else "").strip() or "(无 stdout)"
        validation_stderr = ((validation_result.stderr or "") if validation_result else "").strip() or "(无 stderr)"
        validation_section = (
            "## 自动验证\n"
            f"- Command: `{' '.join(validation_command or [])}`\n"
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
            f"## Delivery Contract\n{delivery_contract}\n\n"
            f"## Acceptance Trace\n{acceptance_trace}\n\n"
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
            "tester": "测试/验收文档",
        }.get(agent.role, "执行说明文档")
        return f"{role_title} - {workitem.id}"

    def _build_mock_document(self, workitem: WorkItem, agent: Agent, source_note: str) -> str:
        """Build a high-quality mock artifact."""
        criteria = "\n".join(f"- {item}" for item in workitem.acceptance_criteria) or "- 当前工作项未提供显式验收标准"
        role_sections = {
            "designer": self._mock_designer_sections(workitem),
            "backend_engineer": self._mock_backend_sections(workitem),
            "tester": self._mock_tester_sections(workitem),
        }.get(agent.role, self._mock_generic_sections(workitem))
        return (
            f"# {self._build_artifact_title(workitem, agent)}\n\n"
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
            "- 将需求拆成接口、数据、验收三类交付关注点。\n"
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


    def _mock_tester_sections(self, workitem: WorkItem) -> str:
        return (
            "## 方案\n"
            "- 覆盖主路径、异常输入、边界条件和回归风险。\n"
            "- 将测试用例按接口交互、数据状态和验收标准分组。\n"
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
