"""Task Center context payloads for external workers."""

from __future__ import annotations

from dataclasses import asdict

from conductor.artifacts.store import ArtifactStore
from conductor.domain.models import Artifact, SharedProjectState, TaskAssignment
from conductor.task_center.service import TaskCenterError, TaskCenterService
from conductor.testing.failure_feedback import build_testing_failure_feedback


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
        self._ensure_frozen_requirement_input(
            state,
            workitem.stage,
            input_artifacts,
            include_content=include_content,
            max_content_chars=max_content_chars,
        )
        frozen_requirement_baseline = self._frozen_requirement_baseline(input_artifacts)
        rework_context = self._rework_context(state, workitem, input_artifacts)
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
            "execution_brief": self._execution_brief(
                state,
                assignment,
                input_artifacts,
                frozen_requirement_baseline,
                rework_context,
            ),
            "frozen_requirement_baseline": frozen_requirement_baseline,
            "rework_context": rework_context,
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
        frozen_requirement_baseline = _dict_payload(payload.get("frozen_requirement_baseline"))
        rework_context = _dict_payload(payload.get("rework_context"))
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
            f"- Claim Token: {assignment.get('claim_token', '') or 'not claimed'}",
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
            "## Frozen Requirement Baseline",
            *self._frozen_requirement_markdown(frozen_requirement_baseline),
            "",
            "## Rework Context",
            *self._rework_markdown(rework_context),
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
                    f"- Parent Artifact ID: {artifact.get('parent_artifact_id', '') or '-'}",
                    f"- Review Of: {artifact.get('review_of', '') or '-'}",
                    f"- Derived From: {_join_or_none(_list_payload(artifact.get('derived_from')))}",
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

    def _frozen_requirement_baseline(self, input_artifacts: list[dict[str, object]]) -> dict[str, object]:
        """Return the frozen requirement artifact that controls downstream work."""
        for artifact in input_artifacts:
            if artifact.get("kind") == "frozen_requirement_spec":
                return artifact
        return {}

    def _rework_context(
        self,
        state: SharedProjectState,
        workitem: object,
        input_artifacts: list[dict[str, object]],
    ) -> dict[str, object]:
        """Return explicit rework metadata for agents handling feedback loops."""
        workitem_payload = asdict(workitem)
        feedback_from = _list_payload(workitem_payload.get("feedback_from"))
        rework_of = str(workitem_payload.get("rework_of") or "")
        feedback_artifacts = [
            {
                "id": artifact.get("id", ""),
                "kind": artifact.get("kind", ""),
                "title": artifact.get("title", ""),
                "workitem_id": artifact.get("workitem_id", ""),
            }
            for artifact in input_artifacts
            if artifact.get("workitem_id") in feedback_from
        ]
        original_artifacts = [
            {
                "id": artifact.get("id", ""),
                "kind": artifact.get("kind", ""),
                "title": artifact.get("title", ""),
                "workitem_id": artifact.get("workitem_id", ""),
            }
            for artifact in input_artifacts
            if rework_of and artifact.get("workitem_id") == rework_of
        ]
        testing_feedback = self._testing_feedback_payloads(state, feedback_from)
        return {
            "is_rework": bool(feedback_from or rework_of),
            "feedback_from": feedback_from,
            "rework_of": rework_of,
            "feedback_artifacts": feedback_artifacts,
            "original_artifacts": original_artifacts,
            "testing_feedback": testing_feedback,
        }

    def _testing_feedback_payloads(self, state: SharedProjectState, feedback_from: list[str]) -> list[dict[str, object]]:
        """Return machine-readable structured feedback for failed testing WorkItems."""
        feedback_items = [
            item
            for item in state.workitems
            if item.id in feedback_from and item.stage == "testing"
        ]
        payloads: list[dict[str, object]] = []
        for item in feedback_items:
            artifacts = [artifact for artifact in state.artifacts if artifact.workitem_id == item.id]
            feedback = build_testing_failure_feedback(item, artifacts)
            payloads.append(asdict(feedback))
        return payloads

    def _ensure_frozen_requirement_input(
        self,
        state: SharedProjectState,
        stage: str,
        input_artifacts: list[dict[str, object]],
        *,
        include_content: bool,
        max_content_chars: int,
    ) -> None:
        """Downstream task contexts must carry the latest frozen requirement."""
        if stage not in {"design", "development", "testing"}:
            return
        if any(artifact.get("kind") == "frozen_requirement_spec" for artifact in input_artifacts):
            return
        frozen_requirement = next(
            (artifact for artifact in reversed(state.artifacts) if artifact.kind == "frozen_requirement_spec"),
            None,
        )
        if frozen_requirement is not None:
            input_artifacts.insert(
                0,
                self._artifact_payload(
                    frozen_requirement,
                    include_content=include_content,
                    max_content_chars=max_content_chars,
                ),
            )

    def _frozen_requirement_markdown(self, baseline: dict[str, object]) -> list[str]:
        """Render the controlling requirement contract section."""
        if not baseline:
            return ["- None"]
        return [
            "- Treat this frozen requirement as the controlling contract.",
            "- Do not add features outside this baseline unless the task explicitly asks for requirement rework.",
            f"- Artifact ID: {baseline.get('id', '')}",
            f"- Title: {baseline.get('title', '')}",
            f"- Path: {baseline.get('path', '')}",
            "- Full content is included again under `Input Artifacts`.",
        ]

    def _rework_markdown(self, context: dict[str, object]) -> list[str]:
        """Render feedback-loop guidance when the task is a rework item."""
        if not context.get("is_rework"):
            return ["- None"]
        feedback_artifacts = _list_payload(context.get("feedback_artifacts"))
        artifact_lines = [
            f"- {artifact.get('id', '')} ({artifact.get('kind', '')}) from {artifact.get('workitem_id', '')}"
            for artifact in feedback_artifacts
            if isinstance(artifact, dict)
        ]
        original_artifacts = _list_payload(context.get("original_artifacts"))
        original_artifact_lines = [
            f"- {artifact.get('id', '')} ({artifact.get('kind', '')}) from {artifact.get('workitem_id', '')}"
            for artifact in original_artifacts
            if isinstance(artifact, dict)
        ]
        testing_feedback_lines = self._testing_feedback_markdown(_list_payload(context.get("testing_feedback")))
        return [
            "- This is a rework task. Preserve the frozen requirement scope and fix only the referenced feedback.",
            f"- Rework Of: {context.get('rework_of', '') or '-'}",
            f"- Feedback From: {_join_or_none(_list_payload(context.get('feedback_from')))}",
            "- Feedback Artifacts:",
            *(artifact_lines or ["- None"]),
            "- Original Artifacts:",
            *(original_artifact_lines or ["- None"]),
            "- Structured Testing Feedback:",
            *(testing_feedback_lines or ["- None"]),
        ]

    def _testing_feedback_markdown(self, feedback_payloads: list[object]) -> list[str]:
        """Render structured testing feedback compactly for CLI prompts."""
        lines: list[str] = []
        for payload in feedback_payloads:
            if not isinstance(payload, dict):
                continue
            failing_checks = _join_or_none(_list_payload(payload.get("failing_checks")))
            missing_coverage = _join_or_none(_list_payload(payload.get("missing_coverage")))
            suggestions = _join_or_none(_list_payload(payload.get("suggested_actions")))
            lines.extend(
                [
                    (
                        f"- {payload.get('workitem_id', '')}: "
                        f"type={payload.get('failure_type', '') or 'unknown'}, "
                        f"exit_code={payload.get('exit_code', '') or '-'}, "
                        f"summary={payload.get('summary', '') or '-'}"
                    ),
                    f"- Failing Checks: {failing_checks}",
                    f"- Missing Coverage: {missing_coverage}",
                    f"- Suggested Fixes: {suggestions}",
                ]
            )
        return lines

    def _execution_brief(
        self,
        state: SharedProjectState,
        assignment: TaskAssignment,
        input_artifacts: list[dict[str, object]],
        frozen_requirement_baseline: dict[str, object],
        rework_context: dict[str, object],
    ) -> str:
        criteria = "\n".join(f"- {item}" for item in self._workitem_criteria(state, assignment)) or "- Not specified"
        inputs = "\n".join(f"- {item['id']} ({item['kind']}): {item['title']}" for item in input_artifacts) or "- None"
        frozen_requirement = self._frozen_requirement_brief(frozen_requirement_baseline)
        rework_brief = "\n".join(self._rework_markdown(rework_context))
        return (
            f"Project: {state.project.goal}\n"
            f"TaskAssignment: {assignment.id}\n"
            f"WorkItem: {assignment.workitem_id}\n"
            f"Role: {assignment.role}\n\n"
            "Frozen Requirement Baseline:\n"
            f"{frozen_requirement}\n\n"
            "Rework Context:\n"
            f"{rework_brief}\n\n"
            "Acceptance Criteria:\n"
            f"{criteria}\n\n"
            "Input Artifacts To Read:\n"
            f"{inputs}\n\n"
            "Return Protocol:\n"
            "- Complete with a concise result_summary.\n"
            "- Attach output_artifact_content or --output-file when returning substantive work.\n"
            "- Use fail/blocked_reason if the task cannot be completed safely."
        )

    def _frozen_requirement_brief(self, baseline: dict[str, object]) -> str:
        """Return a compact baseline instruction for the execution brief."""
        if not baseline:
            return "- None"
        return (
            f"- {baseline.get('id', '')} ({baseline.get('title', '')})\n"
            "- This is the controlling contract for design, implementation, and testing.\n"
            "- Do not expand non-goals or introduce unrelated features."
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
            "parent_artifact_id": artifact.parent_artifact_id or "",
            "derived_from": list(artifact.derived_from),
            "review_of": artifact.review_of or "",
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
    claim_token = str(assignment.get("claim_token", "") or "<claim-token>")
    complete_command = (
        f'python -m app.task_center complete "{assignment_id}" '
        f'--project-root "{project_root}" '
        '--agent-id "<agent-id>" '
        f'--claim-token "{claim_token}" '
        '--result-summary "completed" '
        '--output-file result.md'
    )
    fail_command = (
        f'python -m app.task_center fail "{assignment_id}" '
        f'--project-root "{project_root}" '
        '--agent-id "<agent-id>" '
        f'--claim-token "{claim_token}" '
        '--result-summary "failed" '
        '--blocked-reason "explain blocker"'
    )
    heartbeat_command = (
        f'python -m app.task_center heartbeat "{assignment_id}" '
        f'--project-root "{project_root}" '
        '--agent-id "<agent-id>" '
        f'--claim-token "{claim_token}"'
    )
    release_command = (
        f'python -m app.task_center release "{assignment_id}" '
        f'--project-root "{project_root}" '
        '--agent-id "<agent-id>" '
        f'--claim-token "{claim_token}" '
        '--release-reason "worker interrupted"'
    )
    return [
        "Use one of these commands after finishing the task:",
        "",
        "Complete successfully:",
        _fenced(complete_command),
        "",
        "Return failure/blocker:",
        _fenced(fail_command),
        "",
        "Refresh heartbeat while still working:",
        _fenced(heartbeat_command),
        "",
        "Release for retry:",
        _fenced(release_command),
    ]


__all__ = ["TaskContextBuilder"]
