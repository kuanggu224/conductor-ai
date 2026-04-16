"""WorkflowTemplate 与 GateDecision 定义。

支持从配置系统读取工作流程配置，而不是硬编码。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from conductor.config.system import SystemConfig
from conductor.domain.models import Stage, WorkItem, WorkItemStatus


class GateDecision(StrEnum):
    """阶段 Gate 判断。"""

    PASS = "pass"
    REWORK = "rework"
    RETRY = "retry"
    ESCALATE = "escalate"


@dataclass(slots=True)
class WorkflowTemplate:
    """默认线性 Workflow 模板。"""

    stages: list[Stage] = field(default_factory=list)
    config: SystemConfig | None = None

    def __post_init__(self) -> None:
        """在未显式传入时注入默认阶段。"""
        self.config = self.config or SystemConfig.load()
        if not self.stages:
            self.stages = [
                Stage(**stage_config) for stage_config in self.config.workflow.stages
            ]

    def get_first_stage(self) -> Stage:
        """返回首个 Stage。"""
        return self.stages[0]

    def get_next_stage(self, current_stage_name: str) -> Stage | None:
        """根据当前阶段名查找下一个 Stage。"""
        for index, stage in enumerate(self.stages):
            if stage.name == current_stage_name:
                next_index = index + 1
                if next_index < len(self.stages):
                    return self.stages[next_index]
                return None
        raise ValueError(f"未找到阶段: {current_stage_name}")


class WorkflowGateEvaluator:
    """最小 Gate 评估器。"""

    def evaluate(self, stage_name: str | None, workitems: list[WorkItem]) -> GateDecision:
        """根据当前阶段 WorkItem 状态给出 GateDecision。"""
        if not stage_name:
            return GateDecision.ESCALATE
        if not workitems:
            return GateDecision.PASS
        if any(item.status == WorkItemStatus.FAILED and item.retry_count >= item.max_retries for item in workitems):
            return GateDecision.ESCALATE
        if any(item.status == WorkItemStatus.FAILED for item in workitems):
            return GateDecision.RETRY
        if any(item.status in {WorkItemStatus.PENDING, WorkItemStatus.RUNNING} for item in workitems):
            return GateDecision.REWORK
        if all(item.status == WorkItemStatus.DONE for item in workitems):
            return GateDecision.PASS
        return GateDecision.REWORK
