"""Task Center context payloads for external workers."""

from __future__ import annotations

from dataclasses import asdict

from conductor.artifacts.store import ArtifactStore
from conductor.domain.models import Artifact, SharedProjectState, TaskAssignment
from conductor.task_center.service import TaskCenterError, TaskCenterService


class TaskContextBuilder:
    """Build the readable context an external agent needs for one assignment."""

    def __init__(self, artifact_store: ArtifactStore | None = None) -> None:
        self.artifact_store = artifact_store or ArtifactStore()

    def build(
        self,
        state: SharedProjectState,
        assignment_id: str,
        service: TaskCenterService | None = None,
        *,
        include_content: bool = True,
        max_content_chars: int = 12000,
    ) -> dict[str, object]:
        """Return assignment, WorkItem, and input artifact content."""
        task_center = service or TaskCenterService(_ReadOnlyStateStore(state))
        assignment = task_center.require_assignment(state, assignment_id)
        workitem = next((item for item in state.workitems if item.id == assignment.workitem_id), None)
        if workitem is None:
            raise TaskCenterError(f"WorkItem not found: {assignment.workitem_id}", status_code=404)
        artifacts_by_id = {artifact.id: artifact for artifact in state.artifacts}
        input_artifacts = [
            self._artifact_payload(artifact, include_content=include_content, max_content_chars=max_content_chars)
            for artifact_id in assignment.input_artifact_ids
            if (artifact := artifacts_by_id.get(artifact_id)) is not None
        ]
        output_artifacts = [
            self._artifact_payload(artifact, include_content=False, max_content_chars=max_content_chars)
            for artifact in state.artifacts
            if artifact.workitem_id == assignment.workitem_id
        ]
        return {
            "ok": True,
            "project_id": state.project.id,
            "project_goal": state.project.goal,
            "project_root": state.project.project_root,
            "execution_brief": self._execution_brief(state, assignment, input_artifacts),
            "assignment": {
                **asdict(assignment),
                "status": assignment.status.value,
                "claimable": task_center.claimable(state, assignment),
                "unmet_dependency_ids": task_center.unmet_dependency_ids(state, assignment),
            },
            "workitem": asdict(workitem),
            "input_artifacts": input_artifacts,
            "output_artifacts": output_artifacts,
        }

    def render_markdown(self, payload: dict[str, object]) -> str:
        """Render a context payload as a CLI-agent friendly Markdown prompt."""
        assignment = _dict_payload(payload.get("assignment"))
        workitem = _dict_payload(payload.get("workitem"))
        input_artifacts = _list_payload(payload.get("input_artifacts"))
        output_artifacts = _list_payload(payload.get("output_artifacts"))
        acceptance_criteria = _list_payload(workitem.get("acceptance_criteria"))

        lines = [
            "# Task Assignment Context",
            "",
            "## Project",
            f"- Project ID: {payload.get('project_id', '')}",
            f"- Goal: {payload.get('project_goal', '')}",
            f"- Root: {payload.get('project_root', '')}",
            "",
            "## Assignment",
            f"- Assignment ID: {assignment.get('id', '')}",
            f"- Status: {assignment.get('status', '')}",
            f"- Role: {assignment.get('role', '')}",
            f"- Assigned Agent: {assignment.get('assigned_agent_id', '') or 'unassigned'}",
            f"- Claimable: {assignment.get('claimable', '')}",
            f"- Dependencies: {_join_or_none(_list_payload(assignment.get('dependencies')))}",
            f"- Unmet Dependencies: {_join_or_none(_list_payload(assignment.get('unmet_dependency_ids')))}",
            f"- Prompt File: {assignment.get('prompt_file', '') or 'not recorded'}",
            "",
            "## WorkItem",
            f"- WorkItem ID: {workitem.get('id', '')}",
            f"- Stage: {workitem.get('stage', '')}",
            f"- Kind: {workitem.get('kind', '')}",
            f"- Status: {workitem.get('status', '')}",
            "",
            "### Description",
            str(workitem.get("description", "") or "Not specified"),
            "",
            "### Acceptance Criteria",
            *_bullet_lines(acceptance_criteria),
            "",
            "## Execution Brief",
            _fenced(str(payload.get("execution_brief", "") or "")),
            "",
            "## Input Artifacts",
        ]
        lines.extend(self._artifact_markdown(input_artifacts, include_content=True))
        lines.extend(
            [
                "",
                "## Existing Output Artifacts",
            ]
        )
        lines.extend(self._artifact_markdown(output_artifacts, include_content=False))
        lines.extend(
            [
                "",
                "## CLI Return Commands",
                *_return_command_lines(payload, assignment),
                "",
                "## Return Protocol",
                "- Return a concise result summary.",
                "- Attach substantive output with `--output-file` or `output_artifact_content`.",
                "- Use fail with a clear blocked reason if the task cannot be completed safely.",
            ]
        )
        return "\n".join(lines).rstrip() + "\n"

    def _artifact_markdown(self, artifacts: list[object], *, include_content: bool) -> list[str]:
        if not artifacts:
            return ["- None"]
        lines: list[str] = []
        for artifact_item in artifacts:
            artifact = _dict_payload(artifact_item)
            lines.extend(
                [
                    f"### {artifact.get('id', '')}",
                    f"- Kind: {artifact.get('kind', '')}",
                    f"- Title: {artifact.get('title', '')}",
                    f"- WorkItem ID: {artifact.get('workitem_id', '')}",
                    f"- Agent ID: {artifact.get('agent_id', '')}",
                    f"- Source Backend: {artifact.get('source_backend', '')}",
                    f"- Path: {artifact.get('path', '')}",
                    f"- Version: {artifact.get('version', '')}",
                ]
            )
            if include_content and "content" in artifact:
                if artifact.get("content_truncated"):
                    lines.append("- Content: truncated")
                else:
                    lines.append("- Content:")
                lines.append(_fenced(str(artifact.get("content", ""))))
            lines.append("")
        return lines[:-1] if lines and lines[-1] == "" else lines

    def _execution_brief(
        self,
        state: SharedProjectState,
        assignment: TaskAssignment,
        input_artifacts: list[dict[str, object]],
    ) -> str:
        criteria = "\n".join(f"- {item}" for item in self._workitem_criteria(state, assignment)) or "- Not specified"
        inputs = "\n".join(f"- {item['id']} ({item['kind']}): {item['title']}" for item in input_artifacts) or "- None"
        return (
            f"Project: {state.project.goal}\n"
            f"TaskAssignment: {assignment.id}\n"
            f"WorkItem: {assignment.workitem_id}\n"
            f"Role: {assignment.role}\n\n"
            "Acceptance Criteria:\n"
            f"{criteria}\n\n"
            "Input Artifacts To Read:\n"
            f"{inputs}\n\n"
            "Return Protocol:\n"
            "- Complete with a concise result_summary.\n"
            "- Attach output_artifact_content or --output-file when returning substantive work.\n"
            "- Use fail/blocked_reason if the task cannot be completed safely."
        )

    def _workitem_criteria(self, state: SharedProjectState, assignment: TaskAssignment) -> list[str]:
        workitem = next((item for item in state.workitems if item.id == assignment.workitem_id), None)
        return list(workitem.acceptance_criteria) if workitem else []

    def _artifact_payload(
        self,
        artifact: Artifact,
        *,
        include_content: bool,
        max_content_chars: int,
    ) -> dict[str, object]:
        payload = {
            "id": artifact.id,
            "kind": artifact.kind,
            "title": artifact.title,
            "workitem_id": artifact.workitem_id,
            "agent_id": artifact.agent_id,
            "source_backend": artifact.source_backend,
            "path": artifact.path or "",
            "version": artifact.version,
        }
        if include_content:
            content = self.artifact_store.read_content(artifact)
            truncated = len(content) > max_content_chars
            payload["content"] = content[:max_content_chars].rstrip() if truncated else content
            payload["content_truncated"] = truncated
        return payload


