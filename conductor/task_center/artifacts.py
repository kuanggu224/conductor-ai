"""Helpers for creating Task Center return artifacts."""

from __future__ import annotations

from conductor.artifacts.store import ArtifactStore
from conductor.domain.models import Artifact, SharedProjectState, TaskAssignment
from conductor.state.store import InMemoryStateStore


def create_task_return_artifact(
    *,
    state_store: InMemoryStateStore,
    artifact_store: ArtifactStore,
    state: SharedProjectState,
    assignment: TaskAssignment,
    content: str,
    kind: str = "external_result",
    title: str = "",
) -> Artifact:
    """Persist an external worker artifact and attach it to project state."""
    if not content.strip():
        raise ValueError("Task return artifact content cannot be empty.")
    artifact_id = _next_external_artifact_id(state, assignment.workitem_id)
    artifact = Artifact(
        id=artifact_id,
        project_id=state.project.id,
        workitem_id=assignment.workitem_id,
        agent_id=assignment.assigned_agent_id or "external-agent",
        kind=kind or "external_result",
        title=title or f"External Result - {assignment.workitem_id}",
        content=content,
        source_backend="task_center/external",
        parent_artifact_id=_parent_artifact_id_for_return(state, assignment),
        derived_from=list(assignment.input_artifact_ids),
    )
    persisted = artifact_store.save_markdown(artifact, project_root=state.project.project_root)
    state_store.add_artifact(state.project.id, persisted)
    return persisted


def _next_external_artifact_id(state: SharedProjectState, workitem_id: str) -> str:
    prefix = f"artifact-{workitem_id}-external"
    existing_ids = {artifact.id for artifact in state.artifacts}
    if prefix not in existing_ids:
        return prefix
    index = 2
    while f"{prefix}-{index}" in existing_ids:
        index += 1
    return f"{prefix}-{index}"


def _parent_artifact_id_for_return(state: SharedProjectState, assignment: TaskAssignment) -> str:
    """Infer the direct parent artifact for rework return artifacts."""
    workitem = next((item for item in state.workitems if item.id == assignment.workitem_id), None)
    if workitem is None or not workitem.rework_of:
        return ""
    input_ids = set(assignment.input_artifact_ids)
    for artifact in reversed(state.artifacts):
        if artifact.workitem_id != workitem.rework_of:
            continue
        if input_ids and artifact.id not in input_ids:
            continue
        return artifact.id
    return ""


__all__ = ["create_task_return_artifact"]
