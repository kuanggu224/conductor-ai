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
        specs = list(candidate.agent_specs)
        reasons = ["TL reviewed current stage, work scope, risk, and retry state.", *candidate.reasons]
        summary_parts = [
            f"candidate_specs={len(candidate.agent_specs)}",
            f"failed={len(failed)}",
            f"retried={len(retried)}",
            f"blockers={len(blockers)}",
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
        specs = self._dedupe_specs(specs)
        return replace(
            candidate,
            complexity_level=self._tl_complexity_level(candidate.complexity_level, failed, retried, specs),
            reasons=self._dedupe([*reasons, *self._runtime_reasons(failed, retried)]),
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

    def _tl_complexity_level(
        self,
        candidate_level: str,
        failed: list,
        retried: list,
        specs: list[DynamicAgentSpec],
    ) -> str:
        if failed or len(retried) >= 2 or len(specs) >= 5:
            return "complex"
        if retried or len(specs) >= 3:
            return "standard"
        return candidate_level

    def _runtime_reasons(self, failed: list, retried: list) -> list[str]:
        reasons: list[str] = []
        if failed:
            reasons.append("TL detected failed WorkItems in the current stage.")
        if retried:
            reasons.append("TL detected retry history in the current stage.")
        return reasons

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
