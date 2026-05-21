"""Rule-based planner that minimally decomposes requirements into WorkItems."""

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


class Planner:
    """Split a requirement into stage-specific WorkItems with keyword rules."""

    UI_KEYWORDS = ("界面", "页面", "ui", "前端", "web", "页面设计", "交互")
    UI_NEGATION_KEYWORDS = ("no frontend", "no browser ui", "no ui", "without ui", "without frontend", "out of scope: browser ui")
    API_KEYWORDS = ("接口", "api", "后端", "服务", "restful", "http")
    TEST_KEYWORDS = ("测试", "test", "验证", "验收", "单元测试", "集成测试")
    DATA_KEYWORDS = ("数据", "数据库", "存储", "持久化", "mysql", "postgres", "sql")
    FRONTEND_ONLY_KEYWORDS = ("只做前端", "纯前端", "静态 web", "静态页面", "本地静态", "localstorage")
    BACKEND_NEGATION_KEYWORDS = ("不接后端", "无后端", "不涉及后端", "无需后端", "不需要后端", "不接 api", "不接api")
    DATABASE_NEGATION_KEYWORDS = ("不接数据库", "无数据库", "不涉及数据库", "无需数据库", "不需要数据库")

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
        """Generate the smallest useful set of WorkItems for a stage."""
        normalized_requirement = requirement.lower()
        drafts = self._build_drafts(stage.name, normalized_requirement, requirement)
        drafts = [draft for draft in drafts if self.scope_config.is_workitem_kind_enabled(draft.kind)]
        return [self._build_workitem(stage.name, draft) for draft in drafts]

    def _build_drafts(
        self,
        stage_name: str,
        normalized_requirement: str,
        original_requirement: str,
    ) -> list[WorkItemDraft]:
        if stage_name == "requirement":
            return self._plan_requirement_drafts(normalized_requirement, original_requirement)
        if stage_name == "design":
            return self._plan_design_drafts(normalized_requirement, original_requirement)
        if stage_name == "development":
            return self._plan_development_drafts(normalized_requirement, original_requirement)
        if stage_name == "testing":
            return self._plan_testing_drafts(normalized_requirement, original_requirement)
        return [
            WorkItemDraft(
                kind="generic",
                description=f"{stage_name} 阶段处理需求：{original_requirement}",
                acceptance_criteria=[f"{stage_name} 阶段产出可用于下一阶段"],
            )
        ]

    def _plan_requirement_drafts(self, normalized_requirement: str, original_requirement: str) -> list[WorkItemDraft]:
        """Plan the formal requirement clarification and freeze WorkItem."""
        return [
            WorkItemDraft(
                kind="requirement_spec",
                description=f"澄清需求、收敛范围并形成冻结需求规格：{original_requirement}",
                acceptance_criteria=[
                    "明确用户目标、核心功能和非目标范围",
                    "列出可执行验收标准、风险假设和待确认问题",
                    "通过多职责视角评审后生成冻结需求规格",
                ],
            )
        ]

    def _plan_design_drafts(self, normalized_requirement: str, original_requirement: str) -> list[WorkItemDraft]:
        frontend_only = self._is_frontend_only_requirement(normalized_requirement)
        drafts = [
            WorkItemDraft(
                kind="design_overview",
                description=f"基于冻结需求规格形成总体设计：{original_requirement}",
                acceptance_criteria=["输出设计要点", "明确下一阶段实现边界", "不得改变冻结需求范围"],
            )
        ]
        if self._contains_any(normalized_requirement, self.UI_KEYWORDS) and not self._excludes_ui_requirement(normalized_requirement):
            drafts.append(
                WorkItemDraft(
                    kind="ui_design",
                    description="整理 UI/页面相关设计约束与交互要点",
                    acceptance_criteria=["识别核心页面或交互点", "形成前端实现输入"],
                )
            )
        if self._contains_any(normalized_requirement, self.API_KEYWORDS) and not frontend_only:
            drafts.append(
                WorkItemDraft(
                    kind="api_design",
                    description="明确接口边界、输入输出和调用关系",
                    acceptance_criteria=["列出核心接口", "形成开发阶段接口说明"],
                )
            )
        if self._contains_any(normalized_requirement, self.TEST_KEYWORDS):
            drafts.append(
                WorkItemDraft(
                    kind="test_design",
                    description="梳理测试目标、验证方式与通过标准",
                    acceptance_criteria=["明确测试关注点", "形成测试阶段检查项"],
                )
            )
        return drafts

    def _plan_development_drafts(
        self,
        normalized_requirement: str,
        original_requirement: str,
    ) -> list[WorkItemDraft]:
        drafts: list[WorkItemDraft] = []
        frontend_only = self._is_frontend_only_requirement(normalized_requirement)
        if self._contains_any(normalized_requirement, self.API_KEYWORDS) and not frontend_only:
            drafts.append(
                WorkItemDraft(
                    kind="api_implementation",
                    description="实现接口与后端逻辑骨架",
                    acceptance_criteria=["接口行为可运行", "实现结果可供测试阶段验证"],
                )
            )
        if self._contains_any(normalized_requirement, self.UI_KEYWORDS) and not self._excludes_ui_requirement(normalized_requirement):
            drafts.append(
                WorkItemDraft(
                    kind="ui_implementation",
                    description="实现页面或交互相关代码骨架",
                    acceptance_criteria=["页面结构可运行", "交互入口清晰"],
                )
            )
        if self._contains_any(normalized_requirement, self.DATA_KEYWORDS) and not frontend_only:
            drafts.append(
                WorkItemDraft(
                    kind="data_implementation",
                    description="补充数据模型或状态结构实现",
                    acceptance_criteria=["数据结构满足当前需求", "与流程状态保持一致"],
                )
            )
        if not drafts:
            drafts.append(
                WorkItemDraft(
                    kind="generic_implementation",
                    description=f"完成需求对应的基础实现：{original_requirement}",
                    acceptance_criteria=["产出可运行的基础功能"],
                )
            )
        return drafts

    def _plan_testing_drafts(self, normalized_requirement: str, original_requirement: str) -> list[WorkItemDraft]:
        frontend_only = self._is_frontend_only_requirement(normalized_requirement)
        coverage_criteria = self._coverage_acceptance_criteria(original_requirement)
        testing_checklist = build_testing_checklist(original_requirement)
        drafts = [
            WorkItemDraft(
                kind="acceptance_check",
                description=f"校验交付结果是否满足需求：{original_requirement}",
                acceptance_criteria=[
                    "确认功能闭环",
                    "无阻塞当前交付的关键问题",
                    *coverage_criteria,
                ],
                testing_checklist=testing_checklist,
            )
        ]
        if self._contains_any(normalized_requirement, self.TEST_KEYWORDS):
            drafts.append(
                WorkItemDraft(
                    kind="automated_test",
                    description="补充自动化测试或验证脚本",
                    acceptance_criteria=["测试覆盖主要路径", "执行结果清晰可读"],
                )
            )
        if self._contains_any(normalized_requirement, self.API_KEYWORDS) and not frontend_only:
            drafts.append(
                WorkItemDraft(
                    kind="api_validation",
                    description="验证接口输入输出与关键边界",
                    acceptance_criteria=[
                        "接口行为符合预期",
                        "关键边界已覆盖",
                        "记录 endpoint、status code 和 response payload 证据",
                    ],
                )
            )
        if self._contains_any(normalized_requirement, self.UI_KEYWORDS) and not self._excludes_ui_requirement(normalized_requirement):
            drafts.append(
                WorkItemDraft(
                    kind="ui_validation",
                    description="验证页面展示和主要交互路径",
                    acceptance_criteria=["核心页面可访问", "关键交互路径可验证"],
                )
            )
        return drafts

    def _coverage_acceptance_criteria(self, requirement: str) -> list[str]:
        """Build testing acceptance criteria from inferred requirement concerns."""
        return [
            f"Provide validation evidence for frozen requirement: {rule.label}"
            for rule in infer_coverage_rules(requirement)
        ]

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
        """Return True when the text contains any keyword."""
        return any(self._contains_keyword(text, keyword) for keyword in keywords)

    def _contains_keyword(self, text: str, keyword: str) -> bool:
        """Return whether text contains a keyword without short ASCII substring drift."""
        if keyword.isascii() and keyword.replace("-", "").isalnum():
            pattern = rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])"
            return re.search(pattern, text) is not None
        return keyword in text

    def _is_frontend_only_requirement(self, text: str) -> bool:
        """Return whether the requirement explicitly excludes backend/database work."""
        if self._contains_any(text, self.API_KEYWORDS) and not self._contains_any(text, self.BACKEND_NEGATION_KEYWORDS):
            return False
        has_frontend_scope = self._contains_any(text, self.UI_KEYWORDS) or self._contains_any(text, self.FRONTEND_ONLY_KEYWORDS)
        excludes_backend = self._contains_any(text, self.BACKEND_NEGATION_KEYWORDS)
        excludes_database = self._contains_any(text, self.DATABASE_NEGATION_KEYWORDS)
        local_static_scope = self._contains_any(text, self.FRONTEND_ONLY_KEYWORDS) and (
            "localstorage" in text or "本地" in text or "静态" in text
        )
        return has_frontend_scope and (excludes_backend or excludes_database or local_static_scope)

    def _excludes_ui_requirement(self, text: str) -> bool:
        """Return whether UI terms describe excluded scope rather than requested UI work."""
        return self._contains_any(text, self.UI_NEGATION_KEYWORDS)
