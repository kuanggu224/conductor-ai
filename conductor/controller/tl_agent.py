"""Deterministic technical-lead control-plane agent."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from conductor.agents.team_planner import AgentTeamPlanner
from conductor.domain.models import AgentTeamPlan, DynamicAgentSpec, ProjectStatus, SharedProjectState, TLDecision, WorkItemStatus


class TechnicalLeadAgent:
    """Summarize project-level risk without taking over LeadController control."""

    def evaluate(self, state: SharedProjectState, action: str) -> TLDecision:
        """Return one TL decision snapshot for the current orchestration step."""
        failed = [item for item in state.workitems if item.status == WorkItemStatus.FAILED]
        pending = [item for item in state.workitems if item.status == WorkItemStatus.PENDING]
        running = [item for item in state.workitems if item.status == WorkItemStatus.RUNNING]
        blockers = list(state.blockers)
        human_action_required = bool(blockers) or state.project_status == ProjectStatus.BLOCKED or action == "human_hold"
        risk_level = self._risk_level(state, failed, blockers)
        recommendations = self._recommendations(action, failed, blockers, pending, running)
        created_at = datetime.now(timezone.utc).isoformat()
        return TLDecision(
            id=f"tl-{len(state.tl_decisions) + 1:04d}",
            project_id=state.project.id,
            stage=state.current_stage or "",
            action=action,
            risk_level=risk_level,
            summary=self._summary(action, risk_level, failed, blockers, pending, running),
            recommendations=recommendations,
            human_action_required=human_action_required,
            created_at=created_at,
        )

    def append_decision(self, state: SharedProjectState, action: str) -> SharedProjectState:
        """Append a TL decision to SharedProjectState."""
        decision = self.evaluate(state, action)
        return replace(state, tl_decisions=[*state.tl_decisions, decision])

    def plan_agent_team(
        self,
        state: SharedProjectState,
        planner: AgentTeamPlanner,
        *,
        trigger: str = "stage_start",
    ) -> AgentTeamPlan:
        """Make the final TL-owned dynamic Agent team decision for the current state."""
        candidate = planner.plan(state, trigger=trigger)
        stage = state.current_stage or ""
        stage_workitems = [item for item in state.workitems if item.stage == stage]
        failed = [item for item in stage_workitems if item.status == WorkItemStatus.FAILED]
        retried = [item for item in stage_workitems if item.retry_count > 0]
        blockers = list(state.blockers)
        history_risks = self._history_risk_roles(state)
        rework_evidence_items = self._rework_evidence_items(stage_workitems)
        integration_risk = self._integration_risk_detected(stage, candidate.agent_specs, stage_workitems)
        specs = list(candidate.agent_specs)
        reasons = ["TL reviewed current stage, work scope, risk, and retry state.", *candidate.reasons]
        summary_parts = [
            f"candidate_specs={len(candidate.agent_specs)}",
            f"failed={len(failed)}",
            f"retried={len(retried)}",
            f"blockers={len(blockers)}",
            f"history_risks={len(history_risks)}",
            f"rework_evidence_items={len(rework_evidence_items)}",
            f"integration_risk={int(integration_risk)}",
        ]

        if blockers or state.project_status == ProjectStatus.BLOCKED:
            return replace(
                candidate,
                agent_specs=[],
                reasons=[*reasons, "TL held dynamic team expansion because the project is blocked."],
                decision_source="tl_agent",
                decided_by="tl_agent",
                decision_summary="TL held team planning until blockers are resolved; " + ", ".join(summary_parts),
            )

        specs.extend(self._recovery_specs(planner, stage, failed, retried))
        specs.extend(self._history_risk_specs(planner, stage, history_risks))
        specs.extend(self._rework_evidence_specs(planner, stage, rework_evidence_items))
        specs.extend(self._integration_risk_specs(planner, stage, stage_workitems, integration_risk))
        specs = self._dedupe_specs(specs)
        return replace(
            candidate,
            complexity_level=self._tl_complexity_level(candidate.complexity_level, failed, retried, specs, rework_evidence_items),
            reasons=self._dedupe(
                [
                    *reasons,
                    *self._runtime_reasons(failed, retried),
                    *self._history_risk_reasons(history_risks),
                    *self._rework_evidence_reasons(rework_evidence_items),
                    *self._integration_risk_reasons(integration_risk),
                ]
            ),
            agent_specs=specs,
            decision_source="tl_agent",
            decided_by="tl_agent",
            decision_summary="TL accepted and adjusted dynamic team plan; " + ", ".join(summary_parts),
            fallback_reason="",
        )

    def _recovery_specs(
        self,
        planner: AgentTeamPlanner,
        stage: str,
        failed: list,
        retried: list,
    ) -> list[DynamicAgentSpec]:
        """Add runtime recovery reviewers when TL sees failed or retried work."""
        if not failed and not retried:
            return []
        kinds = list(dict.fromkeys([item.kind for item in [*failed, *retried]]))
        if stage == "development":
            return [
                planner.build_spec(
                    role="tester",
                    instance_id="failure_triage",
                    stage=stage,
                    mission="Review failed implementation evidence and define a focused retry checklist.",
                    reason="TL detected failed or retried development work requiring independent triage.",
                    scope="failure cause, retry checklist, regression risk, acceptance evidence",
                    mode="sequential_review",
                    workitem_kinds=kinds,
                )
            ]
        if stage == "testing":
            return [
                planner.build_spec(
                    role="solution_designer",
                    instance_id="release_risk",
                    stage=stage,
                    mission="Assess whether testing failures require scope change, rework, or release hold.",
                    reason="TL detected failed or retried testing work requiring release-risk review.",
                    scope="release risk, rework boundary, blocker escalation, acceptance impact",
                    mode="sequential_review",
                    workitem_kinds=kinds,
                )
            ]
        return []

    def _history_risk_roles(self, state: SharedProjectState) -> list[tuple[str, int, int]]:
        """Return roles whose recent aggregate execution history needs extra review."""
        totals: dict[str, tuple[int, int]] = {}
        for stats in state.agent_capability_stats:
            if not stats.role or stats.role == "unknown":
                continue
            completed, failed = totals.get(stats.role, (0, 0))
            totals[stats.role] = (completed + stats.completed_count, failed + stats.failed_count)
        risky: list[tuple[str, int, int]] = []
        for role, (completed, failed) in totals.items():
            total = completed + failed
            if failed >= 2 and total >= 3 and failed >= completed:
                risky.append((role, completed, failed))
        return sorted(risky, key=lambda item: (-item[2], item[0]))

    def _history_risk_specs(
        self,
        planner: AgentTeamPlanner,
        stage: str,
        history_risks: list[tuple[str, int, int]],
    ) -> list[DynamicAgentSpec]:
        """Add independent reviewers when TL sees weak historical role performance."""
        specs: list[DynamicAgentSpec] = []
        risky_roles = {role for role, _completed, _failed in history_risks}
        if stage == "development" and risky_roles & {"frontend_engineer", "backend_engineer"}:
            specs.append(
                planner.build_spec(
                    role="tester",
                    instance_id="history_quality_review",
                    stage=stage,
                    mission="Review implementation plans against prior role failure patterns before more work is assigned.",
                    reason="TL detected elevated historical implementation failure rate and added independent quality review.",
                    scope="historical failure patterns, implementation checklist, regression risk, validation evidence",
                    mode="sequential_review",
                    workitem_kinds=["ui_implementation", "api_implementation", "data_implementation", "generic_implementation"],
                )
            )
        if stage == "testing" and "tester" in risky_roles:
            specs.append(
                planner.build_spec(
                    role="solution_designer",
                    instance_id="test_strategy_review",
                    stage=stage,
                    mission="Review testing strategy because tester history shows repeated validation misses.",
                    reason="TL detected elevated historical tester failure rate and added strategy review.",
                    scope="test strategy, coverage gaps, release risk, acceptance traceability",
                    mode="sequential_review",
                    workitem_kinds=["acceptance_check", "automated_test", "api_validation", "ui_validation"],
                )
            )
        return specs

    def _rework_evidence_items(self, workitems: list) -> list:
        """Return current-stage rework WorkItems that carry explicit missing evidence targets."""
        return [
            item
            for item in workitems
            if (item.feedback_from or item.rework_of)
            and any(
                "Address missing testing checklist" in criterion
                or "produce evidence" in criterion
                for criterion in item.acceptance_criteria
            )
        ]

    def _rework_evidence_specs(
        self,
        planner: AgentTeamPlanner,
        stage: str,
        rework_items: list,
    ) -> list[DynamicAgentSpec]:
        """Add an independent verification seat for development rework with missing evidence targets."""
        if stage != "development" or not rework_items:
            return []
        return [
            planner.build_spec(
                role="tester",
                instance_id="rework_acceptance_guard",
                stage=stage,
                mission="Verify that development rework directly satisfies missing testing checklist evidence.",
                reason="TL detected feedback rework with explicit missing evidence acceptance criteria.",
                scope="missing checklist evidence, rework acceptance criteria, regression risk, retest readiness",
                mode="sequential_review",
                workitem_kinds=list(dict.fromkeys(item.kind for item in rework_items)),
            )
        ]

    def _integration_risk_detected(
        self,
        stage: str,
        candidate_specs: list[DynamicAgentSpec],
        workitems: list,
    ) -> bool:
        """Return whether parallel implementation needs an explicit integration boundary owner."""
        if stage != "development":
            return False
        roles = {spec.role for spec in candidate_specs if spec.parallel_safe}
        if not {"frontend_engineer", "backend_engineer"} <= roles:
            return False
        text = " ".join([item.kind for item in workitems] + [item.description for item in workitems]).lower()
        integration_terms = (
            "api",
            "backend",
            "frontend",
            "ui",
            "data",
            "storage",
            "validation",
            "contract",
            "schema",
            "接口",
            "前端",
            "后端",
            "数据",
            "存储",
            "校验",
            "契约",
        )
        return any(term in text for term in integration_terms)

    def _integration_risk_specs(
        self,
        planner: AgentTeamPlanner,
        stage: str,
        workitems: list,
        integration_risk: bool,
    ) -> list[DynamicAgentSpec]:
        """Add a solution-design guard when parallel frontend/backend work may diverge."""
        if not integration_risk:
            return []
        return [
            planner.build_spec(
                role="solution_designer",
                instance_id="integration_contract_guard",
                stage=stage,
                mission="Review API/UI/data integration boundaries before parallel implementation diverges.",
                reason="TL detected parallel frontend/backend implementation with integration contract risk.",
                scope="API contracts, UI data flow, validation boundaries, shared ownership handoff",
                mode="sequential_review",
                workitem_kinds=list(dict.fromkeys(item.kind for item in workitems)),
            )
        ]

    def _tl_complexity_level(
        self,
        candidate_level: str,
        failed: list,
        retried: list,
        specs: list[DynamicAgentSpec],
        rework_evidence_items: list | None = None,
    ) -> str:
        if failed or len(retried) >= 2 or len(specs) >= 5:
            return "complex"
        if rework_evidence_items or retried or len(specs) >= 3:
            return "standard"
        return candidate_level

    def _runtime_reasons(self, failed: list, retried: list) -> list[str]:
        reasons: list[str] = []
        if failed:
            reasons.append("TL detected failed WorkItems in the current stage.")
        if retried:
            reasons.append("TL detected retry history in the current stage.")
        return reasons

    def _history_risk_reasons(self, history_risks: list[tuple[str, int, int]]) -> list[str]:
        return [
            f"TL detected weak historical performance for {role}: completed={completed}, failed={failed}."
            for role, completed, failed in history_risks
        ]

    def _rework_evidence_reasons(self, rework_items: list) -> list[str]:
        if not rework_items:
            return []
        ids = ", ".join(item.id for item in rework_items)
        return [f"TL detected development rework with missing checklist evidence targets: {ids}."]

    def _integration_risk_reasons(self, integration_risk: bool) -> list[str]:
        if not integration_risk:
            return []
        return ["TL detected frontend/backend parallel implementation requiring an integration contract guard."]

    def _risk_level(self, state: SharedProjectState, failed: list, blockers: list[str]) -> str:
        if state.project_status == ProjectStatus.BLOCKED or blockers:
            return "high"
        if failed:
            return "medium"
        return "low"

    def _recommendations(self, action: str, failed: list, blockers: list[str], pending: list, running: list) -> list[str]:
        if action == "human_hold":
            return ["等待人类恢复、审批或覆盖当前控制动作后再继续推进。"]
        if blockers:
            return ["需要人类确认 blocker 后再继续推进。"]
        if failed:
            return ["优先处理失败 WorkItem，并检查是否需要返工或缩小范围。"]
        if action == "advance_stage":
            return ["进入下一阶段前确认上一阶段 artifact 已归档并可追溯。"]
        if action == "execute_workitem":
            return ["继续执行当前阶段就绪任务，并保持产物与验收标准可追溯。"]
        if running:
            return ["等待运行中任务归还结果，避免重复分配。"]
        if pending:
            return ["检查 pending 任务依赖是否满足。"]
        return ["保持当前流程推进，暂无人工接管需求。"]

    def _summary(self, action: str, risk_level: str, failed: list, blockers: list[str], pending: list, running: list) -> str:
        return (
            f"TL action={action}, risk={risk_level}, "
            f"pending={len(pending)}, running={len(running)}, failed={len(failed)}, blockers={len(blockers)}"
        )

    def _dedupe_specs(self, specs: list[DynamicAgentSpec]) -> list[DynamicAgentSpec]:
        result: list[DynamicAgentSpec] = []
        seen: set[str] = set()
        for spec in specs:
            if spec.agent_id in seen:
                continue
            seen.add(spec.agent_id)
            result.append(spec)
        return result

    def _dedupe(self, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            if value and value not in result:
                result.append(value)
        return result


__all__ = ["TechnicalLeadAgent"]
