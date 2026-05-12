"""Deterministic technical-lead control-plane agent."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from conductor.domain.models import ProjectStatus, SharedProjectState, TLDecision, WorkItemStatus


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


__all__ = ["TechnicalLeadAgent"]
