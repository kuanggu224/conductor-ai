"""Project JSONL logging and report generation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from conductor.domain.models import SharedProjectState


@dataclass(slots=True)
class ProjectLogEntry:
    """One structured project log entry."""

    timestamp: str
    project_id: str
    event_index: int
    message: str
    event_type: str = "event"
    stage: str = ""
    project_status: str = ""
    workitem_id: str = ""
    agent_id: str = ""
    metadata: dict[str, Any] | None = None


class ProjectLogStore:
    """Append project logs as JSONL and render readable project reports."""

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def append_event(
        self,
        project_id: str,
        event_index: int,
        message: str,
        project_root: str | Path | None = None,
        *,
        event_type: str | None = None,
        stage: str = "",
        project_status: str = "",
        workitem_id: str = "",
        agent_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Append one structured event to a project JSONL log."""
        entry = ProjectLogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            project_id=project_id,
            event_index=event_index,
            message=message,
            event_type=event_type or self.classify_event(message),
            stage=stage,
            project_status=project_status,
            workitem_id=workitem_id,
            agent_id=agent_id,
            metadata=metadata or {},
        )
        with self._project_log_path(project_id, project_root).open("a", encoding="utf-8") as file:
            file.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")

    def append_state_event(
        self,
        state: SharedProjectState,
        event_index: int,
        message: str,
    ) -> None:
        """Append an event with a compact snapshot of current project state."""
        self.append_event(
            project_id=state.project.id,
            event_index=event_index,
            message=message,
            project_root=state.project.project_root,
            stage=state.current_stage or "",
            project_status=state.project_status.value,
            workitem_id=self._extract_prefixed_token(message, "workitem-"),
            agent_id=self._extract_prefixed_token(message, "agent-"),
            metadata={
                "workitem_counts": self._workitem_counts(state),
                "artifact_count": len(state.artifacts),
                "execution_count": len(state.executions),
                "planned_roles": list(state.planned_roles),
                "active_agents": [activation.agent_id for activation in state.agent_activations],
            },
        )

    def read_events(self, project_id: str, project_root: str | Path | None = None) -> list[ProjectLogEntry]:
        """Read all log entries for one project."""
        path = self._project_log_path(project_id, project_root)
        if not path.exists():
            return []
        entries: list[ProjectLogEntry] = []
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                payload = json.loads(line)
                payload.setdefault("event_type", self.classify_event(payload.get("message", "")))
                payload.setdefault("stage", "")
                payload.setdefault("project_status", "")
                payload.setdefault("workitem_id", "")
                payload.setdefault("agent_id", "")
                payload.setdefault("metadata", {})
                entries.append(ProjectLogEntry(**payload))
        return entries

    def write_project_report(self, state: SharedProjectState) -> Path:
        """Write a Markdown execution report for the project."""
        path = self._project_report_path(state.project.id, state.project.project_root)
        entries = self.read_events(state.project.id, state.project.project_root)
        path.write_text(self.render_project_report(state, entries), encoding="utf-8")
        return path

    def render_project_report(self, state: SharedProjectState, entries: list[ProjectLogEntry]) -> str:
        """Render a compact Markdown report from state and structured logs."""
        lines = [
            f"# Conductor Project Report: {state.project.id}",
            "",
            "## Overview",
            f"- Goal: {state.project.goal}",
            f"- Status: {state.project_status.value}",
            f"- Current Stage: {state.current_stage or '-'}",
            f"- Project Root: {state.project.project_root or '-'}",
            f"- Events: {len(entries)}",
            f"- WorkItems: {len(state.workitems)}",
            f"- Executions: {len(state.executions)}",
            f"- Artifacts: {len(state.artifacts)}",
            "",
            "## Activated Agents",
        ]
        if state.agent_activations:
            for activation in state.agent_activations:
                kinds = ", ".join(activation.related_workitem_kinds) or "-"
                lines.append(
                    f"- {activation.agent_id} ({activation.role}) | stage={activation.stage} | "
                    f"backend={activation.execution_backend} | reason={activation.reason} | kinds={kinds}"
                )
        else:
            lines.append("- None")

        lines.extend(["", "## WorkItems"])
        for item in state.workitems:
            lines.append(
                f"- {item.id} | stage={item.stage} | kind={item.kind} | "
                f"status={item.status.value} | owner={item.owner_agent or '-'}"
            )

        lines.extend(["", "## Artifacts"])
        if state.artifacts:
            for artifact in state.artifacts:
                lines.append(
                    f"- {artifact.id} | kind={artifact.kind} | agent={artifact.agent_id} | "
                    f"source={artifact.source_backend} | version={artifact.version} | path={artifact.path or '-'}"
                )
        else:
            lines.append("- None")

        lines.extend(["", "## Blockers"])
        if state.blockers:
            lines.extend(f"- {blocker}" for blocker in state.blockers)
        else:
            lines.append("- None")

        lines.extend(["", "## Event Timeline"])
        for entry in entries:
            lines.append(
                f"- [{entry.event_index}] {entry.event_type} | stage={entry.stage or '-'} | "
                f"status={entry.project_status or '-'} | {entry.message}"
            )
        lines.append("")
        return "\n".join(lines)

    def classify_event(self, message: str) -> str:
        """Classify a controller event into a stable log category."""
        if "创建 Agent" in message or "激活 Agent" in message:
            return "agent_activation"
        if "WorkItem" in message and ("开始执行" in message or "使用 Agent CLI" in message or "使用 Harness" in message):
            return "execution_start"
        if "WorkItem" in message and ("执行完成" in message or "执行失败" in message):
            return "execution_result"
        if "创建 WorkItem" in message or "回流 WorkItem" in message:
            return "workitem_created"
        if "LeadController 决策" in message:
            return "controller_decision"
        if "GateDecision" in message:
            return "gate_decision"
        if "进入阶段" in message or "切回阶段" in message:
            return "stage_transition"
        if "Artifact" in message or "产物" in message:
            return "artifact"
        if "协作" in message or "review" in message.lower():
            return "collaboration"
        if "阻塞" in message or "升级处理" in message:
            return "blocker"
        return "event"

    def _project_log_path(self, project_id: str, project_root: str | Path | None = None) -> Path:
        """Return the JSONL log path for one project."""
        root = (Path(project_root) / ".conductor" / "logs") if project_root else self.root_dir
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{project_id}.jsonl"

    def _project_report_path(self, project_id: str, project_root: str | Path | None = None) -> Path:
        """Return the Markdown report path for one project."""
        root = (Path(project_root) / ".conductor" / "reports") if project_root else self.root_dir / "reports"
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{project_id}.md"

    def _workitem_counts(self, state: SharedProjectState) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in state.workitems:
            counts[item.status.value] = counts.get(item.status.value, 0) + 1
        return counts

    def _extract_prefixed_token(self, message: str, prefix: str) -> str:
        normalized = message.replace("`", " ").replace(":", " ").replace(",", " ")
        for token in normalized.split():
            cleaned = token.strip("()，。")
            if cleaned.startswith(prefix):
                return cleaned
        return ""
