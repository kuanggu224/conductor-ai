"""Lead controller for project orchestration."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from conductor.agents.registry import AgentRegistry
from conductor.agents.team_planner import AgentTeamPlanner
from conductor.collaboration.models import CollaborationStatus
from conductor.collaboration.runner import CollaborationRunner
from conductor.control.human import HumanControlService
from conductor.controller.tl_agent import TechnicalLeadAgent
from conductor.context.builder import ContextBuilder
from conductor.design_quality import evaluate_design_document
from conductor.domain.models import (
    AgentActivation,
    AgentCapabilityStats,
    AgentTeamPlan,
    DynamicAgentSpec,
    Execution,
    ExecutionStatus,
    Project,
    ProjectStatus,
    SharedProjectState,
    TaskAssignment,
    TaskAssignmentStatus,
    WorkItem,
    WorkItemStatus,
)
from conductor.execution.planner import Planner
from conductor.execution.failure_policy import parse_retryable_failure
from conductor.execution.router import Router
from conductor.execution.runner import Runner
from conductor.requirement_benchmark import build_requirement_case_from_text, evaluate_requirement_document
from conductor.state.store import InMemoryStateStore
from conductor.testing.failure_feedback import build_testing_failure_feedback
from conductor.workflow.template import GateDecision, WorkflowGateEvaluator, WorkflowTemplate


class LeadController:
    """Advance a project through workflow stages using explicit shared state."""

    MAX_REQUIREMENT_REWORK_DEPTH = 1
    MAX_DESIGN_REWORK_DEPTH = 1
    MAX_TEST_FEEDBACK_REWORK_CYCLES = 2
    MAX_FAILURE_REWORK_DEPTH = 1

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
        self.context_builder = ContextBuilder()
        self.tl_agent = TechnicalLeadAgent()
        self.human_control = HumanControlService(state_store)
        self.agent_team_planner = AgentTeamPlanner()

    def initialize_project(self, requirement: str, project_root: str | None = None) -> SharedProjectState:
        """Initialize a project and register its first-stage tasks."""
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
        task_assignments = self._build_task_assignments(initial_workitems)
        agent_activations = self._build_agent_activations(first_stage.name, planned_roles, initial_workitems)
        activation_events = self._build_agent_activation_events(agent_activations)
        state = SharedProjectState(
            project=project,
            project_status=project.status,
            current_stage=first_stage.name,
            workitems=initial_workitems,
            task_assignments=task_assignments,
            agent_activations=agent_activations,
            planned_roles=planned_roles,
            recent_events=[
                f"Project {project.id} 已创建",
                f"进入阶段 {first_stage.name}",
                f"规划角色: {', '.join(planned_roles)}",
                f"任务中心登记 {len(task_assignments)} 个 WorkItem",
                *activation_events,
                *[f"创建 WorkItem {workitem.id} ({workitem.kind})" for workitem in initial_workitems],
            ],
        )
        state = self._apply_agent_team_plan_to_state(state, trigger="project_initialize")
        self.state_store.save_state(state)
        return state

    def decide_next_action(self, state: SharedProjectState) -> str:
        """Decide the next controller action from explicit project state."""
        if self.human_control.controller_hold_reason(state):
            return "human_hold"
        current_stage = state.current_stage
        stage_workitems = [item for item in state.workitems if item.stage == current_stage]
        pending_items = [item for item in stage_workitems if item.status == WorkItemStatus.PENDING]
        if any(self._has_failed_dependency(state, item) for item in pending_items):
            return "block_dependency"
        if any(self._is_ready_to_execute(state, item) for item in pending_items):
            return "execute_workitem"
        if pending_items:
            return "wait_for_dependencies"

        gate_decision = self.gate_evaluator.evaluate(current_stage, stage_workitems)
        if gate_decision == GateDecision.RETRY:
            if self._has_non_retryable_failure(stage_workitems):
                return "escalate_project"
            return "retry_workitem"
        if gate_decision == GateDecision.ESCALATE:
            if self._can_create_failure_rework(state):
                return "failure_rework"
            if current_stage == "testing" and self._can_create_feedback_rework(state):
                return "feedback_rework"
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
        """Advance one orchestration step."""
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
        latest = self.tl_agent.append_decision(latest, action)
        if action in {"retry_workitem", "failure_rework", "feedback_rework", "escalate_project"}:
            latest = self._apply_agent_team_plan_to_state(latest, trigger="runtime_risk")
        self.state_store.save_state(latest)
        self.state_store.add_event(project_id, f"TLAgent 决策: {latest.tl_decisions[-1].summary}")
        self.state_store.add_event(project_id, f"GateDecision: {state.current_stage} -> {gate_decision.value}")

        latest = self.state_store.get_state(project_id)
        if self._tl_requires_human_gate(latest, action):
            return self.human_control.request_approval(
                project_id,
                actor="tl_agent",
                reason=f"TL requires human approval before controller action `{action}`.",
                payload={"controller_action": action, "stage": latest.current_stage or ""},
            )

        if action == "execute_workitem":
            return self._execute_next_ready_workitem(state)
        if action == "retry_workitem":
            return self._retry_failed_workitem(state)
        if action == "advance_stage":
            return self._advance_stage(state)
        if action == "complete_project":
            return self._complete_project(project_id)
        if action == "escalate_project":
            return self._escalate_project(project_id)
        if action == "feedback_rework":
            return self._create_feedback_rework(project_id)
        if action == "failure_rework":
            return self._create_failure_rework(project_id)
        if action == "block_dependency":
            return self._block_dependency(project_id)
        if action == "wait_for_dependencies":
            return self.state_store.add_event(project_id, "等待依赖 WorkItem 完成")
        if action == "human_hold":
            latest = self.state_store.get_state(project_id)
            hold_reason = self.human_control.controller_hold_reason(latest) or "human control hold"
            return self.state_store.add_event(project_id, f"HumanControl: controller hold - {hold_reason}")
        return self.state_store.get_state(project_id)

    def _tl_requires_human_gate(self, state: SharedProjectState, action: str) -> bool:
        """Return whether TL must pause before a high-risk automatic action."""
        gated_actions = {"escalate_project"}
        if action not in gated_actions:
            return False
        latest_decision = state.tl_decisions[-1] if state.tl_decisions else None
        if latest_decision is not None and not latest_decision.human_action_required:
            return False
        return not self.human_control.has_clearance(state, action, state.current_stage or "")

    def _execute_next_ready_workitem(self, state: SharedProjectState) -> SharedProjectState:
        project_id = state.project.id
        workitem = next(
            item
            for item in state.workitems
            if item.stage == state.current_stage and self._is_ready_to_execute(state, item)
        )
        agent, route_decision = self.router.route(workitem)
        self.state_store.add_event(project_id, f"路由 WorkItem {workitem.id} -> {agent.role} ({agent.id})")
        self._claim_assignment(project_id, workitem, agent.id)
        execution = self.runner.run(project_id=project_id, workitem=workitem, agent=agent)
        latest_state = self.state_store.get_state(project_id)
        latest_state = self._append_execution(latest_state, execution, route_decision)
        self.state_store.save_state(latest_state)
        if execution.status != ExecutionStatus.SUCCESS:
            return self._return_assignment(project_id, workitem.id, execution)
        if self.collaboration_runner and self.collaboration_runner.should_collaborate(project_id, workitem):
            draft_artifact = self._find_artifact_for_workitem(project_id, workitem.id)
            if draft_artifact:
                self._ensure_collaboration_agent_activations(project_id, workitem)
                collaboration = self.collaboration_runner.run_review_loop(project_id, workitem, draft_artifact)
                if collaboration.status != CollaborationStatus.ACCEPTED:
                    return self._fail_workitem_on_collaboration_gate(project_id, workitem.id, collaboration)
                self.state_store.update_workitem(
                    project_id,
                    workitem.id,
                    self._workitem_status(self.state_store.get_state(project_id), workitem.id) or WorkItemStatus.DONE,
                    collaboration_session_id=collaboration.id,
                )
        return self._return_assignment(project_id, workitem.id, execution)

    def _fail_workitem_on_collaboration_gate(
        self,
        project_id: str,
        workitem_id: str,
        collaboration,
    ) -> SharedProjectState:
        """Fail a WorkItem when collaboration cannot approve the draft."""
        reason = (
            "failure_type=validation_failed; retryable=false; "
            f"summary=collaboration {collaboration.id} ended with {collaboration.status.value}"
        )
        failure_summary = self._collaboration_failure_summary(project_id, workitem_id, collaboration)
        latest = self.state_store.update_workitem(
            project_id,
            workitem_id,
            WorkItemStatus.FAILED,
            blocked_reason=reason,
            failure_type="validation_failed",
            retryable=False,
            failure_summary=failure_summary,
            collaboration_session_id=collaboration.id,
        )
        failed_workitem = next((item for item in latest.workitems if item.id == workitem_id), None)
        if failed_workitem and failed_workitem.kind == "requirement_spec":
            return self._create_requirement_rework_from_collaboration_gate(
                project_id=project_id,
                failed_workitem=failed_workitem,
                collaboration_id=collaboration.id,
                reason=reason,
            )
        if failed_workitem and failed_workitem.kind == "design_overview":
            return self._create_design_rework_from_collaboration_gate(
                project_id=project_id,
                failed_workitem=failed_workitem,
                collaboration_id=collaboration.id,
                reason=reason,
            )
        assignment = self._assignment_for(latest, workitem_id)
        if assignment:
            output_artifact_ids = [
                artifact.id for artifact in latest.artifacts if artifact.workitem_id == workitem_id
            ]
            self.state_store.upsert_task_assignment(
                project_id,
                replace(
                    assignment,
                    status=TaskAssignmentStatus.FAILED,
                    output_artifact_ids=output_artifact_ids,
                    blocked_reason=reason,
                    result_summary=f"Collaboration gate failed: {collaboration.status.value}",
                ),
            )
        self.state_store.add_event(
            project_id,
            f"需求/设计协作门禁未通过: {collaboration.id} -> {collaboration.status.value}",
        )
        return self.state_store.get_state(project_id)

    def _collaboration_failure_summary(self, project_id: str, workitem_id: str, collaboration) -> str:
        """Return actionable gate failure details for rework prompts and logs."""
        state = self.state_store.get_state(project_id)
        workitem = next((item for item in state.workitems if item.id == workitem_id), None)
        base = f"Collaboration gate did not accept requirement/design draft: {collaboration.status.value}"
        if workitem is None:
            return base
        latest_draft = collaboration.draft_versions[-1].content if collaboration.draft_versions else ""
        if not latest_draft:
            return base
        if workitem.kind == "design_overview":
            evaluation = evaluate_design_document(latest_draft)
            if evaluation.passed:
                return base
            findings = "; ".join(evaluation.findings) or "design quality gate failed"
            return f"{base}. Design quality findings: {findings}"
        if workitem.kind != "requirement_spec":
            return base
        case = build_requirement_case_from_text(project_id, state.project.goal, name="project_requirement")
        evaluation = evaluate_requirement_document(latest_draft, case)
        if evaluation.passed:
            return base
        findings = "; ".join(evaluation.findings) or "requirement quality gate failed"
        return f"{base}. Requirement quality findings: {findings}"

    def _create_design_rework_from_collaboration_gate(
        self,
        *,
        project_id: str,
        failed_workitem: WorkItem,
        collaboration_id: str,
        reason: str,
    ) -> SharedProjectState:
        """Create a design rework WorkItem instead of blocking on weak design output."""
        latest = self.state_store.get_state(project_id)
        if self._has_existing_design_rework(latest, failed_workitem.id):
            return self.state_store.get_state(project_id)
        rework_depth = self._design_rework_depth(latest, failed_workitem)
        if rework_depth >= self.MAX_DESIGN_REWORK_DEPTH:
            return self._block_design_rework_exhausted(
                project_id=project_id,
                failed_workitem=failed_workitem,
                collaboration_id=collaboration_id,
                reason=reason,
                rework_depth=rework_depth,
            )
        output_artifact_ids = [
            artifact.id for artifact in latest.artifacts if artifact.workitem_id == failed_workitem.id
        ]
        rework_item = WorkItem(
            id=self._next_workitem_id(latest, []),
            description=(
                f"根据设计协作/质量门禁反馈返工 `{failed_workitem.id}`：{failed_workitem.description}\n\n"
                f"门禁原因: {failed_workitem.failure_summary or reason}\n"
                "要求：补齐需求追踪、范围边界、架构/模块方案、数据与状态、验收测试、风险假设，"
                "并重新接受多角色评审后生成冻结设计规格。"
            ),
            stage="design",
            kind="design_overview",
            dependencies=list(failed_workitem.dependencies),
            input_artifact_ids=output_artifact_ids,
            acceptance_criteria=[
                f"修复设计门禁失败 {failed_workitem.id}",
                "产出可冻结的总体设计规格",
                "设计质量评分达到阈值并通过协作评审",
                "不扩展冻结需求定义之外的范围",
            ],
            feedback_from=[failed_workitem.id],
            rework_of=failed_workitem.id,
        )
        assignments = self._build_task_assignments([rework_item], latest)
        updated_workitems = [
            replace(item, status=WorkItemStatus.DONE, blocked_reason="设计门禁失败已转入返工 WorkItem")
            if item.id == failed_workitem.id
            else item
            for item in latest.workitems
        ]
        latest_state = replace(
            latest,
            workitems=[*updated_workitems, rework_item],
            task_assignments=[*latest.task_assignments, *assignments],
            planned_roles=list(dict.fromkeys([*latest.planned_roles, "designer"])),
            recent_events=[
                *latest.recent_events,
                f"设计门禁返工: {failed_workitem.id} -> {rework_item.id}, collaboration={collaboration_id}",
            ],
        )
        self.state_store.save_state(latest_state)
        assignment = self._assignment_for(latest_state, failed_workitem.id)
        if assignment:
            failed_assignment = replace(
                assignment,
                status=TaskAssignmentStatus.FAILED,
                output_artifact_ids=output_artifact_ids,
                blocked_reason=reason,
                result_summary=f"Design gate rework created: {rework_item.id}",
            )
            self.state_store.upsert_task_assignment(
                project_id,
                self._assignment_with_transition(
                    failed_assignment,
                    action="return",
                    status=TaskAssignmentStatus.FAILED,
                    reason=reason,
                    details={
                        "result_summary": f"Design gate rework created: {rework_item.id}",
                        "output_artifact_ids": output_artifact_ids,
                    },
                ),
            )
        return self.state_store.get_state(project_id)

    def _block_design_rework_exhausted(
        self,
        *,
        project_id: str,
        failed_workitem: WorkItem,
        collaboration_id: str,
        reason: str,
        rework_depth: int,
    ) -> SharedProjectState:
        """Block the project when design rework keeps failing."""
        latest = self.state_store.get_state(project_id)
        output_artifact_ids = [
            artifact.id for artifact in latest.artifacts if artifact.workitem_id == failed_workitem.id
        ]
        blocker = (
            f"设计门禁连续返工仍未通过: {failed_workitem.id}, "
            f"depth={rework_depth}, max={self.MAX_DESIGN_REWORK_DEPTH}, "
            f"collaboration={collaboration_id}"
        )
        assignment = self._assignment_for(latest, failed_workitem.id)
        assignments = latest.task_assignments
        if assignment:
            assignments = [
                self._assignment_with_transition(
                    replace(
                        item,
                        status=TaskAssignmentStatus.FAILED,
                        output_artifact_ids=output_artifact_ids,
                        blocked_reason=reason,
                        result_summary=blocker,
                    ),
                    action="return",
                    status=TaskAssignmentStatus.FAILED,
                    reason=reason,
                    details={"result_summary": blocker, "output_artifact_ids": output_artifact_ids},
                )
                if item.workitem_id == failed_workitem.id
                else item
                for item in latest.task_assignments
            ]
        blocked_state = replace(
            latest,
            project=replace(latest.project, status=ProjectStatus.BLOCKED),
            project_status=ProjectStatus.BLOCKED,
            task_assignments=assignments,
            blockers=[*latest.blockers, blocker],
            recent_events=[*latest.recent_events, f"设计门禁返工上限触发: {blocker}"],
        )
        self.state_store.save_state(blocked_state)
        return blocked_state

    def _create_requirement_rework_from_collaboration_gate(
        self,
        project_id: str,
        failed_workitem: WorkItem,
        collaboration_id: str,
        reason: str,
    ) -> SharedProjectState:
        """Create a requirement rework WorkItem instead of letting a weak requirement pass."""
        latest = self.state_store.get_state(project_id)
        if self._has_existing_requirement_rework(latest, failed_workitem.id):
            return self.state_store.get_state(project_id)
        rework_depth = self._requirement_rework_depth(latest, failed_workitem)
        if rework_depth >= self.MAX_REQUIREMENT_REWORK_DEPTH:
            return self._block_requirement_rework_exhausted(
                project_id=project_id,
                failed_workitem=failed_workitem,
                collaboration_id=collaboration_id,
                reason=reason,
                rework_depth=rework_depth,
            )
        output_artifact_ids = [
            artifact.id for artifact in latest.artifacts if artifact.workitem_id == failed_workitem.id
        ]
        rework_item = WorkItem(
            id=self._next_workitem_id(latest, []),
            description=(
                f"根据需求协作/质量门禁反馈返工 `{failed_workitem.id}`：{failed_workitem.description}\n\n"
                f"门禁原因: {failed_workitem.failure_summary or reason}\n"
                "要求：补齐用户目标、范围边界、非目标、验收标准、风险假设和待确认问题，并重新接受多角色评审。"
            ),
            stage="requirement",
            kind="requirement_spec",
            input_artifact_ids=output_artifact_ids,
            acceptance_criteria=[
                f"修复需求门禁失败 {failed_workitem.id}",
                "产出可冻结的需求规格",
                "需求质量评分达到阈值并通过协作评审",
            ],
            feedback_from=[failed_workitem.id],
            rework_of=failed_workitem.id,
        )
        assignments = self._build_task_assignments([rework_item], latest)
        updated_workitems = [
            replace(item, status=WorkItemStatus.DONE, blocked_reason="需求门禁失败已转入返工 WorkItem")
            if item.id == failed_workitem.id
            else item
            for item in latest.workitems
        ]
        latest_state = replace(
            latest,
            workitems=[*updated_workitems, rework_item],
            task_assignments=[*latest.task_assignments, *assignments],
            planned_roles=list(dict.fromkeys([*latest.planned_roles, "requirement_designer"])),
            recent_events=[
                *latest.recent_events,
                f"需求门禁返工: {failed_workitem.id} -> {rework_item.id}, collaboration={collaboration_id}",
            ],
        )
        self.state_store.save_state(latest_state)
        assignment = self._assignment_for(latest_state, failed_workitem.id)
        if assignment:
            failed_assignment = replace(
                assignment,
                status=TaskAssignmentStatus.FAILED,
                output_artifact_ids=output_artifact_ids,
                blocked_reason=reason,
                result_summary=f"Requirement gate rework created: {rework_item.id}",
            )
            self.state_store.upsert_task_assignment(
                project_id,
                self._assignment_with_transition(
                    failed_assignment,
                    action="return",
                    status=TaskAssignmentStatus.FAILED,
                    reason=reason,
                    details={
                        "result_summary": f"Requirement gate rework created: {rework_item.id}",
                        "output_artifact_ids": output_artifact_ids,
                    },
                ),
        )
        return self.state_store.get_state(project_id)

    def _block_requirement_rework_exhausted(
        self,
        *,
        project_id: str,
        failed_workitem: WorkItem,
        collaboration_id: str,
        reason: str,
        rework_depth: int,
    ) -> SharedProjectState:
        """Block the project when requirement rework keeps failing."""
        latest = self.state_store.get_state(project_id)
        output_artifact_ids = [
            artifact.id for artifact in latest.artifacts if artifact.workitem_id == failed_workitem.id
        ]
        blocker = (
            f"需求门禁连续返工仍未通过: {failed_workitem.id}, "
            f"depth={rework_depth}, max={self.MAX_REQUIREMENT_REWORK_DEPTH}, "
            f"collaboration={collaboration_id}"
        )
        assignment = self._assignment_for(latest, failed_workitem.id)
        assignments = latest.task_assignments
        if assignment:
            assignments = [
                self._assignment_with_transition(
                    replace(
                        item,
                        status=TaskAssignmentStatus.FAILED,
                        output_artifact_ids=output_artifact_ids,
                        blocked_reason=reason,
                        result_summary=blocker,
                    ),
                    action="return",
                    status=TaskAssignmentStatus.FAILED,
                    reason=reason,
                    details={"result_summary": blocker, "output_artifact_ids": output_artifact_ids},
                )
                if item.workitem_id == failed_workitem.id
                else item
                for item in latest.task_assignments
            ]
        blocked_state = replace(
            latest,
            project=replace(latest.project, status=ProjectStatus.BLOCKED),
            project_status=ProjectStatus.BLOCKED,
            task_assignments=assignments,
            blockers=[*latest.blockers, blocker],
            recent_events=[*latest.recent_events, f"需求门禁返工上限触发: {blocker}"],
        )
        self.state_store.save_state(blocked_state)
        return blocked_state

    def _has_existing_requirement_rework(self, state: SharedProjectState, failed_workitem_id: str) -> bool:
        """Return whether a requirement WorkItem already has a rework child."""
        return any(
            item.stage == "requirement"
            and item.kind == "requirement_spec"
            and failed_workitem_id in item.feedback_from
            for item in state.workitems
        )

    def _has_existing_design_rework(self, state: SharedProjectState, failed_workitem_id: str) -> bool:
        """Return whether a design WorkItem already has a rework child."""
        return any(
            item.stage == "design"
            and item.kind == "design_overview"
            and failed_workitem_id in item.feedback_from
            for item in state.workitems
        )

    def _requirement_rework_depth(self, state: SharedProjectState, workitem: WorkItem) -> int:
        """Return how many requirement rework hops led to this WorkItem."""
        by_id = {item.id: item for item in state.workitems}
        depth = 0
        current = workitem
        seen: set[str] = set()
        while current.rework_of and current.rework_of not in seen:
            seen.add(current.id)
            parent = by_id.get(current.rework_of)
            if parent is None:
                break
            depth += 1
            current = parent
        return depth

    def _design_rework_depth(self, state: SharedProjectState, workitem: WorkItem) -> int:
        """Return how many design rework hops led to this WorkItem."""
        return self._requirement_rework_depth(state, workitem)

    def _retry_failed_workitem(self, state: SharedProjectState) -> SharedProjectState:
        project_id = state.project.id
        failed_workitem = next(
            item for item in state.workitems if item.stage == state.current_stage and item.status == WorkItemStatus.FAILED
        )
        retry_count = failed_workitem.retry_count + 1
        self.state_store.add_event(
            project_id,
            (
                f"失败策略: WorkItem {failed_workitem.id} "
                f"failure_type={failed_workitem.failure_type or 'unknown'}, "
                f"retryable={failed_workitem.retryable}, summary={failed_workitem.failure_summary or '-'}"
            ),
        )
        latest_state = self.state_store.update_workitem(
            project_id=project_id,
            workitem_id=failed_workitem.id,
            status=WorkItemStatus.PENDING,
            retry_count=retry_count,
            blocked_reason=None,
            failure_type="",
            retryable=True,
            failure_summary="",
        )
        assignment = self._assignment_for(latest_state, failed_workitem.id)
        if assignment:
            self.state_store.upsert_task_assignment(
                project_id,
                replace(assignment, status=TaskAssignmentStatus.QUEUED, blocked_reason=None),
            )
        return self.state_store.add_event(
            project_id,
            f"WorkItem {failed_workitem.id} 进入重试，第 {retry_count} 次",
        )

    def _advance_stage(self, state: SharedProjectState) -> SharedProjectState:
        project_id = state.project.id
        next_stage = self.workflow_template.get_next_stage(current_stage_name=state.current_stage or "")
        if next_stage is None:
            raise RuntimeError("advance_stage 时未找到下一阶段")
        latest = self.state_store.get_state(project_id)
        planning_requirement = self._planning_requirement_text(latest)
        new_workitems = self.planner.plan_stage_workitems(next_stage, planning_requirement)
        new_workitems = self._dedupe_new_workitem_ids(latest, new_workitems)
        new_workitems = self._apply_pending_test_scope(latest, next_stage.name, new_workitems)
        new_workitems = self._attach_stage_dependencies(new_workitems, latest.workitems)
        new_workitems = self._attach_stage_input_artifacts(new_workitems, latest)
        new_workitems = self._attach_stage_handoff_contracts(new_workitems, latest)
        new_assignments = self._build_task_assignments(new_workitems, latest)
        updated_project = replace(
            latest.project,
            current_stage=next_stage.name,
            status=ProjectStatus.IN_PROGRESS,
        )
        stage_roles = self.router.plan_roles_for_workitems(new_workitems)
        merged_roles = list(dict.fromkeys([*latest.planned_roles, *stage_roles]))
        existing_activation_roles = {activation.role for activation in latest.agent_activations}
        new_activation_roles = [role for role in stage_roles if role not in existing_activation_roles]
        new_activations = self._build_agent_activations(next_stage.name, new_activation_roles, new_workitems)
        activation_events = self._build_agent_activation_events(new_activations)
        latest_state = replace(
            latest,
            project=updated_project,
            project_status=ProjectStatus.IN_PROGRESS,
            current_stage=next_stage.name,
            workitems=[*latest.workitems, *new_workitems],
            task_assignments=[*latest.task_assignments, *new_assignments],
            agent_activations=[*latest.agent_activations, *new_activations],
            planned_roles=merged_roles,
            pending_test_scope=[] if next_stage.name == "testing" else latest.pending_test_scope,
            recent_events=[
                *latest.recent_events,
                f"进入阶段 {next_stage.name}",
                *self._test_scope_events(latest, next_stage.name, new_workitems),
                f"规划角色: {', '.join(stage_roles)}",
                f"任务中心登记 {len(new_assignments)} 个 WorkItem",
                *activation_events,
                *[f"创建 WorkItem {workitem.id} ({workitem.kind})" for workitem in new_workitems],
            ],
        )
        latest_state = self._apply_agent_team_plan_to_state(latest_state, trigger="stage_start")
        self.state_store.save_state(latest_state)
        return latest_state

    def _complete_project(self, project_id: str) -> SharedProjectState:
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

    def _planning_requirement_text(self, state: SharedProjectState) -> str:
        """Prefer the frozen requirement baseline when planning downstream stages."""
        frozen_requirement = next(
            (artifact for artifact in reversed(state.artifacts) if artifact.kind == "frozen_requirement_spec"),
            None,
        )
        return frozen_requirement.content if frozen_requirement is not None else state.project.goal

    def _escalate_project(self, project_id: str) -> SharedProjectState:
        latest = self.state_store.get_state(project_id)
        failed_workitems = [
            item for item in latest.workitems if item.stage == latest.current_stage and item.status == WorkItemStatus.FAILED
        ]
        details = ", ".join(
            f"{item.id}({item.failure_type or 'unknown'}, retryable={item.retryable})"
            for item in failed_workitems
        )
        blocker = f"阶段 {latest.current_stage} 存在无法继续自动恢复的失败 WorkItem: {details}"
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

    def _create_feedback_rework(self, project_id: str) -> SharedProjectState:
        """Create development rework tasks from exhausted testing failures."""
        latest = self.state_store.get_state(project_id)
        if self._feedback_rework_limit_reached(latest):
            return self._escalate_project(project_id)
        failed_tests = [
            item
            for item in latest.workitems
            if item.stage == "testing"
            and item.status == WorkItemStatus.FAILED
            and item.retry_count >= item.max_retries
            and not self._has_existing_feedback_rework(latest, item.id)
        ]
        if not failed_tests:
            return self._escalate_project(project_id)

        rework_items: list[WorkItem] = []
        pending_test_scope: list[str] = []
        for failed in failed_tests:
            target_kind = self._feedback_target_kind(failed)
            pending_test_scope.extend(self._feedback_test_scope(failed))
            failed_artifact_ids = self._workitem_artifact_ids(latest, failed.id)
            original_workitem_id = self._primary_development_workitem_id(latest, target_kind)
            original_artifact_ids = self._workitem_artifact_ids(latest, original_workitem_id) if original_workitem_id else []
            input_artifact_ids = list(dict.fromkeys([*failed_artifact_ids, *original_artifact_ids]))
            failed_artifacts = [artifact for artifact in latest.artifacts if artifact.id in failed_artifact_ids]
            failed_executions = [execution for execution in latest.executions if execution.workitem_id == failed.id]
            feedback = build_testing_failure_feedback(failed, failed_artifacts, failed_executions)
            checklist_acceptance_criteria = self._feedback_rework_acceptance_criteria(feedback)
            rework_items.append(
                WorkItem(
                    id=self._next_workitem_id(latest, [*rework_items]),
                    description=(
                        f"根据测试失败回流修复 `{failed.id}`: {failed.description}\n\n"
                        f"失败摘要: {(failed.failure_summary or failed.result or failed.blocked_reason or '无详细结果')[:500]}\n"
                        f"原始实现 WorkItem: {original_workitem_id or '未找到'}\n"
                        "要求：只修复测试反馈指向的问题，保持冻结需求和既有设计边界，不扩展新功能。\n\n"
                        f"{feedback.render_markdown()}"
                    ),
                    stage="development",
                    kind=target_kind,
                    dependencies=self._development_dependency_ids_for_feedback(latest, target_kind),
                    acceptance_criteria=[
                        f"修复测试反馈 {failed.id}",
                        *checklist_acceptance_criteria,
                        "明确引用失败测试产物和原始实现产物",
                        "保持冻结需求和设计产物定义的范围边界",
                        "完成后重新进入测试阶段验证",
                    ],
                    input_artifact_ids=input_artifact_ids,
                    feedback_from=[failed.id],
                    rework_of=original_workitem_id,
                )
            )

        rework_items = self._attach_stage_input_artifacts(rework_items, latest)
        assignments = self._build_task_assignments(rework_items, latest)
        updated_project = replace(
            latest.project,
            current_stage="development",
            status=ProjectStatus.IN_PROGRESS,
        )
        rework_roles = self.router.plan_roles_for_workitems(rework_items)
        existing_activation_roles = {activation.role for activation in latest.agent_activations}
        new_activation_roles = [role for role in rework_roles if role not in existing_activation_roles]
        new_activations = self._build_agent_activations("development", new_activation_roles, rework_items)
        failed_ids = {item.id for item in failed_tests}
        reclassified_workitems = [
            replace(item, status=WorkItemStatus.DONE, blocked_reason="测试失败已回流到研发返工")
            if item.id in failed_ids
            else item
            for item in latest.workitems
        ]
        latest_state = replace(
            latest,
            project=updated_project,
            project_status=ProjectStatus.IN_PROGRESS,
            current_stage="development",
            workitems=[*reclassified_workitems, *rework_items],
            task_assignments=[*latest.task_assignments, *assignments],
            agent_activations=[*latest.agent_activations, *new_activations],
            planned_roles=list(dict.fromkeys([*latest.planned_roles, *rework_roles])),
            pending_test_scope=list(dict.fromkeys([*latest.pending_test_scope, *pending_test_scope])),
            recent_events=[
                *latest.recent_events,
                f"测试失败回流: 创建 {len(rework_items)} 个研发返工 WorkItem",
                f"测试失败已标记为已回流: {', '.join(sorted(failed_ids))}",
                *self._build_agent_activation_events(new_activations),
                *[
                    f"回流 WorkItem {item.id} ({item.kind}) 来源: {', '.join(item.feedback_from)}"
                    for item in rework_items
                ],
                f"切回阶段 development 执行返工",
            ],
        )
        self.state_store.save_state(latest_state)
        return latest_state

    def _feedback_rework_acceptance_criteria(self, feedback) -> list[str]:
        """Build rework acceptance criteria from missing testing checklist evidence."""
        criteria: list[str] = []
        for item in feedback.missing_checklist_items:
            if not isinstance(item, dict):
                continue
            rule_id = str(item.get("rule_id", "")).strip()
            label = str(item.get("label", "")).strip()
            evidence_terms = [
                str(value).strip()
                for value in item.get("required_evidence_terms", []) or []
                if str(value).strip()
            ]
            if not rule_id and not evidence_terms:
                continue
            evidence = ", ".join(evidence_terms) if evidence_terms else "explicit validation evidence"
            name = f"`{rule_id}` {label}".strip() if rule_id else label
            criteria.append(f"Address missing testing checklist {name}: produce evidence {evidence}")
        return criteria

    def _create_failure_rework(self, project_id: str) -> SharedProjectState:
        """Create executable same-stage rework tasks for exhausted retryable failures."""
        latest = self.state_store.get_state(project_id)
        failed_items = [
            item
            for item in latest.workitems
            if item.stage == latest.current_stage
            and item.status == WorkItemStatus.FAILED
            and item.retryable
            and item.retry_count >= item.max_retries
            and self._failure_rework_depth(latest, item) < self.MAX_FAILURE_REWORK_DEPTH
            and not self._has_existing_failure_rework(latest, item.id)
        ]
        if not failed_items:
            return self._escalate_project(project_id)

        rework_items: list[WorkItem] = []
        for failed in failed_items:
            input_artifact_ids = list(
                dict.fromkeys([*failed.input_artifact_ids, *self._workitem_artifact_ids(latest, failed.id)])
            )
            rework_items.append(
                WorkItem(
                    id=self._next_workitem_id(latest, rework_items),
                    description=(
                        f"Failure recovery rework for `{failed.id}`: {failed.description}\n\n"
                        f"Failure type: {failed.failure_type or 'unknown'}\n"
                        f"Failure summary: {failed.failure_summary or failed.blocked_reason or failed.result or '-'}\n"
                        f"Original retry count: {failed.retry_count}/{failed.max_retries}\n\n"
                        "Instructions: fix only the failed delivery scope, preserve the frozen requirement and existing "
                        "design boundary, and include concrete evidence for the next validation step."
                    ),
                    stage=failed.stage,
                    kind=failed.kind,
                    dependencies=list(failed.dependencies),
                    input_artifact_ids=input_artifact_ids,
                    acceptance_criteria=[
                        f"Resolve failure from {failed.id}",
                        "Reference the prior failure evidence and affected artifacts.",
                        "Do not expand scope beyond the frozen requirement/design boundary.",
                        "Produce output that can be validated by the next gate.",
                    ],
                    feedback_from=[failed.id],
                    rework_of=failed.id,
                )
            )

        failed_ids = {item.id for item in failed_items}
        reclassified_workitems = [
            replace(item, status=WorkItemStatus.DONE, blocked_reason="failure converted to explicit rework task")
            if item.id in failed_ids
            else item
            for item in latest.workitems
        ]
        assignments = self._build_task_assignments(rework_items, latest)
        rework_roles = self.router.plan_roles_for_workitems(rework_items)
        existing_activation_roles = {activation.role for activation in latest.agent_activations}
        new_activation_roles = [role for role in rework_roles if role not in existing_activation_roles]
        new_activations = self._build_agent_activations(latest.current_stage or "", new_activation_roles, rework_items)
        updated_project = replace(latest.project, status=ProjectStatus.IN_PROGRESS)
        latest_state = replace(
            latest,
            project=updated_project,
            project_status=ProjectStatus.IN_PROGRESS,
            workitems=[*reclassified_workitems, *rework_items],
            task_assignments=[*latest.task_assignments, *assignments],
            agent_activations=[*latest.agent_activations, *new_activations],
            planned_roles=list(dict.fromkeys([*latest.planned_roles, *rework_roles])),
            recent_events=[
                *latest.recent_events,
                f"FailureRecovery: created {len(rework_items)} executable rework WorkItem(s)",
                f"FailureRecovery: converted failed WorkItems to rework source: {', '.join(sorted(failed_ids))}",
                *self._build_agent_activation_events(new_activations),
                *[f"FailureRecovery WorkItem {item.id} ({item.kind}) source={', '.join(item.feedback_from)}" for item in rework_items],
            ],
        )
        self.state_store.save_state(latest_state)
        return latest_state

    def _block_dependency(self, project_id: str) -> SharedProjectState:
        latest = self.state_store.get_state(project_id)
        blocked_items = [
            item
            for item in latest.workitems
            if item.stage == latest.current_stage
            and item.status == WorkItemStatus.PENDING
            and self._has_failed_dependency(latest, item)
        ]
        blocker = self._dependency_blocker_message(latest, blocked_items)
        blocked_workitems = []
        for item in latest.workitems:
            if any(blocked.id == item.id for blocked in blocked_items):
                blocked_workitems.append(replace(item, status=WorkItemStatus.FAILED, blocked_reason=blocker))
                assignment = self._assignment_for(latest, item.id)
                if assignment:
                    self.state_store.upsert_task_assignment(
                        project_id,
                        replace(assignment, status=TaskAssignmentStatus.BLOCKED, blocked_reason=blocker),
                    )
            else:
                blocked_workitems.append(item)
        blocked_project = replace(latest.project, status=ProjectStatus.BLOCKED)
        blocked_state = replace(
            self.state_store.get_state(project_id),
            project=blocked_project,
            project_status=ProjectStatus.BLOCKED,
            workitems=blocked_workitems,
            blockers=[*latest.blockers, blocker],
            recent_events=[*latest.recent_events, f"依赖阻断: {blocker}"],
        )
        self.state_store.save_state(blocked_state)
        return blocked_state

    def _can_create_feedback_rework(self, state: SharedProjectState) -> bool:
        """Return whether exhausted testing failures can be turned into rework tasks."""
        if self._feedback_rework_limit_reached(state):
            return False
        return any(
            item.stage == "testing"
            and item.status == WorkItemStatus.FAILED
            and item.retry_count >= item.max_retries
            and not self._has_existing_feedback_rework(state, item.id)
            for item in state.workitems
        )

    def _can_create_failure_rework(self, state: SharedProjectState) -> bool:
        """Return whether exhausted same-stage failures can become explicit rework tasks."""
        if state.current_stage != "development":
            return False
        return any(
            item.stage == state.current_stage
            and item.status == WorkItemStatus.FAILED
            and item.retryable
            and item.retry_count >= item.max_retries
            and self._failure_rework_depth(state, item) < self.MAX_FAILURE_REWORK_DEPTH
            and not self._has_existing_failure_rework(state, item.id)
            for item in state.workitems
        )

    def _has_non_retryable_failure(self, workitems: list[WorkItem]) -> bool:
        """Return whether failed WorkItems should not be retried automatically."""
        return any(
            item.status == WorkItemStatus.FAILED
            and (item.retryable is False or not parse_retryable_failure(item.blocked_reason))
            for item in workitems
        )

    def _has_existing_feedback_rework(self, state: SharedProjectState, failed_test_id: str) -> bool:
        """Return whether a failed testing WorkItem already produced a rework task."""
        return any(failed_test_id in item.feedback_from for item in state.workitems)

    def _has_existing_failure_rework(self, state: SharedProjectState, failed_workitem_id: str) -> bool:
        """Return whether one failed WorkItem already produced a same-stage rework task."""
        return any(item.rework_of == failed_workitem_id or failed_workitem_id in item.feedback_from for item in state.workitems)

    def _failure_rework_depth(self, state: SharedProjectState, workitem: WorkItem) -> int:
        """Return rework depth for generic same-stage failure recovery."""
        by_id = {item.id: item for item in state.workitems}
        depth = 0
        current = workitem
        seen: set[str] = set()
        while current.rework_of and current.rework_of not in seen:
            seen.add(current.id)
            parent = by_id.get(current.rework_of)
            if parent is None:
                break
            depth += 1
            current = parent
        return depth

    def _feedback_rework_limit_reached(self, state: SharedProjectState) -> bool:
        """Return whether testing feedback has already re-entered development too many times."""
        return (
            len([item for item in state.workitems if item.stage == "development" and item.feedback_from])
            >= self.MAX_TEST_FEEDBACK_REWORK_CYCLES
        )

    def _feedback_target_kind(self, failed_test: WorkItem) -> str:
        """Map testing failures to the development Agent that should fix them."""
        if failed_test.kind == "ui_validation":
            return "ui_implementation"
        if failed_test.kind == "api_validation":
            return "api_implementation"
        return "generic_implementation"

    def _feedback_test_scope(self, failed_test: WorkItem) -> list[str]:
        """Map a failed testing item to the smallest retest scope."""
        if failed_test.kind in {"ui_validation", "api_validation", "automated_test", "acceptance_check"}:
            return [failed_test.kind]
        return ["acceptance_check"]

    def _apply_pending_test_scope(
        self,
        state: SharedProjectState,
        next_stage_name: str,
        new_workitems: list[WorkItem],
    ) -> list[WorkItem]:
        """Limit regenerated testing work to feedback-related checks."""
        if next_stage_name != "testing" or not state.pending_test_scope:
            return new_workitems
        scoped_items = [item for item in new_workitems if item.kind in set(state.pending_test_scope)]
        return scoped_items or new_workitems

    def _test_scope_events(
        self,
        state: SharedProjectState,
        next_stage_name: str,
        new_workitems: list[WorkItem],
    ) -> list[str]:
        """Explain test scope narrowing in event logs."""
        if next_stage_name != "testing" or not state.pending_test_scope:
            return []
        kinds = ", ".join(item.kind for item in new_workitems) or "none"
        return [f"按回流范围生成测试 WorkItem: {kinds}"]

    def _development_dependency_ids_for_feedback(self, state: SharedProjectState, target_kind: str) -> list[str]:
        """Build safe dependencies for a feedback rework task."""
        preferred = [
            item.id
            for item in state.workitems
            if item.stage == "development"
            and item.status == WorkItemStatus.DONE
            and (item.kind == target_kind or target_kind == "generic_implementation")
        ]
        if preferred:
            return preferred
        return [
            item.id
            for item in state.workitems
            if item.stage == "design" and item.status == WorkItemStatus.DONE
        ]

    def _workitem_artifact_ids(self, state: SharedProjectState, workitem_id: str) -> list[str]:
        """Return artifacts produced by one WorkItem in stable order."""
        return [artifact.id for artifact in state.artifacts if artifact.workitem_id == workitem_id]

    def _primary_development_workitem_id(self, state: SharedProjectState, target_kind: str) -> str | None:
        """Find the original development WorkItem for lineage."""
        for item in reversed(state.workitems):
            if item.stage == "development" and item.kind == target_kind:
                return item.id
        return None

    def _next_workitem_id(self, state: SharedProjectState, pending_new_items: list[WorkItem]) -> str:
        """Return the next WorkItem id without depending on planner internals."""
        numbers = []
        for item in [*state.workitems, *pending_new_items]:
            try:
                numbers.append(int(item.id.rsplit("-", 1)[-1]))
            except ValueError:
                continue
        for assignment in state.task_assignments:
            try:
                numbers.append(int(assignment.workitem_id.rsplit("-", 1)[-1]))
            except ValueError:
                continue
        return f"workitem-{(max(numbers) + 1 if numbers else 1):03d}"

    def _dedupe_new_workitem_ids(self, state: SharedProjectState, new_workitems: list[WorkItem]) -> list[WorkItem]:
        """Ensure planner-created WorkItems do not collide with existing feedback tasks."""
        existing_ids = {item.id for item in state.workitems}
        deduped: list[WorkItem] = []
        for workitem in new_workitems:
            if workitem.id not in existing_ids and all(item.id != workitem.id for item in deduped):
                deduped.append(workitem)
                continue
            deduped.append(replace(workitem, id=self._next_workitem_id(state, deduped)))
        return deduped

    def _append_execution(self, state: SharedProjectState, execution: Execution, route_decision) -> SharedProjectState:
        """Append execution and route records."""
        return replace(
            state,
            executions=[*state.executions, execution],
            route_decisions=[*state.route_decisions, route_decision],
        )

    def _find_artifact_for_workitem(self, project_id: str, workitem_id: str):
        """Find the primary artifact for a WorkItem."""
        state = self.state_store.get_state(project_id)
        for artifact in reversed(state.artifacts):
            if artifact.workitem_id == workitem_id and artifact.kind != "collaboration_review":
                return artifact
        return None

    def _ensure_collaboration_agent_activations(self, project_id: str, workitem: WorkItem) -> None:
        """Activate collaboration-only reviewer agents before review starts."""
        if self.collaboration_runner is None:
            return
        policy = self.collaboration_runner.policy
        lead_role_for_workitem = getattr(self.collaboration_runner, "lead_role_for_workitem", None)
        lead_role = (
            lead_role_for_workitem(workitem)
            if callable(lead_role_for_workitem)
            else policy.lead_role_by_stage.get(workitem.stage, "designer")
        )
        roles = [
            lead_role,
            *policy.peer_reviewer_roles_by_stage.get(workitem.stage, []),
            *policy.reviewer_roles_by_stage.get(workitem.stage, []),
        ]
        unique_roles = list(dict.fromkeys(role for role in roles if role))
        latest = self.state_store.get_state(project_id)
        existing_roles = {activation.role for activation in latest.agent_activations}
        missing_roles = [role for role in unique_roles if role not in existing_roles]
        if not missing_roles:
            return
        activations = self._build_agent_activations(workitem.stage, missing_roles, [workitem])
        latest = self.state_store.get_state(project_id)
        updated = replace(
            latest,
            agent_activations=[*latest.agent_activations, *activations],
            planned_roles=list(dict.fromkeys([*latest.planned_roles, *missing_roles])),
            recent_events=[
                *latest.recent_events,
                f"协作评审规划角色: {', '.join(missing_roles)}",
                *self._build_agent_activation_events(activations),
            ],
        )
        self.state_store.save_state(updated)

    def _build_task_assignments(
        self,
        workitems: list[WorkItem],
        state: SharedProjectState | None = None,
    ) -> list[TaskAssignment]:
        """Create Task Center records for WorkItems."""
        assignments: list[TaskAssignment] = []
        for workitem in workitems:
            role = self.router.resolve_role(workitem)
            input_artifact_ids = self._context_input_artifact_ids(state, workitem)
            assignments.append(
                TaskAssignment(
                    id=f"assignment-{workitem.id}",
                    workitem_id=workitem.id,
                    role=role,
                    claim_reason=f"{workitem.kind} 需要 {role} 处理",
                    dependencies=[*workitem.dependencies],
                    input_artifact_ids=input_artifact_ids,
                )
            )
        return assignments

    def _context_input_artifact_ids(
        self,
        state: SharedProjectState | None,
        workitem: WorkItem,
    ) -> list[str]:
        """Return explicit WorkItem inputs plus artifacts selected by ContextBuilder."""
        artifact_ids = [*workitem.input_artifact_ids]
        if state is not None:
            context = self.context_builder.build(state=state, workitem=workitem)
            artifact_ids.extend(context.artifact_ids)
        return list(dict.fromkeys(artifact_ids))

    def _attach_stage_dependencies(self, new_workitems: list[WorkItem], existing_workitems: list[WorkItem]) -> list[WorkItem]:
        """Attach linear stage dependencies to newly planned WorkItems."""
        if not new_workitems:
            return []
        stage = new_workitems[0].stage
        prerequisite_stage = {"design": "requirement", "development": "design", "testing": "development"}.get(stage)
        if prerequisite_stage is None:
            return new_workitems
        dependency_ids = [item.id for item in existing_workitems if item.stage == prerequisite_stage]
        return [
            replace(workitem, dependencies=[*dict.fromkeys([*workitem.dependencies, *dependency_ids])])
            for workitem in new_workitems
        ]

    def _attach_stage_input_artifacts(
        self,
        new_workitems: list[WorkItem],
        state: SharedProjectState,
    ) -> list[WorkItem]:
        """Persist context-selected artifact inputs on newly planned WorkItems."""
        return [
            replace(
                workitem,
                input_artifact_ids=self._context_input_artifact_ids(state, workitem),
            )
            for workitem in new_workitems
        ]

    def _attach_stage_handoff_contracts(
        self,
        new_workitems: list[WorkItem],
        state: SharedProjectState,
    ) -> list[WorkItem]:
        """Turn frozen requirement/design inputs into explicit development acceptance constraints."""
        if not new_workitems:
            return []
        artifact_by_id = {artifact.id: artifact for artifact in state.artifacts}
        design_workitem_ids = {item.id for item in state.workitems if item.stage == "design"}
        updated: list[WorkItem] = []
        for workitem in new_workitems:
            if workitem.stage != "development":
                updated.append(workitem)
                continue
            input_artifacts = [
                artifact_by_id[artifact_id]
                for artifact_id in workitem.input_artifact_ids
                if artifact_id in artifact_by_id
            ]
            has_requirement_baseline = any(
                artifact.kind in {"frozen_requirement_spec", "requirement_spec"}
                for artifact in input_artifacts
            )
            has_design_baseline = any(
                artifact.kind == "frozen_design_spec"
                or artifact.kind in {"design_overview", "ui_design", "api_design", "test_design"}
                or artifact.workitem_id in design_workitem_ids
                for artifact in input_artifacts
            )
            acceptance_criteria = list(workitem.acceptance_criteria)
            if has_requirement_baseline:
                acceptance_criteria.append("遵守输入产物中的冻结需求/需求基线范围、非目标和验收标准")
            if has_design_baseline:
                acceptance_criteria.append("遵守输入产物中的冻结设计/设计约束，必要偏离必须显式说明")
            updated.append(
                replace(
                    workitem,
                    acceptance_criteria=list(dict.fromkeys(acceptance_criteria)),
                )
            )
        return updated

    def _is_ready_to_execute(self, state: SharedProjectState, workitem: WorkItem) -> bool:
        """Return whether a pending WorkItem can be claimed by an Agent."""
        return workitem.status == WorkItemStatus.PENDING and all(
            self._workitem_status(state, dependency_id) == WorkItemStatus.DONE
            for dependency_id in workitem.dependencies
        )

    def _has_failed_dependency(self, state: SharedProjectState, workitem: WorkItem) -> bool:
        """Return whether any dependency failed or is missing."""
        for dependency_id in workitem.dependencies:
            if self._workitem_status(state, dependency_id) in {WorkItemStatus.FAILED, None}:
                return True
        return False

    def _workitem_status(self, state: SharedProjectState, workitem_id: str) -> WorkItemStatus | None:
        """Find dependency status by WorkItem id."""
        for item in state.workitems:
            if item.id == workitem_id:
                return item.status
        return None

    def _assignment_for(self, state: SharedProjectState, workitem_id: str) -> TaskAssignment | None:
        """Find the Task Center assignment for a WorkItem."""
        for assignment in state.task_assignments:
            if assignment.workitem_id == workitem_id:
                return assignment
        return None

    def _assignment_with_transition(
        self,
        assignment: TaskAssignment,
        *,
        action: str,
        status: TaskAssignmentStatus,
        agent_id: str = "",
        reason: str = "",
        details: dict[str, object] | None = None,
    ) -> TaskAssignment:
        """Append a Task Center transition for controller-owned internal execution."""
        record: dict[str, object] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "status": status.value,
            "agent_id": agent_id,
            "reason": reason,
        }
        if details:
            record["details"] = details
        returned_at = str(record["at"]) if action == "return" else assignment.returned_at
        return replace(assignment, returned_at=returned_at, transition_history=[*assignment.transition_history, record])

    def _claim_assignment(self, project_id: str, workitem: WorkItem, agent_id: str) -> None:
        """Mark a Task Center assignment as claimed by an Agent."""
        latest = self.state_store.get_state(project_id)
        input_artifact_ids = self._context_input_artifact_ids(latest, workitem)
        assignment = self._assignment_for(latest, workitem.id)
        if assignment is None:
            assignment = self._build_task_assignments([workitem], latest)[0]
            self.state_store.upsert_task_assignment(project_id, assignment)
        updated = replace(
            assignment,
            status=TaskAssignmentStatus.CLAIMED,
            assigned_agent_id=agent_id,
            input_artifact_ids=input_artifact_ids,
        )
        updated = self._assignment_with_transition(
            updated,
            action="claim",
            status=TaskAssignmentStatus.CLAIMED,
            agent_id=agent_id,
            reason=assignment.claim_reason,
        )
        self.state_store.upsert_task_assignment(project_id, updated)
        self.state_store.add_event(project_id, f"任务中心: {agent_id} 领取 {workitem.id}")

    def _return_assignment(self, project_id: str, workitem_id: str, execution: Execution) -> SharedProjectState:
        """Return execution status and artifact links to the Task Center."""
        latest = self.state_store.get_state(project_id)
        output_artifact_ids = [artifact.id for artifact in latest.artifacts if artifact.workitem_id == workitem_id]
        assignment_status = TaskAssignmentStatus.COMPLETED if execution.status.value == "success" else TaskAssignmentStatus.FAILED
        if self._assignment_for(latest, workitem_id) is None:
            workitem = next((item for item in latest.workitems if item.id == workitem_id), None)
            if workitem is not None:
                self.state_store.upsert_task_assignment(project_id, self._build_task_assignments([workitem], latest)[0])
        latest = self.state_store.get_state(project_id)
        assignment = self._assignment_for(latest, workitem_id)
        if assignment is not None:
            updated_assignment = replace(
                assignment,
                status=assignment_status,
                output_artifact_ids=output_artifact_ids,
                result_summary=execution.result[:240],
                blocked_reason=(
                    execution.failure_summary or execution.result[:240]
                    if assignment_status == TaskAssignmentStatus.FAILED
                    else assignment.blocked_reason
                ),
                returned_at=datetime.now(timezone.utc).isoformat(),
            )
            updated_assignment = self._assignment_with_transition(
                updated_assignment,
                action="return",
                status=assignment_status,
                agent_id=execution.agent_id,
                reason=execution.failure_summary or execution.result[:240],
                details={
                    "result_summary": execution.result[:240],
                    "output_artifact_ids": output_artifact_ids,
                    "failure_type": execution.failure_type,
                },
            )
            self.state_store.upsert_task_assignment(project_id, updated_assignment)
        latest = self.state_store.get_state(project_id)
        current_status = self._workitem_status(latest, workitem_id)
        if current_status is not None:
            self.state_store.update_workitem(
                project_id,
                workitem_id,
                current_status,
                output_artifact_ids=output_artifact_ids,
            )
        self.state_store.add_event(project_id, f"任务中心: {workitem_id} 归还状态 {assignment_status.value}")
        return self._update_agent_capability_stats(project_id, workitem_id, execution)

    def _update_agent_capability_stats(
        self,
        project_id: str,
        workitem_id: str,
        execution: Execution,
    ) -> SharedProjectState:
        """Update runtime capability profile from assignment return records."""
        latest = self.state_store.get_state(project_id)
        workitem = next((item for item in latest.workitems if item.id == workitem_id), None)
        assignment = self._assignment_for(latest, workitem_id)
        role = assignment.role if assignment else "unknown"
        updated: list[AgentCapabilityStats] = []
        found = False
        for stats in latest.agent_capability_stats:
            if stats.agent_id != execution.agent_id:
                updated.append(stats)
                continue
            found = True
            workitem_kinds = [*stats.workitem_kinds]
            if workitem and workitem.kind not in workitem_kinds:
                workitem_kinds.append(workitem.kind)
            updated.append(
                replace(
                    stats,
                    role=role,
                    completed_count=stats.completed_count + (1 if execution.status.value == "success" else 0),
                    failed_count=stats.failed_count + (1 if execution.status.value != "success" else 0),
                    workitem_kinds=workitem_kinds,
                    last_workitem_id=workitem_id,
                    last_status=execution.status.value,
                )
            )
        if not found:
            updated.append(
                AgentCapabilityStats(
                    agent_id=execution.agent_id,
                    role=role,
                    completed_count=1 if execution.status.value == "success" else 0,
                    failed_count=1 if execution.status.value != "success" else 0,
                    workitem_kinds=[workitem.kind] if workitem else [],
                    last_workitem_id=workitem_id,
                    last_status=execution.status.value,
                )
            )
        updated_state = replace(latest, agent_capability_stats=updated)
        self.state_store.save_state(updated_state)
        return updated_state

    def _dependency_blocker_message(self, state: SharedProjectState, blocked_items: list[WorkItem]) -> str:
        """Build a readable dependency blocker message."""
        details = []
        for item in blocked_items:
            failed = [
                dependency_id
                for dependency_id in item.dependencies
                if self._workitem_status(state, dependency_id) in {WorkItemStatus.FAILED, None}
            ]
            details.append(f"{item.id} 依赖 {', '.join(failed)}")
        return f"当前阶段存在依赖失败的 WorkItem: {'; '.join(details)}"

    def _build_agent_activations(
        self,
        stage: str,
        roles: list[str],
        workitems: list[WorkItem],
    ) -> list[AgentActivation]:
        """Create project-scoped Agent activation records."""
        activations: list[AgentActivation] = []
        for role in roles:
            agent = self.registry.get_agent_by_role(role)
            related_kinds = [
                workitem.kind
                for workitem in workitems
                if self._role_for_workitem_kind(workitem.kind) == role
            ]
            unique_kinds = list(dict.fromkeys(related_kinds))
            reason = ", ".join(unique_kinds) or "当前阶段需要该角色"
            activations.append(
                AgentActivation(
                    role=role,
                    agent_id=agent.id,
                    stage=stage,
                    reason=reason,
                    related_workitem_kinds=unique_kinds,
                    execution_backend=agent.execution_backend,
                    preferred_backend=agent.preferred_llm_backend,
                )
            )
        return activations

    def _apply_agent_team_plan_to_state(self, state: SharedProjectState, trigger: str) -> SharedProjectState:
        """Generate and persist dynamic Agent instances for the current stage."""
        if any(plan.stage == state.current_stage and plan.trigger == trigger for plan in state.agent_team_plans):
            return state
        plan = self.tl_agent.plan_agent_team(state, self.agent_team_planner, trigger=trigger)
        if not plan.agent_specs:
            return state
        existing_agent_ids = {activation.agent_id for activation in state.agent_activations}
        activations = [
            self._activation_from_dynamic_spec(spec)
            for spec in plan.agent_specs
            if spec.agent_id not in existing_agent_ids
        ]
        for spec in plan.agent_specs:
            self._register_dynamic_agent(spec)
        roles = list(dict.fromkeys([*state.planned_roles, *[spec.role for spec in plan.agent_specs]]))
        return replace(
            state,
            agent_team_plans=[*state.agent_team_plans, plan],
            agent_activations=[*state.agent_activations, *activations],
            planned_roles=roles,
            recent_events=[
                *state.recent_events,
                self._agent_team_plan_event(plan),
                *self._build_agent_activation_events(activations),
            ],
        )

    def _register_dynamic_agent(self, spec: DynamicAgentSpec) -> None:
        """Register a dynamic Agent instance in the runtime registry."""
        base_profile = self.registry.get_profile_by_role(spec.role)
        profile = self.agent_team_planner.profile_for_spec(base_profile, spec)
        self.registry.register_dynamic_agent(profile, agent_id=spec.agent_id, base_role=spec.role)

    def _activation_from_dynamic_spec(self, spec: DynamicAgentSpec) -> AgentActivation:
        """Convert a dynamic Agent spec into a persistent activation record."""
        return AgentActivation(
            role=spec.role,
            agent_id=spec.agent_id,
            stage=spec.stage,
            reason=spec.reason,
            related_workitem_kinds=list(spec.workitem_kinds),
            execution_backend="cli",
            preferred_backend=spec.preferred_backend,
            instance_id=spec.instance_id,
            scope=spec.scope,
            dynamic=True,
            parallel_safe=spec.parallel_safe,
            write_scope=list(spec.write_scope),
        )

    def _agent_team_plan_event(self, plan: AgentTeamPlan) -> str:
        """Build a compact event for a generated Agent team plan."""
        agent_ids = ", ".join(spec.agent_id for spec in plan.agent_specs)
        return (
            f"TLAgent 生成团队计划 {plan.id}: "
            f"stage={plan.stage}, level={plan.complexity_level}, source={plan.decision_source}, agents={agent_ids}"
        )

    def _build_agent_activation_events(self, activations: list[AgentActivation]) -> list[str]:
        """Build events explaining why Agents were created for the project."""
        return [
            (
                f"创建 Agent {activation.agent_id} ({activation.role})，"
                f"阶段: {activation.stage}，原因: {activation.reason}"
            )
            for activation in activations
        ]

    def _role_for_workitem_kind(self, kind: str) -> str | None:
        """Infer the default role for a WorkItem kind."""
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
