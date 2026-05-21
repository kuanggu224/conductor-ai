"""Build read-only replay traces from Conductor run manifests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from conductor.replay_verifier import ManifestVerificationResult, verify_manifest


@dataclass(slots=True)
class ReplayTraceEvent:
    """One deterministic event reconstructed from a run manifest."""

    index: int
    event_type: str
    message: str
    stage: str = ""
    workitem_id: str = ""
    agent_id: str = ""
    status: str = ""
    artifact_ids: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable event."""
        return {
            "index": self.index,
            "event_type": self.event_type,
            "message": self.message,
            "stage": self.stage,
            "workitem_id": self.workitem_id,
            "agent_id": self.agent_id,
            "status": self.status,
            "artifact_ids": list(self.artifact_ids),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class ManifestReplayTrace:
    """Read-only replay trace plus verifier evidence."""

    manifest_path: str
    project_id: str
    schema_version: str
    final_status: str
    current_stage: str
    passed: bool
    verification: ManifestVerificationResult
    events: list[ReplayTraceEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable trace payload."""
        return {
            "manifest_path": self.manifest_path,
            "project_id": self.project_id,
            "schema_version": self.schema_version,
            "final_status": self.final_status,
            "current_stage": self.current_stage,
            "passed": self.passed,
            "verification": self.verification.to_dict(),
            "event_count": len(self.events),
            "events": [event.to_dict() for event in self.events],
        }

    def to_markdown(self) -> str:
        """Render a compact Markdown replay trace."""
        lines = [
            f"# Replay Trace: {self.project_id or 'unknown'}",
            "",
            f"- Manifest: `{self.manifest_path}`",
            f"- Schema: `{self.schema_version or 'unknown'}`",
            f"- Final status: `{self.final_status or 'unknown'}`",
            f"- Current stage: `{self.current_stage or 'unknown'}`",
            f"- Verification: `{'passed' if self.verification.passed else 'failed'}`",
            "",
            "## Events",
            "",
        ]
        for event in self.events:
            details = [
                f"type={event.event_type}",
                f"status={event.status}" if event.status else "",
                f"stage={event.stage}" if event.stage else "",
                f"workitem={event.workitem_id}" if event.workitem_id else "",
                f"agent={event.agent_id}" if event.agent_id else "",
                f"artifacts={', '.join(event.artifact_ids)}" if event.artifact_ids else "",
                _event_lineage_detail(event),
                _event_rework_detail(event),
                _event_testing_feedback_detail(event),
                _event_pending_test_scope_detail(event),
                _event_human_control_detail(event),
            ]
            detail_text = ", ".join(item for item in details if item)
            lines.append(f"{event.index}. {event.message}")
            if detail_text:
                lines.append(f"   - {detail_text}")
        if self.verification.errors:
            lines.extend(["", "## Verification Errors", ""])
            lines.extend(f"- {error}" for error in self.verification.errors)
        if self.verification.warnings:
            lines.extend(["", "## Verification Warnings", ""])
            lines.extend(f"- {warning}" for warning in self.verification.warnings)
        return "\n".join(lines) + "\n"


class ManifestReplayTraceBuilder:
    """Construct a deterministic, side-effect-free trace from one manifest."""

    def build(self, manifest_path: str | Path, *, check_files: bool = True) -> ManifestReplayTrace:
        """Build a replay trace. Invalid manifests return a failed trace."""
        path = Path(manifest_path)
        verification = verify_manifest(path, check_files=check_files)
        payload = self._load_payload(path) if path.exists() else {}
        trace = ManifestReplayTrace(
            manifest_path=str(path),
            project_id=str(payload.get("project_id", verification.project_id)),
            schema_version=str(payload.get("schema_version", verification.schema_version)),
            final_status=str(payload.get("final_status", payload.get("status", ""))),
            current_stage=str(payload.get("current_stage", "")),
            passed=verification.passed,
            verification=verification,
        )
        if not verification.passed:
            return trace
        trace.events = self._events(payload)
        return trace

    def _load_payload(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _events(self, payload: dict[str, Any]) -> list[ReplayTraceEvent]:
        events: list[ReplayTraceEvent] = []
        self._append_project_event(events, payload)
        for action in self._list(payload.get("human_control_actions")):
            if isinstance(action, dict):
                self._append_human_control_event(events, action)
        for workitem in self._list(payload.get("workitems")):
            if isinstance(workitem, dict):
                self._append_workitem_event(events, workitem)
                for assignment in self._assignments_for_workitem(payload, str(workitem.get("id", ""))):
                    self._append_task_assignment_event(events, assignment)
                for execution in self._executions_for_workitem(payload, str(workitem.get("id", ""))):
                    self._append_execution_event(events, execution)
                for artifact in self._artifacts_for_workitem(payload, str(workitem.get("id", ""))):
                    self._append_artifact_event(events, artifact)
        self._append_terminal_event(events, payload)
        return events

    def _append_project_event(self, events: list[ReplayTraceEvent], payload: dict[str, Any]) -> None:
        summary = payload.get("summary", {}) if isinstance(payload.get("summary", {}), dict) else {}
        events.append(
            ReplayTraceEvent(
                index=len(events) + 1,
                event_type="project",
                message=f"Project {payload.get('project_id', '')} loaded from manifest",
                stage=str(payload.get("current_stage", "")),
                status=str(payload.get("status", payload.get("final_status", ""))),
                metadata={
                    "run_id": str(payload.get("run_id", "")),
                    "run_profile": str(payload.get("run_profile", "")),
                    "project_root": str(payload.get("project_root", "")),
                    "pending_test_scope": self._string_list(summary.get("pending_test_scope", [])),
                },
            )
        )

    def _append_human_control_event(self, events: list[ReplayTraceEvent], action: dict[str, Any]) -> None:
        events.append(
            ReplayTraceEvent(
                index=len(events) + 1,
                event_type="human_control",
                message=f"HumanControl {action.get('id', '')} recorded {action.get('action', '')}",
                stage=str(action.get("stage", "")),
                workitem_id=str(action.get("workitem_id", "")),
                status=str(action.get("action", "")),
                metadata={
                    "action_id": str(action.get("id", "")),
                    "action": str(action.get("action", "")),
                    "actor": str(action.get("actor", "")),
                    "reason": str(action.get("reason", "")),
                    "payload": action.get("payload", {}) if isinstance(action.get("payload", {}), dict) else {},
                    "created_at": str(action.get("created_at", "")),
                },
            )
        )

    def _append_workitem_event(self, events: list[ReplayTraceEvent], workitem: dict[str, Any]) -> None:
        testing_feedback = self._list(workitem.get("testing_feedback", []))
        events.append(
            ReplayTraceEvent(
                index=len(events) + 1,
                event_type="workitem",
                message=f"WorkItem {workitem.get('id', '')} reached {workitem.get('status', '')}",
                stage=str(workitem.get("stage", "")),
                workitem_id=str(workitem.get("id", "")),
                status=str(workitem.get("status", "")),
                metadata={
                    "kind": str(workitem.get("kind", "")),
                    "owner_agent": str(workitem.get("owner_agent", "")),
                    "retry_count": workitem.get("retry_count", 0),
                    "max_retries": workitem.get("max_retries", 0),
                    "dependencies": self._string_list(workitem.get("dependencies", [])),
                    "input_artifact_ids": self._string_list(workitem.get("input_artifact_ids", [])),
                    "output_artifact_ids": self._string_list(workitem.get("output_artifact_ids", [])),
                    "feedback_from": self._string_list(workitem.get("feedback_from", [])),
                    "rework_of": str(workitem.get("rework_of", "")),
                    "testing_feedback": testing_feedback,
                },
            )
        )

    def _append_execution_event(self, events: list[ReplayTraceEvent], execution: dict[str, Any]) -> None:
        artifact_ids = self._string_list(execution.get("artifact_ids", []))
        events.append(
            ReplayTraceEvent(
                index=len(events) + 1,
                event_type="execution",
                message=f"Execution for {execution.get('workitem_id', '')} finished as {execution.get('status', '')}",
                workitem_id=str(execution.get("workitem_id", "")),
                agent_id=str(execution.get("agent_id", "")),
                status=str(execution.get("status", "")),
                artifact_ids=artifact_ids,
                metadata={
                    "source_backend": str(execution.get("source_backend", "")),
                    "cli_name": str(execution.get("cli_name", "")),
                    "model": str(execution.get("model", "")),
                    "working_directory": str(execution.get("working_directory", "")),
                    "execution_exit_code": execution.get("execution_exit_code"),
                    "validation_success": execution.get("validation_success"),
                    "prompt_hash": str(execution.get("prompt_hash", "")),
                },
            )
        )

    def _append_task_assignment_event(self, events: list[ReplayTraceEvent], assignment: dict[str, Any]) -> None:
        events.append(
            ReplayTraceEvent(
                index=len(events) + 1,
                event_type="task_assignment",
                message=f"TaskAssignment {assignment.get('id', '')} is {assignment.get('status', '')}",
                workitem_id=str(assignment.get("workitem_id", "")),
                agent_id=str(assignment.get("assigned_agent_id", "")),
                status=str(assignment.get("status", "")),
                metadata={
                    "assignment_id": str(assignment.get("id", "")),
                    "role": str(assignment.get("role", "")),
                    "claim_reason": str(assignment.get("claim_reason", "")),
                    "claimable": bool(assignment.get("claimable", False)),
                    "stale_claimed": bool(assignment.get("stale_claimed", False)),
                    "prompt_file": str(assignment.get("prompt_file", "")),
                },
            )
        )

    def _append_artifact_event(self, events: list[ReplayTraceEvent], artifact: dict[str, Any]) -> None:
        artifact_id = str(artifact.get("id", ""))
        events.append(
            ReplayTraceEvent(
                index=len(events) + 1,
                event_type="artifact",
                message=f"Artifact {artifact_id} recorded",
                workitem_id=str(artifact.get("workitem_id", "")),
                agent_id=str(artifact.get("agent_id", "")),
                artifact_ids=[artifact_id] if artifact_id else [],
                metadata={
                    "kind": str(artifact.get("kind", "")),
                    "title": str(artifact.get("title", "")),
                    "path": str(artifact.get("path", "")),
                    "version": artifact.get("version", 1),
                    "parent_artifact_id": str(artifact.get("parent_artifact_id", "")),
                    "derived_from": self._string_list(artifact.get("derived_from", [])),
                    "review_of": str(artifact.get("review_of", "")),
                    "collaboration_session_id": str(artifact.get("collaboration_session_id", "")),
                },
            )
        )

    def _append_terminal_event(self, events: list[ReplayTraceEvent], payload: dict[str, Any]) -> None:
        cursor = payload.get("resume_cursor", {}) if isinstance(payload.get("resume_cursor", {}), dict) else {}
        summary = payload.get("summary", {}) if isinstance(payload.get("summary", {}), dict) else {}
        events.append(
            ReplayTraceEvent(
                index=len(events) + 1,
                event_type="terminal",
                message=f"Project finished as {payload.get('final_status', payload.get('status', ''))}",
                stage=str(payload.get("current_stage", "")),
                status=str(payload.get("final_status", payload.get("status", ""))),
                metadata={
                    "next_action": str(cursor.get("next_action", "")),
                    "blocked": bool(cursor.get("blocked", False)),
                    "terminal": bool(cursor.get("terminal", False)),
                    "pending_test_scope": self._string_list(summary.get("pending_test_scope", [])),
                    "next_pending_workitem_ids": self._string_list(cursor.get("next_pending_workitem_ids", [])),
                },
            )
        )

    def _executions_for_workitem(self, payload: dict[str, Any], workitem_id: str) -> list[dict[str, Any]]:
        return [
            execution
            for execution in self._list(payload.get("executions"))
            if isinstance(execution, dict) and str(execution.get("workitem_id", "")) == workitem_id
        ]

    def _artifacts_for_workitem(self, payload: dict[str, Any], workitem_id: str) -> list[dict[str, Any]]:
        return [
            artifact
            for artifact in self._list(payload.get("artifacts"))
            if isinstance(artifact, dict) and str(artifact.get("workitem_id", "")) == workitem_id
        ]

    def _assignments_for_workitem(self, payload: dict[str, Any], workitem_id: str) -> list[dict[str, Any]]:
        return [
            assignment
            for assignment in self._list(payload.get("task_assignments"))
            if isinstance(assignment, dict) and str(assignment.get("workitem_id", "")) == workitem_id
        ]

    def _list(self, value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item)]


def _event_lineage_detail(event: ReplayTraceEvent) -> str:
    """Render compact artifact lineage metadata for Markdown traces."""
    if event.event_type != "artifact":
        return ""
    derived_from = _string_list(event.metadata.get("derived_from", []))
    parent_artifact_id = str(event.metadata.get("parent_artifact_id", ""))
    review_of = str(event.metadata.get("review_of", ""))
    parts = []
    if derived_from:
        parts.append(f"derived_from={', '.join(derived_from)}")
    if parent_artifact_id:
        parts.append(f"parent={parent_artifact_id}")
    if review_of:
        parts.append(f"review_of={review_of}")
    return "; ".join(parts)


def _event_rework_detail(event: ReplayTraceEvent) -> str:
    """Render compact WorkItem rework metadata for Markdown traces."""
    if event.event_type != "workitem":
        return ""
    feedback_from = _string_list(event.metadata.get("feedback_from", []))
    rework_of = str(event.metadata.get("rework_of", ""))
    parts = []
    if feedback_from:
        parts.append(f"feedback_from={', '.join(feedback_from)}")
    if rework_of:
        parts.append(f"rework_of={rework_of}")
    return "; ".join(parts)


def _event_testing_feedback_detail(event: ReplayTraceEvent) -> str:
    """Render compact structured testing feedback for Markdown traces."""
    if event.event_type != "workitem":
        return ""
    feedback_items = event.metadata.get("testing_feedback", [])
    if not isinstance(feedback_items, list):
        return ""
    parts: list[str] = []
    for feedback in feedback_items[:3]:
        if not isinstance(feedback, dict):
            continue
        workitem_id = str(feedback.get("workitem_id", ""))
        summary = str(feedback.get("summary", ""))
        validation_command = _string_list(feedback.get("validation_command", []))
        validation_exit_code = str(feedback.get("validation_exit_code", ""))
        failing_checks = _string_list(feedback.get("failing_checks", []))
        missing_coverage = _string_list(feedback.get("missing_coverage", []))
        detail = summary or (failing_checks[0] if failing_checks else "") or (missing_coverage[0] if missing_coverage else "")
        if validation_exit_code:
            detail = f"{detail}; validation_exit_code={validation_exit_code}" if detail else f"validation_exit_code={validation_exit_code}"
        if validation_command:
            command = " ".join(validation_command)
            detail = f"{detail}; validation_command={command}" if detail else f"validation_command={command}"
        if workitem_id or detail:
            parts.append(f"testing_feedback[{workitem_id or '-'}]={detail or '-'}")
    return "; ".join(parts)


def _event_pending_test_scope_detail(event: ReplayTraceEvent) -> str:
    """Render pending retest scope and queued work from summary/cursor metadata."""
    pending_scope = _string_list(event.metadata.get("pending_test_scope", []))
    next_workitems = _string_list(event.metadata.get("next_pending_workitem_ids", []))
    parts = []
    if pending_scope:
        parts.append(f"pending_test_scope={', '.join(pending_scope)}")
    if next_workitems:
        parts.append(f"next_pending_workitems={', '.join(next_workitems)}")
    return "; ".join(parts)


def _event_human_control_detail(event: ReplayTraceEvent) -> str:
    """Render compact human control decision metadata for Markdown traces."""
    if event.event_type != "human_control":
        return ""
    payload = event.metadata.get("payload", {})
    payload = payload if isinstance(payload, dict) else {}
    parts = []
    actor = str(event.metadata.get("actor", ""))
    reason = str(event.metadata.get("reason", ""))
    controller_action = str(payload.get("controller_action", ""))
    if actor:
        parts.append(f"actor={actor}")
    if reason:
        parts.append(f"reason={reason}")
    if controller_action:
        parts.append(f"controller_action={controller_action}")
    return "; ".join(parts)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def build_manifest_replay_trace(
    manifest_path: str | Path,
    *,
    check_files: bool = True,
) -> ManifestReplayTrace:
    """Build a read-only replay trace from one manifest."""
    return ManifestReplayTraceBuilder().build(manifest_path, check_files=check_files)


__all__ = [
    "ManifestReplayTrace",
    "ManifestReplayTraceBuilder",
    "ReplayTraceEvent",
    "build_manifest_replay_trace",
]
