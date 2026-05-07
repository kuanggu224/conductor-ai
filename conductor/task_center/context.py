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


__all__ = ["TaskContextBuilder"]
