"""BoardSnapshot 组装服务。"""

from __future__ import annotations

from conductor.board.models import (
    BoardActivationNodeView,
    BoardArtifactView,
    BoardDesignCollaborationView,
    BoardExecutionView,
    BoardExecutionRuntimeView,
    BoardMeetingAgentView,
    BoardProjectAgentView,
    BoardProjectSummary,
    BoardReviewView,
    BoardSnapshot,
    BoardTaskAssignmentView,
    BoardWorkItemView,
)
from conductor.domain.models import SharedProjectState
from conductor.config.cli import CLISelectionConfig
from conductor.config.llm import LLMRuntimeConfig
from conductor.execution.failure_policy import remediation_suggestions
from conductor.task_center.service import TaskCenterService

STAGE_LABELS = {
    "requirement": "需求",
    "design": "设计",
    "development": "研发",
    "testing": "测试",
    "-": "-",
}

PROJECT_STATUS_LABELS = {
    "initialized": "已初始化",
    "in_progress": "进行中",
    "completed": "已完成",
    "blocked": "已阻塞",
}

WORKITEM_STATUS_LABELS = {
    "pending": "待执行",
    "running": "执行中",
    "done": "已完成",
    "failed": "失败",
}

EXECUTION_STATUS_LABELS = {
    "success": "成功",
    "failed": "失败",
}

ROLE_LABELS = {
    "requirement_designer": "需求设计",
    "solution_designer": "方案设计",
    "designer": "产品/设计",
    "backend_engineer": "后端研发",
    "frontend_engineer": "前端研发",
    "tester": "测试",
}

AGENT_LABELS = {
    "agent-requirement-designer": "需求设计 Agent",
    "agent-solution-designer": "方案设计 Agent",
    "agent-designer": "产品/设计 Agent",
    "agent-backend": "后端研发 Agent",
    "agent-frontend": "前端研发 Agent",
    "agent-tester": "测试 Agent",
}

ROLE_SHORT_LABELS = {
    "requirement_designer": "REQ",
    "solution_designer": "SOL",
    "designer": "UX",
    "backend_engineer": "BE",
    "frontend_engineer": "FE",
    "tester": "QA",
}

ROLE_POSITION_CLASSES = {
    "designer": "node-top-left",
    "backend_engineer": "node-top-right",
    "frontend_engineer": "node-bottom-right",
    "tester": "node-bottom-left",
}

WORKITEM_KIND_LABELS = {
    "requirement_spec": "冻结需求规格",
    "frozen_requirement_spec": "冻结需求规格",
    "design_overview": "总体设计",
    "ui_design": "界面设计",
    "api_design": "接口设计",
    "test_design": "测试设计",
    "generic_implementation": "通用实现说明",
    "api_implementation": "后端接口说明",
    "data_implementation": "数据实现说明",
    "ui_implementation": "前端实现说明",
    "acceptance_check": "验收检查",
    "automated_test": "自动化测试说明",
    "api_validation": "接口验证",
    "ui_validation": "界面验证",
    "fail_once": "失败重试模拟",
    "collaboration_review": "多 Agent 协作评审",
}

SOURCE_BACKEND_LABELS = {
    "mock": "模拟执行",
    "mock_fallback": "模拟兜底",
    "llm/local": "本地 LLM",
    "llm/cloud": "云端 LLM",
    "llm/hybrid": "混合 LLM",
    "cli": "CLI",
    "cli/shell": "Shell Harness",
    "agent_cli/claude": "Claude CLI",
    "agent_cli/codex": "Codex CLI",
    "agent_cli/qwen": "Qwen CLI",
    "agent_cli/opencode": "OpenCode CLI",
    "collaboration": "多 Agent 协作",
    "real_backend_required": "等待真实 Agent 后端",
    "unknown": "未知来源",
}

COLLABORATION_STATUS_LABELS = {
    "running": "协作中",
    "accepted": "已通过",
    "max_rounds_reached": "达到最大轮次",
    "failed": "失败",
}

REVIEW_DECISION_LABELS = {
    "approve": "通过",
    "request_changes": "请求修改",
}

