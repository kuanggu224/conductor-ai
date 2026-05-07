"""Sequential review collaboration runner."""

from __future__ import annotations

from dataclasses import dataclass

from conductor.agents.agent import Agent
from conductor.agents.llm import LLMHTTPConfig
from conductor.agents.cli_executor import AgentCLIExecutor
from conductor.agents.registry import AgentRegistry
from conductor.artifacts.store import ArtifactStore
from conductor.collaboration.models import (
    Collaboration,
    CollaborationDraftVersion,
    CollaborationStatus,
    ReviewContribution,
    ReviewDecision,
)
from conductor.collaboration.policy import CollaborationPolicy
from conductor.config.cli import CLISelectionConfig
from conductor.domain.models import Artifact, WorkItem
from conductor.harness.llm import LLMHarnessRequest, OpenAICompatibleLLMHarness
from conductor.execution.runtime_stream import RuntimeStreamStore
from conductor.state.store import InMemoryStateStore


@dataclass(slots=True)
class CollaborationRunResult:
    """Structured runtime metadata for one collaboration LLM/CLI call."""

    content: str
    source_backend: str = ""
    model: str = ""
    output_path: str = ""
    duration_ms: int = 0


class CollaborationRunner:
    """Run reviewer-isolated sequential collaboration loops."""

    def __init__(
        self,
        state_store: InMemoryStateStore,
        registry: AgentRegistry,
        artifact_store: ArtifactStore,
        policy: CollaborationPolicy | None = None,
        cli_selection_config: CLISelectionConfig | None = None,
        runtime_stream_store: RuntimeStreamStore | None = None,
        require_real_outputs: bool = False,
        use_llm: bool = True,
        llm_harness: OpenAICompatibleLLMHarness | None = None,
        llm_harness_config: LLMHTTPConfig | None = None,
    ) -> None:
        self.state_store = state_store
        self.registry = registry
        self.artifact_store = artifact_store
        self.policy = policy or CollaborationPolicy()
        self.cli_selection_config = cli_selection_config or CLISelectionConfig()
        self.runtime_stream_store = runtime_stream_store or RuntimeStreamStore()
        self.require_real_outputs = require_real_outputs
        self.use_llm = use_llm
        self.llm_harness = llm_harness
        self.llm_harness_config = llm_harness_config
        self.agent_cli_executor = AgentCLIExecutor(cli_selection_config=self.cli_selection_config)

    def should_collaborate(self, project_id: str, workitem: WorkItem) -> bool:
        """Return whether the workitem should enter the collaboration loop."""
        if not self.policy.enabled or workitem.kind not in self.policy.enabled_kinds:
            return False
        state = self.state_store.get_state(project_id)
        return not any(item.workitem_id == workitem.id for item in state.collaborations)

    def run_review_loop(self, project_id: str, workitem: WorkItem, draft_artifact: Artifact) -> Collaboration:
        """Run one collaboration session around an existing draft artifact."""
        project_root = self.state_store.get_state(project_id).project.project_root
        lead = self.registry.get_agent_by_role(self.policy.lead_role_by_stage[workitem.stage])
        peer_reviewers = [
            self.registry.get_agent_by_role(role)
            for role in self.policy.peer_reviewer_roles_by_stage.get(workitem.stage, [])
        ]
        functional_reviewers = [
            self.registry.get_agent_by_role(role)
            for role in self.policy.reviewer_roles_by_stage.get(workitem.stage, [])
        ]
        reviewers = [*peer_reviewers, *functional_reviewers]
        collaboration_id = f"collaboration-{workitem.id}"
        draft = self.artifact_store.read_content(draft_artifact)
        contributions: list[ReviewContribution] = []
        draft_versions = [
            CollaborationDraftVersion(
                version=1,
                round_index=0,
                author_agent_id=lead.id,
                content=draft,
                source_backend=draft_artifact.source_backend,
                model=self._model_from_source_backend(draft_artifact.source_backend),
                output_path=draft_artifact.path or "",
            )
        ]
        status = CollaborationStatus.RUNNING
        collaboration = Collaboration(
            id=collaboration_id,
            project_id=project_id,
            workitem_id=workitem.id,
            lead_agent_id=lead.id,
            reviewer_agent_ids=[reviewer.id for reviewer in reviewers],
            status=status,
            max_rounds=self.policy.max_rounds,
            current_round=1,
            contributions=[],
            draft_versions=draft_versions,
        )

        self.runtime_stream_store.start(
            project_id=project_id,
            workitem_id=workitem.id,
            agent_role=lead.role,
            backend="collaboration",
            cli_name=self.cli_selection_config.role_cli_bindings.get(lead.role) or "-",
        )
        self.state_store.add_event(project_id, f"协作 {collaboration_id} 已开始，lead={lead.role}")
        self.state_store.upsert_collaboration(project_id, collaboration)

        for round_index in range(1, self.policy.max_rounds + 1):
            self.state_store.add_event(project_id, f"协作 {collaboration_id} 进入第 {round_index} 轮审阅")
            round_reviews: list[ReviewContribution] = []

            peer_reviews = self._run_review_phase(
                project_id=project_id,
                collaboration_id=collaboration_id,
                round_index=round_index,
                phase="design_peer_review",
                reviewers=peer_reviewers,
                workitem=workitem,
                draft=draft,
                project_root=project_root,
            )
            if peer_reviews:
                round_reviews.extend(peer_reviews)
                contributions.extend(peer_reviews)
                collaboration = self._update_collaboration(
                    collaboration=collaboration,
                    status=CollaborationStatus.RUNNING,
                    current_round=round_index,
                    contributions=contributions,
                    draft_versions=draft_versions,
                )
                self.state_store.upsert_collaboration(project_id, collaboration)
                if any(review.decision == ReviewDecision.REQUEST_CHANGES for review in peer_reviews):
                    revision = self._revise(
                        project_id,
                        lead,
                        workitem,
                        draft,
                        peer_reviews,
                        round_index,
                        project_root,
                        phase="design_peer_review",
                    )
                    draft = revision.content
                    draft_versions.append(
                        CollaborationDraftVersion(
                            version=len(draft_versions) + 1,
                            round_index=round_index,
                            author_agent_id=lead.id,
                            content=draft,
                            review_ids=[review.id for review in peer_reviews],
                            source_backend=revision.source_backend,
                            model=revision.model,
                            output_path=revision.output_path,
                            duration_ms=revision.duration_ms,
                        )
                    )

            functional_reviews = self._run_review_phase(
                project_id=project_id,
                collaboration_id=collaboration_id,
                round_index=round_index,
                phase="cross_functional_review",
                reviewers=functional_reviewers,
                workitem=workitem,
                draft=draft,
                project_root=project_root,
            )
            round_reviews.extend(functional_reviews)
            contributions.extend(functional_reviews)
            collaboration = self._update_collaboration(
                collaboration=collaboration,
                status=CollaborationStatus.RUNNING,
                current_round=round_index,
                contributions=contributions,
                draft_versions=draft_versions,
            )
            self.state_store.upsert_collaboration(project_id, collaboration)

            if all(review.decision == ReviewDecision.APPROVE for review in round_reviews):
                status = CollaborationStatus.ACCEPTED
                break

            rework_reviews = functional_reviews or peer_reviews
            if rework_reviews:
                revision = self._revise(
                    project_id,
                    lead,
                    workitem,
                    draft,
                    rework_reviews,
                    round_index,
                    project_root,
                    phase="cross_functional_review",
                )
                draft = revision.content
                draft_versions.append(
                    CollaborationDraftVersion(
                        version=len(draft_versions) + 1,
                        round_index=round_index,
                        author_agent_id=lead.id,
                        content=draft,
                        review_ids=[review.id for review in rework_reviews],
                        source_backend=revision.source_backend,
                        model=revision.model,
                        output_path=revision.output_path,
                        duration_ms=revision.duration_ms,
                    )
                )
                collaboration = self._update_collaboration(
                    collaboration=collaboration,
                    status=CollaborationStatus.RUNNING,
                    current_round=round_index,
                    contributions=contributions,
                    draft_versions=draft_versions,
                )
                self.state_store.upsert_collaboration(project_id, collaboration)

        if status == CollaborationStatus.RUNNING:
            status = CollaborationStatus.MAX_ROUNDS_REACHED

        final_artifact = self._create_final_artifact(
            project_id=project_id,
            workitem=workitem,
            lead=lead,
            draft=draft,
            contributions=contributions,
            status=status,
            project_root=project_root,
            draft_versions=draft_versions,
        )
        collaboration = self._update_collaboration(
            collaboration=collaboration,
            status=status,
            current_round=min(self.policy.max_rounds, max((item.round_index for item in contributions), default=1)),
            contributions=contributions,
            draft_versions=draft_versions,
            final_artifact_id=final_artifact.id,
        )
        self.state_store.upsert_collaboration(project_id, collaboration)
        self.state_store.add_event(project_id, f"协作 {collaboration_id} 已结束，状态={status.value}")
        self.runtime_stream_store.finish(
            project_id,
            success=status == CollaborationStatus.ACCEPTED,
            message=f"[collaboration] {status.value}",
        )
        return collaboration

    def _run_review_phase(
        self,
        project_id: str,
        collaboration_id: str,
        round_index: int,
        phase: str,
        reviewers: list[Agent],
        workitem: WorkItem,
        draft: str,
        project_root: str,
    ) -> list[ReviewContribution]:
        """Run one named review phase against the current draft."""
        if not reviewers:
            return []
        self.state_store.add_event(project_id, f"协作 {collaboration_id} 第 {round_index} 轮进入 {phase}")
        return [
            self._review(project_id, collaboration_id, round_index, phase, reviewer, workitem, draft, project_root)
            for reviewer in reviewers
        ]

    def _update_collaboration(
        self,
        collaboration: Collaboration,
        status: CollaborationStatus,
        current_round: int,
        contributions: list[ReviewContribution],
        draft_versions: list[CollaborationDraftVersion] | None = None,
        final_artifact_id: str | None = None,
    ) -> Collaboration:
        """Return a refreshed collaboration snapshot."""
        return Collaboration(
            id=collaboration.id,
            project_id=collaboration.project_id,
            workitem_id=collaboration.workitem_id,
            lead_agent_id=collaboration.lead_agent_id,
            reviewer_agent_ids=collaboration.reviewer_agent_ids,
            status=status,
            max_rounds=collaboration.max_rounds,
            current_round=current_round,
            contributions=[*contributions],
            draft_versions=[*(draft_versions if draft_versions is not None else collaboration.draft_versions)],
            final_artifact_id=final_artifact_id if final_artifact_id is not None else collaboration.final_artifact_id,
        )

    def _review(
        self,
        project_id: str,
        collaboration_id: str,
        round_index: int,
        phase: str,
        reviewer: Agent,
        workitem: WorkItem,
        draft: str,
        project_root: str,
    ) -> ReviewContribution:
        """Run one reviewer against the same draft."""
        result = self._run_agent_or_mock_review(project_id, reviewer, workitem, draft, round_index, project_root, phase)
        decision = self._parse_review_decision(result.content)
        self.state_store.add_event(
            project_id,
            f"协作 {collaboration_id} 第 {round_index} 轮 {phase}: {reviewer.role} -> {decision.value}",
        )
        return ReviewContribution(
            id=f"{collaboration_id}-{phase}-round-{round_index}-{reviewer.id}",
            round_index=round_index,
            agent_id=reviewer.id,
            role=reviewer.role,
            decision=decision,
            content=result.content,
            phase=phase,
            source_backend=result.source_backend,
            model=result.model,
            output_path=result.output_path,
            duration_ms=result.duration_ms,
        )

    def _revise(
        self,
        project_id: str,
        lead: Agent,
        workitem: WorkItem,
        draft: str,
        reviews: list[ReviewContribution],
        round_index: int,
        project_root: str,
        phase: str,
    ) -> CollaborationRunResult:
        """Let the lead revise the draft after all reviews in the round are collected."""
        review_text = "\n\n".join(
            f"### {review.role} ({review.decision.value})\n{self._clip_text(review.content, 1000)}"
            for review in reviews
        )
        draft_excerpt = self._clip_text(draft, 3200)
        prompt = (
            "你是需求设计阶段的 lead designer agent。请在完整阅读所有 reviewer 意见后统一修订草案。\n"
            f"当前评审阶段: {phase}\n"
            f"WorkItem: {workitem.id} / {workitem.description}\n\n"
            f"# 当前草案摘要\n{draft_excerpt}\n\n"
            f"# 本轮审阅意见\n{review_text}\n\n"
            "请直接输出修订后的完整中文 Markdown 需求设计文档，不要只输出差异。"
            "必须保留并完善：目标、需求理解、范围边界、关键假设、方案、交付物、验收标准、风险。"
        )
        if self.agent_cli_executor.resolve_binding(lead) == "claude":
            prompt = (
                "You are the lead requirement/design agent in Conductor.\n"
                f"Work item kind: {workitem.kind}\n"
                f"Review phase: {phase}\n"
                "Revise the current design draft after reading all reviewer feedback.\n"
                "Return a full Chinese markdown requirement design document, not a diff.\n"
                "Keep the sections clear and practical: 目标, 需求理解, 范围边界, 关键假设, 方案, 交付物, 验收标准, 风险.\n\n"
                f"Current draft:\n{draft_excerpt[:2400]}\n\n"
                f"Reviewer feedback:\n{review_text[:2400]}"
            )
        cli_execution = self.agent_cli_executor.execute(
            lead,
            prompt,
            working_directory=project_root,
            stream_callback=self._build_stream_callback(project_id),
        )
        if cli_execution is not None:
            cli_stdout = (cli_execution.result.stdout or "").strip()
            if cli_execution.result.success and cli_stdout:
                self.state_store.add_event(project_id, f"协作修订使用 Agent CLI: {lead.role} -> {cli_execution.cli_name}")
                return CollaborationRunResult(
                    content=cli_stdout,
                    source_backend=f"agent_cli/{cli_execution.cli_name}",
                    model=cli_execution.cli_name,
                )
            self.state_store.add_event(
                project_id,
                f"协作修订 Agent CLI 失败，尝试 LLM: exit_code={cli_execution.result.exit_code}",
            )
        if self._can_use_llm_harness():
            return self._run_llm_harness_revision(project_id, lead, workitem, prompt, project_root, phase)
        if not self.use_llm or lead.llm_backend is None:
            if self.require_real_outputs:
                raise RuntimeError(self._real_backend_required_message(lead, "协作修订未配置 LLM backend"))
            return CollaborationRunResult(
                content=self._build_mock_revision(workitem, draft, reviews, round_index),
                source_backend="mock",
            )
        try:
            revision = lead.think(
                prompt=prompt,
                preferred_backend=lead.preferred_llm_backend,
            )
            if self._is_disabled_llm_response(revision):
                if self.require_real_outputs:
                    raise RuntimeError(self._real_backend_required_message(lead, "协作修订 LLM backend 未启用"))
                return CollaborationRunResult(
                    content=self._build_mock_revision(workitem, draft, reviews, round_index),
                    source_backend="mock_fallback",
                )
            return CollaborationRunResult(
                content=revision,
                source_backend=f"llm/{lead.preferred_llm_backend}",
                model=getattr(lead.llm_backend, "model_name", ""),
            )
        except Exception as error:
            if self.require_real_outputs:
                raise RuntimeError(self._real_backend_required_message(lead, f"协作修订 LLM 失败: {error}")) from error
            self.state_store.add_event(project_id, f"协作修订 LLM 失败，使用 mock 修订: {error}")
            return CollaborationRunResult(
                content=self._build_mock_revision(workitem, draft, reviews, round_index),
                source_backend="mock_fallback",
            )

    def _run_agent_or_mock_review(
        self,
        project_id: str,
        reviewer: Agent,
        workitem: WorkItem,
        draft: str,
        round_index: int,
        project_root: str,
        phase: str,
    ) -> CollaborationRunResult:
        """Run a review through Agent CLI, LLM, or structured mock fallback."""
        review_focus = {
            "requirement_designer": "重点检查需求是否符合用户目标、范围是否合理、业务规则是否完整、是否存在需求歧义。",
            "solution_designer": "重点检查方案是否自洽、流程是否完整、信息结构是否清晰、验收标准是否可执行。",
            "backend_engineer": "重点检查接口边界、数据结构、状态流转、错误处理、后端实现风险。",
            "frontend_engineer": "重点检查页面结构、交互路径、状态展示、空/错/加载状态、前端实现风险。",
            "tester": "重点检查验收标准、测试覆盖、边界条件、异常路径、可验证性。",
        }.get(reviewer.role, "重点检查当前角色负责的交付风险和缺失信息。")
        phase_instruction = (
            "这是设计同侪评审阶段。请优先判断需求本身是否合理、完整、符合用户目标；不要只从代码实现难度出发。"
            if phase == "design_peer_review"
            else "这是跨职能评审阶段。请基于已修订的需求设计，从本角色交付风险和验收可执行性角度审阅。"
        )
        prompt = (
            f"你是 {reviewer.role} reviewer，正在参与需求设计评审。请审阅同一轮固定 draft，不要修改原文。\n"
            f"评审阶段：{phase}\n"
            f"{phase_instruction}\n"
            f"评审重点：{review_focus}\n"
            f"WorkItem: {workitem.id} / {workitem.description}\n\n"
            f"# Draft 摘要\n{self._clip_text(draft, 4200)}\n\n"
            "请用中文输出 Markdown，必须包含 `Decision: approve` 或 `Decision: request_changes`，"
            "并列出主要问题、建议、风险和你认为后续 Agent 必须遵守的约束。"
        )
        cli_execution = self.agent_cli_executor.execute(
            reviewer,
            prompt,
            working_directory=project_root,
            stream_callback=self._build_stream_callback(project_id),
        )
        if cli_execution is not None:
            cli_stdout = (cli_execution.result.stdout or "").strip()
            if cli_execution.result.success and cli_stdout:
                self.state_store.add_event(project_id, f"协作审阅使用 Agent CLI: {reviewer.role} -> {cli_execution.cli_name}")
                return CollaborationRunResult(
                    content=cli_stdout,
                    source_backend=f"agent_cli/{cli_execution.cli_name}",
                    model=cli_execution.cli_name,
                )
            self.state_store.add_event(
                project_id,
                f"协作审阅 Agent CLI 失败，尝试 LLM: {reviewer.role} -> exit_code={cli_execution.result.exit_code}",
            )
        if self._can_use_llm_harness():
            return self._run_llm_harness_review(project_id, reviewer, workitem, prompt, round_index, project_root, phase)
        if not self.use_llm or reviewer.llm_backend is None:
            if self.require_real_outputs:
                raise RuntimeError(self._real_backend_required_message(reviewer, "协作审阅未配置 LLM backend"))
            return CollaborationRunResult(
                content=self._build_mock_review(reviewer, workitem, round_index),
                source_backend="mock",
            )
        try:
            review = reviewer.think(
                prompt=prompt,
                preferred_backend=reviewer.preferred_llm_backend,
            )
            if self._is_disabled_llm_response(review):
                if self.require_real_outputs:
                    raise RuntimeError(self._real_backend_required_message(reviewer, "协作审阅 LLM backend 未启用"))
                return CollaborationRunResult(
                    content=self._build_mock_review(reviewer, workitem, round_index),
                    source_backend="mock_fallback",
                )
            return CollaborationRunResult(
                content=review,
                source_backend=f"llm/{reviewer.preferred_llm_backend}",
                model=getattr(reviewer.llm_backend, "model_name", ""),
            )
        except Exception as error:
            if self.require_real_outputs:
                raise RuntimeError(self._real_backend_required_message(reviewer, f"协作审阅 LLM 失败: {error}")) from error
            self.state_store.add_event(project_id, f"协作审阅 LLM 失败，使用 mock review: {reviewer.role} -> {error}")
            return CollaborationRunResult(
                content=self._build_mock_review(reviewer, workitem, round_index),
                source_backend="mock_fallback",
            )

    def _can_use_llm_harness(self) -> bool:
        """Return whether collaboration can use the controlled LLM harness."""
        return (
            self.llm_harness is not None
            and self.llm_harness_config is not None
            and self.llm_harness_config.enabled
        )

    def _model_from_source_backend(self, source_backend: str) -> str:
        """Extract the model identifier from a source backend string."""
        if source_backend.startswith(("llm_harness/", "llm_harness_code/")):
            return source_backend.split("/", 1)[1]
        return ""

    def _run_llm_harness_review(
        self,
        project_id: str,
        reviewer: Agent,
        workitem: WorkItem,
        prompt: str,
        round_index: int,
        project_root: str,
        phase: str,
    ) -> CollaborationRunResult:
        """Run one collaboration review through the controlled LLM harness."""
        assert self.llm_harness is not None
        assert self.llm_harness_config is not None
        result = self.llm_harness.run(
            LLMHarnessRequest(
                prompt=(
                    f"{prompt}\n\n"
                    "Output contract:\n"
                    "- Must include exactly one line starting with `Decision: approve` or `Decision: request_changes`.\n"
                    "- Include sections: 主要问题, 建议, 风险, 可执行验收关注点.\n"
                    "- Be specific to the current requirement. Do not use generic filler.\n"
                ),
                system_prompt=(
                    "You are a strict non-interactive reviewer agent in a multi-agent design review. "
                    "Return concise Chinese Markdown only."
                ),
                working_directory=project_root,
                output_path=f".conductor/llm_outputs/{workitem.id}.{phase}.{reviewer.role}.round-{round_index}.review.md",
                config=self.llm_harness_config,
                max_tokens=2048,
                temperature=0.1,
                stream_callback=self._build_stream_callback(project_id),
                metadata={
                    "project_id": project_id,
                    "workitem_id": workitem.id,
                    "agent_role": reviewer.role,
                    "mode": "collaboration_review",
                    "phase": phase,
                    "round": str(round_index),
                },
            )
        )
        if result.success and result.content.strip():
            self.state_store.add_event(project_id, f"协作审阅使用 LLMHarness: {reviewer.role} -> {result.model_name}")
            return CollaborationRunResult(
                content=result.content,
                source_backend=f"llm_harness/{result.model_name}",
                model=result.model_name,
                output_path=result.output_path or "",
                duration_ms=result.duration_ms,
            )
        if self.require_real_outputs:
            raise RuntimeError(self._real_backend_required_message(reviewer, f"协作审阅 LLMHarness 失败: {result.error}"))
        return CollaborationRunResult(
            content=self._build_mock_review(reviewer, workitem, round_index),
            source_backend="mock_fallback",
        )

    def _run_llm_harness_revision(
        self,
        project_id: str,
        lead: Agent,
        workitem: WorkItem,
        prompt: str,
        project_root: str,
        phase: str,
    ) -> CollaborationRunResult:
        """Run collaboration draft revision through the controlled LLM harness."""
        assert self.llm_harness is not None
        assert self.llm_harness_config is not None
        result = self.llm_harness.run(
            LLMHarnessRequest(
                prompt=(
                    f"{prompt}\n\n"
                    "Output contract:\n"
                    "- Return the full revised Chinese requirement/design Markdown, not a diff.\n"
                    "- Must include executable acceptance cases with concrete input and expected output.\n"
                    "- Incorporate all reviewer roles before approving the draft.\n"
                ),
                system_prompt=(
                    "You are the lead designer agent revising a requirement document after multi-agent review. "
                    "Return Chinese Markdown only."
                ),
                working_directory=project_root,
                output_path=f".conductor/llm_outputs/{workitem.id}.{phase}.designer.revision.md",
                config=self.llm_harness_config,
                max_tokens=4096,
                temperature=0.1,
                stream_callback=self._build_stream_callback(project_id),
                metadata={
                    "project_id": project_id,
                    "workitem_id": workitem.id,
                    "agent_role": lead.role,
                    "mode": "collaboration_revision",
                    "phase": phase,
                },
            )
        )
        if result.success and result.content.strip():
            self.state_store.add_event(project_id, f"协作修订使用 LLMHarness: {lead.role} -> {result.model_name}")
            return CollaborationRunResult(
                content=result.content,
                source_backend=f"llm_harness/{result.model_name}",
                model=result.model_name,
                output_path=result.output_path or "",
                duration_ms=result.duration_ms,
            )
        if self.require_real_outputs:
            raise RuntimeError(self._real_backend_required_message(lead, f"协作修订 LLMHarness 失败: {result.error}"))
        return CollaborationRunResult(
            content=self._build_mock_revision(workitem, "", [], 0),
            source_backend="mock_fallback",
        )

    def _is_disabled_llm_response(self, content: str) -> bool:
        """Return whether the response is a disabled-backend placeholder."""
        return content.startswith("[local-disabled]") or content.startswith("[cloud-disabled]")

    def _clip_text(self, text: str, limit: int) -> str:
        """Clip prompt context while keeping the boundary explicit."""
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "\n\n[内容已截断，保留关键摘要用于本轮协作]"

    def _real_backend_required_message(self, agent: Agent, reason: str) -> str:
        """Build a clear error for real-only collaboration paths."""
        return (
            f"{agent.role} 参与需求评审时要求真实 Agent 产出，但当前不可用。"
            f"原因: {reason}。请为该角色绑定可用 Agent CLI，或启用 LLM。"
        )

    def _parse_review_decision(self, content: str) -> ReviewDecision:
        """Parse the review decision from reviewer output."""
        lowered = content.lower()
        if "request_changes" in lowered or "请求修改" in content or "需要修改" in content:
            return ReviewDecision.REQUEST_CHANGES
        return ReviewDecision.APPROVE

    def _build_mock_review(self, reviewer: Agent, workitem: WorkItem, round_index: int) -> str:
        """Build a structured mock review note."""
        decision = "request_changes" if round_index == 1 else "approve"
        focus = {
            "backend_engineer": "接口边界、数据结构、错误码和状态流转需要更明确。",
            "frontend_engineer": "页面状态、空状态、错误状态和关键交互需要更明确。",
            "tester": "验收标准、异常路径和边界条件需要更可测试。",
        }.get(reviewer.role, "需要补充当前角色关注点。")
        return (
            f"## {reviewer.role} 审阅意见\n\n"
            f"Decision: {decision}\n\n"
            f"### 主要问题\n- {focus}\n\n"
            f"### 建议\n- 为 WorkItem `{workitem.id}` 补充可验证的输入、输出和边界条件。\n\n"
            "### 风险\n- 如果不补齐这些信息，后续 Agent 可能会对范围理解不一致。\n"
        )

    def _build_mock_revision(
        self,
        workitem: WorkItem,
        draft: str,
        reviews: list[ReviewContribution],
        round_index: int,
    ) -> str:
        """Build a structured mock revision."""
        review_summary = "\n".join(f"- {review.role}: {review.decision.value}" for review in reviews)
        return (
            f"# 修订版协作草案 - {workitem.id}\n\n"
            f"## 修订轮次\n第 {round_index} 轮\n\n"
            f"## 原始草案摘要\n{draft[:1200]}\n\n"
            f"## 本轮审阅结论\n{review_summary}\n\n"
            "## 统一修订\n"
            "- 补充接口、页面、测试三类关注点，确保后续研发和测试有一致输入。\n"
            "- 明确主路径、异常路径、非目标范围和验收标准。\n"
            "- 将 reviewer 意见作为后续实现文档和测试文档的约束。\n"
        )

    def _create_final_artifact(
        self,
        project_id: str,
        workitem: WorkItem,
        lead: Agent,
        draft: str,
        contributions: list[ReviewContribution],
        status: CollaborationStatus,
        project_root: str,
        draft_versions: list[CollaborationDraftVersion],
    ) -> Artifact:
        """Persist the final collaboration artifact."""
        contribution_text = "\n\n".join(
            f"### 第 {item.round_index} 轮 | {item.phase} | {item.role} | {item.decision.value}\n{item.content}"
            for item in contributions
        )
        draft_version_text = "\n\n".join(
            f"### Version {item.version} / Round {item.round_index}\n{item.content}"
            for item in draft_versions
        )
        artifact = Artifact(
            id=f"artifact-collaboration-{workitem.id}",
            project_id=project_id,
            workitem_id=workitem.id,
            agent_id=lead.id,
            kind="collaboration_review",
            title=f"多 Agent 协作评审 - {workitem.id}",
            content=(
                f"# 多 Agent 协作评审 - {workitem.id}\n\n"
                f"## 最终状态\n{status.value}\n\n"
                f"## 最终草案\n{draft}\n\n"
                f"## Draft Versions\n{draft_version_text}\n\n"
                f"## Review Rounds\n{contribution_text}\n"
            ),
            source_backend="collaboration",
            parent_artifact_id=self._find_previous_collaboration_artifact(project_id, workitem.id),
            derived_from=self._build_derived_from(project_id, workitem.id),
            review_of=f"artifact-{workitem.id}",
            version=self._next_collaboration_version(project_id, workitem.id),
            collaboration_session_id=f"collaboration-{workitem.id}",
        )
        persisted = self.artifact_store.save_markdown(artifact, project_root=project_root)
        self.state_store.add_artifact(project_id, persisted)
        return persisted

    def _find_previous_collaboration_artifact(self, project_id: str, workitem_id: str) -> str | None:
        """Return the previous collaboration artifact id for the same workitem."""
        state = self.state_store.get_state(project_id)
        for artifact in reversed(state.artifacts):
            if artifact.workitem_id == workitem_id and artifact.kind == "collaboration_review":
                return artifact.id
        return None

    def _next_collaboration_version(self, project_id: str, workitem_id: str) -> int:
        """Return the next collaboration artifact version."""
        state = self.state_store.get_state(project_id)
        versions = [
            artifact.version
            for artifact in state.artifacts
            if artifact.workitem_id == workitem_id and artifact.kind == "collaboration_review"
        ]
        return (max(versions) + 1) if versions else 1

    def _build_derived_from(self, project_id: str, workitem_id: str) -> list[str]:
        """Return the artifact lineage for the collaboration result."""
        state = self.state_store.get_state(project_id)
        return [artifact.id for artifact in state.artifacts if artifact.workitem_id == workitem_id]

    def _build_stream_callback(self, project_id: str):
        """Build a collaboration runtime stream callback."""
        def callback(channel: str, line: str) -> None:
            self.runtime_stream_store.append(project_id, channel, line)

        return callback
