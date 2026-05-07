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
        dependency_artifacts = [
            artifact for artifact in previous_artifacts if artifact.workitem_id in dependency_ids
        ]
        design_artifacts = [
            artifact for artifact in previous_artifacts if self._infer_artifact_stage(artifact.kind) == "design"
        ]
        stage_rank = {"design": 0, "development": 1, "testing": 2}
        current_rank = stage_rank.get(workitem.stage, 99)
        relevant = [
            artifact
            for artifact in previous_artifacts
            if stage_rank.get(self._infer_artifact_stage(artifact.kind), 99) <= current_rank
        ]
        priority = [*dependency_artifacts, *design_artifacts]
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

    def _infer_artifact_stage(self, kind: str) -> str:
        """根据产物类型推断来源阶段。"""
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