TASK_ASSIGNMENT_STATUS_LABELS = {
    "queued": "待领取",
    "claimed": "已领取",
    "completed": "已归还",
    "failed": "执行失败",
    "blocked": "依赖阻断",
}


def label_stage(value: str) -> str:
    """转换阶段展示名。"""
    return STAGE_LABELS.get(value, value)


def label_role(value: str) -> str:
    """转换角色展示名。"""
    return ROLE_LABELS.get(value, AGENT_LABELS.get(value, value))


class _BoardStateStore:
    """Read-only adapter for Board task-center readiness calculations."""

    def __init__(self, state: SharedProjectState) -> None:
        self.state = state

    def get_state(self, project_id: str) -> SharedProjectState:
        if self.state.project.id != project_id:
            raise KeyError(project_id)
        return self.state


class BoardService:
    """把内部 SharedProjectState 转换成 BoardSnapshot。"""

    def build_snapshot(
        self,
        state: SharedProjectState,
        cli_config: CLISelectionConfig | None = None,
        llm_runtime_config: LLMRuntimeConfig | None = None,
    ) -> BoardSnapshot:
        """构建页面展示快照。"""
        current_stage = state.current_stage or "-"
        project_status = state.project_status.value
        artifacts = self._build_artifact_views(state)
        code_execution_artifacts = [
            artifact
            for artifact in artifacts
            if artifact.kind in {"api_implementation", "data_implementation", "generic_implementation", "ui_implementation"}
            and artifact.source_backend.startswith("agent_cli/")
        ]
        task_center = TaskCenterService(_BoardStateStore(state))
        return BoardSnapshot(
            project_id=state.project.id,
            project_goal=state.project.goal,
            project_root=state.project.project_root,
            project_status=project_status,
            project_status_label=PROJECT_STATUS_LABELS.get(project_status, project_status),
            current_stage=current_stage,
            current_stage_label=label_stage(current_stage),
            planned_roles=state.planned_roles,
            planned_role_labels=[label_role(role) for role in state.planned_roles],
            blockers=state.blockers,
            gate_history=state.gate_history,
            recent_events=state.recent_events,
            recent_events_tail=state.recent_events[-16:],
            workitems=[
                BoardWorkItemView(
                    id=workitem.id,
                    stage=workitem.stage,
                    stage_label=label_stage(workitem.stage),
                    kind=workitem.kind,
                    kind_label=WORKITEM_KIND_LABELS.get(workitem.kind, workitem.kind),
                    status=workitem.status.value,
                    status_label=WORKITEM_STATUS_LABELS.get(workitem.status.value, workitem.status.value),
                    owner_agent=workitem.owner_agent or "-",
                    owner_agent_label=AGENT_LABELS.get(workitem.owner_agent or "-", workitem.owner_agent or "-"),
                    description=workitem.description,
                    retry_text=f"{workitem.retry_count}/{workitem.max_retries}",
                    failure_type=workitem.failure_type,
                    failure_summary=workitem.failure_summary,
                    remediation_suggestions=remediation_suggestions(
                        workitem.failure_type,
                        retryable=workitem.retryable,
                        summary=workitem.failure_summary or workitem.blocked_reason or "",
                    )
                    if workitem.failure_type or workitem.blocked_reason
                    else [],
                )
                for workitem in state.workitems
            ],
            executions=[
                BoardExecutionView(
                    workitem_id=execution.workitem_id,
                    agent_id=execution.agent_id,
                    agent_label=AGENT_LABELS.get(execution.agent_id, execution.agent_id),
                    status=execution.status.value,
                    status_label=EXECUTION_STATUS_LABELS.get(execution.status.value, execution.status.value),
                    result=execution.result,
                    failure_type=execution.failure_type,
                    failure_summary=execution.failure_summary,
                    remediation_suggestions=remediation_suggestions(
                        execution.failure_type,
                        retryable=True,
                        summary=execution.failure_summary or execution.cli_stderr_tail or execution.cli_stdout_tail,
                    )
                    if execution.failure_type or execution.failure_summary
                    else [],
                )
                for execution in state.executions
            ],
            code_execution_artifacts=code_execution_artifacts,
            artifacts=artifacts,
            route_lines=[
                f"{decision.workitem_id} 路由到 {AGENT_LABELS.get(decision.selected_agent, decision.selected_agent)}"
                for decision in state.route_decisions
            ],
            project_agents=self._build_project_agents(state),
            activation_nodes=self._build_activation_nodes(state),
            task_center_summary=task_center.summary(state),
            task_assignments=[
                BoardTaskAssignmentView(
                    id=assignment.id,
                    workitem_id=assignment.workitem_id,
                    role=assignment.role,
                    role_label=label_role(assignment.role),
                    status=assignment.status.value,
                    status_label=TASK_ASSIGNMENT_STATUS_LABELS.get(assignment.status.value, assignment.status.value),
                    assigned_agent_id=assignment.assigned_agent_id or "-",
                    assigned_agent_label=AGENT_LABELS.get(assignment.assigned_agent_id or "-", assignment.assigned_agent_id or "-"),
                    claim_token=assignment.claim_token,
                    claimable=task_center.claimable(state, assignment),
                    unmet_dependency_ids=task_center.unmet_dependency_ids(state, assignment),
                    dependencies=assignment.dependencies,
                    input_artifact_ids=assignment.input_artifact_ids,
                    output_artifact_ids=assignment.output_artifact_ids,
                    claim_reason=assignment.claim_reason,
                    blocked_reason=assignment.blocked_reason or "",
                    claimed_age_seconds=task_center.claimed_age_seconds(assignment),
                    last_heartbeat_at=assignment.last_heartbeat_at,
                    heartbeat_age_seconds=task_center.heartbeat_age_seconds(assignment),
                    stale_claimed=task_center.stale_claimed(assignment),
                    prompt_file=assignment.prompt_file,
                )
                for assignment in state.task_assignments
            ],
            execution_runtime=self._build_execution_runtime_view(state, cli_config, llm_runtime_config),
            design_collaboration=self._build_design_collaboration_view(state, artifacts),
        )

    def build_project_summaries(self, states: list[SharedProjectState]) -> list[BoardProjectSummary]:
        """构建项目列表摘要。"""
        sorted_states = sorted(states, key=lambda item: item.project.id, reverse=True)
        return [
            BoardProjectSummary(
                project_id=state.project.id,
                goal=state.project.goal,
                project_root=state.project.project_root,
                status=state.project_status.value,
                status_label=PROJECT_STATUS_LABELS.get(state.project_status.value, state.project_status.value),
                current_stage=state.current_stage or "-",
                current_stage_label=label_stage(state.current_stage or "-"),
            )
            for state in sorted_states
        ]

    def build_role_labels(self, roles: list[str]) -> list[str]:
        """构建角色中文展示名。"""
        return [label_role(role) for role in roles]

    def _build_artifact_views(self, state: SharedProjectState) -> list[BoardArtifactView]:
        """构建 Artifact 展示模型。"""
        return [
            BoardArtifactView(
                id=artifact.id,
                title=artifact.title,
                kind=artifact.kind,
                kind_label=WORKITEM_KIND_LABELS.get(artifact.kind, artifact.kind),
                agent_id=artifact.agent_id,
                agent_label=AGENT_LABELS.get(artifact.agent_id, artifact.agent_id),
                workitem_id=artifact.workitem_id,
                content=artifact.content,
                path=artifact.path or "-",
                source_backend=artifact.source_backend,
                source_backend_label=SOURCE_BACKEND_LABELS.get(artifact.source_backend, artifact.source_backend),
                version=artifact.version,
                parent_artifact_id=artifact.parent_artifact_id,
                review_of=artifact.review_of,
            )
            for artifact in state.artifacts
        ]

    def _build_design_collaboration_view(
        self,
        state: SharedProjectState,
        artifacts: list[BoardArtifactView],
    ) -> BoardDesignCollaborationView:
        """构建需求/设计阶段会议桌视图。"""
        design_artifacts = [
            artifact
            for artifact in artifacts
            if artifact.kind in {"requirement_spec", "frozen_requirement_spec", "design_overview", "collaboration_review"}
        ]
        current_document = design_artifacts[-1] if design_artifacts else None
        collaboration = state.collaborations[-1] if state.collaborations else None
        reviews = []
        if collaboration:
            reviews = [
                BoardReviewView(
                    round_index=contribution.round_index,
                    role=contribution.role,
                    role_label=label_role(contribution.role),
                    decision=contribution.decision.value,
                    decision_label=REVIEW_DECISION_LABELS.get(contribution.decision.value, contribution.decision.value),
                    content=contribution.content,
                )
                for contribution in collaboration.contributions
            ]
        agent_roles = ["requirement_designer", "solution_designer", "designer", "backend_engineer", "frontend_engineer", "tester"]
        active_role = self._infer_active_meeting_role(state)
        agent_views = [
            BoardMeetingAgentView(
                role=role,
                label=label_role(role),
                agent_id=AGENT_LABELS.get(self._agent_id_for_role(role), self._agent_id_for_role(role)),
                status=self._infer_agent_status(role, active_role, reviews, collaboration is not None),
                status_label=self._status_label(self._infer_agent_status(role, active_role, reviews, collaboration is not None)),
            )
            for role in agent_roles
        ]
        return BoardDesignCollaborationView(
            enabled=bool(design_artifacts or collaboration or state.current_stage in {"requirement", "design"}),
            current_step_label=self._build_current_step_label(active_role, collaboration),
            status_label=(
                COLLABORATION_STATUS_LABELS.get(collaboration.status.value, collaboration.status.value)
                if collaboration
                else "等待初稿"
            ),
            current_document_title=current_document.title if current_document else "暂无需求文档",
            current_document_content=current_document.content if current_document else "",
            current_document_source=current_document.source_backend_label if current_document else "",
            agents=agent_views,
            reviews=reviews,
            history=design_artifacts[:-1],
            team_plan=dict(collaboration.team_plan) if collaboration else {},
        )

    def _build_execution_runtime_view(
        self,
        state: SharedProjectState,
        cli_config: CLISelectionConfig | None,
        llm_runtime_config: LLMRuntimeConfig | None,
    ) -> BoardExecutionRuntimeView:
        """Build current or latest execution runtime view."""
        workitem = next((item for item in state.workitems if item.status.value == "running"), None)
        is_running = workitem is not None
        execution_state_label = "运行中" if is_running else "最近一次"
        if workitem is None and state.executions:
            last_execution = state.executions[-1]
            workitem = next((item for item in state.workitems if item.id == last_execution.workitem_id), None)
        if workitem is None:
            return BoardExecutionRuntimeView()

        agent_label = AGENT_LABELS.get(workitem.owner_agent or "-", workitem.owner_agent or "-")
        backend = "-"
        cli_label = "-"
        model_label = "-"
        execution_mode_label = "文档执行"

        artifact = next((item for item in reversed(state.artifacts) if item.workitem_id == workitem.id), None)
        if artifact is not None:
            backend = SOURCE_BACKEND_LABELS.get(artifact.source_backend, artifact.source_backend)
            cli_label, model_label = self._backend_detail_labels(artifact.source_backend, cli_config, llm_runtime_config)
        else:
            backend, cli_label = self._infer_backend_from_events(state, workitem.id)
            model_label = self._infer_model_label(cli_label, backend, cli_config, llm_runtime_config)

        if workitem.kind in {"api_implementation", "data_implementation", "generic_implementation", "ui_implementation"}:
            execution_mode_label = "代码执行"
        elif workitem.kind in {"automated_test", "api_validation", "ui_validation"}:
            execution_mode_label = "测试执行"

        stage_workitems = [item for item in state.workitems if item.stage == workitem.stage]
        done_count = sum(1 for item in stage_workitems if item.status.value == "done")
        total_count = len(stage_workitems)
        stage_progress_percent = int((done_count / total_count) * 100) if total_count else 0
        ordered_ids = [item.id for item in stage_workitems]
        try:
            task_index = ordered_ids.index(workitem.id) + 1
        except ValueError:
            task_index = 1
        output_summary = self._build_runtime_output_summary(state, workitem.id, artifact)

        return BoardExecutionRuntimeView(
            available=True,
            is_running=is_running,
            headline=(
                f"{agent_label} 正在执行 {WORKITEM_KIND_LABELS.get(workitem.kind, workitem.kind)}"
                if is_running
                else f"{agent_label} 最近完成了 {WORKITEM_KIND_LABELS.get(workitem.kind, workitem.kind)}"
            ),
            workitem_id=workitem.id,
            workitem_kind_label=WORKITEM_KIND_LABELS.get(workitem.kind, workitem.kind),
            stage_label=label_stage(workitem.stage),
            agent_label=agent_label,
            backend_label=backend,
            cli_label=cli_label,
            model_label=model_label,
            working_directory=state.project.project_root or ".",
            execution_mode_label=execution_mode_label,
            state_label=execution_state_label,
            stage_progress_label=f"{label_stage(workitem.stage)}阶段进度 {done_count}/{total_count}",
            stage_progress_percent=stage_progress_percent,
            task_position_label=f"本阶段第 {task_index}/{max(total_count, 1)} 个任务",
            output_summary_title="当前输出摘要" if is_running else "最近输出摘要",
            output_summary=output_summary,
        )

    def _build_runtime_output_summary(
        self,
        state: SharedProjectState,
        workitem_id: str,
        artifact,
    ) -> str:
        """Build a compact output summary for the runtime panel."""
        if artifact is not None and artifact.content:
            return self._truncate_summary(artifact.content)
        execution = next((item for item in reversed(state.executions) if item.workitem_id == workitem_id), None)
        if execution is not None and execution.result:
            return self._truncate_summary(execution.result)
        for event in reversed(state.recent_events[-20:]):
            if f"WorkItem {workitem_id}" in event:
                return self._truncate_summary(event)
        return "当前还没有可展示的输出，等待本轮执行产出。"

    def _truncate_summary(self, text: str, limit: int = 280) -> str:
        """Normalize and truncate runtime summary text."""
        compact = " ".join(text.split())
        if not compact:
            return "当前还没有可展示的输出，等待本轮执行产出。"
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1].rstrip() + "…"

    def _backend_detail_labels(
        self,
        source_backend: str,
        cli_config: CLISelectionConfig | None,
        llm_runtime_config: LLMRuntimeConfig | None,
    ) -> tuple[str, str]:
        """Map backend source to cli/model labels."""
        if source_backend == "agent_cli/codex":
            if cli_config is None:
                return "Codex CLI", "Codex"
            return "Codex CLI", f"{cli_config.codex_model} / {cli_config.codex_reasoning_effort}"
        if source_backend == "agent_cli/claude":
            return "Claude CLI", "由 Claude CLI 本地配置决定"
        if source_backend == "agent_cli/qwen":
            return "Qwen CLI", "由 Qwen CLI 本地配置决定"
        if source_backend == "agent_cli/opencode":
            return "OpenCode CLI", "由 OpenCode CLI 本地配置决定"
        if source_backend == "cli/shell":
            return "Shell Harness", "pytest"
        if source_backend == "llm/cloud" and llm_runtime_config is not None:
            return "云端 LLM", llm_runtime_config.cloud.model_name
        if source_backend == "llm/local" and llm_runtime_config is not None:
            return "本地 LLM", llm_runtime_config.local.model_name
        if source_backend == "llm/hybrid":
            return "混合 LLM", "hybrid"
        return "-", "-"

    def _infer_backend_from_events(self, state: SharedProjectState, workitem_id: str) -> tuple[str, str]:
        """Infer runtime backend from recent events when artifact is not ready yet."""
        for event in reversed(state.recent_events[-24:]):
            if f"WorkItem {workitem_id}" not in event:
                continue
            if "Agent CLI 执行" in event:
                if "cli=codex" in event:
                    return "Codex CLI", "Codex CLI"
                if "cli=claude" in event:
                    return "Claude CLI", "Claude CLI"
                if "cli=qwen" in event:
                    return "Qwen CLI", "Qwen CLI"
                if "cli=opencode" in event:
                    return "OpenCode CLI", "OpenCode CLI"
                return "Agent CLI", "Agent CLI"
            if "Harness 执行" in event:
                return "Shell Harness", "Shell Harness"
            if "LLM 执行" in event:
                if "backend=cloud" in event:
                    return "云端 LLM", "云端 LLM"
                if "backend=local" in event:
                    return "本地 LLM", "本地 LLM"
                return "LLM", "LLM"
        return "-", "-"

    def _infer_model_label(
        self,
        cli_label: str,
        backend_label: str,
        cli_config: CLISelectionConfig | None,
        llm_runtime_config: LLMRuntimeConfig | None,
    ) -> str:
        """Infer model label when the run is still in-flight."""
        if cli_label == "Codex CLI" and cli_config is not None:
            return f"{cli_config.codex_model} / {cli_config.codex_reasoning_effort}"
        if backend_label == "云端 LLM" and llm_runtime_config is not None:
            return llm_runtime_config.cloud.model_name
        if backend_label == "本地 LLM" and llm_runtime_config is not None:
            return llm_runtime_config.local.model_name
        if cli_label == "Claude CLI":
            return "由 Claude CLI 本地配置决定"
        if cli_label == "Qwen CLI":
            return "由 Qwen CLI 本地配置决定"
        if cli_label == "OpenCode CLI":
            return "由 OpenCode CLI 本地配置决定"
        if cli_label == "Shell Harness":
            return "pytest"
        return "-"

    def _build_project_agents(self, state: SharedProjectState) -> list[BoardProjectAgentView]:
        """构建当前项目已激活 Agent 列表及原因。"""
        if state.agent_activations:
            return [
                BoardProjectAgentView(
                    role=activation.role,
                    role_label=label_role(activation.role),
                    agent_id=activation.agent_id,
                    mission=self._mission_for_role(activation.role),
                    reason=(
                        f"阶段 {label_stage(activation.stage)} 创建：{activation.reason}；"
                        f"执行后端 {activation.execution_backend}"
                    ),
                    related_kinds=[
                        WORKITEM_KIND_LABELS.get(kind, kind)
                        for kind in activation.related_workitem_kinds
                    ],
                )
                for activation in state.agent_activations
            ]
        agent_views: list[BoardProjectAgentView] = []
        for role in state.planned_roles:
            related_kinds = []
            for workitem in state.workitems:
                owner_role = self._role_for_workitem_kind(workitem.kind)
                if owner_role == role:
                    if workitem.kind not in related_kinds:
                        related_kinds.append(workitem.kind)
            role_label = label_role(role)
            agent_id = self._agent_id_for_role(role)
            mission = self._mission_for_role(role)
            kind_labels = [WORKITEM_KIND_LABELS.get(kind, kind) for kind in related_kinds]
            reason = (
                f"因为当前项目存在 {', '.join(kind_labels)} 相关工作项，系统激活 {role_label}。"
                if kind_labels
                else f"因为当前阶段需要 {role_label} 参与，系统激活该 Agent。"
            )
            agent_views.append(
                BoardProjectAgentView(
                    role=role,
                    role_label=role_label,
                    agent_id=agent_id,
                    mission=mission,
                    reason=reason,
                    related_kinds=kind_labels,
                )
            )
        return agent_views

    def _build_activation_nodes(self, state: SharedProjectState) -> list[BoardActivationNodeView]:
        """构建按需激活 Agent 的环形节点视图。"""
        active_by_role = {agent.role: agent for agent in self._build_project_agents(state)}
        ordered_roles = ["requirement_designer", "solution_designer", "designer", "backend_engineer", "frontend_engineer", "tester"]
        nodes: list[BoardActivationNodeView] = []
        for role in ordered_roles:
            active_agent = active_by_role.get(role)
            nodes.append(
                BoardActivationNodeView(
                    role=role,
                    role_label=label_role(role),
                    short_label=ROLE_SHORT_LABELS.get(role, role[:2].upper()),
                    position_class=ROLE_POSITION_CLASSES.get(role, "node-top-left"),
                    active=active_agent is not None,
                    status_label="已激活" if active_agent is not None else "待命",
                    reason=active_agent.reason if active_agent is not None else "当前需求未触发该角色。",
                    mission=active_agent.mission if active_agent is not None else self._mission_for_role(role),
                    related_kinds=active_agent.related_kinds if active_agent is not None else [],
                )
            )
        return nodes

    def _mission_for_role(self, role: str) -> str:
        """返回角色使命摘要。"""
        return {
            "requirement_designer": "澄清用户目标、范围边界、非目标、验收标准和风险假设，并推动需求冻结。",
            "solution_designer": "从方案一致性、流程完整性和可验收性角度审查需求与设计。",
            "designer": "把自然语言需求转成结构化设计文档，并在协作评审后统一修订。",
            "backend_engineer": "从后端实现角度审阅需求和设计，并产出接口与数据结构说明。",
            "frontend_engineer": "从前端交互与页面实现角度审阅设计，并产出页面结构说明。",
            "tester": "从可测试性和验收视角审阅文档，并产出测试计划与验收说明。",
        }.get(role, "参与当前项目流程。")

    def _role_for_workitem_kind(self, kind: str) -> str | None:
        """根据 WorkItem kind 推断负责角色。"""
        if kind == "requirement_spec":
            return "requirement_designer"
        if kind in {"design_overview", "ui_design", "api_design", "test_design"}:
            return "designer"
        if kind in {"api_implementation", "data_implementation", "generic_implementation"}:
            return "backend_engineer"
        if kind == "ui_implementation":
            return "frontend_engineer"
        if kind in {"acceptance_check", "automated_test", "api_validation", "ui_validation"}:
            return "tester"
        return None

    def _agent_id_for_role(self, role: str) -> str:
        """按角色返回默认 Agent ID。"""
        return {
            "requirement_designer": "agent-requirement-designer",
            "solution_designer": "agent-solution-designer",
            "designer": "agent-designer",
            "backend_engineer": "agent-backend",
            "frontend_engineer": "agent-frontend",
            "tester": "agent-tester",
        }.get(role, role)

    def _infer_active_meeting_role(self, state: SharedProjectState) -> str | None:
        """从最近事件中推断当前会议桌高亮角色。"""
        for event in reversed(state.recent_events[-24:]):
            if "requirement_designer" in event or "agent-requirement-designer" in event:
                return "requirement_designer"
            if "solution_designer" in event or "agent-solution-designer" in event:
                return "solution_designer"
            if "backend_engineer" in event or "agent-backend" in event:
                return "backend_engineer"
            if "frontend_engineer" in event or "agent-frontend" in event:
                return "frontend_engineer"
            if "tester" in event or "agent-tester" in event:
                return "tester"
            if "designer" in event or "agent-designer" in event or "lead=designer" in event:
                return "designer"
        if state.current_stage == "requirement":
            return "requirement_designer"
        if state.current_stage == "design":
            return "designer"
        return None

    def _infer_agent_status(
        self,
        role: str,
        active_role: str | None,
        reviews: list[BoardReviewView],
        has_collaboration: bool,
    ) -> str:
        """推断会议桌 Agent 状态。"""
        if role == active_role:
            return "active"
        if role == "designer" and has_collaboration:
            return "active" if active_role == "designer" else "done"
        if any(review.role == role for review in reviews):
            return "done"
        return "waiting"

    def _status_label(self, status: str) -> str:
        """转换会议桌状态文案。"""
        return {
            "active": "进行中",
            "done": "已参与",
            "waiting": "等待",
        }.get(status, status)

    def _build_current_step_label(self, active_role: str | None, collaboration) -> str:
        """构建会议桌中央文案。"""
        if collaboration and collaboration.status.value != "running":
            return f"需求协作评审：{COLLABORATION_STATUS_LABELS.get(collaboration.status.value, collaboration.status.value)}"
        if active_role:
            return f"{label_role(active_role)} 正在参与需求设计"
        return "等待需求设计启动"
