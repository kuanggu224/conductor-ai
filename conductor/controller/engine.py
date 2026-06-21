"""ConductorEngine，统一管理项目实例生命周期。

使用统一的配置系统管理所有可配置选项。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from conductor.agents.registry import AgentRegistry
from conductor.artifacts.store import ArtifactStore
from conductor.collaboration.runner import CollaborationRunner
from conductor.collaboration.policy import CollaborationPolicy
from conductor.config.cli import CLISelectionConfig, load_cli_selection_config
from conductor.config.execution import ExecutionScopeConfig, RunProfile, load_execution_scope_config
from conductor.config.llm import LLMRuntimeConfig, build_default_hybrid_llm_backend, load_llm_runtime_config
from conductor.config.system import SystemConfig
from conductor.controller.lead_controller import LeadController
from conductor.domain.models import SharedProjectState
from conductor.execution.planner import Planner
from conductor.execution.runner import Runner
from conductor.execution.runtime_stream import RuntimeStreamStore
from conductor.execution.router import Router
from conductor.harness.llm import OpenAICompatibleLLMHarness
from conductor.logging.store import ProjectLogStore
from conductor.manifest import RunManifestWriter
from conductor.state.store import InMemoryStateStore
from conductor.workflow.template import WorkflowTemplate


class ConductorEngine:
    """统一封装 Project 创建、读取和推进。"""

    TERMINAL_STATUSES = {"completed", "blocked"}

    def __init__(
        self,
        log_dir: str | Path | None = None,
        artifact_dir: str | Path | None = None,
        llm_runtime_config: LLMRuntimeConfig | None = None,
        execution_scope_config: ExecutionScopeConfig | None = None,
        cli_selection_config: CLISelectionConfig | None = None,
        system_config: SystemConfig | None = None,
        state_store: InMemoryStateStore | None = None,
        run_profile: str | RunProfile = RunProfile.MOCK,
        require_real_design_outputs: bool = False,
        require_real_code_outputs: bool | None = None,
        llm_harness_backend: str | None = None,
    ) -> None:
        self.state_store = state_store or InMemoryStateStore()
        self.system_config = system_config or SystemConfig.load()
        self.workflow_template = WorkflowTemplate(config=self.system_config)
        self.llm_runtime_config = llm_runtime_config or load_llm_runtime_config()
        self.execution_scope_config = execution_scope_config or load_execution_scope_config()
        self.cli_selection_config = cli_selection_config or load_cli_selection_config()
        self.run_profile = run_profile.value if isinstance(run_profile, RunProfile) else str(run_profile)
        self.llm_harness_backend = llm_harness_backend
        self._mock_without_explicit_llm = self.run_profile in {
            RunProfile.MOCK.value,
            RunProfile.API_MOCK.value,
            RunProfile.API_SQLITE.value,
        } and llm_harness_backend is None
        if self._mock_without_explicit_llm:
            self.llm_runtime_config = replace(
                self.llm_runtime_config,
                usage=replace(self.llm_runtime_config.usage, runner_enabled=False),
            )
        llm_harness_config = self._select_llm_harness_config(llm_harness_backend)
        self.planner = Planner(
            scope_config=self.execution_scope_config,
            config=self.system_config,
        )
        self.registry = AgentRegistry(
            llm_backend=build_default_hybrid_llm_backend(self.llm_runtime_config),
            config=self.system_config,
        )
        self.router = Router(
            self.registry,
            config=self.system_config,
        )
        self.artifact_store = ArtifactStore(artifact_dir or Path(".conductor_artifacts"))
        self.runtime_stream_store = RuntimeStreamStore()
        self.runner = Runner(
            state_store=self.state_store,
            llm_usage_policy=self.llm_runtime_config.usage,
            artifact_store=self.artifact_store,
            enable_tester_harness=True,
            enable_api_mock_delivery=self.run_profile == RunProfile.API_MOCK.value,
            enable_api_sqlite_delivery=self.run_profile == RunProfile.API_SQLITE.value,
            cli_selection_config=self.cli_selection_config,
            runtime_stream_store=self.runtime_stream_store,
            require_real_design_outputs=require_real_design_outputs,
            require_real_code_outputs=(
                bool(self.cli_selection_config.selected_cli_names)
                if require_real_code_outputs is None
                else require_real_code_outputs
            ),
            llm_harness=OpenAICompatibleLLMHarness() if llm_harness_config is not None else None,
            llm_harness_config=llm_harness_config,
        )
        self.collaboration_runner = CollaborationRunner(
            state_store=self.state_store,
            registry=self.registry,
            artifact_store=self.artifact_store,
            policy=CollaborationPolicy(
                enabled=self.execution_scope_config.design_collaboration_enabled and self.system_config.collaboration.enabled,
                max_rounds=self.system_config.collaboration.max_rounds,
                lead_role_by_stage=self.system_config.collaboration.lead_role_by_stage,
                lead_role_by_kind=self.system_config.collaboration.lead_role_by_kind,
                peer_reviewer_roles_by_stage=self.system_config.collaboration.peer_reviewer_roles_by_stage,
                reviewer_roles_by_stage=self.system_config.collaboration.reviewer_roles_by_stage,
                enabled_kinds=self.system_config.collaboration.enabled_kinds,
                dynamic_requirement_review_enabled=(
                    self.system_config.collaboration.dynamic_requirement_review_enabled
                ),
            ),
            cli_selection_config=CLISelectionConfig() if llm_harness_backend is not None else self.cli_selection_config,
            runtime_stream_store=self.runtime_stream_store,
            require_real_outputs=llm_harness_config is not None or require_real_design_outputs,
            use_llm=llm_harness_backend is None and not self._mock_without_explicit_llm,
            llm_harness=OpenAICompatibleLLMHarness() if llm_harness_config is not None else None,
            llm_harness_config=llm_harness_config,
        )
        self.log_store = ProjectLogStore(log_dir or Path(".conductor_logs"))
        self.manifest_writer = RunManifestWriter()
        self._logged_event_counts: dict[str, int] = {}
        self.controller = LeadController(
            workflow_template=self.workflow_template,
            state_store=self.state_store,
            runner=self.runner,
            planner=self.planner,
            registry=self.registry,
            router=self.router,
            collaboration_runner=self.collaboration_runner,
        )

    def _select_llm_harness_config(self, backend: str | None):
        """Select the runtime LLM config used by the controlled LLM harness."""
        if backend == "local":
            return self.llm_runtime_config.local
        if backend == "cloud":
            return self.llm_runtime_config.cloud
        return None

    def create_project(self, requirement: str, project_root: str | None = None) -> SharedProjectState:
        """创建新的 Project 实例。"""
        resolved_root = Path(project_root or Path.cwd()).expanduser().resolve()
        resolved_root.mkdir(parents=True, exist_ok=True)
        state = self.controller.initialize_project(requirement=requirement, project_root=str(resolved_root))
        self._sync_logs(state)
        return state

    def get_project(self, project_id: str) -> SharedProjectState:
        """读取指定 Project 状态。"""
        return self.state_store.get_state(project_id)

    def list_projects(self) -> list[SharedProjectState]:
        """返回当前所有项目状态。"""
        return self.state_store.list_states()

    def step_project(self, project_id: str) -> SharedProjectState:
        """推进一个项目一步。"""
        state = self.get_project(project_id)
        if self.is_terminal(state):
            self._sync_logs(state)
            return state
        state = self.controller.advance(state)
        self._sync_logs(state)
        return state

    def run_project(self, project_id: str, max_steps: int = 100) -> SharedProjectState:
        """持续推进项目直到结束或达到最大步数。"""
        state = self.get_project(project_id)
        step_count = 0
        while not self.is_terminal(state) and step_count < max_steps:
            if self.controller.decide_next_action(state) == "human_hold":
                state = self.controller.advance(state)
                self._sync_logs(state)
                break
            state = self.controller.advance(state)
            self._sync_logs(state)
            step_count += 1
        self._sync_logs(state)
        return state

    def is_terminal(self, state: SharedProjectState) -> bool:
        """判断项目是否处于终态。"""
        return state.project_status.value in self.TERMINAL_STATUSES

    def read_project_logs(self, project_id: str):
        """读取指定项目的持久化日志。"""
        state = self.get_project(project_id)
        return self.log_store.read_events(project_id, project_root=state.project.project_root)

    def write_project_report(self, project_id: str) -> Path:
        """Write a Markdown report for the current project state."""
        state = self.get_project(project_id)
        self._sync_logs(state)
        return self.log_store.write_project_report(state)

    def write_run_manifest(
        self,
        project_id: str,
        report_path: str | Path,
        run_options: dict[str, object] | None = None,
        pre_run_maintenance: dict[str, object] | None = None,
    ) -> Path:
        """Write a run manifest for the current project state."""
        state = self.get_project(project_id)
        return self.manifest_writer.write(
            state=state,
            cli_config=self.cli_selection_config,
            run_profile=self.run_profile,
            report_path=report_path,
            llm_runtime_config=self.llm_runtime_config,
            run_options=run_options,
            pre_run_maintenance=pre_run_maintenance,
        )

    def _sync_logs(self, state: SharedProjectState) -> None:
        """把尚未落盘的 recent_events 追加写入 JSONL。"""
        project_id = state.project.id
        start_index = self._logged_event_counts.get(project_id, 0)
        for event_index, message in enumerate(state.recent_events[start_index:], start=start_index):
            self.log_store.append_state_event(state=state, event_index=event_index, message=message)
        self._logged_event_counts[project_id] = len(state.recent_events)
