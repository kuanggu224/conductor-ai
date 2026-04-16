"""Sequential review collaboration runner."""

from __future__ import annotations

from conductor.agents.agent import Agent
from conductor.agents.cli_executor import AgentCLIExecutor
from conductor.agents.registry import AgentRegistry
from conductor.artifacts.store import ArtifactStore
from conductor.collaboration.models import Collaboration, CollaborationStatus, ReviewContribution, ReviewDecision
from conductor.collaboration.policy import CollaborationPolicy
from conductor.config.cli import CLISelectionConfig
from conductor.domain.models import Artifact, WorkItem
from conductor.execution.runtime_stream import RuntimeStreamStore
from conductor.state.store import InMemoryStateStore


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
    ) -> None:
        self.state_store = state_store
        self.registry = registry
        self.artifact_store = artifact_store
        self.policy = policy or CollaborationPolicy()
        self.cli_selection_config = cli_selection_config or CLISelectionConfig()
        self.runtime_stream_store = runtime_stream_store or RuntimeStreamStore()
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
        reviewers = [
            self.registry.get_agent_by_role(role)
            for role in self.policy.reviewer_roles_by_stage.get(workitem.stage, [])
        ]
        collaboration_id = f"collaboration-{workitem.id}"
        draft = self.artifact_store.read_content(draft_artifact)
        contributions: list[ReviewContribution] = []
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
            round_reviews = [
                self._review(project_id, collaboration_id, round_index, reviewer, workitem, draft, project_root)
                for reviewer in reviewers
            ]
            contributions.extend(round_reviews)
            collaboration = self._update_collaboration(
                collaboration=collaboration,
                status=CollaborationStatus.RUNNING,
                current_round=round_index,
                contributions=contributions,
            )
            self.state_store.upsert_collaboration(project_id, collaboration)

            if all(review.decision == ReviewDecision.APPROVE for review in round_reviews):
                status = CollaborationStatus.ACCEPTED
                break

            draft = self._revise(project_id, lead, workitem, draft, round_reviews, round_index, project_root)
            collaboration = self._update_collaboration(
                collaboration=collaboration,
                status=CollaborationStatus.RUNNING,
                current_round=round_index,
                contributions=contributions,
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
        )
        collaboration = self._update_collaboration(
            collaboration=collaboration,
            status=status,
            current_round=min(self.policy.max_rounds, max((item.round_index for item in contributions), default=1)),
            contributions=contributions,
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

    def _update_collaboration(
        self,
        collaboration: Collaboration,
        status: CollaborationStatus,
        current_round: int,
        contributions: list[ReviewContribution],
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
            final_artifact_id=final_artifact_id if final_artifact_id is not None else collaboration.final_artifact_id,
        )

    def _review(
        self,
        project_id: str,
        collaboration_id: str,
        round_index: int,
        reviewer: Agent,
        workitem: WorkItem,
        draft: str,
        project_root: str,
    ) -> ReviewContribution:
        """Run one reviewer against the same draft."""
        content = self._run_agent_or_mock_review(project_id, reviewer, workitem, draft, round_index, project_root)
        decision = self._parse_review_decision(content)
        self.state_store.add_event(
            project_id,
            f"协作 {collaboration_id} 第 {round_index} 轮审阅: {reviewer.role} -> {decision.value}",
        )
        return ReviewContribution(
            id=f"{collaboration_id}-round-{round_index}-{reviewer.id}",
            round_index=round_index,
            agent_id=reviewer.id,
            role=reviewer.role,
            decision=decision,
            content=content,
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
    ) -> str:
        """Let the lead revise the draft after all reviews in the round are collected."""
        review_text = "\n\n".join(
            f"### {review.role} ({review.decision.value})\n{review.content}"
            for review in reviews
        )
        prompt = (
            "你是需求阶段的 lead agent。请在完整阅读所有 reviewer 意见后统一修订草案。\n"
            f"WorkItem: {workitem.id} / {workitem.description}\n\n"
            f"# 当前草案\n{draft}\n\n"
            f"# 本轮审阅意见\n{review_text}\n\n"
            "请直接输出修订后的完整 Markdown 文档，不要只输出差异。"
        )
        if self.agent_cli_executor.resolve_binding(lead) == "claude":
            prompt = (
                "You are the lead design agent in Conductor.\n"
                f"Work item kind: {workitem.kind}\n"
                "Revise the current design draft after reading all reviewer feedback.\n"
                "Return a full Chinese markdown document, not a diff.\n"
                "Keep the sections clear and practical.\n\n"
                f"Current draft:\n{draft[:2400]}\n\n"
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
                return cli_stdout
            self.state_store.add_event(
                project_id,
                f"协作修订 Agent CLI 失败，回退到 LLM/mocks: exit_code={cli_execution.result.exit_code}",
            )
        if lead.llm_backend is None:
            return self._build_mock_revision(workitem, draft, reviews, round_index)
        try:
            revision = lead.think(
                prompt=prompt,
                preferred_backend=lead.preferred_llm_backend,
            )
            if self._is_disabled_llm_response(revision):
                return self._build_mock_revision(workitem, draft, reviews, round_index)
            return revision
        except Exception as error:
            self.state_store.add_event(project_id, f"协作修订 LLM 失败，使用 mock 修订: {error}")
            return self._build_mock_revision(workitem, draft, reviews, round_index)

    def _run_agent_or_mock_review(
        self,
        project_id: str,
        reviewer: Agent,
        workitem: WorkItem,
        draft: str,
        round_index: int,
        project_root: str,
    ) -> str:
        """Run a review through Agent CLI, LLM, or structured mock fallback."""
        prompt = (
            f"你是 {reviewer.role} reviewer。请审阅同一轮固定 draft，不要修改原文。\n"
            f"WorkItem: {workitem.id} / {workitem.description}\n\n"
            f"# Draft\n{draft}\n\n"
            "请用中文输出 Markdown，必须包含 `Decision: approve` 或 `Decision: request_changes`，"
            "并列出主要问题、建议和风险。"
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
                return cli_stdout
            self.state_store.add_event(
                project_id,
                f"协作审阅 Agent CLI 失败，回退到 LLM/mocks: {reviewer.role} -> exit_code={cli_execution.result.exit_code}",
            )
        if reviewer.llm_backend is None:
            return self._build_mock_review(reviewer, workitem, round_index)
        try:
            review = reviewer.think(
                prompt=prompt,
                preferred_backend=reviewer.preferred_llm_backend,
            )
            if self._is_disabled_llm_response(review):
                return self._build_mock_review(reviewer, workitem, round_index)
            return review
        except Exception as error:
            self.state_store.add_event(project_id, f"协作审阅 LLM 失败，使用 mock review: {reviewer.role} -> {error}")
            return self._build_mock_review(reviewer, workitem, round_index)

    def _is_disabled_llm_response(self, content: str) -> bool:
        """Return whether the response is a disabled-backend placeholder."""
        return content.startswith("[local-disabled]") or content.startswith("[cloud-disabled]")

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
    ) -> Artifact:
        """Persist the final collaboration artifact."""
        contribution_text = "\n\n".join(
            f"### 第 {item.round_index} 轮 | {item.role} | {item.decision.value}\n{item.content}"
            for item in contributions
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
