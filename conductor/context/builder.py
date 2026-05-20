"""ContextPack 最小组装器。"""

from __future__ import annotations

from conductor.artifacts.store import ArtifactStore
from conductor.context.models import ContextPack
from conductor.domain.models import Artifact, SharedProjectState, WorkItem
from conductor.memory.models import GlobalMemory


class ContextBuilder:
    """从 SharedProjectState 中为当前 WorkItem 组装最小上下文。"""

    def __init__(
        self,
        max_artifacts: int = 6,
        max_events: int = 12,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self.max_artifacts = max_artifacts
        self.max_events = max_events
        self.artifact_store = artifact_store or ArtifactStore()

    def build(self, state: SharedProjectState, workitem: WorkItem) -> ContextPack:
        """构建当前 WorkItem 的 ContextPack。"""
        artifacts = self._select_artifacts(state, workitem)
        return ContextPack(
            current_workitem=workitem,
            relevant_state=state,
            relevant_memory=GlobalMemory(
                project_memory=[state.project.goal],
                decision_memory=state.gate_history[-self.max_events :],
                artifact_memory=[self._format_artifact_summary(artifact) for artifact in artifacts],
            ),
            artifact_ids=[artifact.id for artifact in artifacts],
            artifacts=[self._format_artifact(artifact) for artifact in artifacts],
            acceptance_criteria=workitem.acceptance_criteria,
        )

    def _select_artifacts(self, state: SharedProjectState, workitem: WorkItem) -> list[Artifact]:
        """选择对当前 WorkItem 有用的历史产物。"""
        previous_artifacts = [
            artifact
            for artifact in state.artifacts
            if artifact.workitem_id != workitem.id
        ]
        dependency_ids = set(workitem.dependencies)
        explicit_artifact_ids = set(workitem.input_artifact_ids)
        explicit_artifacts = [
            artifact for artifact in previous_artifacts if artifact.id in explicit_artifact_ids
        ]
        dependency_artifacts = [
            artifact for artifact in previous_artifacts if artifact.workitem_id in dependency_ids
        ]
        frozen_requirement_artifacts = [
            artifact for artifact in previous_artifacts if artifact.kind == "frozen_requirement_spec"
        ]
        frozen_design_artifacts = [
            artifact for artifact in previous_artifacts if artifact.kind == "frozen_design_spec"
        ]
        lineage_seed_ids = {
            *explicit_artifact_ids,
            *[artifact.id for artifact in dependency_artifacts],
            *[artifact.id for artifact in frozen_requirement_artifacts],
            *[artifact.id for artifact in frozen_design_artifacts],
        }
        lineage_artifacts = self._lineage_artifacts(previous_artifacts, lineage_seed_ids)
        design_artifacts = [
            artifact for artifact in previous_artifacts if self._infer_artifact_stage(artifact.kind) == "design"
        ]
        stage_rank = {"requirement": 0, "design": 1, "development": 2, "testing": 3}
        current_rank = stage_rank.get(workitem.stage, 99)
        relevant = [
            artifact
            for artifact in previous_artifacts
            if stage_rank.get(self._infer_artifact_stage(artifact.kind), 99) <= current_rank
        ]
        priority = [
            *frozen_requirement_artifacts,
            *frozen_design_artifacts,
            *explicit_artifacts,
            *dependency_artifacts,
            *lineage_artifacts,
            *design_artifacts,
        ]
        recents = relevant[-self.max_artifacts :]
        ordered = [*priority, *recents]
        deduped: list[Artifact] = []
        seen: set[str] = set()
        for artifact in ordered:
            if artifact.id in seen:
                continue
            seen.add(artifact.id)
            deduped.append(artifact)
        return deduped[: self.max_artifacts]

    def _lineage_artifacts(self, artifacts: list[Artifact], seed_ids: set[str]) -> list[Artifact]:
        """Return artifacts connected to explicit inputs through lineage fields."""
        if not seed_ids:
            return []
        by_id = {artifact.id: artifact for artifact in artifacts}
        selected_ids: set[str] = set()
        queue = [artifact_id for artifact_id in seed_ids if artifact_id in by_id]
        while queue:
            artifact_id = queue.pop(0)
            if artifact_id in selected_ids:
                continue
            artifact = by_id.get(artifact_id)
            if artifact is None:
                continue
            selected_ids.add(artifact_id)
            related_ids = [
                artifact.parent_artifact_id or "",
                artifact.review_of or "",
                *artifact.derived_from,
            ]
            for related_id in related_ids:
                if related_id and related_id in by_id and related_id not in selected_ids:
                    queue.append(related_id)
            for candidate in artifacts:
                if candidate.id in selected_ids:
                    continue
                if (
                    candidate.parent_artifact_id == artifact_id
                    or candidate.review_of == artifact_id
                    or artifact_id in candidate.derived_from
                ):
                    queue.append(candidate.id)
        return [artifact for artifact in artifacts if artifact.id in selected_ids]

    def _infer_artifact_stage(self, kind: str) -> str:
        """根据产物类型推断来源阶段。"""
        if kind in {"requirement_spec", "frozen_requirement_spec"}:
            return "requirement"
        if kind == "frozen_design_spec":
            return "design"
        if "design" in kind:
            return "design"
        if "test" in kind or "validation" in kind or kind == "acceptance_check":
            return "testing"
        return "development"

    def _format_artifact_summary(self, artifact: Artifact) -> str:
        """格式化产物摘要。"""
        return f"{artifact.title} ({artifact.id}, 来源 {artifact.workitem_id})"

    def _format_artifact(self, artifact: Artifact) -> str:
        """格式化可注入 LLM 的产物内容。"""
        return (
            f"## {artifact.title}\n"
            f"- 产物 ID: {artifact.id}\n"
            f"- 来源 WorkItem: {artifact.workitem_id}\n"
            f"- Agent: {artifact.agent_id}\n"
            f"- 类型: {artifact.kind}\n\n"
            f"{self.artifact_store.read_content(artifact)}"
        )
