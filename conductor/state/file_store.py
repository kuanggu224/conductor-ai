"""File-backed SharedProjectState store."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
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
    AgentTeamPlan,
    Artifact,
    DynamicAgentSpec,
    Execution,
    ExecutionStatus,
    HumanControlAction,
    HumanControlActionType,
    Project,
    ProjectStatus,
    RouteDecision,
    SharedProjectState,
    TaskAssignment,
    TaskAssignmentStatus,
    TLDecision,
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
        self.corrupt_state_files: list[str] = []
        self._load_existing_states()

    def save_state(self, state: SharedProjectState) -> None:
        """Save state in memory and on disk."""
        super().save_state(state)
        path = self._state_path(state.project.id)
        payload = json.dumps(asdict(state), ensure_ascii=False, indent=2)
        temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temp_path.write_text(payload, encoding="utf-8")
        self._replace_with_retry(temp_path, path)

    def _replace_with_retry(self, source: Path, target: Path, *, attempts: int = 10) -> None:
        for attempt in range(attempts):
            try:
                source.replace(target)
                return
            except PermissionError:
                if attempt == attempts - 1:
                    raise
                time.sleep(0.05)

    @contextmanager
    def project_lock(
        self,
        project_id: str,
        *,
        timeout_seconds: float = 10.0,
        poll_seconds: float = 0.05,
    ):
        """Acquire a cross-process project mutation lock and refresh state."""
        lock_path = self.root_dir / f"{project_id}.lock"
        started = time.monotonic()
        file_descriptor: int | None = None
        while file_descriptor is None:
            try:
                file_descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            except FileExistsError as error:
                if time.monotonic() - started >= timeout_seconds:
                    raise TimeoutError(f"Timed out waiting for project state lock: {project_id}") from error
                time.sleep(poll_seconds)
        try:
            self._reload_state(project_id)
            yield
        finally:
            os.close(file_descriptor)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    def _state_path(self, project_id: str) -> Path:
        return self.root_dir / f"{project_id}.state.json"

    def _load_existing_states(self) -> None:
        for path in sorted(self.root_dir.glob("*.state.json")):
            try:
                self._load_state_path(path)
            except Exception:
                self._quarantine_state_path(path)

    def _reload_state(self, project_id: str) -> None:
        path = self._state_path(project_id)
        if path.exists():
            self._load_state_path(path)

    def _load_state_path(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        state = self._state_from_dict(data)
        self._states[state.project.id] = state

    def _quarantine_state_path(self, path: Path) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        target = path.with_name(f"{path.name}.corrupt-{timestamp}")
        counter = 1
        while target.exists():
            target = path.with_name(f"{path.name}.corrupt-{timestamp}-{counter}")
            counter += 1
        self._replace_with_retry(path, target)
        self.corrupt_state_files.append(str(target))

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
            tl_decisions=[self._tl_decision(item) for item in data.get("tl_decisions", [])],
            human_control_actions=[
                self._human_control_action(item) for item in data.get("human_control_actions", [])
            ],
            agent_team_plans=[self._agent_team_plan(item) for item in data.get("agent_team_plans", [])],
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
            testing_checklist=[
                dict(item) for item in data.get("testing_checklist", []) if isinstance(item, dict)
            ],
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
            claim_token=data.get("claim_token", ""),
            claim_reason=data.get("claim_reason", ""),
            dependencies=list(data.get("dependencies", [])),
            input_artifact_ids=list(data.get("input_artifact_ids", [])),
            output_artifact_ids=list(data.get("output_artifact_ids", [])),
            result_summary=data.get("result_summary", ""),
            blocked_reason=data.get("blocked_reason"),
            claimed_at=data.get("claimed_at", ""),
            last_heartbeat_at=data.get("last_heartbeat_at", ""),
            lease_seconds=int(data.get("lease_seconds", 0)),
            lease_expires_at=data.get("lease_expires_at", ""),
            returned_at=data.get("returned_at", ""),
            prompt_file=data.get("prompt_file", ""),
            transition_history=[
                dict(item) for item in data.get("transition_history", []) if isinstance(item, dict)
            ],
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
            execution_command=list(data.get("execution_command", [])),
            execution_exit_code=data.get("execution_exit_code"),
            execution_duration_ms=data.get("execution_duration_ms"),
            prompt_hash=data.get("prompt_hash", ""),
            input_artifact_ids=list(data.get("input_artifact_ids", [])),
            changed_files=list(data.get("changed_files", [])),
            validation_command=list(data.get("validation_command", [])),
            validation_exit_code=data.get("validation_exit_code"),
            validation_success=data.get("validation_success"),
            cli_stdout_tail=data.get("cli_stdout_tail", ""),
            cli_stderr_tail=data.get("cli_stderr_tail", ""),
            failure_type=data.get("failure_type", ""),
            failure_summary=data.get("failure_summary", ""),
            token_usage=self._token_usage(data.get("token_usage", {})),
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
            instance_id=data.get("instance_id", ""),
            scope=data.get("scope", ""),
            dynamic=bool(data.get("dynamic", False)),
            parallel_safe=bool(data.get("parallel_safe", True)),
            write_scope=list(data.get("write_scope", [])),
        )

    def _agent_team_plan(self, data: dict[str, Any]) -> AgentTeamPlan:
        return AgentTeamPlan(
            id=data["id"],
            project_id=data.get("project_id", ""),
            stage=data.get("stage", ""),
            trigger=data.get("trigger", ""),
            complexity_level=data.get("complexity_level", "simple"),
            reasons=list(data.get("reasons", [])),
            agent_specs=[self._dynamic_agent_spec(item) for item in data.get("agent_specs", [])],
            decision_source=data.get("decision_source", "rule_planner"),
            decided_by=data.get("decided_by", ""),
            decision_summary=data.get("decision_summary", ""),
            fallback_reason=data.get("fallback_reason", ""),
        )

    def _dynamic_agent_spec(self, data: dict[str, Any]) -> DynamicAgentSpec:
        return DynamicAgentSpec(
            role=data["role"],
            agent_id=data["agent_id"],
            instance_id=data.get("instance_id", ""),
            stage=data.get("stage", ""),
            mission=data.get("mission", ""),
            reason=data.get("reason", ""),
            scope=data.get("scope", ""),
            collaboration_mode=data.get("collaboration_mode", ""),
            parallel_safe=bool(data.get("parallel_safe", False)),
            write_scope=list(data.get("write_scope", [])),
            output_contract=list(data.get("output_contract", [])),
            review_focus=list(data.get("review_focus", [])),
            revision_rules=list(data.get("revision_rules", [])),
            preferred_backend=data.get("preferred_backend", "local"),
            allowed_collaboration_modes=list(data.get("allowed_collaboration_modes", [])),
            workitem_kinds=list(data.get("workitem_kinds", [])),
        )

    def _tl_decision(self, data: dict[str, Any]) -> TLDecision:
        return TLDecision(
            id=data["id"],
            project_id=data.get("project_id", ""),
            stage=data.get("stage", ""),
            action=data.get("action", ""),
            risk_level=data.get("risk_level", "low"),
            summary=data.get("summary", ""),
            recommendations=list(data.get("recommendations", [])),
            human_action_required=bool(data.get("human_action_required", False)),
            created_at=data.get("created_at", ""),
        )

    def _human_control_action(self, data: dict[str, Any]) -> HumanControlAction:
        return HumanControlAction(
            id=data["id"],
            project_id=data.get("project_id", ""),
            action=HumanControlActionType(data.get("action", HumanControlActionType.PAUSE)),
            actor=data.get("actor", ""),
            reason=data.get("reason", ""),
            stage=data.get("stage", ""),
            workitem_id=data.get("workitem_id"),
            payload=dict(data.get("payload") or {}),
            created_at=data.get("created_at", ""),
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
            token_usage=self._token_usage(data.get("token_usage", {})),
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
            token_usage=self._token_usage(data.get("token_usage", {})),
        )

    def _token_usage(self, data: Any) -> dict[str, int]:
        if not isinstance(data, dict):
            return {}
        normalized: dict[str, int] = {}
        for key, value in data.items():
            if isinstance(value, int):
                normalized[str(key)] = value
            elif isinstance(value, str) and value.isdigit():
                normalized[str(key)] = int(value)
        return normalized

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
