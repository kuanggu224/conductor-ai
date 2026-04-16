"""内存版 Shared State Store。"""

from __future__ import annotations

from dataclasses import replace

from conductor.collaboration.models import Collaboration
from conductor.domain.models import Artifact, ProjectStatus, SharedProjectState, WorkItemStatus


class InMemoryStateStore:
    """使用内存保存项目状态。"""

    ALLOWED_TRANSITIONS: dict[WorkItemStatus, set[WorkItemStatus]] = {
        WorkItemStatus.PENDING: {WorkItemStatus.RUNNING},
        WorkItemStatus.RUNNING: {WorkItemStatus.DONE, WorkItemStatus.FAILED},
        WorkItemStatus.FAILED: {WorkItemStatus.PENDING},
        WorkItemStatus.DONE: set(),
    }

    def __init__(self) -> None:
        self._states: dict[str, SharedProjectState] = {}

    def save_state(self, state: SharedProjectState) -> None:
        """保存或覆盖指定项目状态。"""
        self._states[state.project.id] = state

    def get_state(self, project_id: str) -> SharedProjectState:
        """读取指定项目状态。"""
        try:
            return self._states[project_id]
        except KeyError as error:
            raise KeyError(f"未找到项目状态: {project_id}") from error

    def list_states(self) -> list[SharedProjectState]:
        """返回所有项目状态。"""
        return list(self._states.values())

    def update_workitem(
        self,
        project_id: str,
        workitem_id: str,
        status: WorkItemStatus,
        owner_agent: str | None = None,
        result: str | None = None,
        retry_count: int | None = None,
    ) -> SharedProjectState:
        """更新 WorkItem 状态并返回最新 state。"""
        state = self.get_state(project_id)
        updated_workitems = []
        found = False
        for workitem in state.workitems:
            if workitem.id == workitem_id:
                self._validate_transition(workitem.status, status)
                updated_workitems.append(
                    replace(
                        workitem,
                        status=status,
                        owner_agent=owner_agent if owner_agent is not None else workitem.owner_agent,
                        result=result if result is not None else workitem.result,
                        retry_count=retry_count if retry_count is not None else workitem.retry_count,
                    )
                )
                found = True
            else:
                updated_workitems.append(workitem)
        if not found:
            raise KeyError(f"未找到工作项: {workitem_id}")
        new_state = replace(state, workitems=updated_workitems)
        if status == WorkItemStatus.DONE and new_state.project_status == ProjectStatus.INITIALIZED:
            new_state = replace(new_state, project_status=ProjectStatus.IN_PROGRESS)
        self.save_state(new_state)
        return new_state

    def _validate_transition(self, current: WorkItemStatus, target: WorkItemStatus) -> None:
        """校验 WorkItem 状态迁移是否合法。"""
        if current == target:
            return
        allowed = self.ALLOWED_TRANSITIONS[current]
        if target not in allowed:
            raise ValueError(f"非法状态迁移: {current.value} -> {target.value}")

    def add_event(self, project_id: str, event: str) -> SharedProjectState:
        """向项目事件日志追加事件。"""
        state = self.get_state(project_id)
        new_state = replace(state, recent_events=[*state.recent_events, event])
        self.save_state(new_state)
        return new_state

    def add_artifact(self, project_id: str, artifact: Artifact) -> SharedProjectState:
        """向项目追加或更新产物。"""
        state = self.get_state(project_id)
        replaced = False
        artifacts = []
        for existing in state.artifacts:
            if existing.id == artifact.id:
                artifacts.append(artifact)
                replaced = True
            else:
                artifacts.append(existing)
        if not replaced:
            artifacts.append(artifact)
        new_state = replace(state, artifacts=artifacts)
        self.save_state(new_state)
        return new_state

    def add_collaboration(self, project_id: str, collaboration: Collaboration) -> SharedProjectState:
        """向项目追加协作记录。"""
        state = self.get_state(project_id)
        new_state = replace(state, collaborations=[*state.collaborations, collaboration])
        self.save_state(new_state)
        return new_state

    def upsert_collaboration(self, project_id: str, collaboration: Collaboration) -> SharedProjectState:
        """新增或更新协作记录。"""
        state = self.get_state(project_id)
        replaced = False
        collaborations = []
        for existing in state.collaborations:
            if existing.id == collaboration.id:
                collaborations.append(collaboration)
                replaced = True
            else:
                collaborations.append(existing)
        if not replaced:
            collaborations.append(collaboration)
        new_state = replace(state, collaborations=collaborations)
        self.save_state(new_state)
        return new_state
