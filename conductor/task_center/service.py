"""Shared Task Center state transition service."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field, replace
from uuid import uuid4

from conductor.domain.models import SharedProjectState, TaskAssignment, TaskAssignmentStatus, WorkItemStatus
from conductor.state.store import InMemoryStateStore

DEFAULT_STALE_CLAIMED_AFTER_SECONDS = 3600
DEFAULT_MAX_BULK_CLAIM_LIMIT = 5


class TaskCenterError(Exception):
    """Expected Task Center transition failure."""

    def __init__(
        self,
        message: str,
        status_code: int = 409,
        *,
        code: str = "task_center_error",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class TaskCenterTransition:
    """Result of one Task Center transition."""

    state: SharedProjectState
    assignment: TaskAssignment


@dataclass(frozen=True, slots=True)
class TaskCenterBulkTransition:
    """Result of a bulk Task Center transition."""

    state: SharedProjectState
    assignments: list[TaskAssignment]


@dataclass(frozen=True, slots=True)
class TaskCenterSweepTransition:
    """Result of one Task Center maintenance sweep."""

    state: SharedProjectState
    expired_lease_assignments: list[TaskAssignment]
    stale_assignments: list[TaskAssignment]


@dataclass(frozen=True, slots=True)
class TaskCenterAuditFinding:
    """Task Center audit finding for assignment lifecycle integrity."""

    code: str
    severity: str
    assignment_id: str
    workitem_id: str
    message: str
    recommendation: str = ""
    related_assignment_ids: list[str] = field(default_factory=list)


class TaskCenterService:
    """Coordinate TaskAssignment transitions with WorkItem lifecycle updates."""

    def __init__(
        self,
        state_store: InMemoryStateStore,
        event_prefix: str = "TaskCenter",
        *,
        require_claim_guard: bool = False,
        max_bulk_claim_limit: int = DEFAULT_MAX_BULK_CLAIM_LIMIT,
    ) -> None:
        self.state_store = state_store
        self.event_prefix = event_prefix
        self.require_claim_guard = require_claim_guard
        self.max_bulk_claim_limit = max_bulk_claim_limit

    def list_assignments(self, project_id: str, status: str | None = None) -> list[TaskAssignment]:
        """Return assignments for a project, optionally filtered by status value."""
        state = self._state(project_id)
        return [
            assignment
            for assignment in state.task_assignments
            if status is None or assignment.status.value == status
        ]

    def audit(
        self,
        state: SharedProjectState,
        *,
        stale_after_seconds: int = DEFAULT_STALE_CLAIMED_AFTER_SECONDS,
        now: datetime | None = None,
    ) -> list[TaskCenterAuditFinding]:
        """Return non-mutating audit findings for Task Center lifecycle integrity."""
        workitems_by_id = {item.id: item for item in state.workitems}
        artifact_ids = {artifact.id for artifact in state.artifacts}
        findings: list[TaskCenterAuditFinding] = []
        for assignment in state.task_assignments:
            workitem = workitems_by_id.get(assignment.workitem_id)
            if workitem is None:
                findings.append(
                    TaskCenterAuditFinding(
                        code="missing_workitem",
                        severity="error",
                        assignment_id=assignment.id,
                        workitem_id=assignment.workitem_id,
                        message="TaskAssignment references a missing WorkItem.",
                        recommendation="Repair project state or remove the orphaned assignment.",
                    )
                )
                continue
            findings.extend(self._audit_transition_history_shape(assignment))
            missing_input_artifact_ids = [
                artifact_id for artifact_id in assignment.input_artifact_ids if artifact_id not in artifact_ids
            ]
            if missing_input_artifact_ids:
                findings.append(
                    TaskCenterAuditFinding(
                        code="missing_input_artifact",
                        severity="warning",
                        assignment_id=assignment.id,
                        workitem_id=assignment.workitem_id,
                        message=(
                            "TaskAssignment input artifacts are missing: "
                            f"{', '.join(missing_input_artifact_ids)}"
                        ),
                        recommendation="Restore the input artifact files or regenerate task context.",
                    )
                )
            missing_output_artifact_ids = [
                artifact_id for artifact_id in assignment.output_artifact_ids if artifact_id not in artifact_ids
            ]
            if missing_output_artifact_ids:
                findings.append(
                    TaskCenterAuditFinding(
                        code="missing_output_artifact",
                        severity="error",
                        assignment_id=assignment.id,
                        workitem_id=assignment.workitem_id,
                        message=(
                            "TaskAssignment output artifacts are missing: "
                            f"{', '.join(missing_output_artifact_ids)}"
                        ),
                        recommendation="Restore the output artifact records or rerun the worker return step.",
                    )
                )
            if assignment.status == TaskAssignmentStatus.QUEUED and workitem.status != WorkItemStatus.PENDING:
                findings.append(
                    TaskCenterAuditFinding(
                        code="queued_workitem_not_pending",
                        severity="error",
                        assignment_id=assignment.id,
                        workitem_id=assignment.workitem_id,
                        message=f"Queued assignment has WorkItem status `{workitem.status.value}`.",
                        recommendation="Synchronize the WorkItem to pending or claim/release through Task Center.",
                    )
                )
            if assignment.status == TaskAssignmentStatus.QUEUED and self._queued_has_claim_state(assignment):
                findings.append(
                    TaskCenterAuditFinding(
                        code="queued_has_claim_state",
                        severity="error",
                        assignment_id=assignment.id,
                        workitem_id=assignment.workitem_id,
                        message="Queued assignment retains claimed or returned state fields.",
                        recommendation="Release the task through Task Center or repair stale claim fields before reuse.",
                    )
                )
            if assignment.status == TaskAssignmentStatus.QUEUED:
                latest_transition = self._latest_transition_action(assignment)
                if latest_transition and latest_transition != "release":
                    findings.append(
                        TaskCenterAuditFinding(
                            code="transition_status_mismatch",
                            severity="error",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message=f"Queued assignment latest transition is `{latest_transition}`.",
                            recommendation="Use Task Center release command or repair transition history.",
                        )
                    )
            if assignment.status == TaskAssignmentStatus.CLAIMED:
                write_scope_conflicts = self.write_scope_conflicts(
                    state,
                    assignment,
                    agent_id=assignment.assigned_agent_id or "",
                )
                if write_scope_conflicts:
                    findings.append(
                        TaskCenterAuditFinding(
                            code="write_scope_conflict",
                            severity="error",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message=(
                                "Claimed assignment overlaps dynamic write scope with claimed assignments: "
                                f"{', '.join(write_scope_conflicts)}"
                            ),
                            recommendation="Release or reassign one conflicting task before continuing parallel work.",
                            related_assignment_ids=list(write_scope_conflicts),
                        )
                    )
                if not assignment.assigned_agent_id:
                    findings.append(
                        TaskCenterAuditFinding(
                            code="claimed_missing_agent",
                            severity="error",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message="Claimed assignment has no assigned agent id.",
                            recommendation="Release and reclaim the task so ownership is auditable.",
                        )
                    )
                if not assignment.claim_token:
                    findings.append(
                        TaskCenterAuditFinding(
                            code="claimed_missing_token",
                            severity="error",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message="Claimed assignment has no claim token.",
                            recommendation="Release and reclaim the task so a guarded token is generated.",
                        )
                    )
                if not assignment.claimed_at:
                    findings.append(
                        TaskCenterAuditFinding(
                            code="claimed_missing_timestamp",
                            severity="error",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message="Claimed assignment has no claimed_at timestamp.",
                            recommendation="Release and reclaim the task so claim timing is auditable.",
                        )
                    )
                if assignment.returned_at:
                    findings.append(
                        TaskCenterAuditFinding(
                            code="claimed_has_returned_at",
                            severity="error",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message="Claimed assignment already has a returned_at timestamp.",
                            recommendation="Repair the assignment status or clear stale return fields before continuing.",
                        )
                    )
                if not self._has_transition(assignment, "claim"):
                    findings.append(
                        TaskCenterAuditFinding(
                            code="missing_claim_transition",
                            severity="warning",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message="Claimed assignment has no claim transition audit record.",
                            recommendation="Use Task Center claim commands instead of manual state edits.",
                        )
                    )
                latest_transition = self._latest_transition_action(assignment)
                if latest_transition and latest_transition not in {"claim", "heartbeat"}:
                    findings.append(
                        TaskCenterAuditFinding(
                            code="transition_status_mismatch",
                            severity="error",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message=f"Claimed assignment latest transition is `{latest_transition}`.",
                            recommendation="Repair assignment status or transition history before continuing.",
                        )
                    )
                if workitem.status != WorkItemStatus.RUNNING:
                    findings.append(
                        TaskCenterAuditFinding(
                            code="claimed_workitem_not_running",
                            severity="error",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message=f"Claimed assignment has WorkItem status `{workitem.status.value}`.",
                            recommendation="Synchronize the WorkItem to running or release the assignment.",
                        )
                    )
                if self.lease_expired(assignment, now=now):
                    findings.append(
                        TaskCenterAuditFinding(
                            code="lease_expired",
                            severity="warning",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message="Claimed assignment lease has expired.",
                            recommendation="Run `python -m app.task_center release-expired-leases` or `sweep`.",
                        )
                    )
                if self.stale_claimed(assignment, stale_after_seconds=stale_after_seconds, now=now):
                    findings.append(
                        TaskCenterAuditFinding(
                            code="stale_claimed",
                            severity="warning",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message="Claimed assignment heartbeat is stale.",
                            recommendation="Run `python -m app.task_center release-stale` or `sweep`.",
                        )
                    )
            if assignment.status == TaskAssignmentStatus.COMPLETED:
                if workitem.status != WorkItemStatus.DONE:
                    findings.append(
                        TaskCenterAuditFinding(
                            code="completed_workitem_not_done",
                            severity="error",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message=f"Completed assignment has WorkItem status `{workitem.status.value}`.",
                            recommendation="Synchronize the WorkItem status or reopen the assignment.",
                        )
                    )
                if not assignment.result_summary and not assignment.output_artifact_ids:
                    findings.append(
                        TaskCenterAuditFinding(
                            code="completed_without_evidence",
                            severity="error",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message="Completed assignment has no result summary or output artifacts.",
                            recommendation="Attach an output artifact or record a result summary.",
                        )
                    )
                if not assignment.returned_at:
                    findings.append(
                        TaskCenterAuditFinding(
                            code="missing_return_timestamp",
                            severity="warning",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message="Completed assignment has no returned_at timestamp.",
                            recommendation="Use Task Center complete command so return timing is auditable.",
                        )
                    )
                latest_transition = self._latest_transition_action(assignment)
                if latest_transition and latest_transition != "return":
                    findings.append(
                        TaskCenterAuditFinding(
                            code="transition_status_mismatch",
                            severity="error",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message=f"Completed assignment latest transition is `{latest_transition}`.",
                            recommendation="Repair assignment status or return through Task Center.",
                        )
                    )
                if self._returned_at_mismatches_transition(assignment):
                    findings.append(
                        TaskCenterAuditFinding(
                            code="returned_at_transition_mismatch",
                            severity="error",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message="Completed assignment returned_at does not match latest return transition timestamp.",
                            recommendation="Repair returned_at or transition history so return timing is auditable.",
                        )
                    )
                if not self._has_transition(assignment, "return"):
                    findings.append(
                        TaskCenterAuditFinding(
                            code="missing_return_transition",
                            severity="warning",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message="Completed assignment has no return transition audit record.",
                            recommendation="Use Task Center complete/fail commands instead of manual state edits.",
                        )
                    )
            if assignment.status == TaskAssignmentStatus.FAILED:
                if workitem.status != WorkItemStatus.FAILED and not self._is_gate_failure_transferred_to_rework(
                    state,
                    assignment,
                    workitem,
                ):
                    findings.append(
                        TaskCenterAuditFinding(
                            code="failed_workitem_not_failed",
                            severity="error",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message=f"Failed assignment has WorkItem status `{workitem.status.value}`.",
                            recommendation="Synchronize the WorkItem failure state or release the assignment.",
                        )
                    )
                if not assignment.blocked_reason and not assignment.result_summary:
                    findings.append(
                        TaskCenterAuditFinding(
                            code="failed_without_reason",
                            severity="error",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message="Failed assignment has no blocked reason or result summary.",
                            recommendation="Record a blocked reason so the controller can plan recovery.",
                        )
                    )
                if not assignment.returned_at:
                    findings.append(
                        TaskCenterAuditFinding(
                            code="missing_return_timestamp",
                            severity="warning",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message="Failed assignment has no returned_at timestamp.",
                            recommendation="Use Task Center fail command so return timing is auditable.",
                        )
                    )
                latest_transition = self._latest_transition_action(assignment)
                if latest_transition and latest_transition != "return":
                    findings.append(
                        TaskCenterAuditFinding(
                            code="transition_status_mismatch",
                            severity="error",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message=f"Failed assignment latest transition is `{latest_transition}`.",
                            recommendation="Repair assignment status or fail through Task Center.",
                        )
                    )
                if self._returned_at_mismatches_transition(assignment):
                    findings.append(
                        TaskCenterAuditFinding(
                            code="returned_at_transition_mismatch",
                            severity="error",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message="Failed assignment returned_at does not match latest return transition timestamp.",
                            recommendation="Repair returned_at or transition history so return timing is auditable.",
                        )
                    )
                if not self._has_transition(assignment, "return"):
                    findings.append(
                        TaskCenterAuditFinding(
                            code="missing_return_transition",
                            severity="warning",
                            assignment_id=assignment.id,
                            workitem_id=assignment.workitem_id,
                            message="Failed assignment has no return transition audit record.",
                            recommendation="Use Task Center fail command instead of manual state edits.",
                        )
                    )
        return findings

    def _queued_has_claim_state(self, assignment: TaskAssignment) -> bool:
        """Return whether a queued assignment still carries claimed/returned fields."""
        return bool(
            assignment.assigned_agent_id
            or assignment.claim_token
            or assignment.claimed_at
            or assignment.last_heartbeat_at
            or assignment.lease_seconds
            or assignment.lease_expires_at
            or assignment.returned_at
            or assignment.output_artifact_ids
            or assignment.result_summary
        )

    def _audit_transition_history_shape(self, assignment: TaskAssignment) -> list[TaskCenterAuditFinding]:
        """Return audit findings for malformed transition history records."""
        findings: list[TaskCenterAuditFinding] = []
        allowed_actions = {"claim", "release", "return", "heartbeat"}
        for index, transition in enumerate(assignment.transition_history):
            if not isinstance(transition, dict):
                findings.append(
                    TaskCenterAuditFinding(
                        code="transition_history_malformed",
                        severity="error",
                        assignment_id=assignment.id,
                        workitem_id=assignment.workitem_id,
                        message=f"Transition history entry {index} is not an object.",
                        recommendation="Repair transition history or use Task Center commands for lifecycle changes.",
                    )
                )
                continue
            action = str(transition.get("action", ""))
            if not action:
                findings.append(
                    TaskCenterAuditFinding(
                        code="transition_history_malformed",
                        severity="error",
                        assignment_id=assignment.id,
                        workitem_id=assignment.workitem_id,
                        message=f"Transition history entry {index} has no action.",
                        recommendation="Repair transition history or use Task Center commands for lifecycle changes.",
                    )
                )
            elif action not in allowed_actions:
                findings.append(
                    TaskCenterAuditFinding(
                        code="transition_history_unknown_action",
                        severity="warning",
                        assignment_id=assignment.id,
                        workitem_id=assignment.workitem_id,
                        message=f"Transition history entry {index} has unknown action `{action}`.",
                        recommendation="Normalize transition action names before replay or manifest verification.",
                    )
                )
            if "details" in transition and not isinstance(transition.get("details"), dict):
                findings.append(
                    TaskCenterAuditFinding(
                        code="transition_history_details_not_object",
                        severity="warning",
                        assignment_id=assignment.id,
                        workitem_id=assignment.workitem_id,
                        message=f"Transition history entry {index} details is not an object.",
                        recommendation="Repair transition details so structured lifecycle evidence is preserved.",
                    )
                )
        return findings

    def _latest_transition_action(self, assignment: TaskAssignment) -> str:
        """Return the latest non-empty transition action, if transition history exists."""
        for transition in reversed(assignment.transition_history):
            if not isinstance(transition, dict):
                continue
            action = str(transition.get("action", ""))
            if action:
                return action
        return ""

    def _returned_at_mismatches_transition(self, assignment: TaskAssignment) -> bool:
        if not assignment.returned_at:
            return False
        latest_return_at = ""
        for transition in reversed(assignment.transition_history):
            if not isinstance(transition, dict) or transition.get("action") != "return":
                continue
            latest_return_at = str(transition.get("at", ""))
            break
        if not latest_return_at:
            return False
        return not self._timestamps_match(latest_return_at, assignment.returned_at, tolerance_seconds=1)

    def _timestamps_match(self, left: str, right: str, *, tolerance_seconds: int = 0) -> bool:
        if left == right:
            return True
        try:
            left_dt = datetime.fromisoformat(left)
            right_dt = datetime.fromisoformat(right)
        except ValueError:
            return False
        return abs((left_dt - right_dt).total_seconds()) <= tolerance_seconds

    def summary(
        self,
        state: SharedProjectState,
        *,
        stale_after_seconds: int = DEFAULT_STALE_CLAIMED_AFTER_SECONDS,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """Return compact Task Center counts for dashboards and reports."""
        counts = {
            "total": len(state.task_assignments),
            "claimable": 0,
            "blocked_by_dependencies": 0,
            "stale_claimed": 0,
            "lease_expired": 0,
            "queued": 0,
            "claimed": 0,
            "completed": 0,
            "failed": 0,
            "blocked": 0,
            "blocked_by_write_scope": 0,
        }
        for assignment in state.task_assignments:
            counts[assignment.status.value] = counts.get(assignment.status.value, 0) + 1
            if self.claimable(state, assignment):
                counts["claimable"] += 1
            elif assignment.status == TaskAssignmentStatus.QUEUED and self.unmet_dependency_ids(state, assignment):
                counts["blocked_by_dependencies"] += 1
            elif assignment.status == TaskAssignmentStatus.QUEUED and self.write_scope_conflicts(state, assignment):
                counts["blocked_by_write_scope"] += 1
            if self.stale_claimed(assignment, stale_after_seconds=stale_after_seconds, now=now):
                counts["stale_claimed"] += 1
            if self.lease_expired(assignment, now=now):
                counts["lease_expired"] += 1
        return counts

    def claim(
        self,
        project_id: str,
        assignment_id: str,
        agent_id: str,
        claim_reason: str = "",
        lease_seconds: int = 0,
    ) -> TaskCenterTransition:
        """Claim one queued assignment and mark its WorkItem running."""
        with self._project_mutation(project_id):
            state = self._state(project_id)
            assignment = self.require_assignment(state, assignment_id)
            return self._claim_assignment(project_id, state, assignment, agent_id, claim_reason, lease_seconds)

    def claim_next(
        self,
        project_id: str,
        agent_id: str,
        role: str | None = None,
        claim_reason: str = "",
        lease_seconds: int = 0,
    ) -> TaskCenterTransition:
        """Claim the next queued assignment whose dependencies are satisfied."""
        with self._project_mutation(project_id):
            state = self._state(project_id)
            assignment = self.select_next_assignment(state, role=role, agent_id=agent_id)
            return self._claim_assignment(project_id, state, assignment, agent_id, claim_reason, lease_seconds)

    def claim_batch(
        self,
        project_id: str,
        agent_id: str,
        role: str | None = None,
        claim_reason: str = "",
        limit: int = 1,
        lease_seconds: int = 0,
    ) -> TaskCenterBulkTransition:
        """Claim up to ``limit`` queued assignments while enforcing a local batch cap."""
        if limit < 1:
            raise TaskCenterError("Bulk claim limit must be at least 1.", status_code=400)
        if limit > self.max_bulk_claim_limit:
            raise TaskCenterError(
                f"Bulk claim limit {limit} exceeds configured maximum {self.max_bulk_claim_limit}.",
                status_code=400,
            )
        with self._project_mutation(project_id):
            claimed: list[TaskAssignment] = []
            for _ in range(limit):
                state = self._state(project_id)
                try:
                    assignment = self.select_next_assignment(state, role=role, agent_id=agent_id)
                except TaskCenterError:
                    if claimed:
                        break
                    raise
                transition = self._claim_assignment(
                    project_id,
                    state,
                    assignment,
                    agent_id,
                    claim_reason,
                    lease_seconds,
                )
                claimed.append(transition.assignment)
            return TaskCenterBulkTransition(state=self._state(project_id), assignments=claimed)

    def complete(
        self,
        project_id: str,
        assignment_id: str,
        result_summary: str = "",
        output_artifact_ids: list[str] | None = None,
        agent_id: str = "",
        claim_token: str = "",
    ) -> TaskCenterTransition:
        """Return one claimed assignment as completed and mark its WorkItem done."""
        with self._project_mutation(project_id):
            return self._return_assignment(
                project_id,
                assignment_id,
                status=TaskAssignmentStatus.COMPLETED,
                result_summary=result_summary,
                output_artifact_ids=output_artifact_ids,
                agent_id=agent_id,
                claim_token=claim_token,
            )

    def fail(
        self,
        project_id: str,
        assignment_id: str,
        result_summary: str = "",
        output_artifact_ids: list[str] | None = None,
        blocked_reason: str = "",
        agent_id: str = "",
        claim_token: str = "",
    ) -> TaskCenterTransition:
        """Return one claimed assignment as failed and mark its WorkItem failed."""
        with self._project_mutation(project_id):
            return self._return_assignment(
                project_id,
                assignment_id,
                status=TaskAssignmentStatus.FAILED,
                result_summary=result_summary,
                output_artifact_ids=output_artifact_ids,
                blocked_reason=blocked_reason,
                agent_id=agent_id,
                claim_token=claim_token,
            )

    def release(
        self,
        project_id: str,
        assignment_id: str,
        release_reason: str = "",
        agent_id: str = "",
        claim_token: str = "",
    ) -> TaskCenterTransition:
        """Release a claimed/failed assignment back to queued for another worker."""
        with self._project_mutation(project_id):
            state = self._state(project_id)
            assignment = self.require_assignment(state, assignment_id)
            return self._release_assignment(
                project_id,
                assignment,
                release_reason=release_reason,
                agent_id=agent_id,
                claim_token=claim_token,
            )

    def release_stale(
        self,
        project_id: str,
        *,
        stale_after_seconds: int = DEFAULT_STALE_CLAIMED_AFTER_SECONDS,
        release_reason: str = "stale claimed assignment",
        now: datetime | None = None,
    ) -> TaskCenterBulkTransition:
        """Release all stale claimed assignments back to queued."""
        with self._project_mutation(project_id):
            state = self._state(project_id)
            stale_assignments = [
                assignment
                for assignment in state.task_assignments
                if self.stale_claimed(assignment, stale_after_seconds=stale_after_seconds, now=now)
            ]
            released: list[TaskAssignment] = []
            for assignment in stale_assignments:
                transition = self._release_assignment(
                    project_id,
                    assignment,
                    release_reason=release_reason,
                    validate_claim_guard=False,
                )
                released.append(transition.assignment)
            return TaskCenterBulkTransition(state=self._state(project_id), assignments=released)

    def release_expired_leases(
        self,
        project_id: str,
        *,
        release_reason: str = "expired task lease",
        now: datetime | None = None,
    ) -> TaskCenterBulkTransition:
        """Release all claimed assignments whose explicit lease expired."""
        with self._project_mutation(project_id):
            state = self._state(project_id)
            expired_assignments = [
                assignment
                for assignment in state.task_assignments
                if self.lease_expired(assignment, now=now)
            ]
            released: list[TaskAssignment] = []
            for assignment in expired_assignments:
                transition = self._release_assignment(
                    project_id,
                    assignment,
                    release_reason=release_reason,
                    validate_claim_guard=False,
                )
                released.append(transition.assignment)
            return TaskCenterBulkTransition(state=self._state(project_id), assignments=released)

    def sweep(
        self,
        project_id: str,
        *,
        stale_after_seconds: int = DEFAULT_STALE_CLAIMED_AFTER_SECONDS,
        expired_lease_release_reason: str = "expired task lease",
        stale_release_reason: str = "stale claimed assignment",
        now: datetime | None = None,
    ) -> TaskCenterSweepTransition:
        """Run Task Center maintenance: expired leases first, then stale claims."""
        with self._project_mutation(project_id):
            expired_assignments = [
                assignment
                for assignment in self._state(project_id).task_assignments
                if self.lease_expired(assignment, now=now)
            ]
            released_expired: list[TaskAssignment] = []
            for assignment in expired_assignments:
                transition = self._release_assignment(
                    project_id,
                    assignment,
                    release_reason=expired_lease_release_reason,
                    validate_claim_guard=False,
                )
                released_expired.append(transition.assignment)
            stale_assignments = [
                assignment
                for assignment in self._state(project_id).task_assignments
                if self.stale_claimed(assignment, stale_after_seconds=stale_after_seconds, now=now)
            ]
            released_stale: list[TaskAssignment] = []
            for assignment in stale_assignments:
                transition = self._release_assignment(
                    project_id,
                    assignment,
                    release_reason=stale_release_reason,
                    validate_claim_guard=False,
                )
                released_stale.append(transition.assignment)
            return TaskCenterSweepTransition(
                state=self._state(project_id),
                expired_lease_assignments=released_expired,
                stale_assignments=released_stale,
            )

    def heartbeat(
        self,
        project_id: str,
        assignment_id: str,
        agent_id: str = "",
        claim_token: str = "",
        now: datetime | None = None,
        lease_seconds: int | None = None,
    ) -> TaskCenterTransition:
        """Refresh one claimed assignment's worker heartbeat timestamp."""
        with self._project_mutation(project_id):
            state = self._state(project_id)
            assignment = self.require_assignment(state, assignment_id)
            if assignment.status != TaskAssignmentStatus.CLAIMED:
                raise TaskCenterError(f"Task assignment is not claimed: {assignment.status.value}")
            self._validate_claim_guard(assignment, agent_id=agent_id, claim_token=claim_token)
            heartbeat_at = _utc_now(now)
            resolved_lease_seconds = assignment.lease_seconds if lease_seconds is None else lease_seconds
            updated = replace(
                assignment,
                last_heartbeat_at=heartbeat_at,
                lease_seconds=max(0, resolved_lease_seconds),
                lease_expires_at=self._lease_expires_at(heartbeat_at, resolved_lease_seconds),
            )
            updated = self._with_transition(
                updated,
                action="heartbeat",
                agent_id=agent_id or assignment.assigned_agent_id or "",
                status=updated.status.value,
                occurred_at=heartbeat_at,
            )
            self.state_store.upsert_task_assignment(project_id, updated)
            self.state_store.add_event(project_id, f"{self.event_prefix}: heartbeat {assignment.workitem_id}")
            return TaskCenterTransition(state=self._state(project_id), assignment=updated)

    def require_assignment(self, state: SharedProjectState, assignment_id: str) -> TaskAssignment:
        """Return an assignment or raise a Task Center not-found error."""
        for assignment in state.task_assignments:
            if assignment.id == assignment_id:
                return assignment
        raise TaskCenterError(f"Task assignment not found: {assignment_id}", status_code=404)

    def select_next_assignment(
        self,
        state: SharedProjectState,
        role: str | None = None,
        agent_id: str = "",
    ) -> TaskAssignment:
        """Return the first queued assignment whose dependencies are satisfied."""
        for assignment in state.task_assignments:
            if role and assignment.role != role:
                continue
            if not self.claimable(state, assignment, agent_id=agent_id):
                continue
            return assignment
        suffix = f" for role {role}" if role else ""
        raise TaskCenterError(f"No queued task assignment available{suffix}.", status_code=404)

    def claimable(self, state: SharedProjectState, assignment: TaskAssignment, agent_id: str = "") -> bool:
        """Return whether an assignment is queued and all dependencies are done."""
        return (
            assignment.status == TaskAssignmentStatus.QUEUED
            and self.dependencies_satisfied(state, assignment)
            and not self.write_scope_conflicts(state, assignment, agent_id=agent_id)
        )

    def write_scope_conflicts(
        self,
        state: SharedProjectState,
        assignment: TaskAssignment,
        agent_id: str = "",
    ) -> list[str]:
        """Return claimed assignment ids that overlap this assignment's dynamic write scope."""
        candidate_scope = self._assignment_write_scope(state, assignment, agent_id=agent_id)
        if not candidate_scope:
            return []
        conflicts: list[str] = []
        for claimed in state.task_assignments:
            if claimed.id == assignment.id or claimed.status != TaskAssignmentStatus.CLAIMED:
                continue
            claimed_scope = self._assignment_write_scope(
                state,
                claimed,
                agent_id=claimed.assigned_agent_id or "",
            )
            if claimed_scope and candidate_scope.intersection(claimed_scope):
                conflicts.append(claimed.id)
        return conflicts

    def claimed_age_seconds(self, assignment: TaskAssignment, now: datetime | None = None) -> int | None:
        """Return assignment claim age in seconds, or None when not claimed."""
        return self._timestamp_age_seconds(assignment, assignment.claimed_at, now=now)

    def heartbeat_age_seconds(self, assignment: TaskAssignment, now: datetime | None = None) -> int | None:
        """Return age of the latest heartbeat or claim timestamp for a claimed assignment."""
        if assignment.last_heartbeat_at:
            heartbeat_age = self._timestamp_age_seconds(assignment, assignment.last_heartbeat_at, now=now)
            if heartbeat_age is not None:
                return heartbeat_age
        return self._timestamp_age_seconds(assignment, assignment.claimed_at, now=now)

    def _timestamp_age_seconds(
        self,
        assignment: TaskAssignment,
        timestamp: str,
        *,
        now: datetime | None = None,
    ) -> int | None:
        """Return age in seconds for a claimed assignment timestamp."""
        if assignment.status != TaskAssignmentStatus.CLAIMED or not assignment.claimed_at:
            return None
        try:
            parsed_timestamp = datetime.fromisoformat(timestamp)
        except ValueError:
            return None
        if parsed_timestamp.tzinfo is None:
            parsed_timestamp = parsed_timestamp.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return max(0, int((current - parsed_timestamp).total_seconds()))

    def stale_claimed(
        self,
        assignment: TaskAssignment,
        *,
        stale_after_seconds: int = DEFAULT_STALE_CLAIMED_AFTER_SECONDS,
        now: datetime | None = None,
    ) -> bool:
        """Return whether a claimed assignment has exceeded the stale threshold."""
        age = self.heartbeat_age_seconds(assignment, now=now)
        return age is not None and age >= stale_after_seconds

    def lease_expired(self, assignment: TaskAssignment, *, now: datetime | None = None) -> bool:
        """Return whether a claimed assignment's explicit lease has expired."""
        if assignment.status != TaskAssignmentStatus.CLAIMED or not assignment.lease_expires_at:
            return False
        try:
            expires_at = datetime.fromisoformat(assignment.lease_expires_at)
        except ValueError:
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current >= expires_at

    def dependencies_satisfied(self, state: SharedProjectState, assignment: TaskAssignment) -> bool:
        """Return whether all WorkItem dependencies for an assignment are done."""
        return not self.unmet_dependency_ids(state, assignment)

    def unmet_dependency_ids(self, state: SharedProjectState, assignment: TaskAssignment) -> list[str]:
        """Return dependency WorkItem ids that are not completed."""
        status_by_workitem = {item.id: item.status for item in state.workitems}
        return [
            dependency_id
            for dependency_id in assignment.dependencies
            if status_by_workitem.get(dependency_id) != WorkItemStatus.DONE
        ]

    def _assignment_write_scope(
        self,
        state: SharedProjectState,
        assignment: TaskAssignment,
        agent_id: str = "",
    ) -> set[str]:
        """Infer the dynamic write scope for an assignment and optional Agent instance."""
        workitem = next((item for item in state.workitems if item.id == assignment.workitem_id), None)
        if workitem is None:
            return set()
        scopes: set[str] = set()
        for activation in state.agent_activations:
            if agent_id and activation.agent_id != agent_id:
                continue
            if activation.role != assignment.role:
                continue
            if activation.stage and activation.stage != workitem.stage:
                continue
            if activation.related_workitem_kinds and workitem.kind not in activation.related_workitem_kinds:
                continue
            scopes.update(self._normalize_write_scope_item(item) for item in activation.write_scope)
        return {item for item in scopes if item}

    def _normalize_write_scope_item(self, item: object) -> str:
        """Normalize write-scope labels for conservative conflict checks."""
        return " ".join(str(item).strip().lower().split())

    def _project_mutation(self, project_id: str):
        project_lock = getattr(self.state_store, "project_lock", None)
        if callable(project_lock):
            return project_lock(project_id)
        return nullcontext()

    def _release_assignment(
        self,
        project_id: str,
        assignment: TaskAssignment,
        release_reason: str = "",
        agent_id: str = "",
        claim_token: str = "",
        validate_claim_guard: bool = True,
    ) -> TaskCenterTransition:
        if assignment.status not in {TaskAssignmentStatus.CLAIMED, TaskAssignmentStatus.FAILED}:
            raise TaskCenterError(f"Task assignment cannot be released: {assignment.status.value}")
        if validate_claim_guard:
            self._validate_claim_guard(assignment, agent_id=agent_id, claim_token=claim_token)
        updated = replace(
            assignment,
            status=TaskAssignmentStatus.QUEUED,
            assigned_agent_id=None,
            claim_token="",
            claim_reason=release_reason or assignment.claim_reason,
            output_artifact_ids=[],
            result_summary="",
            blocked_reason=None,
            claimed_at="",
            last_heartbeat_at="",
            lease_seconds=0,
            lease_expires_at="",
            returned_at="",
            prompt_file="",
        )
        updated = self._with_transition(
            updated,
            action="release",
            agent_id=agent_id or assignment.assigned_agent_id or "",
            reason=release_reason,
            status=updated.status.value,
        )
        self._sync_workitem_release(project_id, assignment)
        self.state_store.upsert_task_assignment(project_id, updated)
        self.state_store.add_event(project_id, f"{self.event_prefix}: {assignment.workitem_id} released")
        return TaskCenterTransition(state=self._state(project_id), assignment=updated)

    def _claim_assignment(
        self,
        project_id: str,
        state: SharedProjectState,
        assignment: TaskAssignment,
        agent_id: str,
        claim_reason: str = "",
        lease_seconds: int = 0,
    ) -> TaskCenterTransition:
        if assignment.status != TaskAssignmentStatus.QUEUED:
            raise TaskCenterError(f"Task assignment is not queued: {assignment.status.value}")
        unmet_dependency_ids = self.unmet_dependency_ids(state, assignment)
        if unmet_dependency_ids:
            raise TaskCenterError(
                f"Task assignment dependencies are not satisfied: {', '.join(unmet_dependency_ids)}"
            )
        conflicts = self.write_scope_conflicts(state, assignment, agent_id=agent_id)
        if conflicts:
            raise TaskCenterError(
                "Task assignment write scope conflicts with claimed assignments: "
                + ", ".join(conflicts),
                code="write_scope_conflict",
                details={"write_scope_conflict_assignment_ids": list(conflicts)},
            )
        now = _utc_now()
        resolved_lease_seconds = max(0, lease_seconds)
        updated = replace(
            assignment,
            status=TaskAssignmentStatus.CLAIMED,
            assigned_agent_id=agent_id,
            claim_token=uuid4().hex,
            claim_reason=claim_reason or assignment.claim_reason,
            blocked_reason=None,
            claimed_at=now,
            last_heartbeat_at=now,
            lease_seconds=resolved_lease_seconds,
            lease_expires_at=self._lease_expires_at(now, resolved_lease_seconds),
            returned_at="",
        )
        updated = self._with_transition(
            updated,
            action="claim",
            agent_id=agent_id,
            reason=claim_reason or assignment.claim_reason,
            status=updated.status.value,
            occurred_at=now,
        )
        self._sync_workitem_claim(project_id, assignment, agent_id)
        self.state_store.upsert_task_assignment(project_id, updated)
        self.state_store.add_event(project_id, f"{self.event_prefix}: {agent_id} claimed {assignment.workitem_id}")
        return TaskCenterTransition(state=self._state(project_id), assignment=updated)

    def _return_assignment(
        self,
        project_id: str,
        assignment_id: str,
        status: TaskAssignmentStatus,
        result_summary: str = "",
        output_artifact_ids: list[str] | None = None,
        blocked_reason: str = "",
        agent_id: str = "",
        claim_token: str = "",
    ) -> TaskCenterTransition:
        state = self._state(project_id)
        assignment = self.require_assignment(state, assignment_id)
        if assignment.status != TaskAssignmentStatus.CLAIMED:
            raise TaskCenterError(f"Task assignment is not claimed: {assignment.status.value}")
        self._validate_claim_guard(assignment, agent_id=agent_id, claim_token=claim_token)
        artifact_ids = list(output_artifact_ids or [])
        returned_at = _utc_now()
        updated = replace(
            assignment,
            status=status,
            result_summary=result_summary,
            output_artifact_ids=artifact_ids,
            blocked_reason=blocked_reason or None,
            returned_at=returned_at,
        )
        updated = self._with_transition(
            updated,
            action="return",
            agent_id=agent_id or assignment.assigned_agent_id or "",
            reason=blocked_reason or result_summary,
            status=updated.status.value,
            occurred_at=returned_at,
            details={
                "result_summary": result_summary,
                "blocked_reason": blocked_reason,
                "output_artifact_ids": artifact_ids,
            },
        )
        self._sync_workitem_return(
            project_id,
            assignment,
            status=status,
            result_summary=result_summary,
            output_artifact_ids=artifact_ids,
            blocked_reason=blocked_reason,
        )
        self.state_store.upsert_task_assignment(project_id, updated)
        self.state_store.add_event(project_id, f"{self.event_prefix}: {assignment.workitem_id} returned {status.value}")
        return TaskCenterTransition(state=self._state(project_id), assignment=updated)

    def _lease_expires_at(self, timestamp: str, lease_seconds: int | None) -> str:
        """Return lease expiration timestamp, or empty string when leases are disabled."""
        if lease_seconds is None or lease_seconds <= 0:
            return ""
        try:
            started_at = datetime.fromisoformat(timestamp)
        except ValueError:
            started_at = datetime.now(timezone.utc)
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        return (started_at + timedelta(seconds=lease_seconds)).isoformat()

    def _with_transition(
        self,
        assignment: TaskAssignment,
        *,
        action: str,
        agent_id: str = "",
        reason: str = "",
        status: str = "",
        occurred_at: str = "",
        details: dict[str, object] | None = None,
    ) -> TaskAssignment:
        """Append an auditable transition record to a TaskAssignment."""
        record: dict[str, object] = {
            "at": occurred_at or _utc_now(),
            "action": action,
            "status": status or assignment.status.value,
            "agent_id": agent_id,
            "reason": reason,
        }
        if details:
            record["details"] = details
        return replace(assignment, transition_history=[*assignment.transition_history, record])

    def _has_transition(self, assignment: TaskAssignment, action: str) -> bool:
        """Return whether an assignment has an audit transition action."""
        return any(
            item.get("action") == action
            for item in assignment.transition_history
            if isinstance(item, dict)
        )

    def _is_gate_failure_transferred_to_rework(
        self,
        state: SharedProjectState,
        assignment: TaskAssignment,
        workitem,
    ) -> bool:
        """Return whether a failed assignment is consistent with a completed gate-rework source."""
        if workitem.status != WorkItemStatus.DONE:
            return False
        transfer_text = " ".join(
            item
            for item in (assignment.result_summary, assignment.blocked_reason or "", workitem.blocked_reason or "")
            if item
        ).lower()
        if "rework" not in transfer_text and "返工" not in transfer_text:
            return False
        return any(
            child.rework_of == workitem.id or workitem.id in child.feedback_from
            for child in state.workitems
            if child.id != workitem.id
        )

    def _validate_claim_guard(self, assignment: TaskAssignment, *, agent_id: str = "", claim_token: str = "") -> None:
        """Reject guarded mutations from stale or non-owner workers."""
        if agent_id and assignment.assigned_agent_id and assignment.assigned_agent_id != agent_id:
            raise TaskCenterError(
                f"Task assignment is claimed by another agent: {assignment.assigned_agent_id}",
                status_code=403,
            )
        if claim_token and assignment.claim_token and assignment.claim_token != claim_token:
            raise TaskCenterError("Task assignment claim token does not match.", status_code=403)
        if self.require_claim_guard and assignment.assigned_agent_id and not agent_id:
            raise TaskCenterError("Task assignment requires agent id guard.", status_code=403)
        if self.require_claim_guard and assignment.claim_token and not claim_token:
            raise TaskCenterError("Task assignment requires claim token guard.", status_code=403)

    def _sync_workitem_claim(self, project_id: str, assignment: TaskAssignment, agent_id: str) -> None:
        try:
            self.state_store.update_workitem(
                project_id,
                assignment.workitem_id,
                WorkItemStatus.RUNNING,
                owner_agent=agent_id,
            )
        except KeyError as error:
            raise TaskCenterError(f"WorkItem not found: {assignment.workitem_id}", status_code=404) from error
        except ValueError as error:
            raise TaskCenterError(str(error)) from error

    def _sync_workitem_release(self, project_id: str, assignment: TaskAssignment) -> None:
        try:
            self.state_store.update_workitem(
                project_id,
                assignment.workitem_id,
                WorkItemStatus.PENDING,
                owner_agent="",
                result="",
                blocked_reason="",
                failure_type="",
                failure_summary="",
            )
        except KeyError as error:
            raise TaskCenterError(f"WorkItem not found: {assignment.workitem_id}", status_code=404) from error
        except ValueError as error:
            raise TaskCenterError(str(error)) from error

    def _sync_workitem_return(
        self,
        project_id: str,
        assignment: TaskAssignment,
        status: TaskAssignmentStatus,
        result_summary: str,
        output_artifact_ids: list[str],
        blocked_reason: str = "",
    ) -> None:
        workitem_status = WorkItemStatus.DONE if status == TaskAssignmentStatus.COMPLETED else WorkItemStatus.FAILED
        try:
            self.state_store.update_workitem(
                project_id,
                assignment.workitem_id,
                workitem_status,
                owner_agent=assignment.assigned_agent_id,
                result=result_summary,
                output_artifact_ids=output_artifact_ids,
                blocked_reason=blocked_reason or None,
                failure_type="task_center" if workitem_status == WorkItemStatus.FAILED else "",
                failure_summary=(blocked_reason or result_summary) if workitem_status == WorkItemStatus.FAILED else "",
            )
        except KeyError as error:
            raise TaskCenterError(f"WorkItem not found: {assignment.workitem_id}", status_code=404) from error
        except ValueError as error:
            raise TaskCenterError(str(error)) from error

    def _state(self, project_id: str) -> SharedProjectState:
        try:
            return self.state_store.get_state(project_id)
        except KeyError as error:
            raise TaskCenterError(f"Project not found: {project_id}", status_code=404) from error


__all__ = [
    "DEFAULT_MAX_BULK_CLAIM_LIMIT",
    "DEFAULT_STALE_CLAIMED_AFTER_SECONDS",
    "TaskCenterAuditFinding",
    "TaskCenterBulkTransition",
    "TaskCenterError",
    "TaskCenterService",
    "TaskCenterSweepTransition",
    "TaskCenterTransition",
]


def _utc_now(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.isoformat()
