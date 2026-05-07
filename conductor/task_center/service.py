"""Shared Task Center state transition service."""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass, replace

from conductor.domain.models import SharedProjectState, TaskAssignment, TaskAssignmentStatus, WorkItemStatus
from conductor.state.store import InMemoryStateStore

DEFAULT_STALE_CLAIMED_AFTER_SECONDS = 3600


class TaskCenterError(Exception):
    """Expected Task Center transition failure."""

    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class TaskCenterTransition:
    """Result of one Task Center transition."""

    state: SharedProjectState
    assignment: TaskAssignment


class TaskCenterService:
    """Coordinate TaskAssignment transitions with WorkItem lifecycle updates."""

    def __init__(self, state_store: InMemoryStateStore, event_prefix: str = "TaskCenter") -> None:
        self.state_store = state_store
        self.event_prefix = event_prefix

    def list_assignments(self, project_id: str, status: str | None = None) -> list[TaskAssignment]:
        """Return assignments for a project, optionally filtered by status value."""
        state = self._state(project_id)
        return [
            assignment
            for assignment in state.task_assignments
            if status is None or assignment.status.value == status
        ]

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
            "queued": 0,
            "claimed": 0,
            "completed": 0,
            "failed": 0,
            "blocked": 0,
        }
        for assignment in state.task_assignments:
            counts[assignment.status.value] = counts.get(assignment.status.value, 0) + 1
            if self.claimable(state, assignment):
                counts["claimable"] += 1
            elif assignment.status == TaskAssignmentStatus.QUEUED and self.unmet_dependency_ids(state, assignment):
                counts["blocked_by_dependencies"] += 1
            if self.stale_claimed(assignment, stale_after_seconds=stale_after_seconds, now=now):
                counts["stale_claimed"] += 1
        return counts

    def claim(
        self,
        project_id: str,
        assignment_id: str,
        agent_id: str,
        claim_reason: str = "",
    ) -> TaskCenterTransition:
        """Claim one queued assignment and mark its WorkItem running."""
        state = self._state(project_id)
        assignment = self.require_assignment(state, assignment_id)
        return self._claim_assignment(project_id, state, assignment, agent_id, claim_reason)

    def claim_next(
        self,
        project_id: str,
        agent_id: str,
        role: str | None = None,
        claim_reason: str = "",
    ) -> TaskCenterTransition:
        """Claim the next queued assignment whose dependencies are satisfied."""
        state = self._state(project_id)
        assignment = self.select_next_assignment(state, role=role)
        return self._claim_assignment(project_id, state, assignment, agent_id, claim_reason)

    def complete(
        self,
        project_id: str,
        assignment_id: str,
        result_summary: str = "",
        output_artifact_ids: list[str] | None = None,
    ) -> TaskCenterTransition:
        """Return one claimed assignment as completed and mark its WorkItem done."""
        return self._return_assignment(
            project_id,
            assignment_id,
            status=TaskAssignmentStatus.COMPLETED,
            result_summary=result_summary,
            output_artifact_ids=output_artifact_ids,
        )

    def fail(
        self,
        project_id: str,
        assignment_id: str,
        result_summary: str = "",
        output_artifact_ids: list[str] | None = None,
        blocked_reason: str = "",
    ) -> TaskCenterTransition:
        """Return one claimed assignment as failed and mark its WorkItem failed."""
        return self._return_assignment(
            project_id,
            assignment_id,
            status=TaskAssignmentStatus.FAILED,
            result_summary=result_summary,
            output_artifact_ids=output_artifact_ids,
            blocked_reason=blocked_reason,
        )

    def release(
        self,
        project_id: str,
        assignment_id: str,
        release_reason: str = "",
    ) -> TaskCenterTransition:
        """Release a claimed/failed assignment back to queued for another worker."""
        state = self._state(project_id)
        assignment = self.require_assignment(state, assignment_id)
        if assignment.status not in {TaskAssignmentStatus.CLAIMED, TaskAssignmentStatus.FAILED}:
            raise TaskCenterError(f"Task assignment cannot be released: {assignment.status.value}")
        updated = replace(
            assignment,
            status=TaskAssignmentStatus.QUEUED,
            assigned_agent_id=None,
            claim_reason=release_reason or assignment.claim_reason,
            output_artifact_ids=[],
            result_summary="",
            blocked_reason=None,
            claimed_at="",
            returned_at="",
            prompt_file="",
        )
        self._sync_workitem_release(project_id, assignment)
        self.state_store.upsert_task_assignment(project_id, updated)
        self.state_store.add_event(project_id, f"{self.event_prefix}: {assignment.workitem_id} released")
        return TaskCenterTransition(state=self._state(project_id), assignment=updated)

    def require_assignment(self, state: SharedProjectState, assignment_id: str) -> TaskAssignment:
        """Return an assignment or raise a Task Center not-found error."""
        for assignment in state.task_assignments:
            if assignment.id == assignment_id:
                return assignment
        raise TaskCenterError(f"Task assignment not found: {assignment_id}", status_code=404)

    def select_next_assignment(self, state: SharedProjectState, role: str | None = None) -> TaskAssignment:
        """Return the first queued assignment whose dependencies are satisfied."""
        for assignment in state.task_assignments:
            if role and assignment.role != role:
                continue
            if not self.claimable(state, assignment):
                continue
            return assignment
        suffix = f" for role {role}" if role else ""
        raise TaskCenterError(f"No queued task assignment available{suffix}.", status_code=404)

    def claimable(self, state: SharedProjectState, assignment: TaskAssignment) -> bool:
        """Return whether an assignment is queued and all dependencies are done."""
        return assignment.status == TaskAssignmentStatus.QUEUED and self.dependencies_satisfied(state, assignment)

    def claimed_age_seconds(self, assignment: TaskAssignment, now: datetime | None = None) -> int | None:
        """Return assignment claim age in seconds, or None when not claimed."""
        if assignment.status != TaskAssignmentStatus.CLAIMED or not assignment.claimed_at:
            return None
        try:
            claimed_at = datetime.fromisoformat(assignment.claimed_at)
        except ValueError:
            return None
        if claimed_at.tzinfo is None:
            claimed_at = claimed_at.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return max(0, int((current - claimed_at).total_seconds()))

    def stale_claimed(
        self,
        assignment: TaskAssignment,
        *,
        stale_after_seconds: int = DEFAULT_STALE_CLAIMED_AFTER_SECONDS,
        now: datetime | None = None,
    ) -> bool:
        """Return whether a claimed assignment has exceeded the stale threshold."""
        age = self.claimed_age_seconds(assignment, now=now)
        return age is not None and age >= stale_after_seconds

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

    def _claim_assignment(
        self,
        project_id: str,
        state: SharedProjectState,
        assignment: TaskAssignment,
        agent_id: str,
        claim_reason: str = "",
    ) -> TaskCenterTransition:
        if assignment.status != TaskAssignmentStatus.QUEUED:
            raise TaskCenterError(f"Task assignment is not queued: {assignment.status.value}")
        unmet_dependency_ids = self.unmet_dependency_ids(state, assignment)
        if unmet_dependency_ids:
            raise TaskCenterError(
                f"Task assignment dependencies are not satisfied: {', '.join(unmet_dependency_ids)}"
            )
        updated = replace(
            assignment,
            status=TaskAssignmentStatus.CLAIMED,
            assigned_agent_id=agent_id,
            claim_reason=claim_reason or assignment.claim_reason,
            blocked_reason=None,
            claimed_at=_utc_now(),
            returned_at="",
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
    ) -> TaskCenterTransition:
        state = self._state(project_id)
        assignment = self.require_assignment(state, assignment_id)
        if assignment.status != TaskAssignmentStatus.CLAIMED:
            raise TaskCenterError(f"Task assignment is not claimed: {assignment.status.value}")
        artifact_ids = list(output_artifact_ids or [])
        updated = replace(
            assignment,
            status=status,
            result_summary=result_summary,
            output_artifact_ids=artifact_ids,
            blocked_reason=blocked_reason or None,
            returned_at=_utc_now(),
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
    "DEFAULT_STALE_CLAIMED_AFTER_SECONDS",
    "TaskCenterError",
    "TaskCenterService",
    "TaskCenterTransition",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
