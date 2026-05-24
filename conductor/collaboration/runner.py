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
from conductor.collaboration.team import ReviewSeat, plan_requirement_review_team
from conductor.config.cli import CLISelectionConfig
from conductor.design_quality import DesignQualityResult, evaluate_design_document
from conductor.domain.models import Artifact, WorkItem
from conductor.harness.llm import LLMHarnessRequest, OpenAICompatibleLLMHarness
from conductor.execution.runtime_stream import RuntimeStreamStore
from conductor.requirement_benchmark import build_requirement_case_from_text, evaluate_requirement_document
from conductor.state.store import InMemoryStateStore


@dataclass(slots=True)
class CollaborationRunResult:
    """Structured runtime metadata for one collaboration LLM/CLI call."""

    content: str
    source_backend: str = ""
    model: str = ""
    output_path: str = ""
    duration_ms: int = 0
    token_usage: dict[str, int] | None = None


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
        self._review_focus_overrides: dict[str, str] = {}
        self._current_team_plan: dict[str, object] = {}

    def should_collaborate(self, project_id: str, workitem: WorkItem) -> bool:
        """Return whether the workitem should enter the collaboration loop."""
        if not self.policy.enabled or workitem.kind not in self.policy.enabled_kinds:
            return False
        state = self.state_store.get_state(project_id)
        return not any(item.workitem_id == workitem.id for item in state.collaborations)

    def run_review_loop(self, project_id: str, workitem: WorkItem, draft_artifact: Artifact) -> Collaboration:
        """Run one collaboration session around an existing draft artifact."""
        project_root = self.state_store.get_state(project_id).project.project_root
        lead = self.registry.get_agent_by_role(self.lead_role_for_workitem(workitem))
        peer_reviewers, functional_reviewers = self._build_reviewers(project_id, workitem)
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
        requirement_quality = None
        design_quality = None
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
            team_plan=dict(self._current_team_plan),
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
            peer_revision_done = False

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
                            token_usage=dict(revision.token_usage or {}),
                        )
                    )
                    peer_revision_done = True

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

            rework_reviews = functional_reviews or ([] if peer_revision_done else peer_reviews)
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
                        token_usage=dict(revision.token_usage or {}),
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
            if workitem.kind == "requirement_spec":
                requirement_quality = self._evaluate_requirement_quality(project_id, draft)
                feedback_coverage = self._review_feedback_resolution_coverage(draft, contributions)
                self.state_store.add_event(
                    project_id,
                    (
                        "Requirement final arbitration: "
                        f"score={requirement_quality.score}, passed={requirement_quality.passed}, "
                        f"feedback_coverage={feedback_coverage}"
                    ),
                )
                if requirement_quality.passed and feedback_coverage >= 70 and len(draft_versions) > 1:
                    status = CollaborationStatus.ACCEPTED
                else:
                    status = CollaborationStatus.MAX_ROUNDS_REACHED
            else:
                feedback_coverage = self._review_feedback_resolution_coverage(draft, contributions)
                design_quality = self._evaluate_design_quality(draft, workitem)
                self.state_store.add_event(
                    project_id,
                    (
                        "Design final arbitration: "
                        f"score={design_quality.score}, passed={design_quality.passed}, "
                        f"feedback_coverage={feedback_coverage}"
                    ),
                )
                if design_quality.passed and feedback_coverage >= 60 and len(draft_versions) > 1:
                    status = CollaborationStatus.ACCEPTED
                else:
                    status = CollaborationStatus.MAX_ROUNDS_REACHED
        if status == CollaborationStatus.ACCEPTED and workitem.kind == "requirement_spec":
            quality = requirement_quality or self._evaluate_requirement_quality(project_id, draft)
            self.state_store.add_event(
                project_id,
                f"需求质量评分: score={quality.score}, passed={quality.passed}",
            )
            if not quality.passed:
                status = CollaborationStatus.FAILED
                self.state_store.add_event(
                    project_id,
                    f"需求质量门禁未通过: {'; '.join(quality.findings) or 'score below threshold'}",
                )
        if status == CollaborationStatus.ACCEPTED and workitem.kind == "design_overview":
            quality = design_quality or self._evaluate_design_quality(draft, workitem)
            self.state_store.add_event(
                project_id,
                f"设计质量评分: score={quality.score}, passed={quality.passed}",
            )
            if not quality.passed:
                status = CollaborationStatus.FAILED
                self.state_store.add_event(
                    project_id,
                    f"设计质量门禁未通过: {'; '.join(quality.findings) or 'score below threshold'}",
                )

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
        if status == CollaborationStatus.ACCEPTED and workitem.kind == "requirement_spec":
            self._create_frozen_requirement_artifact(
                project_id=project_id,
                workitem=workitem,
                lead=lead,
                draft=draft,
                review_artifact=final_artifact,
                project_root=project_root,
            )
        if status == CollaborationStatus.ACCEPTED and workitem.kind == "design_overview":
            self._create_frozen_design_artifact(
                project_id=project_id,
                workitem=workitem,
                lead=lead,
                draft=draft,
                review_artifact=final_artifact,
                project_root=project_root,
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

    def lead_role_for_workitem(self, workitem: WorkItem) -> str:
        """Return the lead role for a collaboration target."""
        if workitem.kind in self.policy.lead_role_by_kind:
            return self.policy.lead_role_by_kind[workitem.kind]
        return self.policy.lead_role_by_stage[workitem.stage]

    def _evaluate_requirement_quality(self, project_id: str, draft: str):
        """Evaluate whether an accepted requirement draft is good enough to freeze."""
        state = self.state_store.get_state(project_id)
        case = build_requirement_case_from_text(project_id, state.project.goal, name="project_requirement")
        return evaluate_requirement_document(draft, case)

    def _evaluate_design_quality(self, draft: str, workitem: WorkItem):
        """Evaluate whether an accepted design draft is good enough to freeze."""
        if workitem.kind == "design_overview":
            return evaluate_design_document(draft)
        passed = self._draft_has_minimum_sections(draft, workitem)
        return DesignQualityResult(
            score=100 if passed else 0,
            passed=passed,
            findings=[] if passed else ["minimum sections missing"],
            dimension_scores={"minimum_sections": 100 if passed else 0},
        )

    def _draft_has_minimum_sections(self, draft: str, workitem: WorkItem) -> bool:
        """Return whether a revised non-requirement draft is complete enough to accept."""
        if workitem.kind == "design_overview":
            required = ("目标", "需求理解", "范围边界", "方案", "验收", "风险")
        else:
            required = ("目标", "方案", "验收")
        return all(section in draft for section in required)

    def _review_feedback_resolution_coverage(self, draft: str, contributions: list[ReviewContribution]) -> int:
        """Return whether requested-review topics appear in the final requirement draft."""
        requested_reviews = [
            contribution
            for contribution in contributions
            if contribution.decision == ReviewDecision.REQUEST_CHANGES
        ]
        if not requested_reviews:
            return 100
        requested_topics = self._feedback_topics_for_reviews(requested_reviews)
        if not requested_topics:
            return 100
        draft_text = draft.lower()
        covered = [
            topic
            for topic, terms in requested_topics.items()
            if self._contains_any_text(draft_text, terms)
        ]
        return int((len(covered) / len(requested_topics)) * 100)

    def _feedback_topics_for_reviews(self, reviews: list[ReviewContribution]) -> dict[str, tuple[str, ...]]:
        """Infer high-signal review topics that should be reflected in the final spec."""
        topic_terms: dict[str, tuple[str, ...]] = {
            "persistence": ("持久化", "刷新", "localstorage", "local storage", "本地存储", "数据库", "存储"),
            "sync": ("同步", "一致性", "冲突", "覆盖", "重复", "去重"),
            "network_retry": ("网络", "离线", "重试", "恢复连接", "超时", "timeout"),
            "validation": ("校验", "验证", "必填", "无效", "错误", "格式"),
            "security": ("安全", "隐私", "权限", "认证", "授权", "泄露"),
            "performance": ("性能", "容量", "大量", "并发", "响应速度"),
            "csv_export": ("csv", "导出", "文件名", "字段", "转义"),
            "acceptance": ("验收", "测试", "用例", "期望输出", "可执行"),
            "scope": ("范围", "边界", "非目标", "不支持", "限制"),
            "handoff": ("下游", "约束", "交付", "后续 agent", "设计阶段", "开发团队"),
        }
        review_text = "\n".join(review.content for review in reviews).lower()
        return {
            topic: terms
            for topic, terms in topic_terms.items()
            if self._contains_any_text(review_text, terms)
        }

    def _contains_any_text(self, text: str, terms: tuple[str, ...]) -> bool:
        """Return whether text contains any term."""
        return any(term.lower() in text for term in terms)

    def _build_reviewers(self, project_id: str, workitem: WorkItem) -> tuple[list[Agent], list[Agent]]:
        """Build concrete reviewer agents, including dynamic requirement-stage seats."""
        self._review_focus_overrides = {}
        self._current_team_plan = {}
        lead_role = self.lead_role_for_workitem(workitem)
        peer_roles = self.policy.peer_reviewer_roles_by_stage.get(workitem.stage, [])
        functional_roles = self.policy.reviewer_roles_by_stage.get(workitem.stage, [])
        if workitem.stage != "requirement" or not self.policy.dynamic_requirement_review_enabled:
            peer_seats = [
                ReviewSeat(role=role, seat_id=role, phase="design_peer_review", focus=self._static_review_focus(role))
                for role in self._reviewer_roles_without_lead(peer_roles, lead_role)
            ]
            functional_seats = [
                ReviewSeat(
                    role=role,
                    seat_id=role,
                    phase="cross_functional_review",
                    focus=self._static_review_focus(role),
                )
                for role in self._reviewer_roles_without_lead(functional_roles, lead_role)
            ]
            self._current_team_plan = {
                "complexity_level": "configured",
                "complexity_score": len(peer_seats) + len(functional_seats),
                "reasons": [f"{workitem.stage} collaboration enabled for {workitem.kind}"],
                "lead_role": lead_role,
                "peer_seats": [self._review_seat_to_dict(seat) for seat in peer_seats],
                "functional_seats": [self._review_seat_to_dict(seat) for seat in functional_seats],
            }
            return (
                [self._agent_for_review_seat(seat) for seat in peer_seats],
                [self._agent_for_review_seat(seat) for seat in functional_seats],
            )

        state = self.state_store.get_state(project_id)
        plan = plan_requirement_review_team(
            state.project.goal,
            peer_roles=peer_roles,
            functional_roles=functional_roles,
        )
        self._current_team_plan = self._team_plan_to_dict(plan)
        self.state_store.add_event(
            project_id,
            (
                "Requirement review team planned: "
                f"complexity={plan.complexity_level}, score={plan.complexity_score}, "
                f"peer_seats={len(plan.peer_seats)}, functional_seats={len(plan.functional_seats)}, "
                f"reasons={', '.join(plan.reasons) or '-'}"
            ),
        )
        return (
            [self._agent_for_review_seat(seat) for seat in plan.peer_seats],
            [self._agent_for_review_seat(seat) for seat in plan.functional_seats],
        )

    def _reviewer_roles_without_lead(self, roles: list[str], lead_role: str) -> list[str]:
        """Return reviewer roles without duplicating the lead role."""
        return [role for role in dict.fromkeys(roles) if role and role != lead_role]

    def _static_review_focus(self, role: str) -> str:
        """Return a default review focus for configured non-requirement teams."""
        return {
            "requirement_designer": "Review whether the output still follows frozen requirements and scope boundaries.",
            "solution_designer": "Review consistency, integration boundaries, and downstream handoff risk.",
            "backend_engineer": "Review API, data, persistence, and backend failure-mode impact.",
            "frontend_engineer": "Review UI state, interaction, accessibility, and frontend integration impact.",
            "tester": "Review acceptance coverage, regression risk, and observable pass/fail evidence.",
        }.get(role, "Review from the default responsibility of this role.")

    def _team_plan_to_dict(self, plan) -> dict[str, object]:
        """Return a serializable requirement team plan snapshot."""
        return {
            "complexity_level": plan.complexity_level,
            "complexity_score": plan.complexity_score,
            "reasons": list(plan.reasons),
            "peer_seats": [self._review_seat_to_dict(seat) for seat in plan.peer_seats],
            "functional_seats": [self._review_seat_to_dict(seat) for seat in plan.functional_seats],
        }

    def _review_seat_to_dict(self, seat: ReviewSeat) -> dict[str, str]:
        """Return a serializable review seat."""
        return {
            "role": seat.role,
            "seat_id": seat.seat_id,
            "phase": seat.phase,
            "focus": seat.focus,
        }

    def _agent_for_review_seat(self, seat: ReviewSeat) -> Agent:
        """Return an agent instance for a concrete review seat."""
        base = self.registry.get_agent_by_role(seat.role)
        if seat.seat_id == seat.role:
            self._review_focus_overrides[base.id] = seat.focus
            return base
        agent = Agent(
            id=f"{base.id}:{seat.seat_id}",
            role=base.role,
            profile=base.profile,
            capabilities=list(base.capabilities),
            backend=base.backend,
            llm_backend=base.llm_backend,
            execution_backend=base.execution_backend,
            preferred_llm_backend=base.preferred_llm_backend,
        )
        self._review_focus_overrides[agent.id] = seat.focus
        return agent

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
            team_plan=dict(collaboration.team_plan),
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
            token_usage=dict(result.token_usage or {}),
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
        original_requirement = self._project_goal(project_id)
        prompt = (
            "你是需求设计阶段的 lead designer agent。请在完整阅读所有 reviewer 意见后统一修订草案。\n"
            f"当前评审阶段: {phase}\n"
            f"WorkItem: {workitem.id} / {workitem.description}\n\n"
            f"# 原始用户需求（最高优先级，不得扩展）\n{original_requirement}\n\n"
            f"# 当前草案摘要\n{draft_excerpt}\n\n"
            f"# 本轮审阅意见\n{review_text}\n\n"
            "请直接输出修订后的完整中文 Markdown 需求设计文档，不要只输出差异。"
            "需求规格类文档必须保留并完善：目标、需求理解、范围边界、非目标、验收标准、边界/异常场景、风险与假设、待确认问题、下游交付约束。"
            "不要把用户未明确要求的功能升级为正式范围；例如编辑、删除、登录、同步、导入等能力只能写入待确认问题或非目标，除非原始需求明确要求。"
            "不要使用“增删查”“增删改查”“CRUD”这类会暗含编辑/删除的缩写，除非原始需求明确要求这些动作。"
            "设计类文档必须保留并完善：目标、需求理解、范围边界、关键假设、方案、交付物、验收标准、风险。"
        )
        if self.agent_cli_executor.resolve_binding(lead) == "claude":
            prompt = (
                "You are the lead requirement/design agent in Conductor.\n"
                f"Work item kind: {workitem.kind}\n"
                f"Review phase: {phase}\n"
                f"Original user requirement, highest priority:\n{original_requirement}\n\n"
                "Revise the current design draft after reading all reviewer feedback.\n"
                "Return a full Chinese markdown requirement design document, not a diff.\n"
                "For requirement_spec, keep these sections clear and practical: 目标, 需求理解, 范围边界, 非目标, 验收标准, 边界/异常场景, 风险与假设, 待确认问题, 下游交付约束.\n"
                "Do not promote unrequested features such as edit, delete, login, sync, or import into in-scope requirements; keep them as open questions or non-goals unless explicitly requested.\n"
                "Do not use generic CRUD wording if edit/delete are not explicitly requested.\n"
                "For other design docs, keep sections: 目标, 需求理解, 范围边界, 关键假设, 方案, 交付物, 验收标准, 风险.\n\n"
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
        review_focus = self._review_focus_overrides.get(reviewer.id, review_focus)
        original_requirement = self._project_goal(project_id)
        phase_instruction = (
            "这是设计同侪评审阶段。请优先判断需求本身是否合理、完整、符合用户目标；不要只从代码实现难度出发。"
            if phase == "design_peer_review"
            else "这是跨职能评审阶段。请基于已修订的需求设计，从本角色交付风险和验收可执行性角度审阅。"
        )
        scope_guard = (
            "评审不得要求新增原始需求没有明确提出的功能。"
            "例如编辑、删除、登录、同步、导入等能力，如果原始需求未要求，只能建议写入非目标或待确认问题，不能作为 request_changes 的必改范围。"
            "如果草案已经把未请求功能列为非目标，不能再要求把该功能改为正式范围。"
        )
        prompt = (
            f"你是 {reviewer.role} reviewer，正在参与需求设计评审。请审阅同一轮固定 draft，不要修改原文。\n"
            f"评审阶段：{phase}\n"
            f"{phase_instruction}\n"
            f"{scope_guard}\n"
            f"评审重点：{review_focus}\n"
            f"原始用户需求（最高优先级，不得扩展）：{original_requirement}\n"
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
                    "- Do not request unasked features as mandatory changes; keep them as non-goals or open questions.\n"
                    "- Be specific to the current requirement. Do not use generic filler.\n"
                ),
                system_prompt=(
                    "You are a strict non-interactive reviewer agent in a multi-agent design review. "
                    "Return concise Chinese Markdown only."
                ),
                working_directory=project_root,
                output_path=(
                    f".conductor/llm_outputs/{workitem.id}.{phase}."
                    f"{self._safe_agent_id(reviewer.id)}.round-{round_index}.review.md"
                ),
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
                token_usage=result.token_usage,
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
                    "- For requirement_spec, include non-goals, edge/error cases, open questions, and downstream handoff constraints.\n"
                    "- Do not convert reviewer suggestions into new in-scope features unless the original user requirement explicitly asked for them.\n"
                    "- Do not use generic CRUD wording if edit/delete are not explicitly requested by the original requirement.\n"
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
                token_usage=result.token_usage,
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

    def _project_goal(self, project_id: str) -> str:
        """Return the immutable source requirement for collaboration prompts."""
        return self.state_store.get_state(project_id).project.goal

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
        if workitem.kind == "requirement_spec":
            return self._build_mock_requirement_revision(workitem, reviews, round_index)
        if workitem.kind == "design_overview":
            return self._build_mock_design_revision(workitem, draft, reviews, round_index)
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

    def _build_mock_design_revision(
        self,
        workitem: WorkItem,
        draft: str,
        reviews: list[ReviewContribution],
        round_index: int,
    ) -> str:
        """Build an actionable design baseline for offline smoke runs."""
        if self._is_api_workitem(workitem):
            return self._build_mock_api_design_revision(workitem, draft, reviews, round_index)
        review_summary = "\n".join(f"- {review.role}: {review.decision.value}" for review in reviews) or "- No review notes."
        criteria = "\n".join(f"- {item}" for item in workitem.acceptance_criteria) or "- Preserve the frozen requirement and produce an implementable handoff."
        return (
            f"# Overall Design - {workitem.id}\n\n"
            f"## Revision Round\n{round_index}\n\n"
            "## Goal\n"
            "Define a browser-only implementation plan that downstream development and testing agents can execute without adding unrelated scope.\n\n"
            "## Requirement Understanding\n"
            f"{workitem.description}\n\n"
            "The user workflow is a local single-page experience with visible state updates, durable browser storage, validation feedback, filtering, deletion, and export where requested.\n\n"
            "## Scope Boundary\n"
            "- In scope: one static web page, semantic form controls, localStorage persistence, local CSV generation, delete action, filters, empty state, and validation errors.\n"
            "- Out of scope / non-goals: backend services, server APIs, login/auth, accounts, cloud sync, payments, analytics, and remote storage.\n"
            "- Constraint: keep all product data in the browser and avoid network dependencies.\n\n"
            "## Solution\n"
            "- Architecture: `index.html` defines form, toolbar filters, card/list region, empty state, and export control.\n"
            "- Module: `app.js` owns state, validation, rendering, localStorage read/write, filtering, deletion, and CSV export.\n"
            "- Component flow: load saved data -> render filters and list -> submit validated card -> persist -> rerender -> export current data.\n"
            "- Interface boundary: use browser DOM and localStorage APIs only; no HTTP routes, server endpoint, or external service.\n\n"
            "## Data And State\n"
            "- Fields: id, question, answer, topic, status, createdAt.\n"
            "- State: cards array, active topic filter, active status filter, validation error text, empty/list visibility.\n"
            "- Storage: serialize cards to localStorage after create/delete/status updates and restore during initialization.\n"
            "- CSV: escape commas, quotes, and newlines before creating a local Blob download.\n\n"
            "## Acceptance And Test Plan\n"
            f"{criteria}\n"
            "- Test valid card creation updates the visible list and browser storage.\n"
            "- Test empty required fields show validation errors and do not mutate state.\n"
            "- Test topic/status filters change the visible card set without deleting data.\n"
            "- Test delete removes one card and persists after reload.\n"
            "- Test CSV export contains headers and all card fields.\n\n"
            "## Risks And Assumptions\n"
            "- Assumption: this is a single-user local browser tool.\n"
            "- Risk: localStorage can be cleared by the browser, so persistence is best-effort.\n"
            "- Risk: CSV escaping errors can corrupt exported answers that contain punctuation or line breaks.\n"
            "- Open question: whether status is limited to new/learning/mastered or should be configurable.\n\n"
            "## Review Resolution\n"
            f"{review_summary}\n"
            "- Reviewer concerns are resolved through explicit scope boundary, architecture, data/state, validation, acceptance tests, and risk notes.\n\n"
            "## Previous Draft Summary\n"
            f"{draft[:800]}\n"
        )

    def _build_mock_requirement_revision(
        self,
        workitem: WorkItem,
        reviews: list[ReviewContribution],
        round_index: int,
    ) -> str:
        """Build a deterministic requirement baseline for offline smoke runs."""
        if self._is_api_workitem(workitem):
            return self._build_mock_api_requirement_revision(workitem, reviews, round_index)
        review_summary = "\n".join(f"- {review.role}: {review.decision.value}" for review in reviews) or "- No review notes."
        criteria = "\n".join(f"- {item}" for item in workitem.acceptance_criteria) or "- Downstream stages must verify the stated user requirement."
        return (
            f"# Requirement Specification - {workitem.id}\n\n"
            f"## Revision Round\n{round_index}\n\n"
            "## Goal\n"
            "Deliver the smallest useful product that satisfies the user request while preserving explicit scope boundaries.\n\n"
            "## Requirement Understanding\n"
            f"{workitem.description}\n\n"
            "The product must support the named user workflow, data fields, UI states, persistence behavior, and validation paths described above.\n\n"
            "## Scope Boundary\n"
            "- In scope: the core user flow, visible UI state updates, local data handling, validation, and offline verification.\n"
            "- Out of scope / non-goals: login, cloud sync, payment, notification, recommendation engines, analytics, and backend services unless explicitly requested.\n"
            "- The implementation must not add unrelated platform features beyond the stated requirement.\n\n"
            "## Non-Goals\n"
            "- No authentication or account system.\n"
            "- No network dependency or external service integration.\n"
            "- No hidden admin dashboard or reporting module.\n\n"
            "## Acceptance Criteria\n"
            f"{criteria}\n"
            "- Given valid input, when the user submits the form, then the visible list updates immediately.\n"
            "- Given existing saved data, when the page reloads, then data is restored from localStorage or equivalent local persistence.\n"
            "- Given invalid or empty required input, when the user submits, then a clear validation error is shown and no invalid record is added.\n"
            "- Given a filter action, when the user selects all, active, or finished, then the list reflects the selected state.\n\n"
            "## Edge / Error Cases\n"
            "- Empty title, author, or required field input must be rejected with visible feedback.\n"
            "- Empty state must explain that no records exist yet.\n"
            "- localStorage or browser storage failure must not corrupt the current in-memory UI state.\n"
            "- Duplicate or unusual text input should remain visible and should not break rendering.\n\n"
            "## Risks And Assumptions\n"
            "- Assumption: this is a single-user local browser experience.\n"
            "- Risk: browser storage can be cleared by the user, so persistence is best-effort local persistence.\n"
            "- Risk: weak validation would make downstream tests ambiguous.\n\n"
            "## Open Questions / To Confirm\n"
            "- Confirm whether UI copy should be English, Chinese, or configurable.\n"
            "- Confirm whether records need edit and delete actions if not explicitly requested.\n\n"
            "## Downstream Handoff Constraints\n"
            "- Design must preserve this requirement baseline as the contract for later stages.\n"
            "- Frontend implementation must include index.html, JavaScript, CSS, localStorage persistence, validation, empty state, and filterable visible state.\n"
            "- Testing must cover add, complete, filter, invalid input, empty state, persistence after reload, and offline static validation.\n\n"
            "## Review Resolution\n"
            f"{review_summary}\n"
            "- Reviewer concerns are resolved through explicit scope, acceptance, validation, persistence, edge cases, and downstream handoff constraints.\n"
        )

    def _build_mock_api_requirement_revision(
        self,
        workitem: WorkItem,
        reviews: list[ReviewContribution],
        round_index: int,
    ) -> str:
        """Build a deterministic API requirement baseline for offline API smoke runs."""
        review_summary = "\n".join(f"- {review.role}: {review.decision.value}" for review in reviews) or "- No review notes."
        criteria = "\n".join(f"- {item}" for item in workitem.acceptance_criteria) or "- Downstream stages must verify the stated API requirement."
        sqlite_requested = self._is_sqlite_api_workitem(workitem)
        persistence_scope = (
            "SQLite-backed persistence, schema initialization, filtering/query behavior, update/delete behavior, and stats where requested"
            if sqlite_requested
            else "in-memory mock persistence, filtering/query behavior, update/delete behavior, and stats where requested"
        )
        database_non_goal = (
            "No external production database or account system."
            if sqlite_requested
            else "No production database or account system."
        )
        assumption = (
            "Assumption: this is an offline API service using a local SQLite database file for validation."
            if sqlite_requested
            else "Assumption: this is an offline API mock with in-memory persistence for validation."
        )
        implementation_constraint = (
            "Backend implementation must include app.py routes, SQLite initialization/persistence, and API contract tests."
            if sqlite_requested
            else "Backend implementation must include app.py routes and API contract tests."
        )
        testing_constraint = (
            "Testing must cover create, list, filter/query, update, delete, stats, SQLite persistence, and explicit endpoint/status/payload evidence."
            if sqlite_requested
            else "Testing must cover create, list, filter/query, update, delete, stats, and explicit endpoint/status/payload evidence."
        )
        return (
            f"# Requirement Specification - {workitem.id}\n\n"
            f"## Revision Round\n{round_index}\n\n"
            "## Goal\n"
            "Deliver the smallest useful backend API that satisfies the user request while preserving explicit scope boundaries.\n\n"
            "## Requirement Understanding\n"
            f"{workitem.description}\n\n"
            "The product must expose JSON HTTP endpoints for the requested resource workflow and keep API behavior observable through contract tests.\n\n"
            "## Scope Boundary\n"
            f"- In scope: REST-style API endpoints, JSON request/response payloads, input validation, {persistence_scope}.\n"
            "- Out of scope / non-goals: browser UI, localStorage, authentication, payments, cloud services, external databases, background jobs, and analytics unless explicitly requested.\n"
            "- The implementation must not add unrelated platform features beyond the stated API requirement.\n\n"
            "## Non-Goals\n"
            "- No frontend page or static web UI.\n"
            "- No external network service dependency.\n"
            f"- {database_non_goal}\n\n"
            "## Acceptance Criteria\n"
            f"{criteria}\n"
            "- Given a valid create request, when the API receives it, then it returns HTTP 201 with a JSON response payload containing the created item.\n"
            "- Given existing items, when the list endpoint is called, then it returns HTTP 200 with a JSON response payload containing matching items.\n"
            "- Given filter or query parameters, when the list endpoint is called, then active/completed and keyword matches are reflected in the response payload.\n"
            "- Given an update or delete request, when the target exists, then the API returns a concrete status code and JSON payload proving the state change.\n\n"
            "## Edge / Error Cases\n"
            "- Blank required titles must be rejected with a 4xx status code.\n"
            "- Missing item ids must return 404.\n"
            "- Invalid filter values must be rejected instead of silently ignored.\n\n"
            "## Risks And Assumptions\n"
            f"- {assumption}\n"
            "- Risk: generic test success is not enough evidence; tests must print endpoint, status code, and response payload signals.\n\n"
            "## Downstream Handoff Constraints\n"
            "- Design must preserve this API requirement baseline as the contract for later stages.\n"
            f"- {implementation_constraint}\n"
            f"- {testing_constraint}\n\n"
            "## Review Resolution\n"
            f"{review_summary}\n"
            "- Reviewer concerns are resolved through explicit API scope, contract tests, validation cases, and endpoint evidence requirements.\n"
        )

    def _build_mock_api_design_revision(
        self,
        workitem: WorkItem,
        draft: str,
        reviews: list[ReviewContribution],
        round_index: int,
    ) -> str:
        """Build an actionable API design baseline for offline API smoke runs."""
        review_summary = "\n".join(f"- {review.role}: {review.decision.value}" for review in reviews) or "- No review notes."
        criteria = "\n".join(f"- {item}" for item in workitem.acceptance_criteria) or "- Preserve the frozen requirement and produce an implementable API handoff."
        sqlite_requested = self._is_sqlite_api_workitem(workitem)
        persistence_scope = "SQLite database file, schema initialization" if sqlite_requested else "in-memory mock state"
        database_boundary = "external production database" if sqlite_requested else "external database"
        architecture = (
            "app.py exposes create/list/get/update/delete/stats endpoints for /api/items and persists rows in SQLite."
            if sqlite_requested
            else "app.py exposes create/list/get/update/delete/stats endpoints for /api/items."
        )
        data_model = (
            "SQLite table items(id, title, content, completed, created_at), with blank-title validation."
            if sqlite_requested
            else "item id, title, content, completed flag, with blank-title validation."
        )
        persistence_test = "- Test SQLite persistence by reloading rows from the database file and printing database evidence.\n" if sqlite_requested else ""
        assumption = (
            "Assumption: a local SQLite file is acceptable for the API SQLite flow."
            if sqlite_requested
            else "Assumption: in-memory state is acceptable for the API mock flow."
        )
        return (
            f"# Overall Design - {workitem.id}\n\n"
            f"## Revision Round\n{round_index}\n\n"
            "## Goal\n"
            "Define an API-first implementation plan that downstream development and testing agents can execute without adding UI scope.\n\n"
            "## Requirement Understanding\n"
            f"{workitem.description}\n\n"
            "The workflow is a backend JSON API with observable endpoint behavior, validation, filtering/querying, mutation, deletion, and stats responses.\n\n"
            "## Scope Boundary\n"
            f"- In scope: FastAPI-compatible app.py, /api/items resource routes, JSON payload models, {persistence_scope}, validation errors, and pytest contract tests.\n"
            f"- Out of scope / non-goals: browser UI, localStorage, static assets, login/auth, {database_boundary}, remote integrations, and background workers.\n"
            "- Constraint: keep the mock deterministic and self-contained for offline validation.\n\n"
            "## Solution\n"
            f"- Architecture: {architecture}\n"
            f"- Data model: {data_model}\n"
            "- Contract tests: tests/test_api_contract.py uses FastAPI TestClient and prints endpoint, status code, and response payload evidence.\n"
            "- Validation command: pytest runs in the project root and emits endpoint behavior evidence for Manifest coverage.\n\n"
            "## Acceptance And Test Plan\n"
            f"{criteria}\n"
            "- Test POST /api/items returns 201 and created item payload.\n"
            "- Test GET /api/items returns listed items and supports active/completed plus keyword query.\n"
            "- Test PATCH and DELETE mutate state and return JSON payloads.\n"
            "- Test GET /api/items/stats returns total/completed/active counts.\n\n"
            f"{persistence_test}"
            "## Risks And Assumptions\n"
            f"- {assumption}\n"
            "- Risk: contract tests that hide stdout cannot prove endpoint coverage, so pytest must expose evidence output.\n\n"
            "## Review Resolution\n"
            f"{review_summary}\n"
            "- Reviewer concerns are resolved through explicit API routes, data model, validation boundaries, and contract-test evidence.\n\n"
            "## Previous Draft Summary\n"
            f"{draft[:800]}\n"
        )

    def _is_api_workitem(self, workitem: WorkItem) -> bool:
        """Return whether a mock collaboration artifact should stay API-first."""
        text = f"{workitem.kind} {workitem.description}".lower()
        api_terms = ("api", "rest", "http", "endpoint", "backend", "server", "service", "fastapi")
        frontend_only_terms = ("browser-only", "static web", "localstorage", "local storage", "no backend")
        return any(term in text for term in api_terms) and not any(term in text for term in frontend_only_terms)

    def _is_sqlite_api_workitem(self, workitem: WorkItem) -> bool:
        """Return whether an API collaboration baseline should preserve SQLite persistence scope."""
        text = f"{workitem.kind} {workitem.description}".lower()
        return any(term in text for term in ("sqlite", "database", "db", "sql", "persistence", "persist"))

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

    def _create_frozen_requirement_artifact(
        self,
        project_id: str,
        workitem: WorkItem,
        lead: Agent,
        draft: str,
        review_artifact: Artifact,
        project_root: str,
    ) -> Artifact:
        """Persist the accepted requirement baseline for downstream phases."""
        artifact = Artifact(
            id=f"artifact-frozen-requirement-{workitem.id}",
            project_id=project_id,
            workitem_id=workitem.id,
            agent_id=lead.id,
            kind="frozen_requirement_spec",
            title=f"Frozen Requirement Spec - {workitem.id}",
            content=(
                f"# Frozen Requirement Spec - {workitem.id}\n\n"
                "## Status\naccepted\n\n"
                "## Baseline\n"
                f"{draft}\n\n"
                "## Downstream Contract\n"
                "- 后续设计、开发、测试必须以本冻结需求规格作为需求基线。\n"
                "- 如需改变范围，必须创建新的需求修订或返工 WorkItem。\n"
            ),
            source_backend="collaboration",
            parent_artifact_id=review_artifact.id,
            derived_from=self._build_derived_from(project_id, workitem.id),
            review_of=review_artifact.review_of,
            version=1,
            collaboration_session_id=f"collaboration-{workitem.id}",
        )
        persisted = self.artifact_store.save_markdown(artifact, project_root=project_root)
        self.state_store.add_artifact(project_id, persisted)
        self.state_store.add_event(project_id, f"冻结需求规格 {persisted.id} 已创建，来源 WorkItem={workitem.id}")
        return persisted

    def _create_frozen_design_artifact(
        self,
        project_id: str,
        workitem: WorkItem,
        lead: Agent,
        draft: str,
        review_artifact: Artifact,
        project_root: str,
    ) -> Artifact:
        """Persist the accepted design baseline for implementation and testing."""
        artifact = Artifact(
            id=f"artifact-frozen-design-{workitem.id}",
            project_id=project_id,
            workitem_id=workitem.id,
            agent_id=lead.id,
            kind="frozen_design_spec",
            title=f"Frozen Design Spec - {workitem.id}",
            content=(
                f"# Frozen Design Spec - {workitem.id}\n\n"
                "## Status\naccepted\n\n"
                "## Baseline\n"
                f"{draft}\n\n"
                "## Downstream Contract\n"
                "- 后续开发、测试必须以本冻结设计规格作为实现基线。\n"
                "- 如需改变架构、接口或页面方案，必须创建新的设计修订或返工 WorkItem。\n"
            ),
            source_backend="collaboration",
            parent_artifact_id=review_artifact.id,
            derived_from=self._build_derived_from(project_id, workitem.id),
            review_of=review_artifact.review_of,
            version=1,
            collaboration_session_id=f"collaboration-{workitem.id}",
        )
        persisted = self.artifact_store.save_markdown(artifact, project_root=project_root)
        self.state_store.add_artifact(project_id, persisted)
        self.state_store.add_event(project_id, f"冻结设计规格 {persisted.id} 已创建，来源 WorkItem={workitem.id}")
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

    def _safe_agent_id(self, agent_id: str) -> str:
        """Return an agent id that is safe to use in output file names."""
        return "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in agent_id)

    def _build_stream_callback(self, project_id: str):
        """Build a collaboration runtime stream callback."""
        def callback(channel: str, line: str) -> None:
            self.runtime_stream_store.append(project_id, channel, line)

        return callback
