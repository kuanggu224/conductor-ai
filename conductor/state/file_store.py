"""File-backed SharedProjectState store."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from conductor.collaboration.models import (
    Collaboration,
    CollaborationDraftVersion,
    CollaborationStatus,
    ReviewContribution,
    ReviewDecision,
)
from conductor.domain.models import (
    AgentActivation,
    AgentCapabilityStats,
    Artifact,
    Execution,
    ExecutionStatus,
    Project,
    ProjectStatus,
    RouteDecision,
    SharedProjectState,
    TaskAssignment,
    TaskAssignmentStatus,
    WorkItem,
    WorkItemStatus,
)
from conductor.state.store import InMemoryStateStore


class FileStateStore(InMemoryStateStore):
    """Persist project state snapshots as JSON files.

    This keeps the persistence boundary explicit while reusing the existing
    in-memory update semantics.
    """

    def __init__(self, root_dir: str | Path) -> None:
        super().__init__()
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._load_existing_states()

    def save_state(self, state: SharedProjectState) -> None:
        """Save state in memory and on disk."""
        super().save_state(state)
        path = self._state_path(state.project.id)
        path.write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _state_path(self, project_id: str) -> Path:
        return self.root_dir / f"{project_id}.state.json"

    def _load_existing_states(self) -> None:
        for path in sorted(self.root_dir.glob("*.state.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            state = self._state_from_dict(data)
            self._states[state.project.id] = state

    def _state_from_dict(self, data: dict[str, Any]) -> SharedProjectState:
        return SharedProjectState(
            project=self._project(data["project"]),
            project_status=ProjectStatus(data["project_status"]),
            current_stage=data.get("current_stage"),
            workitems=[self._workitem(item) for item in data.get("workitems", [])],
            blockers=list(data.get("blockers", [])),
            recent_events=list(data.get("recent_events", [])),
            executions=[self._execution(item) for item in data.get("executions", [])],
            artifacts=[self._artifact(item) for item in data.get("artifacts", [])],
            collaborations=[self._collaboration(item) for item in data.get("collaborations", [])],
            task_assignments=[self._task_assignment(item) for item in data.get("task_assignments", [])],
            agent_activations=[self._agent_activation(item) for item in data.get("agent_activations", [])],
            planned_roles=list(data.get("planned_roles", [])),
            route_decisions=[self._route_decision(item) for item in data.get("route_decisions", [])],
            gate_history=list(data.get("gate_history", [])),
            pending_test_scope=list(data.get("pending_test_scope", [])),
            agent_capability_stats=[
                self._agent_capability_stats(item) for item in data.get("agent_capability_stats", [])
            ],
        )

    def _project(self, data: dict[str, Any]) -> Project:
        return Project(
            id=data["id"],
            goal=data["goal"],
            status=ProjectStatus(data.get("status", ProjectStatus.INITIALIZED)),
            current_stage=data.get("current_stage"),
            milestones=list(data.get("milestones", [])),
            project_root=data.get("project_root", ""),
        )

    def _workitem(self, data: dict[str, Any]) -> WorkItem:
        return WorkItem(
            id=data["id"],
            description=data["description"],
            stage=data["stage"],
            kind=data.get("kind", "generic"),
            owner_agent=data.get("owner_agent"),
            status=WorkItemStatus(data.get("status", WorkItemStatus.PENDING)),
            dependencies=list(data.get("dependencies", [])),
            input_artifact_ids=list(data.get("input_artifact_ids", [])),
            output_artifact_ids=list(data.get("output_artifact_ids", [])),
            acceptance_criteria=list(data.get("acceptance_criteria", [])),
            result=data.get("result"),
            retry_count=int(data.get("retry_count", 0)),
            max_retries=int(data.get("max_retries", 1)),
            blocked_reason=data.get("blocked_reason"),
            failure_type=data.get("failure_type", ""),
            retryable=bool(data.get("retryable", True)),
            failure_summary=data.get("failure_summary", ""),
            collaboration_session_id=data.get("collaboration_session_id"),
            feedback_from=list(data.get("feedback_from", [])),
            rework_of=data.get("rework_of"),
        )

    def _task_assignment(self, data: dict[str, Any]) -> TaskAssignment:
        return TaskAssignment(
            id=data["id"],
            workitem_id=data["workitem_id"],
            role=data["role"],
            status=TaskAssignmentStatus(data.get("status", TaskAssignmentStatus.QUEUED)),
            assigned_agent_id=data.get("assigned_agent_id"),
            claim_reason=data.get("claim_reason", ""),
            dependencies=list(data.get("dependencies", [])),
            input_artifact_ids=list(data.get("input_artifact_ids", [])),
            output_artifact_ids=list(data.get("output_artifact_ids", [])),
            result_summary=data.get("result_summary", ""),
            blocked_reason=data.get("blocked_reason"),
            claimed_at=data.get("claimed_at", ""),
            returned_at=data.get("returned_at", ""),
            prompt_file=data.get("prompt_file", ""),
        )

    def _execution(self, data: dict[str, Any]) -> Execution:
        return Execution(
            workitem_id=data["workitem_id"],
            agent_id=data["agent_id"],
            result=data["result"],
            status=ExecutionStatus(data["status"]),
            source_backend=data.get("source_backend", ""),
            cli_name=data.get("cli_name", ""),
            model=data.get("model", ""),
            working_directory=data.get("working_directory", ""),
            input_artifact_ids=list(data.get("input_artifact_ids", [])),
            changed_files=list(data.get("changed_files", [])),
            validation_command=list(data.get("validation_command", [])),
            validation_exit_code=data.get("validation_exit_code"),
            validation_success=data.get("validation_success"),
            cli_stdout_tail=data.get("cli_stdout_tail", ""),
            cli_stderr_tail=data.get("cli_stderr_tail", ""),
            failure_type=data.get("failure_type", ""),
            failure_summary=data.get("failure_summary", ""),
        )

    def _agent_activation(self, data: dict[str, Any]) -> AgentActivation:
        return AgentActivation(
            role=data["role"],
            agent_id=data["agent_id"],
            stage=data.get("stage", ""),
            reason=data.get("reason", ""),
            related_workitem_kinds=list(data.get("related_workitem_kinds", [])),
            execution_backend=data.get("execution_backend", "mock"),
            preferred_backend=data.get("preferred_backend", "local"),
        )

    def _artifact(self, data: dict[str, Any]) -> Artifact:
        return Artifact(
            id=data["id"],
            project_id=data["project_id"],
            workitem_id=data["workitem_id"],
            agent_id=data["agent_id"],
            kind=data["kind"],
            title=data["title"],
            content=data["content"],
            path=data.get("path"),
            source_backend=data.get("source_backend", "unknown"),
            parent_artifact_id=data.get("parent_artifact_id"),
            derived_from=list(data.get("derived_from", [])),
            review_of=data.get("review_of"),
            version=int(data.get("version", 1)),
            collaboration_session_id=data.get("collaboration_session_id"),
        )

    def _route_decision(self, data: dict[str, Any]) -> RouteDecision:
        return RouteDecision(
            workitem_id=data["workitem_id"],
            selected_agent=data["selected_agent"],
        )

    def _collaboration(self, data: dict[str, Any]) -> Collaboration:
        return Collaboration(
            id=data["id"],
            project_id=data["project_id"],
            workitem_id=data["workitem_id"],
            lead_agent_id=data["lead_agent_id"],
            reviewer_agent_ids=list(data.get("reviewer_agent_ids", [])),
            status=CollaborationStatus(data["status"]),
            max_rounds=int(data.get("max_rounds", 1)),
            current_round=int(data.get("current_round", 0)),
            contributions=[
                self._review_contribution(item) for item in data.get("contributions", [])
            ],
            draft_versions=[
                self._draft_version(item) for item in data.get("draft_versions", [])
            ],
            final_artifact_id=data.get("final_artifact_id"),
            team_plan=dict(data.get("team_plan", {})),
        )

    def _review_contribution(self, data: dict[str, Any]) -> ReviewContribution:
        return ReviewContribution(
            id=data["id"],
            round_index=int(data["round_index"]),
            agent_id=data["agent_id"],
            role=data["role"],
            decision=ReviewDecision(data["decision"]),
            content=data["content"],
            phase=data.get("phase", "cross_functional_review"),
            source_backend=data.get("source_backend", ""),
            model=data.get("model", ""),
            output_path=data.get("output_path", ""),
            duration_ms=int(data.get("duration_ms", 0)),
        )

    def _draft_version(self, data: dict[str, Any]) -> CollaborationDraftVersion:
        return CollaborationDraftVersion(
            version=int(data["version"]),
            round_index=int(data["round_index"]),
            author_agent_id=data["author_agent_id"],
            content=data["content"],
            review_ids=list(data.get("review_ids", [])),
            source_backend=data.get("source_backend", ""),
            model=data.get("model", ""),
            output_path=data.get("output_path", ""),
            duration_ms=int(data.get("duration_ms", 0)),
        )

    def _agent_capability_stats(self, data: dict[str, Any]) -> AgentCapabilityStats:
        return AgentCapabilityStats(
            agent_id=data["agent_id"],
            role=data["role"],
            completed_count=int(data.get("completed_count", 0)),
            failed_count=int(data.get("failed_count", 0)),
            workitem_kinds=list(data.get("workitem_kinds", [])),
            last_workitem_id=data.get("last_workitem_id"),
            last_status=data.get("last_status"),
        )
