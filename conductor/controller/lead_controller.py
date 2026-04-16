"""LeadController 最小规则驱动实现。"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from conductor.agents.registry import AgentRegistry
from conductor.collaboration.runner import CollaborationRunner
from conductor.domain.models import (
    Execution,
    Project,
    ProjectStatus,
    SharedProjectState,
    WorkItemStatus,
)
from conductor.execution.runner import Runner
from conductor.execution.planner import Planner
from conductor.execution.router import Router
from conductor.state.store import InMemoryStateStore
from conductor.workflow.template import GateDecision, WorkflowGateEvaluator, WorkflowTemplate


class LeadController:
    """负责初始化项目并推进最小 mock 流程。"""

    def __init__(
        self,
        workflow_template: WorkflowTemplate,
        state_store: InMemoryStateStore,
        runner: Runner,
        planner: Planner | None = None,
        registry: AgentRegistry | None = None,
        router: Router | None = None,
        gate_evaluator: WorkflowGateEvaluator | None = None,
        collaboration_runner: CollaborationRunner | None = None,
    ) -> None:
        self.workflow_template = workflow_template
        self.state_store = state_store
        self.runner = runner
        self.planner = planner or Planner()
        self.registry = registry or AgentRegistry()
        self.router = router or Router(self.registry)
        self.gate_evaluator = gate_evaluator or WorkflowGateEvaluator()
        self.collaboration_runner = collaboration_runner

    def initialize_project(self, requirement: str, project_root: str | None = None) -> SharedProjectState:
        """根据需求初始化项目状态。"""
        first_stage = self.workflow_template.get_first_stage()
        project = Project(
            id=f"project-{uuid4().hex[:8]}",
            goal=requirement,
            status=ProjectStatus.INITIALIZED,
            current_stage=first_stage.name,
            milestones=[stage.name for stage in self.workflow_template.stages],
            project_root=project_root or "",
        )
        initial_workitems = self.planner.plan_stage_workitems(first_stage, requirement)
        planned_roles = self.router.plan_roles_for_workitems(initial_workitems)
        activation_events = self._build_agent_activation_events(planned_roles, initial_workitems)
        state = SharedProjectState(
            project=project,
            project_status=project.status,
            current_stage=first_stage.name,
            workitems=initial_workitems,
            planned_roles=planned_roles,
            recent_events=[
                f"Project {project.id} 已创建",
                f"进入阶段 {first_stage.name}",
                f"规划角色: {', '.join(planned_roles)}",
                *activation_events,
                *[f"创建 WorkItem {workitem.id} ({workitem.kind})" for workitem in initial_workitems],
            ],
        )
        self.state_store.save_state(state)
        return state

    def decide_next_action(self, state: SharedProjectState) -> str:
        """根据当前 state 决定下一步动作。"""
        current_stage = state.current_stage
        stage_workitems = [item for item in state.workitems if item.stage == current_stage]
        if any(item.status == WorkItemStatus.PENDING for item in stage_workitems):
            return "execute_workitem"
        gate_decision = self.gate_evaluator.evaluate(current_stage, stage_workitems)
        if gate_decision == GateDecision.RETRY:
            return "retry_workitem"
        if gate_decision == GateDecision.ESCALATE:
            return "escalate_project"
        if gate_decision == GateDecision.REWORK:
            return "idle"
        if gate_decision == GateDecision.PASS:
            next_stage = self.workflow_template.get_next_stage(current_stage_name=current_stage or "")
            if next_stage is None:
                return "complete_project"
            return "advance_stage"
        return "idle"

    def advance(self, state: SharedProjectState) -> SharedProjectState:
        """推进一次控制循环并返回最新 state。"""
        action = self.decide_next_action(state)
        project_id = state.project.id
        self.state_store.add_event(project_id, f"LeadController 决策: {action}")
        latest_stage_workitems = [item for item in state.workitems if item.stage == state.current_stage]
        gate_decision = self.gate_evaluator.evaluate(state.current_stage, latest_stage_workitems)
        latest = self.state_store.get_state(project_id)
        latest = replace(
            latest,
            gate_history=[*latest.gate_history, f"{state.current_stage}:{gate_decision.value}"],
        )
        self.state_store.save_state(latest)
        self.state_store.add_event(project_id, f"GateDecision: {state.current_stage} -> {gate_decision.value}")

        if action == "execute_workitem":
            workitem = next(
                item for item in state.workitems if item.stage == state.current_stage and item.status == WorkItemStatus.PENDING
            )
            agent, route_decision = self.router.route(workitem)
            self.state_store.add_event(project_id, f"路由 WorkItem {workitem.id} -> {agent.role} ({agent.id})")
            execution = self.runner.run(project_id=project_id, workitem=workitem, agent=agent)
            latest_state = self.state_store.get_state(project_id)
            latest_state = self._append_execution(latest_state, execution, route_decision)
            self.state_store.save_state(latest_state)
            if self.collaboration_runner and self.collaboration_runner.should_collaborate(project_id, workitem):
                draft_artifact = self._find_artifact_for_workitem(project_id, workitem.id)
                if draft_artifact:
                    self.collaboration_runner.run_review_loop(project_id, workitem, draft_artifact)
                    latest_state = self.state_store.get_state(project_id)
            return latest_state

        if action == "retry_workitem":
            failed_workitem = next(
                item for item in state.workitems if item.stage == state.current_stage and item.status == WorkItemStatus.FAILED
            )
            retry_count = failed_workitem.retry_count + 1
            latest_state = self.state_store.update_workitem(
                project_id=project_id,
                workitem_id=failed_workitem.id,
                status=WorkItemStatus.PENDING,
                retry_count=retry_count,
            )
            latest_state = self.state_store.add_event(
                project_id,
                f"WorkItem {failed_workitem.id} 进入重试，第 {retry_count} 次",
            )
            return latest_state

        if action == "advance_stage":
            next_stage = self.workflow_template.get_next_stage(current_stage_name=state.current_stage or "")
            if next_stage is None:
                raise RuntimeError("advance_stage 时未找到下一阶段")
            latest = self.state_store.get_state(project_id)
            new_workitems = self.planner.plan_stage_workitems(next_stage, latest.project.goal)
            updated_project = replace(
                latest.project,
                current_stage=next_stage.name,
                status=ProjectStatus.IN_PROGRESS,
            )
            stage_roles = self.router.plan_roles_for_workitems(new_workitems)
            merged_roles = list(dict.fromkeys([*latest.planned_roles, *stage_roles]))
            activation_events = self._build_agent_activation_events(stage_roles, new_workitems)
            latest_state = replace(
                latest,
                project=updated_project,
                project_status=ProjectStatus.IN_PROGRESS,
                current_stage=next_stage.name,
                workitems=[*latest.workitems, *new_workitems],
                planned_roles=merged_roles,
                recent_events=[
                    *latest.recent_events,
                    f"进入阶段 {next_stage.name}",
                    f"规划角色: {', '.join(stage_roles)}",
                    *activation_events,
                    *[f"创建 WorkItem {workitem.id} ({workitem.kind})" for workitem in new_workitems],
                ],
            )
            self.state_store.save_state(latest_state)
            return latest_state

        if action == "complete_project":
            latest = self.state_store.get_state(project_id)
            completed_project = replace(latest.project, status=ProjectStatus.COMPLETED)
            completed_state = replace(
                latest,
                project=completed_project,
                project_status=ProjectStatus.COMPLETED,
                recent_events=[*latest.recent_events, "Project 已完成"],
            )
            self.state_store.save_state(completed_state)
            return completed_state

        if action == "escalate_project":
            latest = self.state_store.get_state(project_id)
            failed_workitems = [
                item for item in latest.workitems if item.stage == latest.current_stage and item.status == WorkItemStatus.FAILED
            ]
            blocker = f"阶段 {latest.current_stage} 存在超过重试次数的失败 WorkItem: {', '.join(item.id for item in failed_workitems)}"
            blocked_project = replace(latest.project, status=ProjectStatus.BLOCKED)
            blocked_state = replace(
                latest,
                project=blocked_project,
                project_status=ProjectStatus.BLOCKED,
                blockers=[*latest.blockers, blocker],
                recent_events=[*latest.recent_events, f"升级处理: {blocker}"],
            )
            self.state_store.save_state(blocked_state)
            return blocked_state

        return self.state_store.get_state(project_id)

    def _append_execution(
        self,
        state: SharedProjectState,
        execution: Execution,
        route_decision,
    ) -> SharedProjectState:
        """把执行记录和路由记录追加到 SharedProjectState。"""
        return replace(
            state,
            executions=[*state.executions, execution],
            route_decisions=[*state.route_decisions, route_decision],
        )

    def _find_artifact_for_workitem(self, project_id: str, workitem_id: str):
        """查找 WorkItem 对应的主产物。"""
        state = self.state_store.get_state(project_id)
        for artifact in reversed(state.artifacts):
            if artifact.workitem_id == workitem_id and artifact.kind != "collaboration_review":
                return artifact
        return None

    def _build_agent_activation_events(self, roles: list[str], workitems) -> list[str]:
        """构建 Agent 激活事件，解释为什么激活这些角色。"""
        events: list[str] = []
        for role in roles:
            agent = self.registry.get_agent_by_role(role)
            related_kinds = [
                workitem.kind
                for workitem in workitems
                if self._role_for_workitem_kind(workitem.kind) == role
            ]
            reason = ", ".join(dict.fromkeys(related_kinds)) or "当前阶段需要该角色"
            events.append(f"激活 Agent {agent.id} ({role})，原因: {reason}")
        return events

    def _role_for_workitem_kind(self, kind: str) -> str | None:
        """根据 WorkItem 类型推断默认角色。"""
        if kind in {"design_overview", "ui_design", "api_design", "test_design"}:
            return "designer"
        if kind in {"api_implementation", "data_implementation", "generic_implementation"}:
            return "backend_engineer"
        if kind == "ui_implementation":
            return "frontend_engineer"
        if kind in {"acceptance_check", "automated_test", "api_validation", "ui_validation"}:
            return "tester"
        return None
