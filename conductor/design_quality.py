"""Lightweight design-stage quality gate."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class DesignQualityResult:
    """Quality evaluation for a design artifact."""

    score: int
    passed: bool
    findings: list[str] = field(default_factory=list)
    dimension_scores: dict[str, int] = field(default_factory=dict)


SECTION_TERMS: tuple[str, ...] = ("目标", "需求理解", "范围", "方案", "验收")
REQUIREMENT_TRACE_TERMS: tuple[str, ...] = ("需求", "验收", "用户", "目标")
IMPLEMENTATION_BOUNDARY_TERMS: tuple[str, ...] = ("范围", "非目标", "边界", "不做", "约束")
ARCHITECTURE_TERMS: tuple[str, ...] = ("架构", "模块", "组件", "流程", "页面", "接口")
DATA_STATE_TERMS: tuple[str, ...] = ("数据", "字段", "状态", "存储", "localStorage", "API", "接口")
VALIDATION_TERMS: tuple[str, ...] = ("测试", "验收", "验证", "异常", "错误", "边界")
RISK_TERMS: tuple[str, ...] = ("风险", "假设", "待确认", "限制", "依赖")
SECTION_TERMS = (
    *SECTION_TERMS,
    "goal",
    "requirement understanding",
    "scope",
    "solution",
    "acceptance",
)
REQUIREMENT_TRACE_TERMS = (
    *REQUIREMENT_TRACE_TERMS,
    "requirement",
    "acceptance",
    "user",
    "goal",
)
IMPLEMENTATION_BOUNDARY_TERMS = (
    *IMPLEMENTATION_BOUNDARY_TERMS,
    "scope",
    "non-goal",
    "boundary",
    "out of scope",
    "constraint",
)
ARCHITECTURE_TERMS = (
    *ARCHITECTURE_TERMS,
    "architecture",
    "module",
    "component",
    "flow",
    "page",
    "interface",
)
DATA_STATE_TERMS = (
    *DATA_STATE_TERMS,
    "data",
    "field",
    "state",
    "storage",
)
VALIDATION_TERMS = (
    *VALIDATION_TERMS,
    "test",
    "acceptance",
    "validation",
    "error",
    "edge",
    "boundary",
)
RISK_TERMS = (
    *RISK_TERMS,
    "risk",
    "assumption",
    "open question",
    "limit",
    "dependency",
)


def evaluate_design_document(document: str, *, min_score: int = 70) -> DesignQualityResult:
    """Score whether a design document is actionable enough for downstream agents."""
    text = document.lower()
    dimensions = {
        "sections": _coverage_score(text, SECTION_TERMS),
        "requirement_trace": _coverage_score(text, REQUIREMENT_TRACE_TERMS),
        "implementation_boundary": _coverage_score(text, IMPLEMENTATION_BOUNDARY_TERMS),
        "architecture": _coverage_score(text, ARCHITECTURE_TERMS),
        "data_state": _coverage_score(text, DATA_STATE_TERMS),
        "validation": _coverage_score(text, VALIDATION_TERMS),
        "risk": _coverage_score(text, RISK_TERMS),
    }
    weights = {
        "sections": 20,
        "requirement_trace": 15,
        "implementation_boundary": 15,
        "architecture": 20,
        "data_state": 10,
        "validation": 10,
        "risk": 10,
    }
    score = sum(int(dimensions[name] * weight / 100) for name, weight in weights.items())
    findings = [
        f"{name} coverage is weak ({value})"
        for name, value in dimensions.items()
        if value < 50
    ]
    passed = score >= min_score and not any(
        dimensions[name] < 50
        for name in ("sections", "requirement_trace", "implementation_boundary", "architecture", "validation")
    )
    return DesignQualityResult(
        score=score,
        passed=passed,
        findings=findings,
        dimension_scores=dimensions,
    )


def _coverage_score(text: str, terms: tuple[str, ...]) -> int:
    if not terms:
        return 100
    groups = [
        [term for term in terms if not term.isascii()],
        [term for term in terms if term.isascii()],
    ]
    scores = []
    for group in groups:
        if not group:
            continue
        covered = [term for term in group if term.lower() in text]
        scores.append(int(len(covered) / len(group) * 100))
    covered = [term for term in terms if term.lower() in text]
    scores.append(int(len(covered) / len(terms) * 100))
    return max(scores)


__all__ = ["DesignQualityResult", "evaluate_design_document"]
