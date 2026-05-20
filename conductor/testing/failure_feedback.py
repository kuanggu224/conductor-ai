"""Structured testing failure feedback for development rework."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from conductor.domain.models import Artifact, SharedProjectState, WorkItem


@dataclass(slots=True)
class TestingFailureFeedback:
    """Actionable feedback extracted from a failed testing WorkItem."""

    workitem_id: str
    failure_type: str = ""
    summary: str = ""
    exit_code: str = ""
    failing_checks: list[str] = field(default_factory=list)
    missing_coverage: list[str] = field(default_factory=list)
    missing_checklist_items: list[dict[str, object]] = field(default_factory=list)
    suggested_actions: list[str] = field(default_factory=list)

    def render_markdown(self) -> str:
        """Render the feedback as a prompt-ready Markdown section."""
        failing_checks = _bullet_lines(self.failing_checks)
        missing_coverage = _bullet_lines(self.missing_coverage)
        missing_checklist = _checklist_lines(self.missing_checklist_items)
        suggested_actions = _bullet_lines(self.suggested_actions)
        return (
            "## 结构化测试反馈\n"
            f"- Failed WorkItem: `{self.workitem_id}`\n"
            f"- Failure Type: `{self.failure_type or 'unknown'}`\n"
            f"- Summary: {self.summary or '未提供'}\n"
            f"- Exit Code: `{self.exit_code or '-'}`\n\n"
            "### 失败信号\n"
            f"{failing_checks}\n\n"
            "### 缺失需求覆盖\n"
            f"{missing_coverage}\n\n"
            "### 缺失 Testing Checklist\n"
            f"{missing_checklist}\n\n"
            "### 建议修复方向\n"
            f"{suggested_actions}"
        )


def build_testing_failure_feedback(workitem: WorkItem, artifacts: list[Artifact]) -> TestingFailureFeedback:
    """Build structured feedback from WorkItem failure fields and report artifacts."""
    text = "\n".join(
        item
        for item in [
            workitem.failure_summary,
            workitem.result or "",
            workitem.blocked_reason or "",
            *[artifact.content for artifact in artifacts],
        ]
        if item
    )
    failing_checks = _extract_failure_lines(text)
    missing_coverage = _extract_missing_coverage(text)
    missing_checklist_items = _missing_checklist_items(workitem, missing_coverage)
    exit_code = _extract_exit_code(text)
    summary = workitem.failure_summary or _first_non_empty([*missing_coverage, *failing_checks, workitem.result or ""])
    suggested_actions = _suggest_actions(text=text, failing_checks=failing_checks, missing_coverage=missing_coverage)
    return TestingFailureFeedback(
        workitem_id=workitem.id,
        failure_type=workitem.failure_type or ("validation_failed" if failing_checks or missing_coverage else ""),
        summary=summary[:500],
        exit_code=exit_code,
        failing_checks=failing_checks[:8],
        missing_coverage=missing_coverage[:8],
        missing_checklist_items=missing_checklist_items[:8],
        suggested_actions=suggested_actions[:8],
    )


def build_testing_feedback_for_workitem(state: SharedProjectState, workitem: WorkItem) -> list[TestingFailureFeedback]:
    """Return structured testing feedback related to a WorkItem or its feedback sources."""
    testing_items: list[WorkItem] = []
    if workitem.stage == "testing":
        testing_items.append(workitem)
    if workitem.feedback_from:
        by_id = {item.id: item for item in state.workitems}
        testing_items.extend(
            item
            for workitem_id in workitem.feedback_from
            if (item := by_id.get(workitem_id)) is not None and item.stage == "testing"
        )

    feedback_items: list[TestingFailureFeedback] = []
    seen: set[str] = set()
    for testing_item in testing_items:
        if testing_item.id in seen:
            continue
        seen.add(testing_item.id)
        artifacts = [artifact for artifact in state.artifacts if artifact.workitem_id == testing_item.id]
        if not _has_testing_failure_evidence(testing_item) and not artifacts:
            continue
        feedback_items.append(build_testing_failure_feedback(testing_item, artifacts))
    return feedback_items


def _has_testing_failure_evidence(workitem: WorkItem) -> bool:
    return (
        workitem.stage == "testing"
        and bool(workitem.failure_type or workitem.failure_summary or workitem.blocked_reason or workitem.result)
    )


def _extract_failure_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines()]
    findings: list[str] = []
    capture_errors = False
    for line in lines:
        if not line:
            capture_errors = False
            continue
        lowered = line.lower()
        if lowered in {"errors:", "## errors"} or lowered.startswith("errors:"):
            capture_errors = True
            continue
        if capture_errors and line.startswith("-"):
            findings.append(line.lstrip("- ").strip())
            continue
        if _looks_like_failure_signal(line):
            findings.append(line.lstrip("- ").strip())
    return _dedupe(findings)


def _extract_missing_coverage(text: str) -> list[str]:
    findings: list[str] = []
    for match in re.finditer(r"Requirement coverage missing:\s*([^\n]+)", text, flags=re.IGNORECASE):
        findings.extend(_split_labels(match.group(1)))
    for line in text.splitlines():
        if "`missing`" not in line and "| missing |" not in line.lower():
            continue
        cells = [cell.strip(" `") for cell in line.strip().strip("|").split("|")]
        if cells:
            findings.append(cells[0])
    return _dedupe(item for item in findings if item and item.lower() not in {"rule", "status"})


def _extract_exit_code(text: str) -> str:
    match = re.search(r"Exit Code:\s*`?(-?\d+)`?", text, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _suggest_actions(*, text: str, failing_checks: list[str], missing_coverage: list[str]) -> list[str]:
    lowered = text.lower()
    suggestions: list[str] = []
    if missing_coverage:
        suggestions.append("补齐缺失的冻结需求验收证据，优先覆盖列出的 missing coverage 项。")
    if "browser form submit did not change" in lowered or "visible state" in lowered:
        suggestions.append("检查表单/按钮事件绑定，确保提交后页面可见状态发生变化。")
    if (
        "localstorage" in lowered
        or "reload preserved" in lowered
        or "reload did not preserve" in lowered
        or "refresh persistence" in lowered
    ):
        suggestions.append("检查 localStorage 写入、读取和刷新后恢复逻辑。")
    if "export" in lowered or "download" in lowered or "csv" in lowered:
        suggestions.append("检查导出按钮、下载触发和 CSV/text 内容生成。")
    if "filter interaction" in lowered or "browser filter interaction" in lowered:
        suggestions.append("检查筛选/搜索控件事件绑定，确保切换条件后可见结果列表发生变化。")
    if "delete item interaction" in lowered or "browser delete interaction" in lowered:
        suggestions.append("检查删除/移除按钮事件绑定，确保触发后对应条目从可见列表中移除。")
    if "file import" in lowered or "file upload" in lowered or "browser file import" in lowered:
        suggestions.append("Check file selection, import/upload handlers, and CSV/text parsing so sample file content appears in the UI or local storage.")
    if "api endpoint behavior" in lowered or "api validation exercised" in lowered:
        suggestions.append("Check API route wiring, request/response payloads, status codes, and pytest/TestClient coverage for the expected endpoint behavior.")
    if "mojibake" in lowered or "corrupted utf-8" in lowered:
        suggestions.append("修复生成文件中的中文编码问题，确保 HTML/JS/CSS 均为 UTF-8。")
    if "missing" in lowered and ("asset" in lowered or "index.html" in lowered):
        suggestions.append("补齐缺失的入口文件或本地静态资源引用。")
    if not suggestions and failing_checks:
        suggestions.append("根据失败信号定位对应实现路径，完成最小范围修复后重新运行测试。")
    if not suggestions:
        suggestions.append("重新运行测试命令，补充更明确的失败报告后再返工。")
    return _dedupe(suggestions)


def _missing_checklist_items(workitem: WorkItem, missing_coverage: list[str]) -> list[dict[str, object]]:
    """Return testing checklist entries that match missing coverage labels or rule ids."""
    if not missing_coverage or not workitem.testing_checklist:
        return []
    missing_keys = {item.lower() for item in missing_coverage}
    items: list[dict[str, object]] = []
    for checklist_item in workitem.testing_checklist:
        rule_id = str(checklist_item.get("rule_id", ""))
        label = str(checklist_item.get("label", ""))
        if rule_id.lower() not in missing_keys and label.lower() not in missing_keys:
            continue
        copied = dict(checklist_item)
        copied["status"] = "missing"
        items.append(copied)
    return items


def _looks_like_failure_signal(line: str) -> bool:
    lowered = line.lower()
    signals = (
        "failed",
        "error",
        "missing",
        "did not",
        "not found",
        "traceback",
        "assert",
        "mojibake",
        "corrupted utf-8",
    )
    return any(signal in lowered for signal in signals)


def _split_labels(value: str) -> list[str]:
    return [item.strip(" .`") for item in re.split(r",|;", value) if item.strip(" .`")]


def _first_non_empty(values: list[str]) -> str:
    for value in values:
        if value:
            return value
    return ""


def _bullet_lines(values) -> str:
    items = [str(value).strip() for value in values if str(value).strip()]
    return "\n".join(f"- {item}" for item in items) if items else "- None"


def _checklist_lines(items: list[dict[str, object]]) -> str:
    if not items:
        return "- None"
    lines: list[str] = []
    for item in items:
        evidence_terms = ", ".join(str(value) for value in item.get("required_evidence_terms", []) or []) or "-"
        lines.append(
            f"- `{item.get('rule_id', '')}` {item.get('label', '')}: "
            f"status={item.get('status', '')}, required_evidence={evidence_terms}"
        )
    return "\n".join(lines)


def _dedupe(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value).strip()
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


__all__ = ["TestingFailureFeedback", "build_testing_failure_feedback", "build_testing_feedback_for_workitem"]
