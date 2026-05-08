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
        for workitem in self._list(payload.get("workitems")):
            if isinstance(workitem, dict):
                self._append_workitem_event(events, workitem)
                for execution in self._executions_for_workitem(payload, str(workitem.get("id", ""))):
                    self._append_execution_event(events, execution)
                for artifact in self._artifacts_for_workitem(payload, str(workitem.get("id", ""))):
                    self._append_artifact_event(events, artifact)
        self._append_terminal_event(events, payload)
        return events

    def _append_project_event(self, events: list[ReplayTraceEvent], payload: dict[str, Any]) -> None:
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
                },
            )
        )

    def _append_workitem_event(self, events: list[ReplayTraceEvent], workitem: dict[str, Any]) -> None:
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
                    "review_of": str(artifact.get("review_of", "")),
                    "collaboration_session_id": str(artifact.get("collaboration_session_id", "")),
                },
            )
        )

    def _append_terminal_event(self, events: list[ReplayTraceEvent], payload: dict[str, Any]) -> None:
        cursor = payload.get("resume_cursor", {}) if isinstance(payload.get("resume_cursor", {}), dict) else {}
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

    def _list(self, value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    def _string_list(self, value: Any) -> list[str]:
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
