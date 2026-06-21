"""Rule-based backend/API planner."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import count

from conductor.config.execution import ExecutionScopeConfig
from conductor.config.system import SystemConfig
from conductor.domain.models import Stage, WorkItem
from conductor.testing.coverage import build_testing_checklist, infer_coverage_rules


@dataclass(slots=True)
class WorkItemDraft:
    """Intermediate WorkItem description used before entity creation."""

    kind: str
    description: str
    acceptance_criteria: list[str]
    testing_checklist: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FeatureSlice:
    """One user-visible backend/API feature slice inferred from a requirement."""

    slice_id: str
    title: str
    milestone: str
    validation_focus: str


class Planner:
    """Split a requirement into backend/API WorkItems with keyword rules."""

    API_KEYWORDS = ("api", "接口", "后端", "服务", "rest", "restful", "http", "endpoint")
    TEST_KEYWORDS = ("测试", "test", "pytest", "验证", "验收")
    DATA_KEYWORDS = ("数据", "数据库", "存储", "持久化", "mysql", "postgres", "sqlite", "sql", "schema")

    def __init__(
        self,
        start_index: int = 1,
        scope_config: ExecutionScopeConfig | None = None,
        config: SystemConfig | None = None,
    ) -> None:
        self._workitem_counter = count(start_index)
        self.scope_config = scope_config or ExecutionScopeConfig()
        self.config = config or SystemConfig.load()

    def plan_stage_workitems(self, stage: Stage, requirement: str) -> list[WorkItem]:
        """Generate the smallest useful backend/API work set for a stage."""
        normalized_requirement = self._positive_scope_text(requirement.lower())
        drafts = self._build_drafts(stage.name, normalized_requirement, requirement)
        drafts = [draft for draft in drafts if self.scope_config.is_workitem_kind_enabled(draft.kind)]
        return [self._build_workitem(stage.name, draft) for draft in drafts]

    def _build_drafts(self, stage_name: str, normalized_requirement: str, original_requirement: str) -> list[WorkItemDraft]:
        if stage_name == "requirement":
            return self._plan_requirement_drafts(original_requirement)
        if stage_name == "design":
            return self._plan_design_drafts(normalized_requirement, original_requirement)
        if stage_name == "development":
            return self._plan_development_drafts(normalized_requirement, original_requirement)
        if stage_name == "testing":
            return self._plan_testing_drafts(normalized_requirement, original_requirement)
        return [
            WorkItemDraft(
                kind="generic",
                description=f"Handle {stage_name} stage requirement: {original_requirement}",
                acceptance_criteria=[f"{stage_name} output is usable by the next stage"],
            )
        ]

    def _plan_requirement_drafts(self, original_requirement: str) -> list[WorkItemDraft]:
        return [
            WorkItemDraft(
                kind="requirement_spec",
                description=f"Clarify scope and freeze executable requirement: {original_requirement}",
                acceptance_criteria=[
                    "User goals, core functions, non-goals, risks, and assumptions are explicit.",
                    "Acceptance criteria and edge cases are executable by downstream agents.",
                    "Client-facing implementation scope is disabled for this platform.",
                ],
            )
        ]

    def _plan_design_drafts(self, normalized_requirement: str, original_requirement: str) -> list[WorkItemDraft]:
        feature_slices = self._infer_feature_slices(normalized_requirement)
        drafts = [
            WorkItemDraft(
                kind="design_overview",
                description=f"Create backend/API design boundaries for requirement: {original_requirement}",
                acceptance_criteria=[
                    "Backend/API responsibilities and downstream implementation boundaries are clear.",
                    "Client-facing product surfaces are excluded from the design scope.",
                ],
            )
        ]
        if len(feature_slices) >= 2:
            drafts.append(
                WorkItemDraft(
                    kind="feature_slice_plan",
                    description=self._feature_slice_plan_description(original_requirement, feature_slices),
                    acceptance_criteria=self._feature_slice_plan_acceptance_criteria(feature_slices),
                )
            )
        if self._contains_any(normalized_requirement, self.API_KEYWORDS):
            drafts.append(
                WorkItemDraft(
                    kind="api_design",
                    description="Define API boundaries, request/response payloads, and error behavior.",
                    acceptance_criteria=["Core endpoints are identified.", "API contracts are ready for backend implementation."],
                )
            )
        if self._contains_any(normalized_requirement, self.TEST_KEYWORDS):
            drafts.append(
                WorkItemDraft(
                    kind="test_design",
                    description="Define backend/API validation targets and pass/fail evidence.",
                    acceptance_criteria=["Test scope is explicit.", "Validation evidence can be reproduced."],
                )
            )
        return drafts

    def _plan_development_drafts(self, normalized_requirement: str, original_requirement: str) -> list[WorkItemDraft]:
        drafts: list[WorkItemDraft] = []
        feature_slices = self._infer_feature_slices(normalized_requirement)
        slice_criteria = self._feature_slice_execution_criteria(feature_slices)
        if self._contains_any(normalized_requirement, self.API_KEYWORDS):
            drafts.append(
                WorkItemDraft(
                    kind="api_implementation",
                    description="Implement backend service/API behavior.",
                    acceptance_criteria=["API behavior is runnable.", "Implementation can be verified by tests."],
                )
            )
        if self._contains_any(normalized_requirement, self.DATA_KEYWORDS):
            drafts.append(
                WorkItemDraft(
                    kind="data_implementation",
                    description="Implement data model, persistence, or state handling.",
                    acceptance_criteria=["Data structures match the requirement.", "Persistence/state behavior is testable."],
                )
            )
        if not drafts:
            drafts.append(
                WorkItemDraft(
                    kind="generic_implementation",
                    description=f"Implement backend/API-capable functionality for requirement: {original_requirement}",
                    acceptance_criteria=["A runnable backend/API or service-level implementation is produced."],
                )
            )
        if slice_criteria:
            for draft in drafts:
                draft.acceptance_criteria.extend(slice_criteria)
        return drafts

    def _plan_testing_drafts(self, normalized_requirement: str, original_requirement: str) -> list[WorkItemDraft]:
        coverage_criteria = self._coverage_acceptance_criteria(original_requirement)
        feature_slices = self._infer_feature_slices(normalized_requirement)
        testing_checklist = build_testing_checklist(original_requirement)
        drafts = [
            WorkItemDraft(
                kind="acceptance_check",
                description=f"Validate backend/API delivery against requirement: {original_requirement}",
                acceptance_criteria=["Functional behavior is verified.", "No blocking delivery issue remains.", *coverage_criteria],
                testing_checklist=testing_checklist,
            )
        ]
        if self._contains_any(normalized_requirement, self.TEST_KEYWORDS):
            drafts.append(
                WorkItemDraft(
                    kind="automated_test",
                    description="Add or run backend/API automated tests.",
                    acceptance_criteria=["Core paths are covered.", "Execution result is readable and reproducible."],
                )
            )
        if self._contains_any(normalized_requirement, self.API_KEYWORDS):
            drafts.append(
                WorkItemDraft(
                    kind="api_validation",
                    description="Validate API inputs, outputs, status codes, and edge behavior.",
                    acceptance_criteria=["API behavior matches expectations.", "Endpoint/status/payload evidence is recorded."],
                )
            )
        drafts[0].acceptance_criteria.extend(self._feature_slice_validation_criteria(feature_slices))
        return drafts

    def _coverage_acceptance_criteria(self, requirement: str) -> list[str]:
        return [f"Provide validation evidence for frozen requirement: {rule.label}" for rule in infer_coverage_rules(requirement)]

    def _infer_feature_slices(self, text: str) -> list[FeatureSlice]:
        candidates = (
            (("create", "add", "submit", "新增", "添加", "创建"), "create_item", "Create item", "M1 Core input", "create request and persisted/returned record"),
            (("list", "view", "browse", "查询", "列表", "展示"), "list_items", "List items", "M1 Core read", "list response or collection query"),
            (("filter", "search", "query", "过滤", "搜索"), "filter_items", "Filter/search items", "M2 Refinement", "filtered response evidence"),
            (("update", "edit", "complete", "toggle", "修改", "编辑", "完成"), "update_item", "Update item", "M2 Refinement", "update response or changed record"),
            (("delete", "remove", "删除", "移除"), "delete_item", "Delete item", "M2 Refinement", "delete response or removed record"),
            (("stats", "summary", "report", "统计", "汇总", "报表"), "stats", "Stats summary", "M3 Evidence", "stats payload or report evidence"),
            (("import", "upload", "导入", "上传"), "file_import", "File import/upload", "M3 Evidence", "sample file processing evidence"),
            (("export", "download", "csv", "导出", "下载"), "export_csv", "Export/download", "M3 Evidence", "export/download evidence"),
        )
        slices: list[FeatureSlice] = []
        for terms, slice_id, title, milestone, validation_focus in candidates:
            if any(self._contains_keyword(text, term.lower()) for term in terms):
                slices.append(FeatureSlice(slice_id, title, milestone, validation_focus))
        return slices

    def _feature_slice_plan_description(self, requirement: str, slices: list[FeatureSlice]) -> str:
        summary = ", ".join(f"{item.slice_id} ({item.milestone})" for item in slices)
        return f"Plan backend/API feature slices for requirement: {requirement}. Slices: {summary}"

    def _feature_slice_plan_acceptance_criteria(self, slices: list[FeatureSlice]) -> list[str]:
        criteria = ["Define milestone order, dependencies, implementation boundary, and validation evidence for each feature slice."]
        criteria.extend(f"Feature slice {item.slice_id}: {item.title}; milestone={item.milestone}; validation={item.validation_focus}" for item in slices)
        return criteria

    def _feature_slice_execution_criteria(self, slices: list[FeatureSlice]) -> list[str]:
        if len(slices) < 2:
            return []
        return [f"Implement feature slices in milestone order: {' -> '.join(item.slice_id for item in slices)}"]

    def _feature_slice_validation_criteria(self, slices: list[FeatureSlice]) -> list[str]:
        if len(slices) < 2:
            return []
        return [f"Verify feature slice {item.slice_id}: {item.validation_focus}" for item in slices]

    def _build_workitem(self, stage_name: str, draft: WorkItemDraft) -> WorkItem:
        sequence = next(self._workitem_counter)
        return WorkItem(
            id=f"workitem-{sequence:03d}",
            description=draft.description,
            stage=stage_name,
            kind=draft.kind,
            acceptance_criteria=draft.acceptance_criteria,
            testing_checklist=[dict(item) for item in draft.testing_checklist],
        )

    def _contains_any(self, text: str, keywords: tuple[str, ...]) -> bool:
        return any(self._contains_keyword(text, keyword) for keyword in keywords)

    def _positive_scope_text(self, text: str) -> str:
        markers = ("out of scope", "non-goal", "non goal", "not in scope", "excluded", "exclude", "不做", "不包含")
        cleaned_lines: list[str] = []
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            scoped = line
            positions = [scoped.find(marker) for marker in markers if marker in scoped]
            if positions:
                scoped = scoped[: min(position for position in positions if position >= 0)]
            if scoped.strip():
                cleaned_lines.append(scoped)
        return "\n".join(cleaned_lines)

    def _contains_keyword(self, text: str, keyword: str) -> bool:
        if keyword.isascii() and keyword.replace("-", "").isalnum():
            pattern = rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])"
            return re.search(pattern, text) is not None
        return keyword in text