class _ReadOnlyStateStore:
    """Minimal state adapter for TaskCenterService helper methods."""

    def __init__(self, state: SharedProjectState) -> None:
        self.state = state

    def get_state(self, project_id: str) -> SharedProjectState:
        if self.state.project.id != project_id:
            raise KeyError(project_id)
        return self.state


def _dict_payload(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _list_payload(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _bullet_lines(items: list[object]) -> list[str]:
    return [f"- {item}" for item in items] if items else ["- Not specified"]


def _join_or_none(items: list[object]) -> str:
    return ", ".join(str(item) for item in items) if items else "None"


def _fenced(content: str) -> str:
    return f"````text\n{content.rstrip()}\n````"


def _return_command_lines(payload: dict[str, object], assignment: dict[str, object]) -> list[str]:
    project_root = str(payload.get("project_root", "") or ".")
    assignment_id = str(assignment.get("id", "") or "<assignment-id>")
    complete_command = (
        f'python -m app.task_center complete "{assignment_id}" '
        f'--project-root "{project_root}" '
        '--result-summary "completed" '
        '--output-file result.md'
    )
    fail_command = (
        f'python -m app.task_center fail "{assignment_id}" '
        f'--project-root "{project_root}" '
        '--result-summary "failed" '
        '--blocked-reason "explain blocker"'
    )
    return [
        "Use one of these commands after finishing the task:",
        "",
        "Complete successfully:",
        _fenced(complete_command),
        "",
        "Return failure/blocker:",
        _fenced(fail_command),
    ]


__all__ = ["TaskContextBuilder"]
